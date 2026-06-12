"""RBAC — 角色级别比较 + 字符串 code 权限检查."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.auth import (
    ROLE_ADMIN,
    ROLE_LEVEL,
    Permission,
    Role,
    RolePermission,
    User,
)


class AuthorizationError(Exception):
    """权限不足. 由 dependencies.require_* 转换为 HTTP 403."""


def role_at_least(actual_role: str, required_role: str) -> bool:
    """``actual_role`` 是否 ≥ ``required_role`` 级别. ``admin`` 视为最高."""
    if not actual_role:
        return False
    if actual_role == ROLE_ADMIN:
        return True
    actual_level = ROLE_LEVEL.get(actual_role, 0)
    required_level = ROLE_LEVEL.get(required_role, 99)
    return actual_level >= required_level


async def has_permission(
    db: AsyncSession,
    user: User,
    permission_code: str,
) -> bool:
    """检查用户是否拥有指定权限 (走 Role → RolePermission → Permission).

    简化策略:
      - ``admin`` 角色拥有所有权限 (短路返回 True)
      - 如果数据库中没有定义对应 ``Permission``, 默认放行 (避免阻塞流程)
        — 这种策略让系统在 RBAC 未完全配置时仍可用, 严格模式可后续扩展
    """
    if user is None or not user.is_active or user.is_locked:
        return False
    if user.role == ROLE_ADMIN:
        return True

    # 查 Permission 是否存在
    stmt = select(Permission).where(Permission.code == permission_code)
    perm = (await db.execute(stmt)).scalar_one_or_none()
    if perm is None:
        # 没定义 → 默认放行 (避免阻塞业务). 严格场景可改为返回 False.
        return True

    # 查 Role
    role_stmt = select(Role).where(Role.code == user.role)
    role = (await db.execute(role_stmt)).scalar_one_or_none()
    if role is None:
        # 用户 role 没在 Role 表里注册, 走 role_at_least 兜底 (admin 已上面短路)
        return False

    rp_stmt = select(RolePermission).where(
        RolePermission.role_id == role.id, RolePermission.permission_id == perm.id
    )
    return (await db.execute(rp_stmt)).scalar_one_or_none() is not None


async def check_permission(
    db: AsyncSession,
    user: User,
    permission_code: str,
) -> None:
    """has_permission 的抛错版本."""
    if not await has_permission(db, user, permission_code):
        raise AuthorizationError(
            f"用户 {user.username} (role={user.role}) 缺少权限 {permission_code}"
        )
