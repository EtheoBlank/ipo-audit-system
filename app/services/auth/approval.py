"""五级签字流引擎 (Approval Workflow Engine).

工作流模板:
  - 默认流程是 5 步: assistant → manager → partner → qc_partner → signing_partner
  - 调用方可在创建审批时传自定义 ``steps`` 覆盖

状态机:
  pending → in_progress (第一步开始)
  in_progress → approved (所有步骤 approve)
  in_progress → rejected (任一步骤 reject, 流程立即结束)
  pending / in_progress → withdrawn (发起人主动撤回)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.auth import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_IN_PROGRESS,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    APPROVAL_STATUS_WITHDRAWN,
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    ROLE_PARTNER,
    ROLE_QC_PARTNER,
    ROLE_SIGNING_PARTNER,
    ApprovalStep,
    ApprovalWorkflow,
    User,
)
from app.services.auth.rbac import role_at_least

logger = logging.getLogger(__name__)


class InvalidApprovalAction(Exception):
    """审批动作非法 (顺序错 / 权限不足 / 已结束)."""


@dataclass
class StepSpec:
    step_no: int
    required_role: str
    approver_user_id: Optional[int] = None


DEFAULT_FIVE_LEVEL_FLOW: List[StepSpec] = [
    StepSpec(step_no=1, required_role=ROLE_ASSISTANT),
    StepSpec(step_no=2, required_role=ROLE_MANAGER),
    StepSpec(step_no=3, required_role=ROLE_PARTNER),
    StepSpec(step_no=4, required_role=ROLE_QC_PARTNER),
    StepSpec(step_no=5, required_role=ROLE_SIGNING_PARTNER),
]


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ApprovalEngine:
    """高层编排."""

    @staticmethod
    async def create_workflow(
        db: AsyncSession,
        *,
        initiator: Optional[User],
        resource_type: str,
        resource_id: int,
        title: str,
        description: Optional[str] = None,
        project_id: Optional[int] = None,
        steps: Optional[List[StepSpec]] = None,
    ) -> ApprovalWorkflow:
        steps = steps or DEFAULT_FIVE_LEVEL_FLOW
        if not steps:
            raise InvalidApprovalAction("审批步骤不能为空")
        # 防御: step_no 必须 1..N 连续
        nos = sorted({s.step_no for s in steps})
        if nos != list(range(1, len(steps) + 1)):
            raise InvalidApprovalAction("step_no 必须从 1 开始连续")

        wf = ApprovalWorkflow(
            project_id=project_id,
            resource_type=resource_type,
            resource_id=resource_id,
            title=title,
            description=description,
            total_steps=len(steps),
            current_step=1,
            status=APPROVAL_STATUS_IN_PROGRESS,
            initiator_user_id=initiator.id if initiator else None,
            initiator_display=initiator.full_name if initiator else None,
            definition=json.dumps(
                [
                    {
                        "step_no": s.step_no,
                        "required_role": s.required_role,
                        "approver_user_id": s.approver_user_id,
                    }
                    for s in steps
                ],
                ensure_ascii=False,
            ),
            created_at=_utcnow_naive(),
            updated_at=_utcnow_naive(),
        )
        db.add(wf)
        await db.flush()

        for s in steps:
            db.add(
                ApprovalStep(
                    workflow_id=wf.id,
                    step_no=s.step_no,
                    required_role=s.required_role,
                    approver_user_id=s.approver_user_id,
                    created_at=_utcnow_naive(),
                )
            )
        await db.commit()
        await db.refresh(wf)
        return wf

    @staticmethod
    async def get_workflow(db: AsyncSession, workflow_id: int) -> Optional[ApprovalWorkflow]:
        stmt = select(ApprovalWorkflow).where(ApprovalWorkflow.id == workflow_id)
        wf = (await db.execute(stmt)).scalar_one_or_none()
        if wf is None:
            return None
        # 预加载 steps
        steps = list(
            (
                await db.execute(
                    select(ApprovalStep)
                    .where(ApprovalStep.workflow_id == wf.id)
                    .order_by(ApprovalStep.step_no)
                )
            ).scalars().all()
        )
        wf.steps = steps  # type: ignore[attr-defined]
        return wf

    @staticmethod
    async def decide(
        db: AsyncSession,
        *,
        workflow_id: int,
        actor: User,
        action: str,
        comment: Optional[str] = None,
        delegate_to_user_id: Optional[int] = None,
    ) -> ApprovalWorkflow:
        wf = await ApprovalEngine.get_workflow(db, workflow_id)
        if wf is None:
            raise InvalidApprovalAction(f"workflow_id={workflow_id} 不存在")
        if wf.status in {
            APPROVAL_STATUS_APPROVED,
            APPROVAL_STATUS_REJECTED,
            APPROVAL_STATUS_WITHDRAWN,
        }:
            raise InvalidApprovalAction(f"流程已结束 ({wf.status}), 不能再操作")

        # 找到当前步骤
        current_step: Optional[ApprovalStep] = next(
            (s for s in wf.steps if s.step_no == wf.current_step), None
        )
        if current_step is None:
            raise InvalidApprovalAction(f"当前步骤 {wf.current_step} 不存在 (数据损坏)")

        # 权限检查 — 角色必须 >= required_role
        if not role_at_least(actor.role, current_step.required_role):
            raise InvalidApprovalAction(
                f"角色 {actor.role} 不足以处理需 {current_step.required_role} 的步骤"
            )
        # 如果指定了 approver_user_id, 只能本人操作
        if current_step.approver_user_id and current_step.approver_user_id != actor.id:
            raise InvalidApprovalAction(
                f"该步骤指定 user_id={current_step.approver_user_id} 处理, 你无权"
            )

        now = _utcnow_naive()
        current_step.action = action
        current_step.comment = comment
        current_step.approver_user_id = actor.id
        current_step.approver_display = actor.full_name
        current_step.decided_at = now

        if action == "reject":
            wf.status = APPROVAL_STATUS_REJECTED
            wf.completed_at = now
        elif action == "approve":
            if wf.current_step >= wf.total_steps:
                wf.status = APPROVAL_STATUS_APPROVED
                wf.completed_at = now
            else:
                wf.current_step += 1
        elif action == "delegate":
            if delegate_to_user_id is None:
                raise InvalidApprovalAction("delegate 必须指定 delegate_to_user_id")
            current_step.approver_user_id = delegate_to_user_id
            current_step.action = None  # 重置, 等被委托人决定
            current_step.decided_at = None
        elif action == "comment":
            # 留言, 不改变状态 / 步骤
            current_step.action = None
            current_step.decided_at = None
        else:
            raise InvalidApprovalAction(f"未知 action: {action}")

        wf.updated_at = now
        await db.commit()
        await db.refresh(wf)
        # 重新预加载 steps
        return await ApprovalEngine.get_workflow(db, wf.id)  # type: ignore[return-value]

    @staticmethod
    async def withdraw(
        db: AsyncSession,
        *,
        workflow_id: int,
        actor: User,
    ) -> ApprovalWorkflow:
        wf = await ApprovalEngine.get_workflow(db, workflow_id)
        if wf is None:
            raise InvalidApprovalAction(f"workflow_id={workflow_id} 不存在")
        if wf.initiator_user_id and wf.initiator_user_id != actor.id and not role_at_least(
            actor.role, ROLE_QC_PARTNER
        ):
            raise InvalidApprovalAction("仅发起人或质控合伙人以上可撤回")
        if wf.status in {
            APPROVAL_STATUS_APPROVED,
            APPROVAL_STATUS_REJECTED,
            APPROVAL_STATUS_WITHDRAWN,
        }:
            raise InvalidApprovalAction(f"流程已结束 ({wf.status})")
        now = _utcnow_naive()
        wf.status = APPROVAL_STATUS_WITHDRAWN
        wf.completed_at = now
        wf.updated_at = now
        await db.commit()
        await db.refresh(wf)
        return await ApprovalEngine.get_workflow(db, wf.id)  # type: ignore[return-value]
