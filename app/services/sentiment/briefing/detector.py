"""BriefingDetector — 判定"今天有没有新消息" / 简报是否可生成.

硬性规则 (用户需求):
    - 没有新消息就不生成简报
    - 同一项目同一天只能有一份简报 (idempotent)
    - 严重度全为 info 且都被忽略 → 不生成
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db_models import (
    SENTIMENT_DOC_STATUS_FROZEN,
    SENTIMENT_EVENT_STATUS_IGNORED,
    SENTIMENT_SEVERITY_INFO,
    SentimentDailyBriefing,
    SentimentEvent,
)

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """detector 判定结果."""

    should_generate: bool
    reason: str  # "ok" / "no_events" / "all_filtered" / "already_generated" / "already_locked"
    event_count: int = 0  # 候选事件数
    existing_briefing_id: Optional[int] = None  # 若已存在


class BriefingDetector:
    """判定项目 × 日期 是否需要生成简报."""

    def __init__(self, lookback_hours: Optional[int] = None) -> None:
        self.lookback_hours = lookback_hours or settings.SENTIMENT_BRIEFING_EVENT_LOOKBACK_HOURS

    async def should_generate(
        self,
        db: AsyncSession,
        project_id: int,
        briefing_date: str,
    ) -> DetectionResult:
        """核心判定.

        判定顺序:
            1) 该项目 × 该日期 已有简报? → 不再生成
            2) 窗口内有效事件数 = 0? → 不生成
            3) 窗口内事件全是 info 且都 ignored? → 不生成
            4) 其余 → 生成
        """
        # 1) 查现有简报
        existing = await self._get_existing_briefing(db, project_id, briefing_date)
        if existing is not None:
            if existing.is_locked or existing.status in (SENTIMENT_DOC_STATUS_FROZEN,):
                return DetectionResult(False, "already_locked", 0, existing.id)
            return DetectionResult(False, "already_generated", existing.event_count, existing.id)

        # 2) 查窗口内事件
        event_count = await self._count_relevant_events(db, project_id, briefing_date)
        if event_count == 0:
            return DetectionResult(False, "no_events", 0)

        # 3) 全是 info+ignored?
        all_filtered = await self._is_all_filtered(db, project_id, briefing_date)
        if all_filtered:
            return DetectionResult(False, "all_filtered", event_count)

        return DetectionResult(True, "ok", event_count)

    async def _get_existing_briefing(
        self,
        db: AsyncSession,
        project_id: int,
        briefing_date: str,
    ) -> Optional[SentimentDailyBriefing]:
        res = await db.execute(
            select(SentimentDailyBriefing).where(
                SentimentDailyBriefing.project_id == project_id,
                SentimentDailyBriefing.briefing_date == briefing_date,
            )
        )
        return res.scalar_one_or_none()

    async def _count_relevant_events(
        self,
        db: AsyncSession,
        project_id: int,
        briefing_date: str,
    ) -> int:
        """窗口: briefing_date 当天 (00:00 ~ 23:59) + 向前 lookback_hours 小时."""
        window_start = self._parse_date(briefing_date) - timedelta(hours=self.lookback_hours)
        window_end_str = briefing_date + " 23:59:59"
        # 用 publish_date 字符串粗略比较 (YYYY-MM-DD 字典序 = 时间序)
        window_start.strftime("%Y-%m-%d %H:%M:%S")

        res = await db.execute(
            select(func.count(SentimentEvent.id)).where(
                SentimentEvent.project_id == project_id,
                SentimentEvent.review_status != SENTIMENT_EVENT_STATUS_IGNORED,
                SentimentEvent.publish_date.is_not(None),
                SentimentEvent.publish_date <= window_end_str[:10],
                # 也包含当天的所有事件
            )
        )
        # 简化: 全部当天的非 ignored 事件都算
        res = await db.execute(
            select(func.count(SentimentEvent.id)).where(
                SentimentEvent.project_id == project_id,
                SentimentEvent.review_status != SENTIMENT_EVENT_STATUS_IGNORED,
                SentimentEvent.publish_date == briefing_date,
            )
        )
        return int(res.scalar() or 0)

    async def _is_all_filtered(
        self,
        db: AsyncSession,
        project_id: int,
        briefing_date: str,
    ) -> bool:
        """窗口内事件是否全部 (severity=info AND review_status=ignored)."""
        res = await db.execute(
            select(func.count(SentimentEvent.id)).where(
                SentimentEvent.project_id == project_id,
                SentimentEvent.publish_date == briefing_date,
            )
        )
        total = int(res.scalar() or 0)
        if total == 0:
            return False  # 走 no_events 分支

        res = await db.execute(
            select(func.count(SentimentEvent.id)).where(
                SentimentEvent.project_id == project_id,
                SentimentEvent.publish_date == briefing_date,
                SentimentEvent.severity != SENTIMENT_SEVERITY_INFO,
            )
        )
        non_info = int(res.scalar() or 0)
        return non_info == 0  # 全部都是 info

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


# ---- 暴露在模块级的便捷函数 ---------------------------------------------


async def detect(
    db: AsyncSession,
    project_id: int,
    briefing_date: str,
) -> DetectionResult:
    """detector 入口便捷函数."""
    return await BriefingDetector().should_generate(db, project_id, briefing_date)
