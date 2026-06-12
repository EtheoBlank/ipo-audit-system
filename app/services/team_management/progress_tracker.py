"""进度聚合 — 把 WorkPlanItem / DailyReport / Blocker 聚合成 ProgressDashboard。

只读操作，不写入数据库（快照写入由调用方决定）。
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    Blocker,
    BLOCKER_STATUS_OPEN,
    BLOCKER_STATUS_IN_PROGRESS,
    BLOCKER_STATUS_ESCALATED,
    DailyReport,
    MEMBER_LEVEL_LABELS,
    Project,
    ProjectAssignment,
    TeamMember,
    WorkPlan,
    WorkPlanItem,
    TASK_STATUS_DONE,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_PENDING,
    TASK_STATUS_CANCELLED,
)

logger = logging.getLogger(__name__)


# ============================================================
#  占位 — WorkPlanItem 已包含 status，实际不需 Task
# ============================================================


@dataclass
class MemberProgressData:
    """人员进度聚合结果。"""

    member_id: int
    full_name: str
    level: str
    total_items: int
    completed_items: int
    in_progress_items: int
    blocked_items: int
    completion_rate: float
    hours_logged_7d: float
    open_blockers: int
    last_report_date: Optional[str]


# 兼容旧名字 — 上面已定义


class ProgressTracker:
    """进度聚合器 — 全部只读。"""

    @staticmethod
    async def collect_member_progress(
        db: AsyncSession, project_id: int
    ) -> list[MemberProgressData]:
        """聚合项目内每个人员的进度数据。"""
        # 1) 拉项目
        proj = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if not proj:
            return []

        # 2) 拉项目分配的人员
        members_q = await db.execute(
            select(TeamMember, ProjectAssignment)
            .join(ProjectAssignment, ProjectAssignment.member_id == TeamMember.id)
            .where(ProjectAssignment.project_id == project_id)
        )
        members = [m for (m, _a) in members_q.all()]
        if not members:
            return []

        # 3) 拉所有 WorkPlanItem
        items_q = await db.execute(
            select(WorkPlanItem)
            .join(WorkPlan, WorkPlan.id == WorkPlanItem.plan_id)
            .where(WorkPlan.project_id == project_id)
        )
        items = items_q.scalars().all()
        items_by_member: dict[Optional[int], list[WorkPlanItem]] = {}
        for it in items:
            items_by_member.setdefault(it.member_id, []).append(it)

        # 4) 拉近 7 天 DailyReport
        from datetime import date, timedelta as _td
        seven_days_ago = (date.today() - _td(days=7)).isoformat()
        reports_q = await db.execute(
            select(DailyReport)
            .where(
                DailyReport.project_id == project_id,
                DailyReport.report_date >= seven_days_ago,
            )
            .order_by(DailyReport.report_date.desc())
            .limit(500)
        )
        reports = reports_q.scalars().all()

        hours_by_member: dict[int, float] = {}
        last_report_by_member: dict[int, str] = {}
        for r in reports:
            # 严格 7 天过滤 — 与字段名 hours_logged_7d 保持一致
            hours_by_member[r.member_id] = hours_by_member.get(r.member_id, 0.0) + float(
                r.hours_logged or 0
            )
            key = r.member_id
            if key not in last_report_by_member:
                last_report_by_member[key] = r.report_date

        # 5) 拉卡点
        blockers_q = await db.execute(
            select(Blocker).where(
                Blocker.project_id == project_id,
                Blocker.status.in_(
                    [BLOCKER_STATUS_OPEN, BLOCKER_STATUS_IN_PROGRESS, BLOCKER_STATUS_ESCALATED]
                ),
            )
        )
        blockers = blockers_q.scalars().all()
        open_blockers_by_member: dict[int, int] = {}
        for b in blockers:
            open_blockers_by_member[b.member_id] = open_blockers_by_member.get(b.member_id, 0) + 1

        out: list[MemberProgressData] = []
        for m in members:
            mi = items_by_member.get(m.id, [])
            total = len(mi)
            done = sum(1 for x in mi if x.status == TASK_STATUS_DONE)
            inprog = sum(1 for x in mi if x.status == TASK_STATUS_IN_PROGRESS)
            blocked = sum(1 for x in mi if x.status == TASK_STATUS_BLOCKED)
            rate = (done / total) if total > 0 else 0.0
            out.append(
                MemberProgressData(
                    member_id=m.id,
                    full_name=m.full_name,
                    level=m.level,
                    total_items=total,
                    completed_items=done,
                    in_progress_items=inprog,
                    blocked_items=blocked,
                    completion_rate=round(rate, 3),
                    hours_logged_7d=round(hours_by_member.get(m.id, 0.0), 1),
                    open_blockers=open_blockers_by_member.get(m.id, 0),
                    last_report_date=last_report_by_member.get(m.id),
                )
            )
        return out

    @staticmethod
    async def collect_project_summary(
        db: AsyncSession, project_id: int
    ) -> dict[str, Any]:
        """聚合项目级摘要（不展开人员）。"""
        items_q = await db.execute(
            select(WorkPlanItem)
            .join(WorkPlan, WorkPlan.id == WorkPlanItem.plan_id)
            .where(WorkPlan.project_id == project_id)
        )
        items = items_q.scalars().all()

        total = len(items)
        done = sum(1 for x in items if x.status == TASK_STATUS_DONE)
        inprog = sum(1 for x in items if x.status == TASK_STATUS_IN_PROGRESS)
        blocked = sum(1 for x in items if x.status == TASK_STATUS_BLOCKED)
        est_hours = sum(float(x.estimated_hours or 0) for x in items)
        act_hours = sum(float(x.actual_hours or 0) for x in items)
        rate = (done / total) if total > 0 else 0.0

        by_module = Counter(
            (x.related_module or "其他") for x in items if x.status != TASK_STATUS_CANCELLED
        )
        by_status = Counter(x.status for x in items)

        return {
            "total_items": total,
            "completed_items": done,
            "in_progress_items": inprog,
            "blocked_items": blocked,
            "completion_rate": round(rate, 3),
            "total_estimated_hours": round(est_hours, 1),
            "total_actual_hours": round(act_hours, 1),
            "by_module": dict(by_module),
            "by_status": dict(by_status),
        }

    @staticmethod
    async def collect_blocker_summary(
        db: AsyncSession, project_id: int
    ) -> dict[str, Any]:
        """卡点摘要 — 数量 / 严重度分布 / 平均存续时长。"""
        blockers_q = await db.execute(
            select(Blocker).where(
                Blocker.project_id == project_id,
                Blocker.status.in_(
                    [BLOCKER_STATUS_OPEN, BLOCKER_STATUS_IN_PROGRESS, BLOCKER_STATUS_ESCALATED]
                ),
            )
        )
        blockers = blockers_q.scalars().all()

        if not blockers:
            return {
                "total_open": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "avg_age_hours": 0.0,
            }

        from app.models.db_models import (
            BLOCKER_SEVERITY_LOW,
            BLOCKER_SEVERITY_MEDIUM,
            BLOCKER_SEVERITY_HIGH,
            BLOCKER_SEVERITY_CRITICAL,
        )

        c = Counter(b.severity for b in blockers)
        now = datetime.now(timezone.utc)
        ages: list[float] = []
        for b in blockers:
            raised = b.raised_at
            if raised is None:
                continue
            # raised_at 是 tz-aware utc
            try:
                delta = (now - raised).total_seconds() / 3600.0
                ages.append(delta)
            except Exception:  # noqa: BLE001
                continue
        avg_age = sum(ages) / len(ages) if ages else 0.0

        return {
            "total_open": len(blockers),
            "critical": c.get(BLOCKER_SEVERITY_CRITICAL, 0),
            "high": c.get(BLOCKER_SEVERITY_HIGH, 0),
            "medium": c.get(BLOCKER_SEVERITY_MEDIUM, 0),
            "low": c.get(BLOCKER_SEVERITY_LOW, 0),
            "avg_age_hours": round(avg_age, 1),
        }
