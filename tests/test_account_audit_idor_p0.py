"""Round 31 IDOR P0 回归 — 修 round 29 xfail 捕到的 2 个 P0.

覆盖:
  1) account_audit.projects/{id}/scope-overrides/{oid} DELETE 跨所应 403
     (round 31: 加 ensure_project_in_firm)
  2) workbooks.trial-balance 跨所应 403
     (round 31: 加 ensure_project_in_firm + 修 balance_result["standalone"]["ending"] KeyError)
"""
from __future__ import annotations

import os
import tempfile

# 在 import app 之前设环境变量, 用临时文件 DB (AuditLogMiddleware 写日志需要)
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
from app.models.db.auth import (  # noqa: E402
    ROLE_ASSISTANT,
    User,
)
from app.models.db_models import (  # noqa: E402
    AccountBalance,
    Project,
)


# ============================================================
#  Module-level fixtures (与 test_idor_round29 一致)
# ============================================================


@pytest.fixture(autouse=True)
def _no_audit_log(monkeypatch):
    from app.services.auth import audit_log as al_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(al_mod, "record_audit_log", _noop)


@pytest.fixture(autouse=True)
def _enable_auth(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)


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


def _user(uid: int = 1, role: str = ROLE_ASSISTANT, firm_id: int | None = 1) -> User:
    return User(
        id=uid,
        username=f"u{uid}",
        full_name=f"用户{uid}",
        role=role,
        is_active=True,
        is_locked=False,
        password_hash="!",
        firm_id=firm_id,
    )


def _project(pid: int, firm_id: int | None = 1) -> Project:
    return Project(
        id=pid,
        name=f"P{pid}",
        company_name="X",
        fiscal_year=2024,
        status="active",
        firm_id=firm_id,
    )


def _override_user(user: User | None):
    from app.services.auth.dependencies import get_current_user, get_current_user_optional

    def _dep():
        return user

    app.dependency_overrides[get_current_user] = _dep
    app.dependency_overrides[get_current_user_optional] = _dep


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


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ============================================================
#  1) account_audit scope-override DELETE 跨所 → 403
# ============================================================


class TestAccountAuditScopeOverrideDeleteFirmCheck:
    """round 31 P0 回归: account_audit.projects/{id}/scope-overrides/{oid} DELETE
    在 ensure_project_in_firm 后, 跨所 user 应 403.
    """

    async def test_scope_override_delete_cross_firm_403(self, engine):
        sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        from app.models.db.account_audit import LongTermAssetScopeOverride

        async with sm() as s:
            proj = _project(1, firm_id=1)
            ov = LongTermAssetScopeOverride(
                id=30, project_id=1, account_prefix="1601", action="include",
                reason="覆盖", created_by_user_id=1,
            )
            s.add_all([proj, ov])
            await s.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        resp = client.delete("/api/account-audit/projects/1/scope-overrides/30")
        assert resp.status_code == 403, f"期望 403 (跨所 firm 校验), 实际 {resp.status_code}: {resp.text}"


# ============================================================
#  2) workbooks.trial-balance 跨所 → 403; 正常路径不报 KeyError
# ============================================================


class TestWorkbookTrialBalanceFirmCheckAndKeyError:
    """round 31 P0 回归: workbooks.trial-balance
      1) 跨所 user 应 403 (ensure_project_in_firm)
      2) 同所正常路径用 balance_result["standalone"]["ending"], 不再 KeyError
    """

    async def test_trial_balance_cross_firm_403(self, engine):
        sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with sm() as s:
            proj = _project(1, firm_id=2)
            ab = AccountBalance(
                project_id=1, account_code="5001", account_name="营业收入",
                balance_direction="贷", beginning_balance=0, debit_amount=0,
                credit_amount=1_000_000, ending_balance=1_000_000,
            )
            s.add_all([proj, ab])
            await s.commit()

        firm1_user = _user(1, ROLE_ASSISTANT, firm_id=1)
        _override_user(firm1_user)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/workbooks/trial-balance",
            json={"project_id": 1, "period_end": "2024-12-31"},
        )
        assert resp.status_code == 403, f"期望 403 (跨所 firm 校验), 实际 {resp.status_code}: {resp.text}"

    async def test_trial_balance_returns_ending_correctly(self, engine):
        """同所正常路径: TrialBalanceResponse.standalone.ending 路径走通, 不报 KeyError."""
        sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with sm() as s:
            proj = _project(1, firm_id=1)
            ab_debit = AccountBalance(
                project_id=1, account_code="1001", account_name="库存现金",
                balance_direction="借", beginning_balance=0, debit_amount=500_000,
                credit_amount=0, ending_balance=500_000,
            )
            ab_credit = AccountBalance(
                project_id=1, account_code="5001", account_name="营业收入",
                balance_direction="贷", beginning_balance=0, debit_amount=0,
                credit_amount=500_000, ending_balance=500_000,
            )
            s.add_all([proj, ab_debit, ab_credit])
            await s.commit()

        firm1_user = _user(1, ROLE_ASSISTANT, firm_id=1)
        _override_user(firm1_user)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/workbooks/trial-balance",
            json={"project_id": 1, "period_end": "2024-12-31"},
        )
        assert resp.status_code == 200, f"期望 200, 实际 {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["is_balanced"] is True
        assert data["total_debit"] == 500_000.0
        assert data["total_credit"] == 500_000.0
        assert abs(data["difference"]) < 0.01
