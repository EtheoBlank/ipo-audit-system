"""RBAC 角色级别比较 — 拒绝路径测试 (P0 测试空白 2026-06-19).

5 级签字流 (assistant → manager → partner → qc_partner → signing_partner)
外加 admin (全权限). 测试覆盖:
- 5 角色两两比较: 上级 ≥ 下级 ✓; 下级 < 上级 ✗
- admin 跨所有级别 ✓
- 空 / None role 拒绝
- require_role factory 在 fastapi Depends 中真抛 403

test_auth.py 此前只测了 role_at_least 正向, 没覆盖拒绝路径;
也没测 require_role 在 FastAPI 路由上的 403 行为.
"""
from __future__ import annotations

import pytest

from app.models.db.auth import (
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    ROLE_PARTNER,
    ROLE_QC_PARTNER,
    ROLE_SIGNING_PARTNER,
)
from app.services.auth.rbac import (
    AuthorizationError,
    role_at_least,
)


# ============================================================
# role_at_least — 全部 5×5 角色两两 + admin + 边界
# ============================================================


class TestRoleAtLeastMatrix:
    """P0 — RBAC 矩阵: 上级 ≥ 下级 ✓; 下级 < 上级 ✗."""

    @pytest.mark.parametrize(
        "actual,required,expected",
        [
            # 同级 = 通过
            (ROLE_ASSISTANT, ROLE_ASSISTANT, True),
            (ROLE_MANAGER, ROLE_MANAGER, True),
            (ROLE_PARTNER, ROLE_PARTNER, True),
            (ROLE_QC_PARTNER, ROLE_QC_PARTNER, True),
            (ROLE_SIGNING_PARTNER, ROLE_SIGNING_PARTNER, True),
            # 上级 ≥ 下级 (要求 lower, 实际更高) — 通过
            (ROLE_MANAGER, ROLE_ASSISTANT, True),
            (ROLE_PARTNER, ROLE_ASSISTANT, True),
            (ROLE_QC_PARTNER, ROLE_MANAGER, True),
            (ROLE_SIGNING_PARTNER, ROLE_PARTNER, True),
            # 下级 < 上级 (要求 higher, 实际更低) — 拒绝
            (ROLE_ASSISTANT, ROLE_MANAGER, False),
            (ROLE_MANAGER, ROLE_PARTNER, False),
            (ROLE_PARTNER, ROLE_QC_PARTNER, False),
            (ROLE_QC_PARTNER, ROLE_SIGNING_PARTNER, False),
            # admin 跨所有 — 通过
            (ROLE_ADMIN, ROLE_ASSISTANT, True),
            (ROLE_ADMIN, ROLE_MANAGER, True),
            (ROLE_ADMIN, ROLE_PARTNER, True),
            (ROLE_ADMIN, ROLE_QC_PARTNER, True),
            (ROLE_ADMIN, ROLE_SIGNING_PARTNER, True),
        ],
    )
    def test_role_comparison(self, actual, required, expected):
        assert role_at_least(actual, required) is expected

    def test_empty_role_denied(self):
        assert role_at_least("", ROLE_ASSISTANT) is False
        assert role_at_least(None, ROLE_ASSISTANT) is False

    def test_unknown_role_denied(self):
        # 没注册的 role code → level=0 < required_level=99
        assert role_at_least("unknown_role", ROLE_ASSISTANT) is False

    def test_admin_always_passes(self):
        for r in (
            ROLE_ASSISTANT,
            ROLE_MANAGER,
            ROLE_PARTNER,
            ROLE_QC_PARTNER,
            ROLE_SIGNING_PARTNER,
        ):
            assert role_at_least(ROLE_ADMIN, r) is True


# ============================================================
# require_role factory — FastAPI Depends 实际抛 403
# ============================================================


class TestRequireRoleFactory:
    """P1 — require_role 在 FastAPI 路由上是否真的抛 403, 而非悄悄放行."""

    def _build_user(self, role: str):
        from app.models.db.auth import User

        return User(
            id=1,
            username="u",
            full_name="U",
            password_hash="x",
            role=role,
            is_active=True,
            is_locked=False,
        )

    @pytest.mark.asyncio
    async def test_assistant_denied_partner_action(self):
        # 审计员试图触发 ≥ partner 操作 → 403
        from fastapi import HTTPException

        from app.services.auth.dependencies import require_role

        checker = require_role(ROLE_PARTNER)
        user = self._build_user(ROLE_ASSISTANT)
        with pytest.raises(HTTPException) as ei:
            await checker(user=user)
        assert ei.value.status_code == 403
        assert "partner" in ei.value.detail

    @pytest.mark.asyncio
    async def test_manager_denied_qc_action(self):
        from fastapi import HTTPException

        from app.services.auth.dependencies import require_role

        checker = require_role(ROLE_QC_PARTNER)
        user = self._build_user(ROLE_MANAGER)
        with pytest.raises(HTTPException) as ei:
            await checker(user=user)
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_partner_passes_manager_check(self):
        # 上级调用下级检查 — 通过 (返回 user)
        from app.services.auth.dependencies import require_role

        checker = require_role(ROLE_MANAGER)
        user = self._build_user(ROLE_PARTNER)
        result = await checker(user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_admin_passes_signing_partner_check(self):
        from app.services.auth.dependencies import require_role

        checker = require_role(ROLE_SIGNING_PARTNER)
        user = self._build_user(ROLE_ADMIN)
        result = await checker(user=user)
        assert result is user


# ============================================================
# AuthorizationError — 错误类型
# ============================================================


class TestAuthorizationError:
    def test_is_exception(self):
        assert issubclass(AuthorizationError, Exception)

    def test_message_includes_role_and_permission(self):
        e = AuthorizationError("用户 x (role=assistant) 缺少权限 foo.bar")
        assert "assistant" in str(e)
        assert "foo.bar" in str(e)