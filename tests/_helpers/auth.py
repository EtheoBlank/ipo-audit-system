"""认证 / 多租户 helper.

角色常量与项目 ``app.models.db.auth`` 一致; 工厂函数 ``make_user`` / ``make_firm``
直接 ORM 写库, 测试用.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

# 复用项目里的角色常量 — 改一处, 测试跟随
try:
    from app.models.db.auth import (
        ROLE_ADMIN,
        ROLE_QC_PARTNER,
        ROLE_PARTNER,
        ROLE_MANAGER,
        ROLE_ASSISTANT,
    )
    ALL_ROLES = [
        ROLE_ADMIN,
        ROLE_QC_PARTNER,
        ROLE_PARTNER,
        ROLE_MANAGER,
        ROLE_ASSISTANT,
    ]
except ImportError:  # pragma: no cover — 模块尚未导入路径时
    ROLE_ADMIN = "admin"
    ROLE_QC_PARTNER = "qc_partner"
    ROLE_PARTNER = "partner"
    ROLE_MANAGER = "manager"
    ROLE_ASSISTANT = "assistant"
    ALL_ROLES = [
        ROLE_ADMIN, ROLE_QC_PARTNER, ROLE_PARTNER,
        ROLE_MANAGER, ROLE_ASSISTANT,
    ]


async def make_firm(
    db: AsyncSession,
    *,
    name: str = "测试事务所",
    is_active: bool = True,
    commit: bool = False,
):
    """插入一条 Firm. 返回 ORM 实例."""
    from app.models.db.auth import Firm

    firm = Firm(name=name, is_active=is_active, created_at=datetime.utcnow())
    db.add(firm)
    await db.flush()
    if commit:
        await db.commit()
    return firm


async def make_user(
    db: AsyncSession,
    *,
    firm_id: Optional[int] = None,
    username: str = "test_user",
    role: str = ROLE_ASSISTANT,
    full_name: str = "测试用户",
    password_hash: str = "$2b$12$dummy.hash.for.testing.only",
    is_active: bool = True,
    commit: bool = False,
):
    """插入一条 User. ``password_hash`` 默认占位 (测试登录场景用 ``make_token``).

    用法::

        user = await make_user(async_session, firm_id=firm.id, role=ROLE_ADMIN)
    """
    from app.models.db.auth import User

    user = User(
        firm_id=firm_id,
        username=username,
        role=role,
        full_name=full_name,
        password_hash=password_hash,
        is_active=is_active,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    await db.flush()
    if commit:
        await db.commit()
    return user


def make_token(
    user_id: int,
    *,
    firm_id: Optional[int] = None,
    role: str = ROLE_ASSISTANT,
    username: str = "test_user",
) -> str:
    """签发一个测试用 JWT. 复用项目 ``app.services.auth.jwt`` 签发逻辑.

    ``username`` 是项目 JWT payload 必备字段, 这里默认 ``"test_user"``;
    如果调用方已经在 session 里 make_user, 通常不关心 username, 直接传 user_id 即可.
    """
    from app.services.auth.jwt import create_access_token
    return create_access_token(
        user_id=user_id,
        username=username,
        role=role,
        firm_id=firm_id,
    )
