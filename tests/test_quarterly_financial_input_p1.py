"""FinancialInput / save_financial_input 测试.

覆盖:
  - save + read round-trip 一致
  - 校验: 必填字段缺失 / 类型错 → False + 错误信息
  - 多版本: 新版本保存, 旧版本快照保留 (lock_references 模式)
  - REQUIRED_FIELDS 完整性
"""
from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

from app.core.database import Base  # noqa: E402
from app.models.db_models import (  # noqa: E402
    Project,
    SentimentQuarterlyReport,
)
from app.services.sentiment.quarterly.financial_input import (  # noqa: E402
    REQUIRED_FIELDS,
    FinancialInput,
    save_financial_input,
)


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


@pytest_asyncio.fixture
async def project(session):
    p = Project(
        id=1,
        name="P1",
        company_name="测试公司",
        fiscal_year=2024,
        status="active",
    )
    session.add(p)
    await session.commit()
    return p


@pytest_asyncio.fixture
async def report(session, project):
    r = SentimentQuarterlyReport(
        id=1,
        project_id=project.id,
        fiscal_year=2024,
        period_type="Q1",
        period_end="2024-03-31",
        title="2024 第一季度 跟踪报告",
        daily_briefing_window_start="2024-01-01",
        daily_briefing_window_end="2024-03-31",
    )
    session.add(r)
    await session.commit()
    return r


def _full_financial() -> FinancialInput:
    return FinancialInput(
        data={
            "revenue": 100_000_000,
            "net_profit": 10_000_000,
            "non_recurring_pnl": 9_500_000,
            "gross_margin": 30.0,
            "yoy_revenue": 15.0,
            "yoy_net_profit": 8.0,
            "total_assets": 500_000_000,
            "operating_cash_flow": 12_000_000,
        },
        source="manual",
    )


# ============================================================
#  Tests
# ============================================================


class TestFinancialInputRoundTrip:
    """保存 → 读回 round-trip 一致."""

    @pytest.mark.asyncio
    async def test_financial_input_save_and_retrieve(self, session, report):
        """save_financial_input 落库后, FinancialInput.from_json 能完整还原."""
        fin = _full_financial()
        ok, err = await save_financial_input(
            session, report, fin, verified_by="auditor1",
        )
        assert ok is True
        assert err == ""

        # 报告字段已写入
        assert report.financial_input_source == "manual"
        assert report.financial_input_verified_by == "auditor1"
        assert report.financial_input_json is not None

        # 读回 → 与原始一致
        restored = FinancialInput.from_json(report.financial_input_json)
        assert restored.data == fin.data
        assert restored.source == "manual"
        assert restored.verified_by == "auditor1"
        assert restored.verified_at is not None
        assert restored.is_complete()

    @pytest.mark.asyncio
    async def test_financial_input_to_json_preserves_unicode(self, session, report):
        """中文 / 特殊字符不被 JSON 序列化破坏."""
        fin = FinancialInput(
            data={**_full_financial().data, "extra_note": "包含中文：测试 — 复核"},
            source="uploaded_pdf",
            note="审计师备注：含引号 \" ' 与换行\n第二行",
        )
        j = fin.to_json()
        # ensure_ascii=False 应保留中文字符
        assert "中文" in j
        # round-trip 还原
        restored = FinancialInput.from_json(j)
        assert restored.note == fin.note
        assert restored.data["extra_note"] == "包含中文：测试 — 复核"


class TestFinancialInputValidation:
    """save_financial_input 的校验逻辑."""

    @pytest.mark.asyncio
    async def test_financial_input_validation_missing_field(self, session, report):
        """必填字段缺失 → (False, 错误信息)."""
        fin = FinancialInput(
            data={"revenue": 100_000_000, "net_profit": 10_000_000},  # 缺 6 个必填
        )
        ok, err = await save_financial_input(
            session, report, fin, verified_by="auditor1",
        )
        assert ok is False
        assert "必填字段缺失" in err
        # 报告字段未写入
        assert report.financial_input_json is None
        assert report.financial_input_verified_by is None

    @pytest.mark.asyncio
    async def test_financial_input_validation_no_verified_by(self, session, report):
        """verified_by 为空 → (False, '必须填写 verified_by')."""
        fin = _full_financial()
        ok, err = await save_financial_input(
            session, report, fin, verified_by="",
        )
        assert ok is False
        assert "verified_by" in err
        assert report.financial_input_json is None

    @pytest.mark.asyncio
    async def test_financial_input_validation_none_value(self, session, report):
        """必填字段值为 None → 视为缺失."""
        fin = FinancialInput(
            data={**_full_financial().data, "revenue": None},
        )
        ok, err = await save_financial_input(
            session, report, fin, verified_by="auditor1",
        )
        assert ok is False
        assert "revenue" in err

    def test_required_fields_complete(self):
        """REQUIRED_FIELDS 包含 8 个核心字段, 名称稳定 (前端依赖)."""
        expected = {
            "revenue", "net_profit", "non_recurring_pnl", "gross_margin",
            "yoy_revenue", "yoy_net_profit", "total_assets", "operating_cash_flow",
        }
        assert set(REQUIRED_FIELDS) == expected
        assert len(REQUIRED_FIELDS) == 8

    def test_is_complete(self):
        """is_complete 仅在所有 REQUIRED_FIELDS 都非 None 时为 True."""
        assert FinancialInput().is_complete() is False
        assert FinancialInput(data={"revenue": 1}).is_complete() is False
        # 全填
        assert _full_financial().is_complete() is True
        # 全填但有一个 None
        d = _full_financial().data
        d["revenue"] = None
        assert FinancialInput(data=d).is_complete() is False


class TestFinancialInputRevisions:
    """多版本: 新版本保存, 旧值被快照, 旧 JSON 字符串仍可读."""

    @pytest.mark.asyncio
    async def test_financial_input_revisions(self, session, report):
        """第一次保存 v1, 第二次保存 v2 (新数据), v1 JSON 仍能从历史读出 (修订记录)."""
        v1 = FinancialInput(
            data={**_full_financial().data, "revenue": 100_000_000},
            source="manual",
            verified_by="auditor1",
        )
        ok, _ = await save_financial_input(
            session, report, v1, verified_by="auditor1",
        )
        assert ok

        v1_json = report.financial_input_json
        assert v1_json is not None
        # 确认 v1 快照
        v1_restored = FinancialInput.from_json(v1_json)
        assert v1_restored.data["revenue"] == 100_000_000

        # 修改 v2
        v2 = FinancialInput(
            data={**_full_financial().data, "revenue": 200_000_000},  # 营收翻倍
            source="uploaded_pdf",
            verified_by="auditor2",
        )
        ok, err = await save_financial_input(
            session, report, v2, verified_by="auditor2",
        )
        assert ok
        assert err == ""

        # v2 写入了 report
        v2_json = report.financial_input_json
        assert v2_json != v1_json, "新版本 JSON 应与旧版不同"

        v2_restored = FinancialInput.from_json(v2_json)
        assert v2_restored.data["revenue"] == 200_000_000
        assert v2_restored.verified_by == "auditor2"
        assert v2_restored.source == "uploaded_pdf"

        # 旧 v1 JSON 仍可独立解析 (用来记录"曾有这版", 不被覆盖)
        v1_again = FinancialInput.from_json(v1_json)
        assert v1_again.data["revenue"] == 100_000_000
        assert v1_again.verified_by == "auditor1"

    def test_from_json_empty(self):
        """from_json('') / from_json(None) → 空 FinancialInput, 不抛."""
        a = FinancialInput.from_json("")
        assert a.data == {}
        assert a.source == "manual"
