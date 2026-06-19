"""FastAPI dependencies for auth.

提供:
  - get_current_user            — 强制登录 (失败 401)
  - get_current_user_optional   — 不强制 (匿名时返回 None, 用于 AUTH_ENABLED=false 兼容)
  - require_role(role)          — 要求角色 ≥ role
  - require_permission(code)    — 要求拥有权限 code
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db.auth import (
    ROLE_ADMIN,
    User,
)
from app.services.auth.jwt import JWTError, decode_token
from app.services.auth.rbac import AuthorizationError, has_permission, role_at_least

logger = logging.getLogger(__name__)

# auto_error=False — 我们自己处理 401, 支持 AUTH_ENABLED=false 跳过
_bearer = HTTPBearer(auto_error=False)


async def _user_from_bearer(
    credentials: Optional[HTTPAuthorizationCredentials],
    db: AsyncSession,
) -> Optional[User]:
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"token 无效: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="不是 access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 缺 sub",
        )
    try:
        user_id = int(sub)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token sub 非整数",
        ) from exc
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    if not user.is_active or user.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已停用或锁定",
        )
    return user


def _synthetic_admin() -> User:
    """AUTH_ENABLED=false 时, 给一个内存合成的 admin User 对象,
    让下游代码能拿到 user.id / .role / .full_name, 又不持久化任何东西。

    注意:
      - id=0 是 sentinel, 不会与真实 user 冲突 (auto-increment 从 1 开始)
      - audit_logs.user_id=0 + user_display="(AUTH_DISABLED)" 是一致约定
      - 该对象未挂到 session, 不能用作 ORM 写关系外键, 但读 / 记日志够用。
    """
    user = User(
        id=0,
        username="__system__",
        full_name="(AUTH_DISABLED)",  # P0 第 2 轮修复 — 用稳定标记替代 "(未启用认证)" 让审计可查
        role=ROLE_ADMIN,
        is_active=True,
        is_locked=False,
        password_hash="!",
    )
    return user


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """强制登录. AUTH_ENABLED=false 时返回合成 admin (兼容旧调用)."""
    if not settings.AUTH_ENABLED:
        user = _synthetic_admin()
        # P0 第 2 轮修复 — 同样把 synthetic admin 挂到 request.state, 让审计中间件能记
        try:
            request.state.user = user  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            # request.state 写入失败不应该阻断鉴权 (它只是审计中间件便利)
            # 但 silent 留痕, 因为审计中间件拿不到 user 会丢记录
            logger.warning("get_current_user: request.state.user 写入失败: %s", exc)
        return user
    user = await _user_from_bearer(credentials, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供 Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # 把 user 挂到 request.state 方便审计中间件读
    try:
        request.state.user = user  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_current_user: request.state.user 写入失败: %s", exc)
    return user


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """匿名也允许. 用于 GET 列表等开放端点 (按需要在路由层加 require_role)."""
    if not settings.AUTH_ENABLED:
        return _synthetic_admin()
    if credentials is None or not credentials.credentials:
        return None
    try:
        user = await _user_from_bearer(credentials, db)
    except HTTPException:
        return None
    if user is not None:
        try:
            request.state.user = user  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_current_user_optional: request.state.user 写入失败: %s", exc)
    return user


def require_role(required_role: str):
    """工厂: 返回 Depends 检查角色 >= required_role."""

    async def _checker(
        user: User = Depends(get_current_user),
    ) -> User:
        if not role_at_least(user.role, required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要角色 ≥ {required_role}, 当前 {user.role}",
            )
        return user

    return _checker


def require_permission(permission_code: str):
    """工厂: 返回 Depends 检查权限. AUTH_ENABLED=false 时直接放行."""

    async def _checker(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if not settings.AUTH_ENABLED:
            return user
        try:
            if not await has_permission(db, user, permission_code):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"缺少权限 {permission_code}",
                )
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return user

    return _checker
