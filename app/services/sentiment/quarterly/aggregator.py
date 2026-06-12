"""季度窗口聚合 — 拉取 [window_start, window_end] 内所有简报与事件."""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    SentimentDailyBriefing,
    SentimentEvent,
    SentimentQuarterlyReport,
)

logger = logging.getLogger(__name__)


async def aggregate_window(
    db: AsyncSession,
    report: SentimentQuarterlyReport,
) -> tuple[list[SentimentDailyBriefing], list[SentimentEvent]]:
    """返回 (窗口内所有简报, 窗口内所有事件).

    简报: briefing_date 在 [window_start, window_end]
    事件: publish_date 在 [window_start, window_end]
    """
    ws = report.daily_briefing_window_start
    we = report.daily_briefing_window_end

    brs = await db.execute(
        select(SentimentDailyBriefing).where(
            SentimentDailyBriefing.project_id == report.project_id,
            SentimentDailyBriefing.briefing_date >= ws,
            SentimentDailyBriefing.briefing_date <= we,
        ).order_by(SentimentDailyBriefing.briefing_date.asc())
    )
    briefings = list(brs.scalars().all())

    evs = await db.execute(
        select(SentimentEvent).where(
            SentimentEvent.project_id == report.project_id,
            SentimentEvent.publish_date.is_not(None),
            SentimentEvent.publish_date >= ws,
            SentimentEvent.publish_date <= we,
        ).order_by(SentimentEvent.publish_date.asc())
    )
    events = list(evs.scalars().all())

    logger.info(
        "aggregate_window: report=%s 简报=%d 事件=%d 窗口=[%s,%s]",
        report.id, len(briefings), len(events), ws, we,
    )
    return briefings, events


async def lock_references(
    db: AsyncSession,
    report: SentimentQuarterlyReport,
    briefings: list[SentimentDailyBriefing],
    events: list[SentimentEvent],
) -> None:
    """把引用的 briefing_id / event_id 写回 report (锁定快照)."""
    report.referenced_briefing_ids_json = json.dumps(
        [b.id for b in briefings], ensure_ascii=False
    )
    report.referenced_event_ids_json = json.dumps(
        [e.id for e in events], ensure_ascii=False
    )
    await db.commit()
