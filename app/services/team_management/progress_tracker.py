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
from sqlalchemy import func  # round 28 P1-12 SQL 聚合

logger = logging.getLogger(__name__)


# ============================================================
#  占位 — WorkPlanItem 已包含 status，实际不需 Task
# ============================================================


# Round 35 P1: NULL member_id 的 WorkPlanItem 用这个 sentinel 标识.
# 与已有 member.id=0 的合法记录区分 (避免无声吞 unassigned 任务).
UNASSIGNED_MEMBER_ID = -1
_UNASSIGNED_NAME = "(未分配)"
_UNASSIGNED_LEVEL = "—"


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
    # Round 35 P1: 显式 flag — 调用方能区分"该成员真的没任务" vs "全部任务未分配".
    is_unassigned: bool = False


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
        # Round 35 P1: NULL member_id 之前被 int(...) 当 0, 与合法 member.id=0 撞车,
        # 且这些任务 "消失" 在 0 桶里. 改成单独 UNASSIGNED_MEMBER_ID=-1 桶 + 后续产出
        # is_unassigned=True 的伪成员行.
        status_count_by_member: dict[int, dict[str, int]] = {}
        total_by_member: dict[int, int] = {}
        unassigned_total: int = 0
        unassigned_status_count: dict[str, int] = {}
        for mid, st, cnt in items_agg_q.all():
            cnt = int(cnt)
            if mid is None:
                unassigned_total += cnt
                unassigned_status_count[st] = unassigned_status_count.get(st, 0) + cnt
                continue
            mid_int = int(mid)
            status_count_by_member.setdefault(mid_int, {})[st] = cnt
            total_by_member[mid_int] = total_by_member.get(mid_int, 0) + cnt

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
        # Round 35 P1: 即使没人分配, 也要把 unassigned 桶暴露出来, 否则这些任务
        # 在 Dashboard 完全消失, 项目经理看不到 "有 N 个任务没分配".
        if unassigned_total > 0:
            done_u = unassigned_status_count.get(TASK_STATUS_DONE, 0)
            inprog_u = unassigned_status_count.get(TASK_STATUS_IN_PROGRESS, 0)
            blocked_u = unassigned_status_count.get(TASK_STATUS_BLOCKED, 0)
            rate_u = (done_u / unassigned_total) if unassigned_total > 0 else 0.0
            out.append(
                MemberProgressData(
                    member_id=UNASSIGNED_MEMBER_ID,
                    full_name=_UNASSIGNED_NAME,
                    level=_UNASSIGNED_LEVEL,
                    total_items=unassigned_total,
                    completed_items=done_u,
                    in_progress_items=inprog_u,
                    blocked_items=blocked_u,
                    completion_rate=round(rate_u, 3),
                    hours_logged_7d=0.0,
                    open_blockers=0,
                    last_report_date=None,
                    is_unassigned=True,
                )
            )
        return out

    @staticmethod
    async def collect_project_summary(db: AsyncSession, project_id: int) -> dict[str, Any]:
        """聚合项目级摘要（不展开人员）。

        round 28 P1-12 沿用 round 12 模式, 项目级也 SQL 聚合:
          - WorkPlanItem GROUP BY status: 状态计数 + 工时
          - ProjectAssignment GROUP BY project_id: 成员计数
          - DailyReport GROUP BY project_id: 日报计数
        替代 Python 循环 + len() 累加, 大项目从 O(N) Python 全扫降到 O(1) SQL.
        """
        # 1) 项目任务状态聚合 — 一次拿全
        items_status_q = await db.execute(
            select(
                WorkPlanItem.status,
                func.count(WorkPlanItem.id),
                func.coalesce(func.sum(WorkPlanItem.estimated_hours), 0),
                func.coalesce(func.sum(WorkPlanItem.actual_hours), 0),
            )
            .join(WorkPlan, WorkPlan.id == WorkPlanItem.plan_id)
            .where(WorkPlan.project_id == project_id)
            .group_by(WorkPlanItem.status)
        )
        total = 0
        done = 0
        inprog = 0
        blocked = 0
        est_hours = 0.0
        act_hours = 0.0
        by_status: dict[str, int] = {}
        for status, cnt, est_sum, act_sum in items_status_q.all():
            cnt = int(cnt or 0)
            est_sum = float(est_sum or 0)
            act_sum = float(act_sum or 0)
            total += cnt
            est_hours += est_sum
            act_hours += act_sum
            by_status[status] = cnt
            if status == TASK_STATUS_DONE:
                done = cnt
            elif status == TASK_STATUS_IN_PROGRESS:
                inprog = cnt
            elif status == TASK_STATUS_BLOCKED:
                blocked = cnt

        # 2) by_module — 需要相关模块字段, 仍走一次轻量查询 (枚举维度, 一次性)
        #    大多数项目模块数 < 20, GROUP BY 一把梭
        by_module_q = await db.execute(
            select(WorkPlanItem.related_module, func.count(WorkPlanItem.id))
            .join(WorkPlan, WorkPlan.id == WorkPlanItem.plan_id)
            .where(
                WorkPlan.project_id == project_id,
                WorkPlanItem.status != TASK_STATUS_CANCELLED,
            )
            .group_by(WorkPlanItem.related_module)
        )
        by_module: dict[str, int] = {}
        for mod, cnt in by_module_q.all():
            key = mod or "其他"
            by_module[key] = int(cnt or 0)

        rate = (done / total) if total > 0 else 0.0

        return {
            "total_items": total,
            "completed_items": done,
            "in_progress_items": inprog,
            "blocked_items": blocked,
            "completion_rate": round(rate, 3),
            "total_estimated_hours": round(est_hours, 1),
            "total_actual_hours": round(act_hours, 1),
            "by_module": by_module,
            "by_status": by_status,
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
                # round 36 P1: 之前静默 continue, 卡点存续时长算错也不知道
                logger.exception(
                    "progress_tracker: blocker age 计算失败 blocker_id=%s",
                    getattr(b, "id", None),
                )
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
