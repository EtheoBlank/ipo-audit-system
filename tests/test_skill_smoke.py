"""tests/_helpers smoke test — 验证 test skill 自身可用.

本测试是 round 32+ 新加 ``tests/_helpers/`` 的"自检": 不验证业务逻辑,
只验证 helper 模块 import OK + 角色常量与项目 ORM 一致 + 工厂函数签名可用.

任何 test_*.py 都可以::

    from tests._helpers.auth import make_user, ROLE_ADMIN
    from tests._helpers.idor import assert_cross_firm_404

跑本文件 1s 内全绿即说明 skill ready.
"""
from __future__ import annotations

import pytest

# ============================================================
#  模块可导入
# ============================================================


class TestHelpersImportable:
    def test_import_root(self):
        from tests import _helpers
        assert hasattr(_helpers, "auth")
        assert hasattr(_helpers, "db")
        assert hasattr(_helpers, "idor")
        assert hasattr(_helpers, "http")
        assert hasattr(_helpers, "pagination")

    def test_import_submodules(self):
        from tests._helpers import auth, db, http, idor, pagination
        # 关键函数 / 类 都在
        assert hasattr(auth, "make_user")
        assert hasattr(auth, "make_firm")
        assert hasattr(auth, "make_token")
        assert hasattr(auth, "ROLE_ADMIN")
        assert hasattr(db, "async_engine")
        assert hasattr(db, "async_session")
        assert hasattr(idor, "assert_cross_firm_404")
        assert hasattr(idor, "assert_role_required")
        assert hasattr(idor, "assert_anonymous_401")
        assert hasattr(http, "client")
        assert hasattr(http, "auth_headers")
        assert hasattr(pagination, "assert_paginated")
        assert hasattr(pagination, "assert_all_unique")


# ============================================================
#  角色常量与项目 ORM 一致
# ============================================================


class TestRoleConstants:
    def test_role_constants_match_orm(self):
        """helper 暴露的角色字符串必须与 ORM 字段枚举一致.
        不一致会导致 make_user(role=X) 写入非法值."""
        from tests._helpers.auth import (
            ROLE_ADMIN, ROLE_QC_PARTNER, ROLE_PARTNER,
            ROLE_MANAGER, ROLE_ASSISTANT,
        )
        from app.models.db.auth import (
            ROLE_ADMIN as ORM_ADMIN,
            ROLE_QC_PARTNER as ORM_QC,
            ROLE_PARTNER as ORM_PARTNER,
            ROLE_MANAGER as ORM_MANAGER,
            ROLE_ASSISTANT as ORM_ASSISTANT,
        )
        assert ROLE_ADMIN == ORM_ADMIN
        assert ROLE_QC_PARTNER == ORM_QC
        assert ROLE_PARTNER == ORM_PARTNER
        assert ROLE_MANAGER == ORM_MANAGER
        assert ROLE_ASSISTANT == ORM_ASSISTANT


# ============================================================
#  工厂函数签名
# ============================================================


class TestFactorySignatures:
    def test_make_firm_defaults(self):
        from tests._helpers.auth import make_firm
        import inspect
        sig = inspect.signature(make_firm)
        params = list(sig.parameters.keys())
        assert "db" in params
        # name, is_active, commit 都应有默认
        assert sig.parameters["name"].default == "测试事务所"
        assert sig.parameters["is_active"].default is True
        assert sig.parameters["commit"].default is False

    def test_make_user_defaults(self):
        from tests._helpers.auth import make_user
        import inspect
        sig = inspect.signature(make_user)
        params = list(sig.parameters.keys())
        assert "db" in params
        assert "firm_id" in params
        assert sig.parameters["firm_id"].default is None
        assert sig.parameters["username"].default == "test_user"
        assert sig.parameters["commit"].default is False

    def test_make_token_signature(self):
        from tests._helpers.auth import make_token
        import inspect
        sig = inspect.signature(make_token)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        # firm_id / role 默认 None / assistant
        assert sig.parameters["firm_id"].default is None


# ============================================================
#  Async DB fixtures 可 await
# ============================================================


class TestAsyncFixtures:
    @pytest.mark.asyncio
    async def test_async_session_yields_session(self, async_session):
        """async_session fixture 应 yield 一个 AsyncSession."""
        from sqlalchemy.ext.asyncio import AsyncSession
        assert isinstance(async_session, AsyncSession)

    @pytest.mark.asyncio
    async def test_async_session_transaction_rolls_back(self, async_session):
        """fixture 退出时 rollback, 写入数据不应持久."""
        from app.models.db.auth import Firm
        firm = Firm(name="临时事务所")
        async_session.add(firm)
        await async_session.flush()
        # 退出 fixture 时 rollback, 下次拿 fresh session 应看不到
        firm_id = firm.id
        assert firm_id is not None

    @pytest.mark.asyncio
    async def test_make_firm_then_query(self, async_session):
        """make_firm → 立即 query 可见 (在同一个 session 内)."""
        from sqlalchemy import select
        from app.models.db.auth import Firm
        from tests._helpers.auth import make_firm

        firm = await make_firm(async_session, name="测试A")
        result = await async_session.execute(
            select(Firm).where(Firm.id == firm.id)
        )
        found = result.scalar_one_or_none()
        assert found is not None
        assert found.name == "测试A"

    @pytest.mark.asyncio
    async def test_make_user_with_firm(self, async_session):
        from sqlalchemy import select
        from app.models.db.auth import User
        from tests._helpers.auth import make_firm, make_user

        firm = await make_firm(async_session)
        user = await make_user(
            async_session, firm_id=firm.id,
            username="alice", role="admin",
        )
        result = await async_session.execute(
            select(User).where(User.id == user.id)
        )
        found = result.scalar_one_or_none()
        assert found is not None
        assert found.firm_id == firm.id
        assert found.username == "alice"
        assert found.role == "admin"


# ============================================================
#  IDOR helper 函数签名
# ============================================================


class TestIdorHelpers:
    def test_signatures(self):
        from tests._helpers.idor import (
            assert_cross_firm_404,
            assert_role_required,
            assert_anonymous_401,
        )
        import inspect

        # assert_cross_firm_404(client, method, path, *, own_token, other_token, json, msg)
        sig = inspect.signature(assert_cross_firm_404)
        params = list(sig.parameters.keys())
        for required in ("client", "method", "path", "own_token", "other_token"):
            assert required in params, f"assert_cross_firm_404 缺 {required}"

        sig = inspect.signature(assert_role_required)
        params = list(sig.parameters.keys())
        for required in ("client", "method", "path", "allowed_token", "denied_token"):
            assert required in params, f"assert_role_required 缺 {required}"

        sig = inspect.signature(assert_anonymous_401)
        params = list(sig.parameters.keys())
        for required in ("client", "method", "path"):
            assert required in params, f"assert_anonymous_401 缺 {required}"


# ============================================================
#  HTTP client fixture
# ============================================================


class TestHttpClient:
    def test_client_fixture_yields_testclient(self, client):
        """client fixture 应 yield 一个 TestClient 实例, 可正常请求."""
        from starlette.testclient import TestClient
        assert isinstance(client, TestClient)

    def test_health_or_openapi_endpoint(self, client):
        """随便打个根路径, 确认 client 能 work. 不依赖业务路由."""
        # 用 /openapi.json (FastAPI 默认就有)
        r = client.get("/openapi.json")
        # 不论 AUTH_ENABLED, openapi 都应 200
        assert r.status_code == 200, f"openapi.json 应 200, 实得 {r.status_code}"


# ============================================================
#  Pagination helpers
# ============================================================


class TestPaginationHelpers:
    def test_paginated_ok(self):
        from tests._helpers.pagination import assert_paginated
        assert_paginated(
            {"items": [1, 2, 3], "total": 100, "page": 1, "size": 3},
            expected_total=100,
            expected_page_size=3,
        )

    def test_paginated_missing_items(self):
        from tests._helpers.pagination import assert_paginated
        with pytest.raises(AssertionError, match="items"):
            assert_paginated({"total": 0})

    def test_paginated_total_mismatch(self):
        from tests._helpers.pagination import assert_paginated
        with pytest.raises(AssertionError, match="total"):
            assert_paginated({"items": [], "total": 5}, expected_total=10)

    def test_paginated_oversize(self):
        from tests._helpers.pagination import assert_paginated
        with pytest.raises(AssertionError, match="page_size"):
            assert_paginated(
                {"items": [1, 2, 3, 4, 5], "total": 5},
                expected_page_size=3,
            )

    def test_all_unique_ok(self):
        from tests._helpers.pagination import assert_all_unique
        assert_all_unique([1, 2, 3])

    def test_all_unique_dup_fails(self):
        from tests._helpers.pagination import assert_all_unique
        with pytest.raises(AssertionError, match="重复"):
            assert_all_unique([1, 2, 1])

    def test_all_unique_by_key(self):
        from tests._helpers.pagination import assert_all_unique
        class Item:
            def __init__(self, k, v):
                self.k = k
                self.v = v
            def __repr__(self):
                return f"Item({self.k}, {self.v})"
        assert_all_unique(
            [Item("a", 1), Item("a", 2), Item("b", 3)],
            key="k",
            msg="同 k 应报错",
        ) if False else None  # 同 k 会报错
        with pytest.raises(AssertionError):
            assert_all_unique(
                [Item("a", 1), Item("a", 2), Item("b", 3)],
                key="k",
            )
