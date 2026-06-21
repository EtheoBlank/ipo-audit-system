"""P1 修复 (2026-06-19): RelatedParty Pydantic schemas 校验 period_end 必须是 YYYY-MM-DD 格式.

修复前端用 st.text_input 收字符串时, 用户填"2024.12.31"被落库再后端做 == 比较 0 命中.
现在后端 validator 在 schema 阶段拒绝非 ISO 格式, 强迫前端传 st.date_input.isoformat().
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.related_parties import (
    CapitalOccupationCreate,
    FairnessCheckRequest,
    RelatedPartyReportRequest,
    TransactionCreate,
)


class TestTransactionCreateDate:
    def test_period_end_validator_rejects_invalid(self):
        """'2024.12.31' 应当被拒绝."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreate(party_id=1, transaction_type="sales", period_end="2024.12.31")
        errors = exc_info.value.errors()
        assert any("period_end" in str(e["loc"]) for e in errors)

    def test_period_end_validator_accepts_iso(self):
        """'2024-12-31' 应当通过."""
        t = TransactionCreate(
            party_id=1, transaction_type="sales", period_end="2024-12-31", amount=1000
        )
        assert t.period_end == "2024-12-31"

    def test_period_start_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            TransactionCreate(party_id=1, transaction_type="sales", period_start="2024年1月")

    def test_period_end_optional_none(self):
        """period_end 可选为 None."""
        t = TransactionCreate(party_id=1, transaction_type="sales")
        assert t.period_end is None


class TestFairnessCheckRequestDate:
    def test_period_end_validator_rejects_invalid(self):
        with pytest.raises(ValidationError) as exc_info:
            FairnessCheckRequest(period_end="2024.12.31")
        errors = exc_info.value.errors()
        assert any("period_end" in str(e["loc"]) for e in errors)

    def test_period_end_validator_accepts_iso(self):
        f = FairnessCheckRequest(period_end="2024-12-31")
        assert f.period_end == "2024-12-31"


class TestCapitalOccupationCreateDate:
    def test_period_end_validator_rejects_invalid(self):
        with pytest.raises(ValidationError) as exc_info:
            CapitalOccupationCreate(
                party_id=1, period_start="2024-01-01", period_end="2024/12/31"
            )
        errors = exc_info.value.errors()
        assert any("period_end" in str(e["loc"]) for e in errors)

    def test_period_end_validator_accepts_iso(self):
        c = CapitalOccupationCreate(
            party_id=1, period_start="2024-01-01", period_end="2024-12-31"
        )
        assert c.period_end == "2024-12-31"
        assert c.period_start == "2024-01-01"


class TestRelatedPartyReportRequestDate:
    def test_period_end_validator_rejects_invalid(self):
        with pytest.raises(ValidationError):
            RelatedPartyReportRequest(project_id=1, period_end="2024.12.31")

    def test_period_end_validator_accepts_iso(self):
        r = RelatedPartyReportRequest(project_id=1, period_end="2024-12-31")
        assert r.period_end == "2024-12-31"
