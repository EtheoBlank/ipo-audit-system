"""高层编排 — authenticate / login / refresh / change-password."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db.auth import User
from app.services.auth.jwt import (
    JWTError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.auth.password import hash_password, verify_password

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """登录失败."""


class AccountLockedError(Exception):
    """账户被锁定 / 停用."""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def authenticate(
    db: AsyncSession,
    username: str,
    password: str,
    *,
    ip: Optional[str] = None,
) -> User:
    """登录验证. 失败抛 ``AuthenticationError``, 锁定抛 ``AccountLockedError``."""
    if not username or not password:
        raise AuthenticationError("用户名或密码不能为空")

    stmt = select(User).where(User.username == username)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        # 防止账户枚举, 错误信息统一
        raise AuthenticationError("用户名或密码错误")

    if user.is_locked:
        raise AccountLockedError(f"账户 {username} 已被锁定, 请联系管理员")
    if not user.is_active:
        raise AccountLockedError(f"账户 {username} 已停用")

    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if (
            settings.AUTH_MAX_FAILED_LOGIN > 0
            and user.failed_login_count >= settings.AUTH_MAX_FAILED_LOGIN
        ):
            user.is_locked = True
            logger.warning("账户 %s 失败次数 %s 达阈值, 已锁定", username, user.failed_login_count)
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
        raise AuthenticationError("用户名或密码错误")

    # 成功
    user.failed_login_count = 0
    user.last_login_at = _utcnow_naive()
    user.last_login_ip = ip[:64] if ip else None
    try:
        await db.commit()
        await db.refresh(user)
    except Exception:  # noqa: BLE001
        await db.rollback()
    return user


async def login(
    db: AsyncSession,
    username: str,
    password: str,
    *,
    ip: Optional[str] = None,
) -> dict:
    """登录成功后返回 access/refresh token + 用户信息."""
    user = await authenticate(db, username, password, ip=ip)
    access = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        firm_id=user.firm_id,
    )
    refresh = create_refresh_token(user_id=user.id, username=user.username)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        "user": user,
    }


async def refresh_access_token(
    db: AsyncSession,
    refresh_token: str,
) -> dict:
    """用 refresh_token 换一个新的 access_token (refresh 不轮换, 简化设计)."""
    try:
        payload = decode_token(refresh_token)
    except JWTError as exc:
        raise AuthenticationError(f"refresh token 无效: {exc}") from exc
    if payload.get("type") != "refresh":
        raise AuthenticationError("不是 refresh token")
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise AuthenticationError("refresh token 缺 sub")
    try:
        user_id = int(user_id_str)
    except Exception as exc:
        raise AuthenticationError("refresh token sub 非整数") from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active or user.is_locked:
        raise AuthenticationError("用户不可用")

    access = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        firm_id=user.firm_id,
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
    }


async def change_password(
    db: AsyncSession,
    user: User,
    old_password: str,
    new_password: str,
) -> None:
    if not verify_password(old_password, user.password_hash):
        raise AuthenticationError("旧密码错误")
    if len(new_password) < 8:
        raise AuthenticationError("新密码至少 8 位")
    if new_password == old_password:
        raise AuthenticationError("新密码不能与旧密码相同")
    user.password_hash = hash_password(new_password)
    user.password_changed_at = _utcnow_naive()
    await db.commit()


async def reset_password(
    db: AsyncSession,
    user: User,
    new_password: str,
) -> None:
    """管理员强制重置 (无需旧密码)."""
    if len(new_password) < 8:
        raise AuthenticationError("新密码至少 8 位")
    user.password_hash = hash_password(new_password)
    user.password_changed_at = _utcnow_naive()
    user.is_locked = False
    user.failed_login_count = 0
    await db.commit()
