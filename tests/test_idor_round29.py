"""Round 29 IDOR / 多租户矩阵扩展 — 覆盖剩余路由.

本轮聚焦 round 24 / round 25 还没测到的 10 个高风险 IDOR 路径, 每条都跑
'firm_A user 访问 firm_B 数据' 应 403/404 的回归 case.

覆盖:
  1. team_management.create_member  - 跨所创建成员无 firm 限制
  2. confirmations.cases/{id}/generate - 跨所生成案卷统计
  3. confirmations.cases/{id}/unlock - 跨所解锁案卷
  4. confirmations.items/{id}/send - 跨所发函
  5. account_audit.projects/{id}/initialize - 跨所初始化长期资产
  6. account_audit.projects/{id}/scope-overrides/{id} - 跨所删除科目覆盖
  7. account_audit.projects/{id}/trial-balance - 跨所试算平衡
  8. inventory.projects/{id}/count-plan - 跨所生成盘点计划
  9. workbooks.trial-balance - 跨所试算平衡
 10. inventory.projects/{id}/impairments/prior - 跨所上传跌价
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
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    User,
)
from app.models.db_models import (  # noqa: E402
    AccountBalance,
    ConfirmationCase,
    ConfirmationItem,
    Project,
    SalesDocument,
    SalesRecord,
    TeamMember,
    WorkPlan,
)
from app.models.db.account_audit import AccountMovementAudit, MOVEMENT_DIRECTION_DEBIT  # noqa: E402


# ============================================================
#  Module-level: 关闭 audit log, 防污染 test 输出
# ============================================================


@pytest.fixture(autouse=True)
def _no_audit_log(monkeypatch):
    from app.services.auth import audit_log as al_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(al_mod, "record_audit_log", _noop)


@pytest.fixture(autouse=True)
def _enable_auth(monkeypatch):
    """所有测试都开启 AUTH_ENABLED, 让 firm 过滤生效. 单独测试再 monkeypatch 关掉."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)


# ============================================================
#  Fixtures
# ============================================================


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


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        yield s


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


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ============================================================
#  Helper: 装 test app 用同一 in-memory engine (避免 AuditLogMiddleware 写文件)
# ============================================================


def _install_in_memory_db():
    """Override get_db 用 in-memory engine (跳过 AuditLogMiddleware 文件写)."""

    async def _db():
        sm = async_sessionmaker(_TEST_ENGINE, expire_on_commit=False, class_=AsyncSession)
        async with sm() as s:
            yield s

    app.dependency_overrides[get_db] = _db


_TEST_ENGINE = None  # will be set by autouse session


@pytest.fixture(autouse=True)
def _set_test_engine(engine):
    """让 _install_in_memory_db 能拿到当前 test 的 engine."""
    global _TEST_ENGINE
    _TEST_ENGINE = engine
    _install_in_memory_db()
    yield
    _TEST_ENGINE = None


# ============================================================
#  1) team_management.create_member — 跨所创建成员
# ============================================================


class TestCreateMemberFirmCheck:
    """team_management.create_member — 创建成员时, 必须把 user.firm_id 自动落上,
    否则任何 firm_A user 可创建 firm_B 成员, 然后 list_members 跨所可见 (泄漏人员数据).

    现状: TeamMember 模型本身无 firm_id 字段, 关联靠 ProjectAssignment→Project.firm_id.
    P0 修复方向: create_member 应在 user 角色==admin 时允许无 firm, 非 admin 时强制
    后续 ProjectAssignment 校验; 但当前已有 ensure_team_member_in_firm 间接校验成员
    可见性 (通过 ProjectAssignment 跨所查), 因此创建跨所成员的实际风险是"创建匿名
    成员后被 list_members 跨所列出". 这里只验证 create_member 不抛, 后续 list 时
    不返回别所匿名成员.
    """

    async def test_create_member_other_firm_visible_only_via_assignment(
        self, session
    ):
        """firm_A user 创建成员, firm_B user 看不到 (除非通过 ProjectAssignment 跨所)."""
        # firm 1 user 创建成员
        firm1_user = _user(1, ROLE_ASSISTANT, firm_id=1)
        _override_user(firm1_user)
        client = TestClient(app)
        resp = client.post(
            "/api/team-management/members",
            json={
                "full_name": "A创建",
                "level": "senior",
                "role": "auditor",
            },
        )
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]

        # 切到 firm 2 user
        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        resp = client.get("/api/team-management/members")
        assert resp.status_code == 200
        ids = {m["id"] for m in resp.json()}
        # firm 2 不应看到 firm 1 创建的成员 (没 ProjectAssignment 关联)
        assert new_id not in ids


# ============================================================
#  2) confirmations.cases/{id}/generate — 跨所生成案卷统计
# ============================================================


class TestCaseGenerateFirmCheck:
    """case 在 firm 1, firm 2 user 调 /generate 应 403."""

    async def test_cross_firm_generate_403(self, session):
        proj = _project(1, firm_id=1)
        case = ConfirmationCase(
            id=10, project_id=1, case_name="C1", period_end="2024-12-31",
            fiscal_year=2024, generated_by="u1", is_locked=False,
        )
        session.add_all([proj, case])
        await session.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        # GenerateStatsRequest requires case_id in body
        resp = client.post(
            "/api/confirmations/cases/10/generate",
            json={"case_id": 10, "persist": False},
        )
        assert resp.status_code == 403, resp.text


# ============================================================
#  3) confirmations.cases/{id}/unlock — 跨所解锁案卷
# ============================================================


class TestCaseUnlockFirmCheck:
    """case 在 firm 1, firm 2 user 调 /unlock 应 403 (manager 角色 + 同所)."""

    async def test_cross_firm_unlock_403(self, session):
        proj = _project(1, firm_id=1)
        case = ConfirmationCase(
            id=11, project_id=1, case_name="C1", period_end="2024-12-31",
            fiscal_year=2024, generated_by="u1", is_locked=True,
        )
        session.add_all([proj, case])
        await session.commit()

        firm2_user = _user(2, ROLE_MANAGER, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        resp = client.post(
            "/api/confirmations/cases/11/unlock",
            json={"reason": "越权"},
        )
        assert resp.status_code == 403, resp.text


# ============================================================
#  4) confirmations.items/{id}/send — 跨所发函
# ============================================================


class TestItemSendFirmCheck:
    """item 在 firm 1, firm 2 user 调 /send 应 403."""

    async def test_cross_firm_send_403(self, session):
        proj = _project(1, firm_id=1)
        case = ConfirmationCase(
            id=12, project_id=1, case_name="C1", period_end="2024-12-31",
            fiscal_year=2024, generated_by="u1", is_locked=True,
        )
        item = ConfirmationItem(
            id=20, case_id=12, party_name="客户A", party_type="customer",
            status="draft", book_balance=100.0, book_balance_date="2024-12-31",
        )
        session.add_all([proj, case, item])
        await session.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        from datetime import date

        resp = client.post(
            "/api/confirmations/items/20/send",
            json={
                "item_id": 20,
                "sent_date": str(date.today()),
                "template_id": "standard",
                "sent_method": "邮寄",
            },
        )
        assert resp.status_code == 403, resp.text


# ============================================================
#  5) account_audit.projects/{id}/initialize — 跨所初始化长期资产
# ============================================================


class TestAccountAuditInitializeFirmCheck:
    """project 在 firm 1, firm 2 user 调 /initialize 应 403."""

    async def test_cross_firm_initialize_403(self, session):
        proj = _project(1, firm_id=1)
        session.add(proj)
        await session.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        # period_end 是 query param, 不是 body
        resp = client.post(
            "/api/account-audit/projects/1/initialize",
            params={"period_end": "2024-12-31"},
        )
        assert resp.status_code == 403, resp.text


# ============================================================
#  6) account_audit.projects/{id}/scope-overrides/{id} — 跨所删科目覆盖
# ============================================================


class TestAccountAuditScopeOverrideFirmCheck:
    """P0 IDOR 已修: account_audit.projects/{id}/scope-overrides/{oid} endpoint
    在 round 31 加 ensure_project_in_firm, 跨所 user 删别所 scope override 应 403.
    """

    async def test_cross_firm_scope_override_403(self, session):
        proj = _project(1, firm_id=1)
        # 建一个 override 记录以便 endpoint 能 resolve
        from app.models.db.account_audit import LongTermAssetScopeOverride

        ov = LongTermAssetScopeOverride(
            id=30, project_id=1, account_prefix="1601", action="include",
            reason="覆盖", created_by_user_id=1,
        )
        session.add_all([proj, ov])
        await session.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        resp = client.delete("/api/account-audit/projects/1/scope-overrides/30")
        assert resp.status_code == 403, resp.text


# ============================================================
#  7) account_audit.projects/{id}/trial-balance — 跨所试算平衡
# ============================================================


class TestAccountAuditMovementsListFirmCheck:
    """project 在 firm 1, firm 2 user 调 /movements 应 403."""

    async def test_cross_firm_movements_list_403(self, session):
        proj = _project(1, firm_id=1)
        session.add(proj)
        m = AccountMovementAudit(
            project_id=1, account_code="1601", account_name="固定资产",
            period_end="2024-12-31", voucher_date="2024-12-01", voucher_no="JZ-001",
            voucher_line_no=1, direction=MOVEMENT_DIRECTION_DEBIT, book_amount=1000.0,
        )
        session.add(m)
        await session.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        resp = client.get("/api/account-audit/projects/1/movements")
        assert resp.status_code == 403, resp.text


# ============================================================
#  8) inventory.projects/{id}/count-plan — 跨所生成盘点计划
# ============================================================


class TestCountPlanFirmCheck:
    """project 在 firm 1, firm 2 user 调 /count-plan 应 403."""

    async def test_cross_firm_count_plan_403(self, session):
        proj = _project(1, firm_id=1)
        session.add(proj)
        await session.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        resp = client.post(
            "/api/inventory/projects/1/count-plan",
            json={"period_end": "2024-12-31", "count_days_before": 3, "count_days_after": 3},
        )
        assert resp.status_code == 403, resp.text


# ============================================================
#  9) workbooks.trial-balance — 跨所试算平衡
# ============================================================


class TestWorkbookTrialBalanceFirmCheck:
    """P0 IDOR + KeyError 已修: workbooks.trial-balance 端点在 round 31 加
    ensure_project_in_firm + 改 standalone.ending 路径, 跨所 user 应 403.
    """

    async def test_cross_firm_trial_balance_should_403(self, session):
        proj = _project(1, firm_id=2)
        ab = AccountBalance(
            project_id=1, account_code="5001", account_name="营业收入",
            balance_direction="贷", beginning_balance=0, debit_amount=0,
            credit_amount=1_000_000, ending_balance=1_000_000,
        )
        session.add_all([proj, ab])
        await session.commit()

        firm1_user = _user(1, ROLE_ASSISTANT, firm_id=1)
        _override_user(firm1_user)
        # raise_server_exceptions=False 让 TestClient 把 server 异常转成 500
        # 而不是 re-raise 出来
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/workbooks/trial-balance",
            json={"project_id": 1, "period_end": "2024-12-31"},
        )
        assert resp.status_code == 403, resp.text


# ============================================================
#  10) inventory.projects/{id}/impairments/prior — 跨所上传跌价
# ============================================================


class TestPriorImpairmentFirmCheck:
    """project 在 firm 1, firm 2 user 调 /impairments/prior 应 403."""

    async def test_cross_firm_prior_impairment_403(self, session):
        proj = _project(1, firm_id=1)
        session.add(proj)
        await session.commit()

        firm2_user = _user(2, ROLE_ASSISTANT, firm_id=2)
        _override_user(firm2_user)
        client = TestClient(app)
        resp = client.post(
            "/api/inventory/projects/1/impairments/prior",
            params={"period_end": "2023-12-31"},
            json={"items": {"M-001": 1000.0}},
        )
        assert resp.status_code == 403, resp.text
