"""季度报告触发 — 手动 / 财务数据上传后 / scheduled."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    SENTIMENT_PERIOD_TYPE_LABELS,
    SentimentNotification,
    SentimentQuarterlyReport,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class QuarterlyPeriodSpec:
    """季度报告期次规格."""

    period_type: str  # Q1/H1/Q3/ANNUAL
    fiscal_year: int
    period_end: str  # YYYY-MM-DD
    window_start: str  # 简报/事件窗口起点
    window_end: str  # 简报/事件窗口终点

    @property
    def title(self) -> str:
        label = SENTIMENT_PERIOD_TYPE_LABELS.get(self.period_type, self.period_type)
        return f"{self.fiscal_year} {label} 跟踪报告"

    @classmethod
    def for_type(cls, period_type: str, fiscal_year: int) -> "QuarterlyPeriodSpec":
        """根据 period_type 自动算 window 与 period_end."""
        if period_type == "Q1":
            pe = f"{fiscal_year}-03-31"
            ws, we = f"{fiscal_year}-01-01", f"{fiscal_year}-03-31"
        elif period_type == "H1":
            pe = f"{fiscal_year}-06-30"
            ws, we = f"{fiscal_year}-01-01", f"{fiscal_year}-06-30"
        elif period_type == "Q3":
            pe = f"{fiscal_year}-09-30"
            ws, we = f"{fiscal_year}-01-01", f"{fiscal_year}-09-30"
        elif period_type == "ANNUAL":
            pe = f"{fiscal_year}-12-31"
            ws, we = f"{fiscal_year}-01-01", f"{fiscal_year}-12-31"
        else:
            raise ValueError(f"未知 period_type={period_type}")
        return cls(
            period_type=period_type,
            fiscal_year=fiscal_year,
            period_end=pe,
            window_start=ws,
            window_end=we,
        )


async def create_or_get_report(
    db: AsyncSession,
    project_id: int,
    period_type: str,
    fiscal_year: int,
    *,
    trigger_type: str = "manual",
) -> SentimentQuarterlyReport:
    """创建/获取某项目某期次的报告 (UPSERT 语义).

    并发安全: 先 select, 找不到则 insert, IntegrityError 兜底再 select.
    """
    from sqlalchemy.exc import IntegrityError

    spec = QuarterlyPeriodSpec.for_type(period_type, fiscal_year)
    res = await db.execute(
        select(SentimentQuarterlyReport).where(
            SentimentQuarterlyReport.project_id == project_id,
            SentimentQuarterlyReport.period_type == period_type,
            SentimentQuarterlyReport.fiscal_year == fiscal_year,
        )
    )
    rep = res.scalar_one_or_none()
    if rep is not None:
        return rep
    rep = SentimentQuarterlyReport(
        project_id=project_id,
        period_type=period_type,
        fiscal_year=fiscal_year,
        period_end=spec.period_end,
        title=spec.title,
        trigger_type=trigger_type,
        daily_briefing_window_start=spec.window_start,
        daily_briefing_window_end=spec.window_end,
    )
    db.add(rep)
    try:
        await db.commit()
    except IntegrityError:
        # 并发: 另一个请求已插入, 回滚后再次 select
        await db.rollback()
        res = await db.execute(
            select(SentimentQuarterlyReport).where(
                SentimentQuarterlyReport.project_id == project_id,
                SentimentQuarterlyReport.period_type == period_type,
                SentimentQuarterlyReport.fiscal_year == fiscal_year,
            )
        )
        existing = res.scalar_one_or_none()
        if existing is not None:
            return existing
        raise  # 真出错了, 抛
    await db.refresh(rep)
    logger.info(
        "create_or_get_report: project=%s period=%s/%s id=%s",
        project_id,
        period_type,
        fiscal_year,
        rep.id,
    )
    return rep


async def mark_briefing_ready(
    db: AsyncSession,
    project_id: int,
    report_id: int,
) -> None:
    """报告就绪时给所有相关项目成员发红点通知."""
    await _add_notification(
        db,
        project_id,
        "report_ready",
        title="季度跟踪报告已生成, 请审阅",
        link_url=f"/sentiment?project_id={project_id}&tab=quarterly&report_id={report_id}",
    )


async def _add_notification(
    db: AsyncSession,
    project_id: int,
    ntype: str,
    title: str,
    body: str = "",
    link_url: str = "",
) -> None:
    n = SentimentNotification(
        project_id=project_id,
        notification_type=ntype,
        title=title,
        body=body or None,
        link_url=link_url or None,
    )
    db.add(n)
    await db.commit()
