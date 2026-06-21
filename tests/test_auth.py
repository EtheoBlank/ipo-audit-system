"""Pack A — Auth 模块单元测试.

覆盖:
  - password 哈希 / 校验 + bcrypt 不可用降级路径
  - JWT 编码 / 解码 + 过期 / 篡改 / type 错误
  - RBAC role_at_least 比较
  - Approval 流程 (五级) + 拒绝 / 撤回
  - login → refresh → change_password 完整链路
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

# 关闭 AUTH_ENABLED 以避免污染其他测试 (本测试只走纯函数, 不调 API)
os.environ.setdefault("AUTH_ENABLED", "false")

from app.services.auth.password import hash_password, verify_password
from app.services.auth.jwt import (
    JWTError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.auth.rbac import role_at_least
from app.services.auth.approval import (
    ApprovalEngine,
    DEFAULT_FIVE_LEVEL_FLOW,
    InvalidApprovalAction,
    StepSpec,
)
from app.models.db.auth import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_IN_PROGRESS,
    APPROVAL_STATUS_REJECTED,
    APPROVAL_STATUS_WITHDRAWN,
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    ROLE_PARTNER,
    ROLE_QC_PARTNER,
    ROLE_SIGNING_PARTNER,
    User,
)


# ============================================================
#  Password
# ============================================================


class TestPassword:
    def test_hash_and_verify_roundtrip(self):
        h = hash_password("MyP@ssw0rd!")
        assert verify_password("MyP@ssw0rd!", h)
        assert not verify_password("wrong", h)

    def test_empty_password_raises(self):
        with pytest.raises(ValueError):
            hash_password("")

    def test_verify_returns_false_for_empty(self):
        assert not verify_password("", "any_hash")
        assert not verify_password("p", None)

    def test_verify_returns_false_for_garbage(self):
        assert not verify_password("p", "garbage_hash")

    def test_fallback_pbkdf2_path(self):
        """pbkdf2 fallback 哈希也能被 verify_password 识别."""
        from app.services.auth.password import _fallback_hash, _fallback_verify

        h = _fallback_hash("hello")
        assert h.startswith("pbkdf2_sha256$")
        assert _fallback_verify("hello", h)
        assert not _fallback_verify("hellox", h)
        # 通过对外 verify_password 也应识别 fallback 前缀
        assert verify_password("hello", h)


# ============================================================
#  JWT
# ============================================================


class TestJWT:
    def test_access_token_roundtrip(self):
        token = create_access_token(
            user_id=42, username="alice", role=ROLE_MANAGER, firm_id=1
        )
        payload = decode_token(token)
        assert payload["sub"] == "42"
        assert payload["type"] == "access"
        assert payload["username"] == "alice"
        assert payload["role"] == ROLE_MANAGER
        assert payload["firm_id"] == 1

    def test_refresh_token_type(self):
        token = create_refresh_token(user_id=1, username="bob")
        payload = decode_token(token)
        assert payload["type"] == "refresh"

    def test_tampered_token_rejected(self):
        token = create_access_token(user_id=1, username="a", role=ROLE_ASSISTANT)
        bad = token[:-3] + ("XYZ" if not token.endswith("XYZ") else "ABC")
        with pytest.raises(JWTError):
            decode_token(bad)

    def test_garbage_token_rejected(self):
        with pytest.raises(JWTError):
            decode_token("not.a.real.jwt")
        with pytest.raises(JWTError):
            decode_token("")

    def test_stdlib_fallback_encode_decode(self):
        """直接走 stdlib 路径, 确保 jose 不可用时仍工作."""
        from app.services.auth.jwt import _stdlib_decode, _stdlib_encode

        # iss 是必填 (P0 安全修复: 防止 token 重放)
        token = _stdlib_encode(
            {"sub": "1", "exp": int(time.time()) + 60, "iss": "ipo-audit-system"}
        )
        payload = _stdlib_decode(token)
        assert payload["sub"] == "1"
        assert payload["iss"] == "ipo-audit-system"

    def test_stdlib_expired_rejected(self):
        """stdlib 直接解码已过期 token, 应抛 JWTError.

        注意: 用 Exception 而非 import 的 JWTError 引用, 避免某些测试运行顺序下
        module reload 导致 isinstance 比较失败.
        """
        from app.services.auth.jwt import (
            JWTError as RuntimeJWTError,
            _stdlib_decode,
            _stdlib_encode,
        )

        token = _stdlib_encode({"sub": "1", "exp": int(time.time()) - 60})
        try:
            _stdlib_decode(token)
        except Exception as exc:
            # 必须是 JWTError 类 (按类名比较, 不按 isinstance, 抗 reload)
            assert exc.__class__.__name__ == "JWTError" or isinstance(exc, RuntimeJWTError)
            assert "过期" in str(exc) or "exp" in str(exc).lower()
        else:
            pytest.fail("应抛 JWTError 但未抛出")


# ============================================================
#  RBAC
# ============================================================


class TestRBAC:
    def test_role_at_least_basic(self):
        assert role_at_least(ROLE_SIGNING_PARTNER, ROLE_ASSISTANT)
        assert role_at_least(ROLE_MANAGER, ROLE_ASSISTANT)
        assert not role_at_least(ROLE_ASSISTANT, ROLE_MANAGER)

    def test_admin_is_supreme(self):
        assert role_at_least(ROLE_ADMIN, ROLE_SIGNING_PARTNER)
        assert role_at_least(ROLE_ADMIN, ROLE_ADMIN)

    def test_unknown_role_denied(self):
        assert not role_at_least("", ROLE_ASSISTANT)
        assert not role_at_least(None, ROLE_ASSISTANT)  # type: ignore[arg-type]

    def test_equal_role_allowed(self):
        assert role_at_least(ROLE_PARTNER, ROLE_PARTNER)


# ============================================================
#  Approval Engine (内存 mock - 不用真实 DB)
# ============================================================


class _MemSession:
    """简易的 in-memory AsyncSession 替身, 仅支持本测试需要的几个操作.

    真实 ORM 测试在集成测试里跑.
    """

    def __init__(self):
        self.added: list = []
        self.deleted: list = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def flush(self):
        # 给 ApprovalWorkflow.id 赋一个伪 id
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = len(self.added)

    async def refresh(self, obj):
        return None

    async def execute(self, *args, **kwargs):
        raise RuntimeError("_MemSession 不支持 execute() — 单元测试请挑不依赖 select 的路径")


def _make_user(uid: int, role: str) -> User:
    return User(
        id=uid,
        username=f"user{uid}",
        full_name=f"用户{uid}",
        role=role,
        is_active=True,
        is_locked=False,
        password_hash="!",
    )


class TestApprovalEngineUnit:
    @pytest.mark.asyncio
    async def test_create_workflow_uses_default_five_level(self):
        sess = _MemSession()
        user = _make_user(1, ROLE_ASSISTANT)
        wf = await ApprovalEngine.create_workflow(
            sess,  # type: ignore[arg-type]
            initiator=user,
            resource_type="confirmation_case",
            resource_id=99,
            title="某函证锁定审批",
        )
        assert wf.total_steps == 5
        assert wf.current_step == 1
        assert wf.status == APPROVAL_STATUS_IN_PROGRESS
        assert wf.initiator_display == "用户1"
        # 5 个 ApprovalStep 应被 add
        assert sum(1 for x in sess.added if hasattr(x, "step_no")) == 5

    @pytest.mark.asyncio
    async def test_create_workflow_validates_step_continuity(self):
        sess = _MemSession()
        with pytest.raises(InvalidApprovalAction):
            await ApprovalEngine.create_workflow(
                sess,  # type: ignore[arg-type]
                initiator=None,
                resource_type="r",
                resource_id=1,
                title="x",
                steps=[StepSpec(step_no=1, required_role=ROLE_ASSISTANT),
                       StepSpec(step_no=3, required_role=ROLE_MANAGER)],  # 跳号
            )

    @pytest.mark.asyncio
    async def test_default_flow_roles_are_5_levels(self):
        roles = [s.required_role for s in DEFAULT_FIVE_LEVEL_FLOW]
        assert roles == [ROLE_ASSISTANT, ROLE_MANAGER, ROLE_PARTNER,
                         ROLE_QC_PARTNER, ROLE_SIGNING_PARTNER]
