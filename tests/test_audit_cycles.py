"""Pack C — 10 个审计循环单测."""
from __future__ import annotations

import pytest

from app.services.audit_cycles import (
    DepreciationCalculator,
    ECLCalculator,
    ExpensesAnomalyDetector,
    GoingConcernAssessor,
    GoodwillImpairmentCalculator,
    IncomeTaxRecalculator,
    LeaseAmortizer,
    PayablesService,
    RDCapitalizationAssessor,
    SubsequentEventClassifier,
)


class TestPayablesService:
    def test_aging_bucket_for_days(self):
        assert PayablesService.aging_bucket_for_days(15) == "0_30"
        assert PayablesService.aging_bucket_for_days(60) == "31_90"
        assert PayablesService.aging_bucket_for_days(120) == "91_180"
        assert PayablesService.aging_bucket_for_days(300) == "181_365"
        assert PayablesService.aging_bucket_for_days(800) == "over_365"


class TestExpensesAnomalyDetector:
    def test_round_number(self):
        assert ExpensesAnomalyDetector.is_round_number(100000) is True
        assert ExpensesAnomalyDetector.is_round_number(120000) is True
        assert ExpensesAnomalyDetector.is_round_number(123456) is False
        assert ExpensesAnomalyDetector.is_round_number(50000) is False  # 小于阈值

    def test_holiday(self):
        # 2026/06/13 是周六
        assert ExpensesAnomalyDetector.is_holiday("2026-06-13") is True
        # 2026/06/15 是周一
        assert ExpensesAnomalyDetector.is_holiday("2026-06-15") is False
        assert ExpensesAnomalyDetector.is_holiday("invalid") is False

    def test_entertainment_60pct(self):
        # 营收 100w, 招待 10w → 60% = 6w, 1‰ = 1k, 取小 = 1k
        r = ExpensesAnomalyDetector.entertainment_deduction_limit(1_000_000, 100_000)
        assert r["deductible"] == 5_000.0  # min(60000, 1000000*0.005=5000)
        assert r["non_deductible_adjustment"] == 95_000.0

    def test_entertainment_1per_mille_lower(self):
        # 营收 1 亿, 招待 1 万 → 60% = 6000, 1‰ = 5w → min = 6000
        r = ExpensesAnomalyDetector.entertainment_deduction_limit(100_000_000, 10_000)
        assert r["deductible"] == 6_000.0


class TestDepreciationCalculator:
    def test_straight_line_basic(self):
        # 原值 12w, 残值率 5%, 10 年 = 120 月
        # (120000 - 6000) / 120 = 950
        assert DepreciationCalculator.straight_line_monthly(120_000, 0.05, 120) == 950.0

    def test_straight_line_zero_life(self):
        assert DepreciationCalculator.straight_line_monthly(120_000, 0.05, 0) == 0.0

    def test_double_declining(self):
        # NBV 100k, 10 年 → annual rate = 2/10 = 0.2, monthly = 100k * 0.2 / 12
        r = DepreciationCalculator.double_declining_monthly(100_000, 120)
        assert r == round(100_000 * 0.2 / 12, 2)

    def test_sum_of_years_first(self):
        # 原值 100, 残 0, 5 年, 第 1 年 = 100 * 5 / 15 ≈ 33.33
        r = DepreciationCalculator.sum_of_years_yearly(100, 0, 5, 1)
        assert 33.0 <= r <= 34.0

    def test_sum_of_years_last(self):
        r = DepreciationCalculator.sum_of_years_yearly(100, 0, 5, 5)
        assert 6.0 <= r <= 7.0


class TestRDCapitalizationAssessor:
    def test_all_met(self):
        ok, missing = RDCapitalizationAssessor.assess(True, True, True, True, True, True)
        assert ok is True
        assert missing == []

    def test_missing_one(self):
        ok, missing = RDCapitalizationAssessor.assess(True, True, True, True, True, False)
        assert ok is False
        assert "成本可计量" in missing

    def test_super_deduction_manufacturing(self):
        r = RDCapitalizationAssessor.rd_super_deduction(100_000, manufacturing=True)
        assert r["super_deduction"] == 100_000.0
        assert r["total_deductible"] == 200_000.0

    def test_super_deduction_other(self):
        r = RDCapitalizationAssessor.rd_super_deduction(100_000, manufacturing=False)
        assert r["super_deduction"] == 75_000.0


class TestGoodwillImpairmentCalculator:
    def test_npv_basic(self):
        # 1 期 100, 折现率 10% → 100/1.1 ≈ 90.91
        r = GoodwillImpairmentCalculator.npv([100], 0.10)
        assert 90.0 <= r <= 91.0

    def test_npv_5_years(self):
        # 5 期 100, 10% → 应该 ~379.08
        r = GoodwillImpairmentCalculator.npv([100, 100, 100, 100, 100], 0.10)
        assert 378.0 <= r <= 380.0

    def test_impairment_required(self):
        assert GoodwillImpairmentCalculator.impairment_required(1000, 800) == 200.0
        # 可回收 > 账面 → 不减值
        assert GoodwillImpairmentCalculator.impairment_required(800, 1000) == 0.0


class TestLeaseAmortizer:
    def test_present_value_basic(self):
        # 月付 1000, 12 期, 月利率 0
        assert LeaseAmortizer.present_value(1000, 12, 0) == 12000.0

    def test_present_value_with_rate(self):
        # 月付 1000, 12 期, 月利率 0.5% → PV 约 11618
        r = LeaseAmortizer.present_value(1000, 12, 0.005)
        assert 11500 <= r <= 11700


class TestIncomeTaxRecalculator:
    def test_normal(self):
        # 税前 100w + 永久差 5w + 暂时差 2w - 弥补亏损 1w = 应税 106w
        # 25% → 26.5w
        r = IncomeTaxRecalculator.reconcile(
            pretax_profit=1_000_000,
            permanent_diff=50_000,
            temporary_diff=20_000,
            losses_used=10_000,
            nominal_rate=0.25,
        )
        assert r["taxable_income"] == 1_060_000.0
        assert r["current_tax"] == 265_000.0
        assert 0.26 <= r["effective_rate"] <= 0.27

    def test_pretax_zero(self):
        r = IncomeTaxRecalculator.reconcile(0, 0, 0, 0)
        assert r["effective_rate"] == 0
        assert r["current_tax"] == 0.0


class TestECLCalculator:
    def test_stage_for_aging(self):
        assert ECLCalculator.stage_for_aging_days(0) == 1
        assert ECLCalculator.stage_for_aging_days(15) == 1
        assert ECLCalculator.stage_for_aging_days(30) == 2
        assert ECLCalculator.stage_for_aging_days(60) == 2
        assert ECLCalculator.stage_for_aging_days(90) == 3
        assert ECLCalculator.stage_for_aging_days(365) == 3

    def test_default_pd(self):
        assert ECLCalculator.default_pd_for_stage(1) == 0.01
        assert ECLCalculator.default_pd_for_stage(2) == 0.10
        assert ECLCalculator.default_pd_for_stage(3) == 0.50

    def test_compute_ecl(self):
        # 应收 10w, stage 3, pd 0.5, lgd 0.45 → 22500
        r = ECLCalculator.compute_ecl(100_000, 3)
        assert r == 22500.0

    def test_compute_ecl_with_custom_pd(self):
        r = ECLCalculator.compute_ecl(100_000, 1, pd=0.05, lgd=0.50)
        assert r == 2500.0


class TestSubsequentEventClassifier:
    def test_adjusting_keywords(self):
        assert SubsequentEventClassifier.classify(
            "应收账款无法收回", "2025-01-15", "2024-12-31"
        ) == "adjusting"
        assert SubsequentEventClassifier.classify(
            "诉讼判决金额确定", "2025-02-01", "2024-12-31"
        ) == "adjusting"

    def test_non_adjusting(self):
        assert SubsequentEventClassifier.classify(
            "购买新公司股权", "2025-03-01", "2024-12-31"
        ) == "non_adjusting"

    def test_empty(self):
        assert SubsequentEventClassifier.classify("", "2025-01-01", "2024-12-31") == "non_adjusting"


class TestGoingConcernAssessor:
    def test_low_risk(self):
        # 偿债压力 = 100, 资源 = 500 → 5x 覆盖
        level, note = GoingConcernAssessor.assess(
            operating_cashflow_12m=400,
            interest_expense_12m=50,
            debt_due_12m=50,
            cash_balance=100,
        )
        assert level == "low"

    def test_substantial_doubt(self):
        level, note = GoingConcernAssessor.assess(
            operating_cashflow_12m=10,
            interest_expense_12m=100,
            debt_due_12m=100,
            cash_balance=10,
        )
        assert level == "substantial_doubt"

    def test_no_obligation(self):
        level, note = GoingConcernAssessor.assess(
            operating_cashflow_12m=100,
            interest_expense_12m=0,
            debt_due_12m=0,
            cash_balance=10,
        )
        assert level == "low"
        assert "无重大偿债压力" in note
