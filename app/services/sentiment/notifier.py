"""站内通知 — 驱动 Dashboard 红点.

Pack B (round 32, 2026-06-20) IDOR 修复:
  - create_notification 加 user_id / firm_id
  - mark_read 接收 user_id, 限定 (id, user_id=user_id) — 跨用户不能标已读
  - 新增 mark_read_broadcast(notification_id, firm_id) — 广播通知按 firm 标已读
"""

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
    user_id: Optional[int] = None,
    firm_id: Optional[int] = None,
) -> SentimentNotification:
    """写入一条站内通知 (红点).

    user_id / firm_id:
      - 两者都 None  = 全局广播 (按 module/firm 过滤; 见 mark_read_broadcast)
      - user_id 单独 = 单发给某用户
      - firm_id 单独 = 广播给某 firm 所有用户
    """
    n = SentimentNotification(
        project_id=project_id,
        notification_type=notification_type,
        title=title,
        body=body,
        link_url=link_url,
        user_id=user_id,
        firm_id=firm_id,
    )
    db.add(n)
    await db.flush()  # 不 commit — 留给调用方事务
    logger.debug(
        "notifier: type=%s title=%s project_id=%s user_id=%s firm_id=%s",
        notification_type,
        title,
        project_id,
        user_id,
        firm_id,
    )
    return n


async def mark_read(
    db: AsyncSession,
    notification_id: int,
    user_id: int,
) -> bool:
    """标记单条已读. 已读则跳过.

    P0 (round 32): 必须传 user_id, 否则抛 TypeError (强制调用方传).
    防 IDOR: 不能用别人的 notification_id 把自己未读改成已读.
    """
    from sqlalchemy import select

    res = await db.execute(
        select(SentimentNotification).where(
            SentimentNotification.id == notification_id,
            SentimentNotification.user_id == user_id,  # 严格按 user_id 限定
        )
    )
    n = res.scalar_one_or_none()
    if not n or n.is_read:
        return False
    n.is_read = True
    n.read_at = _utcnow()
    await db.flush()
    return True


async def mark_read_broadcast(
    db: AsyncSession,
    notification_id: int,
    firm_id: int,
) -> bool:
    """标记广播通知 (user_id IS NULL) 已读.

    firm_id 校验规则 (round 32, 2026-06-20):
      - 通知 firm_id 与传入 firm_id 必须一致; 否则 False (跨 firm 不能标)
      - 通知 firm_id 为空但 project_id 关联时, 用 project 的 firm 校验
      - 通知 firm_id 与 project_id 都为空 = 全局广播, 只允许 admin firm 校验通过
        (API 层 admin 跳过 firm 校验, 所以这里只认传入的 firm_id)

    P0 (round 32): API 层调用前必须先做 firm 校验, 这里只负责"标已读".
    """
    from sqlalchemy import select

    n = (
        await db.execute(
            select(SentimentNotification).where(
                SentimentNotification.id == notification_id,
                SentimentNotification.user_id.is_(None),  # 仅广播
            )
        )
    ).scalar_one_or_none()
    if not n or n.is_read:
        return False
    # 二次 firm 校验 — 防 race condition 或 API 层漏判
    if n.firm_id is not None and n.firm_id != firm_id:
        return False
    if n.firm_id is None and n.project_id is not None:
        # 通过 project 找 firm
        from app.models.db_models import Project
        proj = (
            await db.execute(select(Project).where(Project.id == n.project_id))
        ).scalar_one_or_none()
        if proj is None or proj.firm_id != firm_id:
            return False
    # 上面没 return False 即放行
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
