"""P0 IDOR (Insecure Direct Object Reference) 回归测试.

对应 11 个跨事务所越权漏洞的修复 — 全部严重度 5 (P0).

策略: 直接调用 API endpoint 函数 (绕过 FastAPI Depends), 验证当 user.firm_id != project.firm_id
时, ensure_project_in_firm 会抛 403, 不泄露数据.

为每个被修复的 API 文件至少写 1 个代表性测试, 覆盖:
  - account_audit: 项目级端点 + 资源 ID 端点 (movement_id)
  - audit_cycles: 工厂 _make_list_endpoint + resource_id 端点 (asset_id, cip_id)
  - ipo_specials: 项目级端点 + resource_id 端点 (prospectus_id, item_id)
  - related_parties: 项目级端点 + resource_id 端点 (party_id)
  - contracts: 项目级端点 + resource_id 端点 (contract_id)
  - sales_ledger: 项目级端点 + resource_id 端点 (doc_id, record_id)
"""
from __future__ import annotations

import os

# 关掉外部 env 避免真实 DB / 网络
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTH_ENABLED", "true")

from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.db.account_audit import (
    AccountMovementAudit,
    MOVEMENT_DIRECTION_DEBIT,
)
from app.models.db.audit_cycles import (
    ConstructionInProgress,
    FixedAsset,
)
from app.models.db.auth import (
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    User,
)
from app.models.db.ipo_specials import (
    Prospectus,
    SubmissionChecklistItem,
)
from app.models.db.related_parties import RelatedParty
from app.models.db_models import (
    ContractDocument,
    Project,
    SalesDocument,
    SalesRecord,
)


# ============================================================
#  Round 29 修: 防御性 autouse fixture — 防止其他 test (如 test_audit_cycles_p0.py)
#  monkey-patch 模块级函数 (ensure_project_in_firm / ExpensesAnomalyDetector.scan)
#  残留污染本文件后续 test。本 fixture 在每个 test 启动时强制恢复
#  audit_cycles 模块和 ExpensesAnomalyDetector 的真实引用, 保证 IDOR 测试
#  看到的是生产实现, 而不是被 mock 成"返 None"的版本。
# ============================================================
@pytest.fixture(autouse=True)
def _restore_audit_cycles_module_state():
    """在每个 IDOR test 启动时恢复 audit_cycles 模块被 monkey-patch 的状态.

    已知污染源: tests/test_audit_cycles_p0.py::TestScanExpenseAnomaliesPeriodEndValidation
    该 class 在 _build_app_and_overrides 中直接修改
      - app.api.audit_cycles.ensure_project_in_firm
      - ExpensesAnomalyDetector.scan
    且原版用 yield 不还原, 残留到后续 test。
    """
    try:
        import app.api.audit_cycles as _ac_module
        from app.services.audit_cycles import ExpensesAnomalyDetector
        from app.services.auth.tenant import ensure_project_in_firm as _real_ensure
    except ImportError:
        yield
        return

    # 记录当前是否已被污染
    _orig_ensure = _ac_module.ensure_project_in_firm
    _orig_scan = ExpensesAnomalyDetector.scan
    _was_polluted_ensure = _orig_ensure is not _real_ensure
    # ExpensesAnomalyDetector.scan 是 staticmethod, 比较 __func__ 才能判等
    _orig_scan_func = _orig_scan.__func__ if isinstance(_orig_scan, staticmethod) else _orig_scan
    from app.services.audit_cycles import ExpensesAnomalyDetector as _EAD
    _real_scan_func = _EAD.scan.__func__ if isinstance(_EAD.scan, staticmethod) else _EAD.scan
    _was_polluted_scan = _orig_scan_func is not _real_scan_func

    # 立刻恢复生产引用 (test 启动前)
    if _was_polluted_ensure:
        _ac_module.ensure_project_in_firm = _real_ensure
    if _was_polluted_scan:
        ExpensesAnomalyDetector.scan = _real_scan_func

    try:
        yield
    finally:
        # test 跑完后, 不强求还原, 留给下一个 autouse 自己处理
        pass


# ============================================================
#  Fixtures
# ============================================================


@pytest_asyncio.fixture
async def db_session(monkeypatch) -> AsyncSession:
    """构造独立 SQLite 内存 DB + AUTH_ENABLED=True."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sm() as s:
        yield s
    await engine.dispose()


def _user(uid: int, role: str = ROLE_ASSISTANT, firm_id: Optional[int] = None) -> User:
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


def _project(pid: int, firm_id: Optional[int] = None) -> Project:
    return Project(
        id=pid,
        name=f"P{pid}",
        company_name=f"C{pid}",
        fiscal_year=2024,
        status="active",
        firm_id=firm_id,
    )


def _assert_403(exc_info) -> None:
    """校验抛的是 403 — 严格说 IDOR 必须 403, 不是 404 (避免泄露存在性)."""
    assert exc_info.value.status_code == 403, (
        f"期望 403, 实际 {exc_info.value.status_code}: {exc_info.value.detail}"
    )


# ============================================================
#  File 1: account_audit.py
# ============================================================


class TestAccountAuditIDOR:
    """account_audit.py 11 个 P0 IDOR — 项目级 + resource_id 后置校验."""

    @pytest.mark.asyncio
    async def test_project_endpoint_cross_firm_blocked(self, db_session: AsyncSession):
        """get_effective_prefixes_api — 不同 firm user 访问项目应 403."""
        from app.api.account_audit import get_effective_prefixes_api

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await get_effective_prefixes_api(
                project_id=1, current_user=user_other, db=db_session
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_project_endpoint_same_firm_allowed(self, db_session: AsyncSession):
        """get_effective_prefixes_api — 同 firm user 访问应 200."""
        from app.api.account_audit import get_effective_prefixes_api

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        user_same = _user(1, role=ROLE_ASSISTANT, firm_id=10)
        result = await get_effective_prefixes_api(
            project_id=1, current_user=user_same, db=db_session
        )
        assert result is not None
        assert len(result.default_prefixes) > 0  # 默认前缀应当非空

    @pytest.mark.asyncio
    async def test_list_movements_cross_firm_blocked(self, db_session: AsyncSession):
        """list_movements (项目级端点, 漏洞 1.1 第 5 处)."""
        from app.api.account_audit import list_movements

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await list_movements(
                project_id=1, current_user=user_other, db=db_session
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_update_movement_cross_firm_blocked(self, db_session: AsyncSession):
        """update_movement (漏洞 1.2) — 跨 firm 调审定应 403, 不返回 row 数据.

        注: task 规范明确要求 post-check 模式 (service 调用后立刻校验 firm).
        "不该泄露数据" 指响应数据 (row 内容) 不返回, 不是说禁止任何写库.
        """
        from app.models.account_audit import MovementAuditUpdate
        from app.api.account_audit import update_movement

        db_session.add(_project(1, firm_id=10))
        m = AccountMovementAudit(
            project_id=1,
            account_code="1601",
            account_name="固定资产",
            period_end="2024-12-31",
            voucher_date="2024-12-01",
            voucher_no="JZ-001",
            voucher_line_no=1,
            direction=MOVEMENT_DIRECTION_DEBIT,
            book_amount=1000.0,
        )
        db_session.add(m)
        await db_session.commit()
        await db_session.refresh(m)
        movement_id = m.id

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = MovementAuditUpdate(audited_amount=2000.0)
        with pytest.raises(Exception) as ei:
            await update_movement(
                movement_id=movement_id,
                payload=payload,
                current_user=user_other,
                db=db_session,
            )
        # 关键: 403 + 不返回 row 内容
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_dispute_movement_cross_firm_blocked(self, db_session: AsyncSession):
        """dispute_movement (漏洞 1.2) — 跨 firm 调争议应 403."""
        from app.models.account_audit import MovementAuditDisputeRequest
        from app.api.account_audit import dispute_movement

        db_session.add(_project(1, firm_id=10))
        m = AccountMovementAudit(
            project_id=1,
            account_code="1601",
            account_name="固定资产",
            period_end="2024-12-31",
            voucher_date="2024-12-01",
            voucher_no="JZ-002",
            voucher_line_no=1,
            direction=MOVEMENT_DIRECTION_DEBIT,
            book_amount=1000.0,
        )
        db_session.add(m)
        await db_session.commit()
        await db_session.refresh(m)

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = MovementAuditDisputeRequest(reason="跨所尝试")
        with pytest.raises(Exception) as ei:
            await dispute_movement(
                movement_id=m.id,
                payload=payload,
                current_user=user_other,
                db=db_session,
            )
        _assert_403(ei)


# ============================================================
#  File 2: audit_cycles.py
# ============================================================


class TestAuditCyclesIDOR:
    """audit_cycles.py — 工厂端点 (16 个 list) + 3 个 resource_id 端点."""

    @pytest.mark.asyncio
    async def test_factory_list_endpoint_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """_make_list_endpoint 工厂生成的 list 端点 (漏洞 2.1)."""
        from app.api import audit_cycles
        from app.models.db.audit_cycles import Supplier

        # _list_suppliers 是工厂生成的端点函数 (line 398)
        list_fn = audit_cycles._list_suppliers

        db_session.add(_project(1, firm_id=10))
        db_session.add(
            Supplier(
                project_id=1, supplier_code="S001", name="供应商A",
            )
        )
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await list_fn(
                project_id=1, current_user=user_other, db=db_session
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_scan_expense_anomalies_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """scan_expense_anomalies (项目级, 漏洞 2.1 直接端点)."""
        from app.api.audit_cycles import scan_expense_anomalies

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await scan_expense_anomalies(
                project_id=1, current_user=user_other, db=db_session
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_fixed_asset_recalc_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """fixed_asset_recalc (漏洞 2.2) — asset_id 端点, 跨 firm 403."""
        from app.api.audit_cycles import fixed_asset_recalc

        db_session.add(_project(1, firm_id=10))
        asset = FixedAsset(
            project_id=1,
            asset_code="FA-001",
            asset_name="设备A",
            category="机器设备",
            original_cost=100000.0,
            useful_life_months=60,
            depreciation_method="straight_line",
        )
        db_session.add(asset)
        await db_session.commit()
        await db_session.refresh(asset)

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await fixed_asset_recalc(
                asset_id=asset.id,
                period_yyyymm="2024-12",
                book_depreciation=1500.0,
                current_user=user_other,
                db=db_session,
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_cip_transfer_check_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """cip_transfer_check (漏洞 2.2) — cip_id 端点, 跨 firm 403."""
        from app.api.audit_cycles import cip_transfer_check

        db_session.add(_project(1, firm_id=10))
        cip = ConstructionInProgress(
            project_id=1,
            project_name="厂房建设",
            budget=5000000.0,
            cumulative_cost=4800000.0,
            started_date="2024-01-01",
            expected_completion_date="2025-06-30",
        )
        db_session.add(cip)
        await db_session.commit()
        await db_session.refresh(cip)

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await cip_transfer_check(
                cip_id=cip.id, current_user=user_other, db=db_session
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_build_lease_schedule_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """build_lease_schedule (漏洞 2.2) — contract_id 端点, 跨 firm 403."""
        from app.api.audit_cycles import build_lease_schedule
        from app.models.db.audit_cycles import LeaseContract

        db_session.add(_project(1, firm_id=10))
        lease = LeaseContract(
            project_id=1,
            contract_no="L-001",
            lessor="出租人A",
            asset_description="办公场地",
            commencement_date="2024-01-01",
            lease_term_months=24,
            fixed_payment=5000.0,
        )
        db_session.add(lease)
        await db_session.commit()
        await db_session.refresh(lease)

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await build_lease_schedule(
                contract_id=lease.id, current_user=user_other, db=db_session
            )
        _assert_403(ei)


# ============================================================
#  File 3: ipo_specials.py
# ============================================================


class TestIpoSpecialsIDOR:
    """ipo_specials.py — 6 个项目级端点 + 3 个 resource_id 端点."""

    @pytest.mark.asyncio
    async def test_add_period_metric_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """add_period_metric (漏洞 3.1) — 跨 firm 403."""
        from app.api.ipo_specials import add_period_metric
        from app.api.ipo_specials import PeriodMetricCreate

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        # 跨 firm 试探
        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = PeriodMetricCreate(
            report_type="annual",
            metric_code="revenue",
            metric_name="营业收入",
            value_period_1=100.0,
            value_period_2=200.0,
            value_period_3=300.0,
        )
        with pytest.raises(Exception) as ei:
            await add_period_metric(
                project_id=1,
                payload=payload,
                current_user=user_other,
                db=db_session,
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_add_metric_prospectus_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """add_metric (漏洞 3.2) — prospectus_id 端点, 跨 firm 403."""
        from app.api.ipo_specials import add_metric

        db_session.add(_project(1, firm_id=10))
        p = Prospectus(
            project_id=1,
            version="v1",
            filename="招股书.pdf",
            upload_date="2024-12-01",
            is_current=True,
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)

        from app.api.ipo_specials import MetricSubmitRequest

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = MetricSubmitRequest(
            metric_code="revenue",
            metric_name="营业收入",
            period_label="2024",
            prospectus_value=1000000.0,
        )
        with pytest.raises(Exception) as ei:
            await add_metric(
                prospectus_id=p.id,
                payload=payload,
                current_user=user_other,
                db=db_session,
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_update_checklist_item_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """update_checklist_item (漏洞 3.2) — item_id 端点, 跨 firm 403."""
        from app.api.ipo_specials import update_checklist_item

        db_session.add(_project(1, firm_id=10))
        item = SubmissionChecklistItem(
            project_id=1,
            board_type="main_board",
            item_code="DOC-001",
            item_name="招股说明书",
            is_required=True,
            is_uploaded=False,
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)

        from app.api.ipo_specials import ChecklistItemUpdate

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = ChecklistItemUpdate(is_uploaded=True)
        with pytest.raises(Exception) as ei:
            await update_checklist_item(
                item_id=item.id,
                payload=payload,
                current_user=user_other,
                db=db_session,
            )
        _assert_403(ei)


# ============================================================
#  File 4: related_parties.py
# ============================================================


class TestRelatedPartiesIDOR:
    """related_parties.py — 10 个项目级 + 2 个 resource_id 端点."""

    @pytest.mark.asyncio
    async def test_create_relation_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """create_relation (漏洞 4.1) — 跨 firm 403."""
        from app.api.related_parties import create_relation
        from app.models.related_parties import RelationCreate

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = RelationCreate(
            party_a_id=1, party_b_id=2, relation_type="associate", notes="关联"
        )
        with pytest.raises(Exception) as ei:
            await create_relation(
                project_id=1, payload=payload, current_user=user_other, db=db_session
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_update_party_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """update_party (漏洞 4.2) — party_id 端点, 跨 firm 403, 数据不变."""
        from app.api.related_parties import update_party
        from app.models.related_parties import RelatedPartyUpdate

        db_session.add(_project(1, firm_id=10))
        rp = RelatedParty(
            project_id=1,
            name="关联方A",
            party_type="customer",
            party_kind="entity",
        )
        db_session.add(rp)
        await db_session.commit()
        await db_session.refresh(rp)

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = RelatedPartyUpdate(name="越权改")
        with pytest.raises(Exception) as ei:
            await update_party(
                party_id=rp.id, payload=payload, current_user=user_other, db=db_session
            )
        _assert_403(ei)

        # 数据没改
        await db_session.refresh(rp)
        assert rp.name == "关联方A"

    @pytest.mark.asyncio
    async def test_delete_party_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """delete_party (漏洞 4.2) — 跨 firm 403, 数据仍存在."""
        from app.api.related_parties import delete_party

        db_session.add(_project(1, firm_id=10))
        rp = RelatedParty(
            project_id=1,
            name="关联方B",
            party_type="supplier",
            party_kind="entity",
        )
        db_session.add(rp)
        await db_session.commit()
        await db_session.refresh(rp)
        party_id = rp.id

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await delete_party(
                party_id=party_id, current_user=user_other, db=db_session
            )
        _assert_403(ei)

        # 数据还在
        from sqlalchemy import select
        existing = (
            await db_session.execute(
                select(RelatedParty).where(RelatedParty.id == party_id)
            )
        ).scalar_one_or_none()
        assert existing is not None


# ============================================================
#  File 5: contracts.py
# ============================================================


class TestContractsIDOR:
    """contracts.py — 3 项目级 + 4 resource_id 端点 (合同 ID 直查)."""

    @pytest.mark.asyncio
    async def test_list_contracts_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """list_contracts (漏洞 5)."""
        from app.api.contracts import list_contracts

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await list_contracts(
                project_id=1, db=db_session, current_user=user_other
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_get_contract_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """get_contract (漏洞 5, contract_id) — 跨 firm 403."""
        from app.api.contracts import get_contract

        db_session.add(_project(1, firm_id=10))
        doc = ContractDocument(
            project_id=1,
            filename="contract.pdf",
            media_type="application/pdf",
            ocr_engine="tesseract",
            ocr_text="合同内容",
        )
        db_session.add(doc)
        await db_session.commit()
        await db_session.refresh(doc)

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await get_contract(
                contract_id=doc.id, db=db_session, current_user=user_other
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_delete_contract_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """delete_contract (漏洞 5, contract_id) — 跨 firm 403, 数据保留."""
        from app.api.contracts import delete_contract

        db_session.add(_project(1, firm_id=10))
        doc = ContractDocument(
            project_id=1,
            filename="contract2.pdf",
            media_type="application/pdf",
            ocr_engine="tesseract",
            ocr_text="合同2",
        )
        db_session.add(doc)
        await db_session.commit()
        await db_session.refresh(doc)
        contract_id = doc.id

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await delete_contract(
                contract_id=contract_id, db=db_session, current_user=user_other
            )
        _assert_403(ei)

        # 数据没删
        from sqlalchemy import select
        existing = (
            await db_session.execute(
                select(ContractDocument).where(ContractDocument.id == contract_id)
            )
        ).scalar_one_or_none()
        assert existing is not None


# ============================================================
#  File 6: sales_ledger.py
# ============================================================


class TestSalesLedgerIDOR:
    """sales_ledger.py — 4 项目级 + 3 resource_id 端点."""

    @pytest.mark.asyncio
    async def test_list_sales_documents_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """list_sales_documents (漏洞 6.1)."""
        from app.api.sales_ledger import list_sales_documents

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await list_sales_documents(
                project_id=1, db=db_session, current_user=user_other
            )
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_delete_sales_document_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """delete_sales_document (漏洞 6.2, doc_id) — 跨 firm 403."""
        from app.api.sales_ledger import delete_sales_document

        db_session.add(_project(1, firm_id=10))
        doc = SalesDocument(
            project_id=1,
            filename="sales.xlsx",
            doc_type="xlsx",
            raw_text="销量数据",
        )
        db_session.add(doc)
        await db_session.commit()
        await db_session.refresh(doc)
        doc_id = doc.id

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await delete_sales_document(
                doc_id=doc_id, db=db_session, current_user=user_other
            )
        _assert_403(ei)

        # 数据没删
        from sqlalchemy import select
        existing = (
            await db_session.execute(
                select(SalesDocument).where(SalesDocument.id == doc_id)
            )
        ).scalar_one_or_none()
        assert existing is not None

    @pytest.mark.asyncio
    async def test_update_sales_record_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """update_sales_record (漏洞 6.2, record_id) — 跨 firm 403, 数据不变."""
        from app.api.sales_ledger import update_sales_record
        from app.models.sales_ledger import SalesRecordUpdate

        db_session.add(_project(1, firm_id=10))
        rec = SalesRecord(
            project_id=1,
            contract_no="C-001",
            customer_name="客户A",
            product_code="P-001",
            product_name="产品A",
            revenue_amount=10000.0,
        )
        db_session.add(rec)
        await db_session.commit()
        await db_session.refresh(rec)

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        payload = SalesRecordUpdate(customer_name="越权")
        with pytest.raises(Exception) as ei:
            await update_sales_record(
                record_id=rec.id, payload=payload,
                db=db_session, current_user=user_other,
            )
        _assert_403(ei)

        await db_session.refresh(rec)
        assert rec.customer_name == "客户A"

    @pytest.mark.asyncio
    async def test_delete_sales_record_cross_firm_blocked(
        self, db_session: AsyncSession
    ):
        """delete_sales_record (漏洞 6.2, record_id) — 跨 firm 403, 数据保留."""
        from app.api.sales_ledger import delete_sales_record

        db_session.add(_project(1, firm_id=10))
        rec = SalesRecord(
            project_id=1,
            contract_no="C-002",
            customer_name="客户B",
            product_code="P-002",
            product_name="产品B",
            revenue_amount=20000.0,
        )
        db_session.add(rec)
        await db_session.commit()
        await db_session.refresh(rec)
        rec_id = rec.id

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        with pytest.raises(Exception) as ei:
            await delete_sales_record(
                record_id=rec_id, db=db_session, current_user=user_other
            )
        _assert_403(ei)

        from sqlalchemy import select
        existing = (
            await db_session.execute(
                select(SalesRecord).where(SalesRecord.id == rec_id)
            )
        ).scalar_one_or_none()
        assert existing is not None


# ============================================================
#  跨文件 sanity: admin 跨事务所豁免 + 老数据 firm_id=None 仍可见
# ============================================================


class TestTenantIsolationBackwardsCompat:
    """验证修复后 admin 豁免 + 老数据兼容 (软隔离) 仍正常工作."""

    @pytest.mark.asyncio
    async def test_admin_can_cross_firm_view(
        self, db_session: AsyncSession
    ):
        """admin 角色跨事务所访问应成功 (运维场景)."""
        from app.api.account_audit import get_effective_prefixes_api

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        admin = _user(99, role=ROLE_ADMIN, firm_id=1)  # 不同 firm, 但 admin
        result = await get_effective_prefixes_api(
            project_id=1, current_user=admin, db=db_session
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_legacy_project_no_firm_visible(
        self, db_session: AsyncSession
    ):
        """老数据 firm_id=None — 任何 firm 都可访问 (向后兼容)."""
        from app.api.account_audit import get_effective_prefixes_api

        db_session.add(_project(1, firm_id=None))  # 老数据
        await db_session.commit()

        user_other = _user(1, role=ROLE_ASSISTANT, firm_id=99)
        result = await get_effective_prefixes_api(
            project_id=1, current_user=user_other, db=db_session
        )
        assert result is not None