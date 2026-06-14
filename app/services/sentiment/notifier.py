"""站内通知 — 驱动 Dashboard 红点."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import SentimentNotification

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_notification(
    db: AsyncSession,
    notification_type: str,
    title: str,
    body: Optional[str] = None,
    project_id: Optional[int] = None,
    link_url: Optional[str] = None,
) -> SentimentNotification:
    """写入一条站内通知 (红点)."""
    n = SentimentNotification(
        project_id=project_id,
        notification_type=notification_type,
        title=title,
        body=body,
        link_url=link_url,
    )
    db.add(n)
    await db.flush()  # 不 commit — 留给调用方事务
    logger.debug(
        "notifier: type=%s title=%s project_id=%s",
        notification_type,
        title,
        project_id,
    )
    return n


async def mark_read(db: AsyncSession, notification_id: int) -> bool:
    """标记单条已读. 已读则跳过."""
    from sqlalchemy import select

    res = await db.execute(
        select(SentimentNotification).where(SentimentNotification.id == notification_id)
    )
    n = res.scalar_one_or_none()
    if not n or n.is_read:
        return False
    n.is_read = True
    n.read_at = _utcnow()
    await db.flush()
    return True


async def mark_all_read(db: AsyncSession, project_id: Optional[int] = None) -> int:
    """标记全部已读 (可按 project_id 过滤). 返回受影响条数."""
    from sqlalchemy import update

    stmt = (
        update(SentimentNotification)
        .where(SentimentNotification.is_read == False)  # noqa: E712
        .values(is_read=True, read_at=_utcnow())
    )
    if project_id is not None:
        stmt = stmt.where(SentimentNotification.project_id == project_id)
    res = await db.execute(stmt)
    return res.rowcount or 0
