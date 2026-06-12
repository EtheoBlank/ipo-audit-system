"""Notification API — unread / list / mark-read."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.notification import (
    NotificationListResponse,
    NotificationMarkReadRequest,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from app.models.db.auth import User
from app.services.auth import get_current_user, get_current_user_optional
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["通知中心"])


def _resolve_user_id(user: Optional[User]) -> Optional[int]:
    if user is None:
        return None
    # synthetic admin (AUTH_ENABLED=false 时) id=0 → 视为广播范围
    return user.id or None


@router.get("/unread", response_model=NotificationUnreadCountResponse)
async def unread_count(
    project_id: Optional[int] = None,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """全局红点用 — 返回未读总数 + 按 module/severity 分组."""
    result = await NotificationService.unread_count(
        db, user_id=_resolve_user_id(current_user), project_id=project_id
    )
    return NotificationUnreadCountResponse(**result)


@router.get("/list", response_model=NotificationListResponse)
async def list_notifications(
    module: Optional[str] = None,
    severity: Optional[str] = None,
    only_unread: bool = False,
    project_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    result = await NotificationService.list(
        db,
        user_id=_resolve_user_id(current_user),
        project_id=project_id,
        module=module,
        severity=severity,
        only_unread=only_unread,
        skip=skip,
        limit=limit,
    )
    return NotificationListResponse(
        total=result["total"],
        unread=result["unread"],
        items=[NotificationResponse.model_validate(n) for n in result["items"]],
    )


@router.post("/mark-read")
async def mark_read(
    payload: NotificationMarkReadRequest,
    current_user: User = Depends(get_current_user),  # 写操作强制登录 (Pack A P0 修复)
    db: AsyncSession = Depends(get_db),
):
    user_id = _resolve_user_id(current_user)
    # P0 第 2 轮修复 — synthetic admin (id=0, AUTH_ENABLED=false) 任何 mark-read 都拒绝.
    # mark_all=True 会标记所有人, ids 列表模式也会跳过 user_id 过滤标记任意通知.
    # 真实场景 (AUTH_ENABLED=true) 才允许.
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "通知标记已读需要真实登录用户. AUTH_ENABLED=false 时 (synthetic admin) "
                "无法判断目标用户, 已拒绝执行."
            ),
        )
    if payload.mark_all and not user_id:
        # 双重保险 (上面已 cover)
        raise HTTPException(
            status_code=400,
            detail="mark_all=True 仅允许真实登录用户使用",
        )
    updated = await NotificationService.mark_read(
        db,
        user_id=user_id,
        ids=payload.ids,
        module=payload.module,
        mark_all=payload.mark_all,
    )
    return {"detail": "ok", "updated": updated}
