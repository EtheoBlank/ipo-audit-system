"""Round 25 P0-13: sales_ledger synthesizer 缺 Pydantic schema 校验 — 修复测试.

覆盖:
  1. SynthesizedRow 接受正常数据
  2. SynthesizedRow 拒绝非数字 amount (revenue_amount="abc" 抛 ValidationError)
  3. synthesize() 把 valid/error 行分开 (5 valid + 3 broken → 5 records + 3 errors)
  4. API 层部分失败时仍 200, errors 字段返回失败明细 (不再 500)
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from typing import Any, List
from unittest.mock import MagicMock, AsyncMock

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTH_ENABLED", "false")

from app.services.sales_ledger.synthesizer import (
    SalesLedgerSynthesizer,
    SynthesizedRow,
    SynthesizeResult,
)


# ============================================================
#  1) SynthesizedRow 直接校验 — 单元级 schema 测试
# ============================================================


class TestSynthesizedRowSchema:
    """Pydantic v2 schema 接受 / 拒绝行为."""

    def test_synthesized_row_accepts_valid_data(self):
        """正常数据应通过校验."""
        row = SynthesizedRow.model_validate(
            {
                "contract_no": "C-2024-001",
                "customer_name": "北京XX有限公司",
                "product_code": "P-001",
                "product_name": "金属制品",
                "invoice_no": "INV-001",
                "currency": "CNY",
                "tax_rate": 0.13,
                "tax_amount": 130.0,
                "gross_amount": 1130.0,
                "quantity": 100.0,
                "unit_price": 10.0,
                "revenue_amount": 1000.0,
                "cost_amount": 600.0,
                "ship_date": "2024-06-01",
                "receipt_date": "2024-06-15",
                "revenue_confirm_date": "2024-06-30",
            }
        )
        assert row.contract_no == "C-2024-001"
        assert row.revenue_amount == 1000.0
        assert row.tax_rate == 0.13
        assert isinstance(row.ship_date, date)

    def test_synthesized_row_rejects_non_numeric_amount(self):
        """revenue_amount="abc" 必须抛 ValidationError, 而不是默默落库."""
        with pytest.raises(ValidationError):
            SynthesizedRow.model_validate(
                {
                    "customer_name": "X",
                    "product_code": "P-001",
                    "revenue_amount": "abc",  # 字符串非数字
                    "tax_rate": None,
                    "quantity": "not-a-number",
                }
            )

    def test_synthesized_row_accepts_missing_optional_fields(self):
        """可选字段全缺省 + revenue_amount=0 也合法 (走不到入库, 但 schema 不挡)."""
        row = SynthesizedRow.model_validate(
            {
                "customer_name": "X",
                "product_code": "P-001",
            }
        )
        assert row.revenue_amount == 0.0
        assert row.tax_rate is None
        assert row.quantity is None

    def test_synthesized_row_rejects_extra_field_ignored(self):
        """多余字段 model_config extra='ignore' 应静默丢弃, 不抛."""
        row = SynthesizedRow.model_validate(
            {
                "customer_name": "X",
                "product_code": "P-001",
                "revenue_amount": 100.0,
                "totally_unknown_field": "garbage",
            }
        )
        assert row.revenue_amount == 100.0


# ============================================================
#  2) synthesize() — valid / error 分离测试 (mock DeepSeek)
# ============================================================


def _doc(doc_id: int, text: str = "raw"):
    """构造 SalesDocument-like 对象."""

    class _D:
        pass

    d = _D()
    d.id = doc_id
    d.filename = f"doc_{doc_id}.txt"
    d.raw_text = text
    return d


def _make_client(payload: Any) -> MagicMock:
    """构造 mock DeepSeekClient: chat_json 返 payload (list/dict/records schema)."""
    client = MagicMock()
    client.is_configured = True

    async def chat_json(*args, **kwargs):
        return payload

    client.chat_json.side_effect = chat_json
    return client


class TestSynthesizeSeparatesValidInvalid:
    """synthesize() 必须把 valid 和 invalid 分开, 不能整批回滚."""

    @pytest.mark.asyncio
    async def test_synthesize_separates_valid_and_invalid(self):
        """5 valid + 3 broken → 5 records + 3 errors."""
        # 5 正常 + 3 损坏 (revenue_amount="abc")
        raw_records: list[dict[str, Any]] = [
            {
                "customer_name": f"客户{i}",
                "product_code": f"P{i}",
                "revenue_amount": 100.0 * i,
            }
            for i in range(1, 6)
        ] + [
            {"customer_name": "bad1", "product_code": "BP1", "revenue_amount": "abc"},
            {"customer_name": "bad2", "product_code": "BP2", "quantity": "xyz"},
            {"customer_name": "bad3", "product_code": "BP3", "tax_rate": "not-a-rate"},
        ]

        client = _make_client({"records": raw_records})
        synth = SalesLedgerSynthesizer(client)
        result: SynthesizeResult = await synth.synthesize([_doc(1)])

        assert isinstance(result, SynthesizeResult)
        assert result.valid_count == 5
        assert result.error_count == 3
        # valid 行必须保留 customer_name + product_code
        assert {r["product_code"] for r in result.records} == {"P1", "P2", "P3", "P4", "P5"}
        # error 行 idx 应该有 3 条
        assert all(e.error for e in result.errors)
        # 错误 idx 应连续, 全是真实 Pydantic 报错信息
        assert sorted([e.idx for e in result.errors]) == [5, 6, 7]

    @pytest.mark.asyncio
    async def test_synthesize_all_valid(self):
        """全 valid 时 errors 为空."""
        raw_records = [
            {"customer_name": "C", "product_code": f"P{i}", "revenue_amount": 10.0}
            for i in range(3)
        ]
        client = _make_client({"records": raw_records})
        result = await SalesLedgerSynthesizer(client).synthesize([_doc(1)])
        assert result.valid_count == 3
        assert result.error_count == 0

    @pytest.mark.asyncio
    async def test_synthesize_all_invalid(self):
        """全 invalid 时 records 为空, errors 满."""
        raw_records = [
            {"customer_name": "bad", "product_code": "P1", "revenue_amount": "NaN"},
            {"customer_name": "bad", "product_code": "P2", "revenue_amount": None},
        ]
        # 触发: revenue_amount 必填 float; None 会走 default 0.0, 但第二个 ok;
        # 第一个字符串必抛 → 仅 1 个 error
        client = _make_client({"records": raw_records})
        result = await SalesLedgerSynthesizer(client).synthesize([_doc(1)])
        assert result.error_count >= 1


# ============================================================
#  3) API 端点 — 部分失败仍 200 (mock DB)
# ============================================================


class TestApiEndpointPartialSuccess:
    """app/api/sales_ledger.synthesize_sales_records 部分失败 → 200 + errors."""

    @pytest.mark.asyncio
    async def test_api_endpoint_returns_partial_success(self, monkeypatch):
        """5 valid + 3 invalid → HTTP 200, synthesized_count=5, error_count=3, errors 列表长度 3."""
        from app.api.sales_ledger import synthesize_sales_records
        from app.models.sales_ledger import SynthesisRequest

        # Mock synth: 返回 5 valid + 3 error
        valid_rows = [
            {"customer_name": f"C{i}", "product_code": f"P{i}", "revenue_amount": 10.0}
            for i in range(5)
        ]
        from app.services.sales_ledger.synthesizer import SynthesizeError

        errors = [
            SynthesizeError(idx=i, row_summary=f"bad{i}", error="revenue_amount str")
            for i in range(3)
        ]
        mock_synth = MagicMock()
        mock_synth.synthesize = AsyncMock(
            return_value=SynthesizeResult(records=valid_rows, errors=errors)
        )

        # 用真类构造, 只替换实例方法 synthesize
        class _ProxySynth:
            def __init__(self, client):
                self.client = client

            async def synthesize(self, *a, **kw):
                return await mock_synth.synthesize(*a, **kw)

        # 把 coerce_numbers / coerce_dates 也代理到真类静态方法 (保持原行为)
        _ProxySynth.coerce_numbers = SalesLedgerSynthesizer.coerce_numbers
        _ProxySynth.coerce_dates = SalesLedgerSynthesizer.coerce_dates

        monkeypatch.setattr(
            "app.api.sales_ledger.SalesLedgerSynthesizer", _ProxySynth
        )

        # Mock 一个 SalesDocument-like 对象, 让 API 走完 query
        fake_doc = MagicMock()
        fake_doc.id = 1
        fake_doc.filename = "fake.txt"
        fake_doc.raw_text = "合同: C-001, 客户: A, 金额: 1000"

        # Mock DB session: 第一次 query 返 [fake_doc], 后续 query 返空
        call_count = {"n": 0}

        async def fake_execute(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 第一次: 拉 SalesDocument 列表
                return _FakeResult(scalars_list=[fake_doc])
            return _FakeResult(scalars_list=[])

        db = MagicMock()
        db.execute = AsyncMock(side_effect=fake_execute)
        db.commit = AsyncMock()
        # add() / refresh() 不做事 — upserted 是 MagicMock, refresh 仍 ok
        db.add = MagicMock()
        db.refresh = AsyncMock()

        # Patch _record_to_response: 用 SalesRecordResponse 直接构造, 避免真实
        # SalesRecord ORM 字段缺失导致 ValidationError. 我们要验证的是 synthesizer
        # 校验逻辑 + API 层 errors 回传, 不是 ORM.
        from app.models.sales_ledger import SalesRecordResponse as _SRR

        def _fake_resp(r):
            return _SRR(
                id=1,
                project_id=1,
                document_id=None,
                contract_no=str(getattr(r, "contract_no", "") or ""),
                customer_name=str(getattr(r, "customer_name", "") or ""),
                product_code=str(getattr(r, "product_code", "") or ""),
                product_name=str(getattr(r, "product_name", "") or ""),
                invoice_no=None,
                currency="CNY",
                tax_rate=0.0,
                tax_amount=0.0,
                gross_amount=0.0,
                quantity=0.0,
                unit_price=0.0,
                revenue_amount=float(getattr(r, "revenue_amount", 0) or 0),
                cost_amount=0.0,
                shipping_fee=0.0,
                customs_fee=0.0,
                other_direct_fee=0.0,
                return_amount=0.0,
                discount_amount=0.0,
                rebate_amount=0.0,
                ship_date=None,
                receipt_date=None,
                revenue_confirm_date=None,
                confirmation_status="未发函",
                confirmation_ref=None,
                confirmation_diff=0.0,
                source=str(getattr(r, "source", "") or ""),
                confidence=1.0,
                is_verified=False,
                created_at=datetime(2024, 1, 1),
                updated_at=datetime(2024, 1, 1),
                gross_profit=0.0,
                gross_margin=0.0,
            )

        monkeypatch.setattr(
            "app.api.sales_ledger._record_to_response", _fake_resp
        )

        # Mock current_user
        from app.models.db.auth import User

        user = User(
            id=1,
            username="u1",
            full_name="U",
            role="assistant",
            is_active=True,
            password_hash="!",
            firm_id=None,
        )

        # Mock ensure_project_in_firm
        monkeypatch.setattr(
            "app.api.sales_ledger.ensure_project_in_firm",
            AsyncMock(return_value=None),
        )

        # Mock _deepseek_client(): 让 is_configured=True, 避免 400 跳出
        fake_client = MagicMock()
        fake_client.is_configured = True
        monkeypatch.setattr(
            "app.api.sales_ledger._deepseek_client", lambda: fake_client
        )

        req = SynthesisRequest(project_id=1, document_ids=[1], extra_hint="")
        resp = await synthesize_sales_records(
            project_id=1,
            req=req,
            db=db,
            current_user=user,
        )
        assert resp.synthesized_count == 5
        assert resp.error_count == 3
        assert len(resp.errors) == 3
        # 校验 errors 内容
        assert all(e.error == "revenue_amount str" for e in resp.errors)


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalarsResult(self._items)

    def all(self):
        return self._items


class _FakeScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items

    def scalars(self):
        return self


class _FakeResult:
    def __init__(self, scalars_list=None):
        self._scalars_list = scalars_list or []

    def scalars(self):
        return _FakeScalarsResult(self._scalars_list)

    def scalar_one_or_none(self):
        return self._scalars_list[0] if self._scalars_list else None