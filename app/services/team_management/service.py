"""TeamManagementService — 高层编排。

负责把 work_plan_generator / quality_assessor / recommendation_generator /
progress_tracker 串起来，暴露给 API 层使用。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    Blocker,
    DailyReport,
    ManagementRecommendation,
    Meeting,
    MeetingRecord,
    Project,
    WorkPlan,
    WorkPlanItem,
    WORK_PLAN_STATUS_DRAFT,
    WORK_PLAN_STATUS_ACTIVE,
    TASK_STATUS_DONE,
    BLOCKER_STATUS_OPEN,
    BLOCKER_STATUS_IN_PROGRESS,
    BLOCKER_STATUS_ESCALATED,
)
from app.models.team_management import (
    MeetingRecordCreate,
    WorkPlanUpdate,
)
from app.services.team_management.progress_tracker import ProgressTracker
from app.services.team_management.quality_assessor import (
    MeetingQualityAssessor,
    MeetingQualityContext,
    MeetingQualityResult,
    meeting_quality_assessor,
)
from app.services.team_management.recommendation_generator import (
    ManagementRecommendationGenerator,
    RecommendationContext,
    RecommendationResult,
    management_recommendation_generator,
)
from app.services.team_management.work_plan_generator import (
    WorkPlanGenerated,
    WorkPlanGenerator,
    work_plan_generator,
)

logger = logging.getLogger(__name__)


class TeamManagementService:
    """团队管理服务 — 编排所有子服务。"""

    def __init__(
        self,
        wpg: Optional[WorkPlanGenerator] = None,
        qa: Optional[MeetingQualityAssessor] = None,
        rg: Optional[ManagementRecommendationGenerator] = None,
    ) -> None:
        self.work_plan_generator = wpg or work_plan_generator
        self.quality_assessor = qa or meeting_quality_assessor
        self.recommendation_generator = rg or management_recommendation_generator

    # ============================================================
    #  账套导入钩子
    # ============================================================

    async def on_accounts_imported(
        self,
        db: AsyncSession,
        project_id: int,
        import_kind: str,
        record_count: int,
    ) -> Optional[dict[str, Any]]:
        """账套导入后的钩子 — 若项目尚无 active 工作计划，AI 自动生成。

        失败 try/except 兜底不阻塞原始导入流程。
        """
        try:
            # 检查是否已有 active / draft 计划
            existing = (
                await db.execute(
                    select(WorkPlan).where(
                        WorkPlan.project_id == project_id,
                        WorkPlan.status.in_([WORK_PLAN_STATUS_DRAFT, WORK_PLAN_STATUS_ACTIVE]),
                    )
                )
            ).scalar_one_or_none()
            if existing:
                logger.info(
                    "项目 %s 已有工作计划 (#%s)，跳过自动生成",
                    project_id,
                    existing.id,
                )
                return {"existing_plan_id": existing.id, "skipped": True}

            plan = await self.generate_work_plan(db, project_id)
            return {
                "existing_plan_id": None,
                "skipped": False,
                "plan_id": plan["plan_id"],
                "item_count": plan["item_count"],
                "ai_enabled": plan["ai_enabled"],
            }
        except Exception:  # noqa: BLE001
            logger.exception("账套导入钩子失败，不阻塞主流程")
            return None

    # ============================================================
    #  工作计划
    # ============================================================

    async def generate_work_plan(self, db: AsyncSession, project_id: int) -> dict[str, Any]:
        """为项目生成工作计划（AI + 模板兜底）。"""
        ctx = await self.work_plan_generator.build_context(db, project_id)
        result: WorkPlanGenerated = await self.work_plan_generator.generate(ctx)

        plan = WorkPlan(
            project_id=project_id,
            name=result.name,
            status=WORK_PLAN_STATUS_DRAFT,
            generated_by="ai" if result.ai_enabled else "template",
            total_estimated_hours=sum(
                float(x.get("estimated_hours", 0) or 0) for x in result.items
            ),
            ai_prompt_used=result.prompt_used[:5000] if result.prompt_used else None,
            ai_enabled=result.ai_enabled,
        )
        db.add(plan)
        await db.flush()  # 拿 plan.id

        for idx, raw in enumerate(result.items):
            item = WorkPlanItem(
                plan_id=plan.id,
                title=raw["title"],
                description=raw.get("description"),
                related_module=raw.get("related_module"),
                priority=raw.get("priority", "medium"),
                status="pending",
                estimated_hours=float(raw.get("estimated_hours", 0) or 0),
                recommended_level=raw.get("recommended_level"),
                sort_order=idx,
            )
            db.add(item)
        await db.commit()
        await db.refresh(plan)
        return {
            "plan_id": plan.id,
            "item_count": len(result.items),
            "ai_enabled": result.ai_enabled,
        }

    async def update_work_plan(
        self, db: AsyncSession, plan_id: int, update: WorkPlanUpdate
    ) -> WorkPlan:
        plan = (
            await db.execute(select(WorkPlan).where(WorkPlan.id == plan_id))
        ).scalar_one_or_none()
        if not plan:
            raise ValueError(f"工作计划不存在: {plan_id}")
        for k, v in update.model_dump(exclude_unset=True).items():
            if v is not None:
                setattr(plan, k, v)
        await db.commit()
        await db.refresh(plan)
        return plan

    async def update_work_plan_item(
        self, db: AsyncSession, item_id: int, payload: dict[str, Any]
    ) -> WorkPlanItem:
        """更新计划任务 — 严格白名单字段。

        不允许修改 id / plan_id / created_at / parent_item_id 等系统字段。
        status 切换到 done 时自动写 completed_at；从 done 切回其它状态时**保留**
        completed_at 以保审计可追溯。
        """
        # 字段白名单 — 与 WorkPlanItemUpdate schema 保持一致
        allowed = {
            "title",
            "description",
            "member_id",
            "related_module",
            "priority",
            "status",
            "estimated_hours",
            "actual_hours",
            "start_date",
            "due_date",
            "sort_order",
            "recommended_level",
        }
        forbidden = set(payload.keys()) - allowed
        if forbidden:
            raise ValueError(f"不允许修改系统字段: {sorted(forbidden)}")

        item = (
            await db.execute(select(WorkPlanItem).where(WorkPlanItem.id == item_id))
        ).scalar_one_or_none()
        if not item:
            raise ValueError(f"计划任务不存在: {item_id}")

        new_status = payload.get("status")
        if new_status and new_status != item.status and new_status == TASK_STATUS_DONE:
            # 切到 done 时自动写完成时间；从 done 切走保留时间戳
            if not item.completed_at:
                item.completed_at = datetime.now(timezone.utc)

        for k, v in payload.items():
            if k in allowed and v is not None:
                setattr(item, k, v)
        await db.commit()
        await db.refresh(item)
        return item

    # ============================================================
    #  会议纪要 + AI 质量评估
    # ============================================================

    async def submit_meeting_record(
        self, db: AsyncSession, meeting_id: int, payload: MeetingRecordCreate
    ) -> MeetingRecord:
        meeting = (
            await db.execute(select(Meeting).where(Meeting.id == meeting_id))
        ).scalar_one_or_none()
        if not meeting:
            raise ValueError(f"会议不存在: {meeting_id}")

        ctx = MeetingQualityContext(
            meeting_title=meeting.title,
            meeting_type=meeting.meeting_type,
            content=payload.content,
            decisions=payload.decisions or [],
            action_items=payload.action_items or [],
            attendees=payload.attendees or [],
        )
        assessment: MeetingQualityResult = await self.quality_assessor.assess(ctx)

        # 序列化 JSON 字段
        decisions_json = (
            json.dumps(payload.decisions, ensure_ascii=False) if payload.decisions else None
        )
        actions_json = (
            json.dumps(payload.action_items, ensure_ascii=False) if payload.action_items else None
        )
        attendees_json = (
            json.dumps(payload.attendees, ensure_ascii=False) if payload.attendees else None
        )
        assessment_json = json.dumps(
            {
                "strengths": assessment.strengths,
                "weaknesses": assessment.weaknesses,
                "suggestions": assessment.suggestions,
            },
            ensure_ascii=False,
        )

        # 是否已有 record？更新
        existing = (
            await db.execute(select(MeetingRecord).where(MeetingRecord.meeting_id == meeting_id))
        ).scalar_one_or_none()
        if existing:
            existing.content = payload.content
            existing.decisions = decisions_json
            existing.action_items = actions_json
            existing.attendees = attendees_json
            existing.quality_score = assessment.quality_score
            existing.ai_assessment = assessment_json
            existing.ai_enabled = assessment.ai_enabled
            existing.recorded_by = payload.recorded_by
            await db.commit()
            await db.refresh(existing)
            return existing

        record = MeetingRecord(
            meeting_id=meeting_id,
            content=payload.content,
            decisions=decisions_json,
            action_items=actions_json,
            attendees=attendees_json,
            quality_score=assessment.quality_score,
            ai_assessment=assessment_json,
            ai_enabled=assessment.ai_enabled,
            recorded_by=payload.recorded_by,
        )
        db.add(record)
        # 会议状态自动完成
        meeting.status = "completed"
        await db.commit()
        await db.refresh(record)
        return record

    # ============================================================
    #  管理建议生成
    # ============================================================

    async def generate_recommendations(
        self,
        db: AsyncSession,
        project_id: int,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> ManagementRecommendation:
        from sqlalchemy import func
        from datetime import date

        proj = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if not proj:
            raise ValueError(f"项目不存在: {project_id}")

        proj_summary = await ProgressTracker.collect_project_summary(db, project_id)
        blocker_summary = await ProgressTracker.collect_blocker_summary(db, project_id)
        members = await ProgressTracker.collect_member_progress(db, project_id)

        # 实际查询逾期任务数（之前硬编码 0 — 修复后建议引擎能正确触发"逾期"分支）
        today_str = date.today().isoformat()
        overdue_count = (
            await db.execute(
                select(func.count(WorkPlanItem.id))
                .join(WorkPlan, WorkPlan.id == WorkPlanItem.plan_id)
                .where(
                    WorkPlan.project_id == project_id,
                    WorkPlanItem.due_date.is_not(None),
                    WorkPlanItem.due_date < today_str,
                    WorkPlanItem.status.notin_([TASK_STATUS_DONE, "cancelled"]),
                )
            )
        ).scalar() or 0

        # 拉近 15 条日报
        reports_q = await db.execute(
            select(DailyReport)
            .where(DailyReport.project_id == project_id)
            .order_by(DailyReport.submitted_at.desc())
            .limit(15)
        )
        reports = reports_q.scalars().all()
        summaries = [
            f"[{r.report_date}] {r.completed_work[:120]}" for r in reports if r.completed_work
        ]

        # 拉卡点清单
        blockers_q = await db.execute(
            select(Blocker).where(
                Blocker.project_id == project_id,
                Blocker.status.in_(
                    [BLOCKER_STATUS_OPEN, BLOCKER_STATUS_IN_PROGRESS, BLOCKER_STATUS_ESCALATED]
                ),
            )
        )
        blockers = blockers_q.scalars().all()
        now = datetime.now(timezone.utc)
        recent_blockers_data: list[dict[str, Any]] = []
        for b in blockers:
            age_hours = 0.0
            if b.raised_at:
                try:
                    age_hours = (now - b.raised_at).total_seconds() / 3600.0
                except Exception:  # noqa: BLE001
                    pass
            recent_blockers_data.append(
                {
                    "title": b.title,
                    "severity": b.severity,
                    "days_open": round(age_hours / 24.0, 1),
                }
            )

        ctx = RecommendationContext(
            project_id=project_id,
            project_name=proj.name,
            completion_rate=proj_summary["completion_rate"],
            blocked_count=blocker_summary["total_open"],
            critical_blockers=blocker_summary["critical"],
            overdue_items=int(overdue_count),
            members_load=[
                {
                    "name": m.full_name,
                    "level": m.level,
                    "completion_rate": m.completion_rate,
                    "hours_logged_7d": m.hours_logged_7d,
                    "open_blockers": m.open_blockers,
                }
                for m in members
            ],
            recent_blockers=recent_blockers_data,
            recent_summaries=summaries,
            period_start=period_start,
            period_end=period_end,
        )
        result: RecommendationResult = await self.recommendation_generator.generate(ctx)

        rec = ManagementRecommendation(
            project_id=project_id,
            period_start=period_start,
            period_end=period_end,
            findings=json.dumps(result.findings, ensure_ascii=False),
            priority_actions=json.dumps(result.priority_actions, ensure_ascii=False),
            recommendations=result.recommendations,
            ai_enabled=result.ai_enabled,
            ai_raw=json.dumps(result.ai_raw, ensure_ascii=False) if result.ai_raw else None,
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)
        return rec

    async def confirm_recommendation(
        self,
        db: AsyncSession,
        rec_id: int,
        confirmed_by_user,
        manager_notes: Optional[str] = None,
    ) -> ManagementRecommendation:
        """P1 修复 (2026-06-19): 旧 confirmed_by: str 自由文本, 任何人可伪造.

        现传 User 对象, 服务端取 full_name 写入 + user_id 入 confirmed_by_user_id 留审计追溯.
        """
        rec = (
            await db.execute(
                select(ManagementRecommendation).where(ManagementRecommendation.id == rec_id)
            )
        ).scalar_one_or_none()
        if not rec:
            raise ValueError(f"管理建议不存在: {rec_id}")
        rec.is_confirmed = True
        rec.confirmed_by = confirmed_by_user.full_name or confirmed_by_user.username
        # P1 (2026-06-19): ORM 加 confirmed_by_user_id 列, 强审计追溯
        if hasattr(rec, "confirmed_by_user_id"):
            rec.confirmed_by_user_id = confirmed_by_user.id
        rec.confirmed_at = datetime.now(timezone.utc)
        if manager_notes is not None:
            rec.manager_notes = manager_notes
        await db.commit()
        await db.refresh(rec)
        return rec


# 全局单例
team_management_service = TeamManagementService()
