"""Round 31 P0-13 JWT 刷新 token 回归 — 验证 refresh token 机制.

覆盖:
  1) login 响应含 refresh_token
  2) /api/auth/refresh 用 refresh_token 换新 access_token
  3) 重复使用同一 refresh_token 应 401 (因 token type 不是 refresh — 用 access token 走 refresh)

注: 项目设计 refresh_token 不轮换, 重复使用同一 refresh_token 仍能换 access_token;
   本测试用 access token (type=access) 走 /refresh 验证 401 路径.
"""
from __future__ import annotations

import os
import tempfile

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.db.auth import ROLE_ADMIN, User  # noqa: E402
from app.services.auth.password import hash_password  # noqa: E402


# ============================================================
#  Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _no_audit_log(monkeypatch):
    from app.services.auth import audit_log as al_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(al_mod, "record_audit_log", _noop)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


_TEST_ENGINE = None


@pytest.fixture(autouse=True)
def _set_test_engine(engine):
    global _TEST_ENGINE
    _TEST_ENGINE = engine

    async def _db():
        sm = async_sessionmaker(_TEST_ENGINE, expire_on_commit=False, class_=AsyncSession)
        async with sm() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    yield
    _TEST_ENGINE = None


@pytest.fixture
def seeded_user(engine):
    """在 test engine 里 seed 一个 user (admin / admin123)."""
    import asyncio

    async def _seed():
        sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with sm() as s:
            u = User(
                id=1,
                username="admin",
                full_name="管理员",
                role=ROLE_ADMIN,
                is_active=True,
                is_locked=False,
                password_hash=hash_password("admin123"),
                firm_id=1,
            )
            s.add(u)
            await s.commit()
            return u

    return asyncio.get_event_loop().run_until_complete(_seed()) if False else _seed


# 简化: 用 sync 写一个 seed_user coroutine helper
def _seed_user_sync(engine, username="admin", password="admin123"):
    """Run coroutine to completion synchronously for TestClient."""
    import asyncio
    from app.services.auth.password import hash_password as _hp

    async def _do():
        sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with sm() as s:
            u = User(
                id=1,
                username=username,
                full_name="管理员",
                role=ROLE_ADMIN,
                is_active=True,
                is_locked=False,
                password_hash=_hp(password),
                firm_id=1,
            )
            s.add(u)
            await s.commit()

    asyncio.get_event_loop().run_until_complete(_do())


@pytest.fixture
def admin_user(engine):
    _seed_user_sync(engine)
    return engine


# ============================================================
#  1) login 响应含 refresh_token
# ============================================================


class TestLoginReturnsRefreshToken:
    def test_login_returns_refresh_token(self, admin_user):
        client = TestClient(app)
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "access_token" in data and data["access_token"]
        assert "refresh_token" in data and data["refresh_token"]
        assert data["token_type"] == "bearer"
        # access_token 和 refresh_token 应不同 (type claim 区分)
        assert data["access_token"] != data["refresh_token"]


# ============================================================
#  2) /api/auth/refresh 用 refresh_token 换新 access_token
# ============================================================


class TestRefreshEndpointReturnsNewAccessToken:
    def test_refresh_endpoint_returns_new_access_token(self, admin_user):
        client = TestClient(app)
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert login_resp.status_code == 200
        refresh_token = login_resp.json()["refresh_token"]

        refresh_resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_resp.status_code == 200, refresh_resp.text
        data = refresh_resp.json()
        assert "access_token" in data and data["access_token"]
        assert data["token_type"] == "bearer"


# ============================================================
#  3) 错误 token 类型走 /refresh 应 401
# ============================================================


class TestRefreshRejectsAccessToken:
    """用 access_token (type=access) 走 /refresh 应 401 — 验证 token type 校验."""

    def test_access_token_used_as_refresh_should_401(self, admin_user):
        client = TestClient(app)
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        access_token = login_resp.json()["access_token"]

        refresh_resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert refresh_resp.status_code == 401, (
            f"期望 401 (access token type=access 不应用作 refresh), "
            f"实际 {refresh_resp.status_code}: {refresh_resp.text}"
        )
