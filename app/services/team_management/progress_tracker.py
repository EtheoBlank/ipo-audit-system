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
    Project,
    ProjectAssignment,
    TeamMember,
    WorkPlan,
    WorkPlanItem,
    TASK_STATUS_DONE,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_IN_PROGRESS,
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

        # 3) 拉所有 WorkPlanItem — P1 性能 (2026-06-19): 旧版拉全表 + Python O(M×N) 计数
        # 新版: SQL GROUP BY member_id, status 一次拿 count, dict lookup O(1)
        items_agg_q = await db.execute(
            select(
                WorkPlanItem.member_id,
                WorkPlanItem.status,
                func.count(WorkPlanItem.id),
            )
            .join(WorkPlan, WorkPlan.id == WorkPlanItem.plan_id)
            .where(WorkPlan.project_id == project_id)
            .group_by(WorkPlanItem.member_id, WorkPlanItem.status)
        )
        # {(member_id, status): count} + {(member_id): total}
        status_count_by_member: dict[int, dict[str, int]] = {}
        total_by_member: dict[int, int] = {}
        for mid, st, cnt in items_agg_q.all():
            mid = int(mid) if mid is not None else 0
            status_count_by_member.setdefault(mid, {})[st] = int(cnt)
            total_by_member[mid] = total_by_member.get(mid, 0) + int(cnt)

        # 4) 拉近 7 天 DailyReport — P1 (2026-06-19): 同理 GROUP BY member_id 一次性聚合
        from datetime import date, timedelta as _td

        seven_days_ago = (date.today() - _td(days=7)).isoformat()
        reports_agg_q = await db.execute(
            select(
                DailyReport.member_id,
                func.sum(DailyReport.hours_logged),
                func.max(DailyReport.report_date),
            )
            .where(
                DailyReport.project_id == project_id,
                DailyReport.report_date >= seven_days_ago,
            )
            .group_by(DailyReport.member_id)
        )
        hours_by_member: dict[int, float] = {}
        last_report_by_member: dict[int, str] = {}
        for mid, hours_sum, max_date in reports_agg_q.all():
            mid = int(mid)
            hours_by_member[mid] = float(hours_sum or 0)
            if max_date is not None:
                last_report_by_member[mid] = max_date

        # 5) 拉卡点 — 同理 GROUP BY
        blockers_agg_q = await db.execute(
            select(Blocker.member_id, func.count(Blocker.id))
            .where(
                Blocker.project_id == project_id,
                Blocker.status.in_(
                    [BLOCKER_STATUS_OPEN, BLOCKER_STATUS_IN_PROGRESS, BLOCKER_STATUS_ESCALATED]
                ),
            )
            .group_by(Blocker.member_id)
        )
        open_blockers_by_member: dict[int, int] = {
            int(mid): int(cnt) for mid, cnt in blockers_agg_q.all()
        }

        out: list[MemberProgressData] = []
        for m in members:
            sc = status_count_by_member.get(m.id, {})
            total = total_by_member.get(m.id, 0)
            done = sc.get(TASK_STATUS_DONE, 0)
            inprog = sc.get(TASK_STATUS_IN_PROGRESS, 0)
            blocked = sc.get(TASK_STATUS_BLOCKED, 0)
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
    async def collect_project_summary(db: AsyncSession, project_id: int) -> dict[str, Any]:
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
    async def collect_blocker_summary(db: AsyncSession, project_id: int) -> dict[str, Any]:
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
        # raised_at 字段在项目中统一为 naive UTC (utc_now()), 这里用 naive now 保持一致
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ages: list[float] = []
        for b in blockers:
            raised = b.raised_at
            if raised is None:
                continue
            # 兼容历史数据: 若 raised_at 是 tz-aware, 转 naive
            if raised.tzinfo is not None:
                raised = raised.replace(tzinfo=None)
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
