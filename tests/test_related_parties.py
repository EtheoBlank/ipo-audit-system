"""Pack B — Related Parties 单元测试."""
from __future__ import annotations

import pytest

from app.models.db.related_parties import (
    ALL_RP_TYPES,
    DISCLOSURE_GAP_CRITICAL,
    DISCLOSURE_GAP_OK,
    DISCLOSURE_GAP_REVIEW,
    RP_SOURCE_CHRONO_SCAN,
    RP_SOURCE_MANUAL,
    RP_TYPE_CONTROLLING_SHAREHOLDER,
    RP_TYPE_DIRECTOR_OR_SENIOR,
    RP_TYPE_OTHER,
)
from app.models.related_parties import (
    CapitalOccupationCreate,
    DetectorRunRequest,
    DisclosureCheckRequest,
    FairnessCheckRequest,
    PeerCompetitionAssessRequest,
    RelatedPartyCreate,
    TransactionCreate,
)
from app.services.related_parties import (
    PeerCompetitionService,
    _normalize_name,
)


class TestNormalizeName:
    def test_strip_suffix_有限公司(self):
        assert _normalize_name("北京某某有限公司") == "北京某某"

    def test_strip_suffix_股份(self):
        assert _normalize_name("上海某某股份有限公司") == "上海某某"

    def test_strip_whitespace(self):
        assert _normalize_name(" 北京 某某 公司 ") == "北京某某"

    def test_strip_parens(self):
        assert _normalize_name("某某 (上海) 有限公司") == "某某"

    def test_empty(self):
        assert _normalize_name("") == ""
        assert _normalize_name(None) == ""  # type: ignore[arg-type]


class TestPeerCompetition:
    def test_overlap_score_full_match(self):
        score = PeerCompetitionService.overlap_score(
            ["芯片", "集成电路", "EDA"],
            "本公司主营芯片设计与集成电路销售, 涉及 EDA 工具开发",
        )
        assert score == 100.0

    def test_overlap_score_partial(self):
        score = PeerCompetitionService.overlap_score(
            ["芯片", "集成电路", "EDA"],
            "本公司主营芯片销售",
        )
        # 1 / 3 ≈ 33.33
        assert 33.0 <= score <= 34.0

    def test_overlap_score_no_match(self):
        assert PeerCompetitionService.overlap_score(
            ["芯片"], "本公司主营服装贸易"
        ) == 0.0

    def test_overlap_score_empty(self):
        assert PeerCompetitionService.overlap_score([], "anything") == 0.0
        assert PeerCompetitionService.overlap_score(["x"], None) == 0.0
        assert PeerCompetitionService.overlap_score(["x"], "") == 0.0

    def test_risk_level_thresholds(self):
        assert PeerCompetitionService.risk_level_for_score(95) == "critical"
        assert PeerCompetitionService.risk_level_for_score(70) == "critical"
        assert PeerCompetitionService.risk_level_for_score(50) == "high"
        assert PeerCompetitionService.risk_level_for_score(40) == "high"
        assert PeerCompetitionService.risk_level_for_score(25) == "medium"
        assert PeerCompetitionService.risk_level_for_score(15) == "medium"
        assert PeerCompetitionService.risk_level_for_score(5) == "low"
        assert PeerCompetitionService.risk_level_for_score(0) == "low"


class TestSchemas:
    def test_party_type_validation(self):
        with pytest.raises(Exception):
            RelatedPartyCreate(name="x", party_type="bogus")

    def test_party_type_known(self):
        c = RelatedPartyCreate(name="x", party_type=RP_TYPE_OTHER)
        assert c.party_type == RP_TYPE_OTHER

    def test_party_kind_pattern(self):
        c = RelatedPartyCreate(name="张三", party_kind="person", party_type=RP_TYPE_DIRECTOR_OR_SENIOR)
        assert c.party_kind == "person"
        with pytest.raises(Exception):
            RelatedPartyCreate(name="x", party_kind="alien", party_type=RP_TYPE_OTHER)

    def test_holding_pct_range(self):
        with pytest.raises(Exception):
            RelatedPartyCreate(name="x", party_type=RP_TYPE_OTHER, holding_pct=150)
        with pytest.raises(Exception):
            RelatedPartyCreate(name="x", party_type=RP_TYPE_OTHER, holding_pct=-1)

    def test_capital_occupation_cleanup_status(self):
        c = CapitalOccupationCreate(
            party_id=1,
            period_start="2024-01-01",
            period_end="2024-12-31",
            cleanup_status="cleared",
        )
        assert c.cleanup_status == "cleared"
        with pytest.raises(Exception):
            CapitalOccupationCreate(
                party_id=1, period_start="2024-01-01", period_end="2024-12-31",
                cleanup_status="bogus",
            )

    def test_transaction_create(self):
        t = TransactionCreate(party_id=1, transaction_type="sales", amount=10000.0)
        assert t.party_id == 1
        assert t.amount == 10000.0
        assert t.currency == "CNY"


class TestConstants:
    def test_all_party_types(self):
        assert RP_TYPE_CONTROLLING_SHAREHOLDER in ALL_RP_TYPES
        assert RP_TYPE_DIRECTOR_OR_SENIOR in ALL_RP_TYPES
        assert len(ALL_RP_TYPES) == 10

    def test_disclosure_gap_statuses(self):
        assert DISCLOSURE_GAP_CRITICAL == "critical"
        assert DISCLOSURE_GAP_REVIEW == "review"
        assert DISCLOSURE_GAP_OK == "ok"

    def test_sources(self):
        assert RP_SOURCE_CHRONO_SCAN == "chronological_scan"
        assert RP_SOURCE_MANUAL == "manual"
