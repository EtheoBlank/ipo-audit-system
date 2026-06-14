"""Notification service — 通用通知中心 (push / mark_read / unread_count)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.notification import (
    ALL_NOTIF_SEVERITIES,
    NOTIF_SEVERITY_INFO,
    Notification,
)

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NotificationService:
    """通用通知服务."""

    @staticmethod
    async def push(
        db: AsyncSession,
        *,
        module: str,
        type: str,  # noqa: A002 (shadows builtin, but对前端字段对齐)
        title: str,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        severity: str = NOTIF_SEVERITY_INFO,
        body: Optional[str] = None,
        link: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[Any] = None,
        payload: Optional[Any] = None,
        commit: bool = True,
    ) -> Optional[Notification]:
        """推送一条通知. 失败仅记日志 (不能让通知失败拖累业务)."""
        if severity not in ALL_NOTIF_SEVERITIES:
            severity = NOTIF_SEVERITY_INFO
        try:
            payload_str: Optional[str] = None
            if payload is not None:
                if isinstance(payload, str):
                    payload_str = payload
                else:
                    try:
                        payload_str = json.dumps(payload, ensure_ascii=False, default=str)
                    except Exception:  # noqa: BLE001
                        payload_str = str(payload)
            notif = Notification(
                user_id=user_id,
                project_id=project_id,
                module=module[:40],
                type=type[:80],
                severity=severity,
                title=title[:300],
                body=body,
                link=link[:500] if link else None,
                resource_type=resource_type[:80] if resource_type else None,
                resource_id=str(resource_id)[:80] if resource_id is not None else None,
                payload=payload_str,
                is_read=False,
                created_at=_utcnow_naive(),
            )
            db.add(notif)
            if commit:
                await db.commit()
                await db.refresh(notif)
            else:
                await db.flush()
            return notif
        except Exception as exc:  # noqa: BLE001
            logger.exception("推送通知失败 (module=%s, type=%s): %s", module, type, exc)
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
            return None

    @staticmethod
    async def push_many(
        db: AsyncSession,
        items: Iterable[Dict[str, Any]],
    ) -> int:
        """批量推送, 返回成功数."""
        count = 0
        for item in items:
            n = await NotificationService.push(db, commit=False, **item)
            if n is not None:
                count += 1
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            count = 0
        return count

    @staticmethod
    async def list(
        db: AsyncSession,
        *,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        module: Optional[str] = None,
        severity: Optional[str] = None,
        only_unread: bool = False,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        conds = []
        if user_id is not None:
            # 用户专属 + 广播给所有人 (user_id IS NULL)
            conds.append(or_(Notification.user_id == user_id, Notification.user_id.is_(None)))
        if project_id is not None:
            conds.append(
                or_(Notification.project_id == project_id, Notification.project_id.is_(None))
            )
        if module:
            conds.append(Notification.module == module)
        if severity:
            conds.append(Notification.severity == severity)
        if only_unread:
            conds.append(Notification.is_read == False)  # noqa: E712

        where_clause = and_(*conds) if conds else None

        count_stmt = select(func.count(Notification.id))
        if where_clause is not None:
            count_stmt = count_stmt.where(where_clause)
        total = int((await db.execute(count_stmt)).scalar_one() or 0)

        unread_stmt = select(func.count(Notification.id)).where(
            Notification.is_read == False  # noqa: E712
        )
        if where_clause is not None:
            unread_stmt = unread_stmt.where(where_clause)
        unread = int((await db.execute(unread_stmt)).scalar_one() or 0)

        list_stmt = select(Notification)
        if where_clause is not None:
            list_stmt = list_stmt.where(where_clause)
        list_stmt = (
            list_stmt.order_by(desc(Notification.created_at))
            .offset(max(0, int(skip)))
            .limit(max(1, min(500, int(limit))))
        )
        items = list((await db.execute(list_stmt)).scalars().all())
        return {"total": total, "unread": unread, "items": items}

    @staticmethod
    async def unread_count(
        db: AsyncSession,
        *,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """返回未读数 + 按 module / severity 分组."""
        conds = [Notification.is_read == False]  # noqa: E712
        if user_id is not None:
            conds.append(or_(Notification.user_id == user_id, Notification.user_id.is_(None)))
        if project_id is not None:
            conds.append(
                or_(Notification.project_id == project_id, Notification.project_id.is_(None))
            )
        where_clause = and_(*conds)

        total = int(
            (await db.execute(select(func.count(Notification.id)).where(where_clause))).scalar_one()
            or 0
        )

        # by module
        by_module_stmt = (
            select(Notification.module, func.count(Notification.id))
            .where(where_clause)
            .group_by(Notification.module)
        )
        by_module = {row[0]: int(row[1]) for row in (await db.execute(by_module_stmt)).all()}

        # by severity
        by_sev_stmt = (
            select(Notification.severity, func.count(Notification.id))
            .where(where_clause)
            .group_by(Notification.severity)
        )
        by_severity = {row[0]: int(row[1]) for row in (await db.execute(by_sev_stmt)).all()}

        return {
            "total_unread": total,
            "by_module": by_module,
            "by_severity": by_severity,
        }

    @staticmethod
    async def mark_read(
        db: AsyncSession,
        *,
        user_id: Optional[int] = None,
        ids: Optional[List[int]] = None,
        module: Optional[str] = None,
        mark_all: bool = False,
    ) -> int:
        """标记已读, 返回更新行数. 仅影响当前用户可见的通知."""
        if not ids and not module and not mark_all:
            return 0
        conds = [Notification.is_read == False]  # noqa: E712
        if user_id is not None:
            conds.append(or_(Notification.user_id == user_id, Notification.user_id.is_(None)))
        if ids:
            conds.append(Notification.id.in_(ids))
        if module:
            conds.append(Notification.module == module)
        stmt = (
            update(Notification).where(and_(*conds)).values(is_read=True, read_at=_utcnow_naive())
        )
        result = await db.execute(stmt)
        await db.commit()
        return int(result.rowcount or 0)
