"""Audit Log — 记录 + 查询 (仅 append, 不允许 UPDATE/DELETE)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db.auth import AuditLog, AUDIT_ACTION_CREATE

logger = logging.getLogger(__name__)


def _truncate(value: Optional[str], max_chars: int) -> Optional[str]:
    if value is None or max_chars <= 0:
        return None
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"... [truncated, original {len(value)} chars]"


def _serialize(payload: Any) -> Optional[str]:
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(payload)


async def record_audit_log(
    db: AsyncSession,
    *,
    user_id: Optional[int] = None,
    user_display: Optional[str] = None,
    user_role: Optional[str] = None,
    firm_id: Optional[int] = None,
    action: str = AUDIT_ACTION_CREATE,
    resource_type: Optional[str] = None,
    resource_id: Optional[Any] = None,
    project_id: Optional[int] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    status_code: Optional[int] = None,
    summary: Optional[str] = None,
    payload: Optional[Any] = None,
    error_detail: Optional[str] = None,
    commit: bool = True,
) -> Optional[AuditLog]:
    """记录一条审计轨迹.

    P0 第 2 轮修复: ``commit=True`` 默认 — 第 1 轮改 False 导致所有路由审计日志
    全部丢失 (业务路由先 ``await db.commit()`` 之后才调本函数, 那时本函数 add 的
    log 在新事务里, 路由结束 dependency cleanup 关闭 session 时未 commit 自动 rollback).

    安全设计:
      - 默认 ``commit=True`` 保证调用方不忘 commit; 写日志失败仅记 logger.exception, 不抛
      - 若调用方明确想"跟随业务事务一起 commit"传 ``commit=False`` + 自己 ``await db.commit()``
      - ``commit=True`` 路径下 db.commit() 失败也只 log + 返 None, 不抛 (不能让审计日志拖累业务)
      - 敏感字段过滤由调用方负责 (例 ``payload=payload.model_dump(exclude={'password'})``)
    """
    try:
        payload_str = _truncate(
            _serialize(payload), settings.AUDIT_LOG_PAYLOAD_MAX_CHARS
        )
        log = AuditLog(
            user_id=user_id,
            user_display=user_display,
            user_role=user_role,
            firm_id=firm_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            project_id=project_id,
            method=method,
            path=path[:500] if path else None,
            ip=ip,
            user_agent=user_agent[:500] if user_agent else None,
            status_code=status_code,
            summary=summary[:500] if summary else None,
            payload=payload_str,
            error_detail=_truncate(error_detail, 4000),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(log)
        if commit:
            try:
                await db.commit()
                await db.refresh(log)
            except Exception as commit_exc:  # noqa: BLE001
                logger.exception("audit_log commit 失败 (已吞, 业务不受影响): %s", commit_exc)
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                return None
        else:
            await db.flush()
        return log
    except Exception as exc:  # noqa: BLE001
        logger.exception("写审计轨迹失败 (action=%s, resource=%s): %s", action, resource_type, exc)
        return None


def _escape_like(text: str) -> str:
    """转义 SQL LIKE 通配符 — 防止用户输入 % / _ 触发全表扫描.

    P0 修复 (Agent #5 W16). 配合 ilike(..., escape='\\\\') 使用.
    """
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def query_audit_logs(
    db: AsyncSession,
    *,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    project_id: Optional[int] = None,
    method: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    """分页查询审计轨迹."""
    conds = []
    if user_id is not None:
        conds.append(AuditLog.user_id == user_id)
    if action:
        conds.append(AuditLog.action == action)
    if resource_type:
        conds.append(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        conds.append(AuditLog.resource_id == str(resource_id))
    if project_id is not None:
        conds.append(AuditLog.project_id == project_id)
    if method:
        conds.append(AuditLog.method == method.upper())
    if start_date:
        try:
            sd = datetime.fromisoformat(start_date)
            conds.append(AuditLog.created_at >= sd)
        except Exception:
            pass
    if end_date:
        try:
            ed = datetime.fromisoformat(end_date)
            conds.append(AuditLog.created_at <= ed)
        except Exception:
            pass
    if keyword:
        # 限长 + 转义防 LIKE 通配符 DoS
        kw = keyword[:200]
        like = f"%{_escape_like(kw)}%"
        conds.append(
            (AuditLog.summary.ilike(like, escape="\\"))
            | (AuditLog.path.ilike(like, escape="\\"))
            | (AuditLog.user_display.ilike(like, escape="\\"))
        )

    where_clause = and_(*conds) if conds else None

    count_stmt = select(func.count(AuditLog.id))
    if where_clause is not None:
        count_stmt = count_stmt.where(where_clause)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    stmt = select(AuditLog)
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    stmt = stmt.order_by(desc(AuditLog.created_at)).offset(max(0, int(skip))).limit(
        max(1, min(500, int(limit)))
    )
    items = list((await db.execute(stmt)).scalars().all())
    return {"total": total, "items": items}
