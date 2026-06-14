"""Pack D — IPO 专属单测."""
from __future__ import annotations

import pytest

from app.services.ipo_specials import (
    DEFAULT_SUBMISSION_CHECKLIST,
    FeedbackSLAMonitor,
    OverlapDetector,
    PeerBenchmarkAnalyzer,
    PeriodAnomalyDetector,
    RevenueCutoffTester,
    WalkthroughSampler,
)
from app.models.db.ipo_specials import PeriodComparisonReport


class TestWalkthroughSampler:
    def test_to_mermaid_empty(self):
        m = WalkthroughSampler.to_mermaid_flowchart([])
        assert "graph TD" in m

    def test_to_mermaid_basic(self):
        steps = [
            {"step_description": "客户下单"},
            {"step_description": "审核信用"},
            {"step_description": "发货"},
        ]
        m = WalkthroughSampler.to_mermaid_flowchart(steps)
        assert "S1" in m and "S2" in m and "S3" in m
        assert "客户下单" in m
        assert "S1 --> S2" in m

    def test_select_samples_basic(self):
        items = [{"amount": x} for x in [100, 200, 50, 300, 150, 80]]
        samples = WalkthroughSampler.select_samples(items, "procurement", n=2)
        assert len(samples) <= 4
        # 应该包含 top 金额
        amounts = [s["amount"] for s in samples]
        assert 300 in amounts

    def test_select_samples_empty(self):
        assert WalkthroughSampler.select_samples([], "sales") == []


class TestRevenueCutoffTester:
    def test_normal_no_dates(self):
        j, d = RevenueCutoffTester.judge(None, None, "2024-12-31")
        assert j == "normal"
        assert d == 0

    def test_early_recognition(self):
        # 发货 2025-01-02 (期末后), 收入 2024-12-30 (期末前) → early
        j, d = RevenueCutoffTester.judge("2025-01-02", "2024-12-30", "2024-12-31")
        assert j == "early"
        assert d == 2

    def test_late_recognition(self):
        # 发货 2024-12-28 (期末前), 收入 2025-01-03 (期末后) → late
        j, d = RevenueCutoffTester.judge("2024-12-28", "2025-01-03", "2024-12-31")
        assert j == "late"
        assert d == 3

    def test_normal_both_before(self):
        j, _ = RevenueCutoffTester.judge("2024-12-20", "2024-12-25", "2024-12-31")
        assert j == "normal"

    def test_out_of_cutoff_window(self):
        # 发货 2024-12-28, 收入 2025-02-15 (期末后 > 5 天) → normal (超出 cutoff window)
        j, _ = RevenueCutoffTester.judge("2024-12-28", "2025-02-15", "2024-12-31", cutoff_days=5)
        assert j == "normal"


class TestPeriodAnomalyDetector:
    def test_gross_margin_swing(self):
        r = PeriodComparisonReport(
            project_id=1, report_type="ratios",
            metric_code="gross_margin", metric_name="毛利率",
            yoy_change_pct=5.0,
        )
        assert PeriodAnomalyDetector.detect_anomaly(r) == "gross_margin_swing_over_3pct"

    def test_turnover_swing(self):
        r = PeriodComparisonReport(
            project_id=1, report_type="ratios",
            metric_code="ar_turnover_days", metric_name="应收周转",
            yoy_change_pct=45.0,
        )
        assert PeriodAnomalyDetector.detect_anomaly(r) == "turnover_swing_over_30pct"

    def test_revenue_negative(self):
        r = PeriodComparisonReport(
            project_id=1, report_type="income_statement",
            metric_code="revenue", metric_name="营业收入",
            value_period_3=-1000, yoy_change_pct=-150,
        )
        anomaly = PeriodAnomalyDetector.detect_anomaly(r)
        # 应该被检出 (revenue_turned_negative 或 yoy_change_over_50pct)
        assert anomaly is not None

    def test_no_anomaly(self):
        r = PeriodComparisonReport(
            project_id=1, report_type="ratios",
            metric_code="gross_margin", metric_name="毛利率",
            yoy_change_pct=1.5,
        )
        assert PeriodAnomalyDetector.detect_anomaly(r) is None


class TestOverlapDetector:
    def test_exact_match(self):
        assert OverlapDetector.fuzzy_score("北京 ABC", "北京 ABC") == 1.0

    def test_substring(self):
        score = OverlapDetector.fuzzy_score("北京ABC", "北京ABC公司")
        assert score >= 0.8

    def test_no_match(self):
        score = OverlapDetector.fuzzy_score("ABC", "XYZ")
        assert score < 0.5

    def test_empty(self):
        assert OverlapDetector.fuzzy_score("", "X") == 0.0
        assert OverlapDetector.fuzzy_score("X", "") == 0.0


class TestPeerBenchmarkAnalyzer:
    def test_basic(self):
        r = PeerBenchmarkAnalyzer.issuer_vs_peers(
            issuer_value=20.0,
            peer_values=[18, 22, 25, 19, 21],
        )
        assert 20 <= r["peer_avg"] <= 22
        assert r["is_outlier"] is False

    def test_outlier(self):
        r = PeerBenchmarkAnalyzer.issuer_vs_peers(
            issuer_value=50.0,
            peer_values=[10, 12, 15],
        )
        assert r["is_outlier"] is True
        assert r["deviation_pct"] > 100

    def test_empty_peers(self):
        r = PeerBenchmarkAnalyzer.issuer_vs_peers(20.0, [])
        assert r["peer_avg"] == 0
        assert r["deviation_pct"] == 0

    def test_median_even(self):
        r = PeerBenchmarkAnalyzer.issuer_vs_peers(20, [10, 20, 30, 40])
        assert r["peer_median"] == 25.0


class TestFeedbackSLAMonitor:
    def test_days_to_deadline(self):
        # 假设今天是 2026-06-13
        d = FeedbackSLAMonitor.days_to_deadline("2026-06-20", today="2026-06-13")
        assert d == 7

    def test_overdue(self):
        d = FeedbackSLAMonitor.days_to_deadline("2026-06-01", today="2026-06-13")
        assert d == -12

    def test_urgency_levels(self):
        assert FeedbackSLAMonitor.urgency_level(-5) == "overdue"
        assert FeedbackSLAMonitor.urgency_level(0) == "critical"
        assert FeedbackSLAMonitor.urgency_level(3) == "critical"
        assert FeedbackSLAMonitor.urgency_level(5) == "warn"
        assert FeedbackSLAMonitor.urgency_level(15) == "normal"


class TestDefaultChecklist:
    def test_checklist_not_empty(self):
        assert len(DEFAULT_SUBMISSION_CHECKLIST) >= 20

    def test_checklist_structure(self):
        for code, name, required in DEFAULT_SUBMISSION_CHECKLIST:
            assert isinstance(code, str) and len(code) > 0
            assert isinstance(name, str) and len(name) > 0
            assert isinstance(required, bool)

    def test_prospectus_required(self):
        codes = [c[0] for c in DEFAULT_SUBMISSION_CHECKLIST]
        assert "PROSPECTUS" in codes
        assert "FIN_REPORT" in codes
        assert "RELATED_PARTY_DISCLOSURE" in codes
        assert "PEER_COMPETITION_COMMITMENT" in codes
