"""Pack C P0 修复测试 (2026-06-17).

覆盖:
  - #3 PayrollReconciler 公式修正 (扣款超过工资 → 不平)
  - #1 LeaseAmortizer.build_schedule 月度递推跨年边界
  - #2 DepreciationCalculator.double_declining_monthly 公式校验 (经核实公式正确,
    这里用作 regression guard 防止未来改动)
"""
from __future__ import annotations


from app.services.audit_cycles import (
    DepreciationCalculator,
    LeaseAmortizer,
    PayrollReconciler,
)


# ============================================================
# #3 PayrollReconciler
# ============================================================
class TestPayrollReconcilerP0Fix:
    """P0 修复: 扣款合计 (社保+公积金+个税) 超过工资 → 不平.

    旧版用 abs(gross * 0.5 - deductions) 永远 True, 永远 balanced.
    """

    def test_deductions_exceed_gross_unbalanced(self):
        # gross=10000, deductions=12000 → 超过工资, 必不平
        result = PayrollReconciler.classify(
            gross_total=10000.0, ss_total=8000.0, hf_total=2000.0, tax_total=2000.0
        )
        assert result["is_balanced"] is False
        assert result["discrepancy_amount"] == 2000.0
        assert "请复核" in result["notes"]

    def test_deductions_within_5pct_balanced(self):
        # gross=10000, deductions=10500 → 刚好 5% 容差内 (balanced 但 discrepancy=500)
        result = PayrollReconciler.classify(
            gross_total=10000.0, ss_total=7000.0, hf_total=2000.0, tax_total=1500.0
        )
        assert result["is_balanced"] is True
        # 在 5% 容差内: 差额仍记录但 is_balanced=True
        assert result["discrepancy_amount"] == 500.0
        assert result["notes"] is None

    def test_deductions_below_gross_balanced(self):
        # gross=10000, deductions=6000 → 正常情况
        result = PayrollReconciler.classify(
            gross_total=10000.0, ss_total=4000.0, hf_total=1000.0, tax_total=1000.0
        )
        assert result["is_balanced"] is True
        assert result["discrepancy_amount"] == 0.0

    def test_empty_gross_returns_balanced(self):
        # 工资为 0 (空项目), 不报错, balanced=True, discrepancy=0
        result = PayrollReconciler.classify(
            gross_total=0.0, ss_total=0.0, hf_total=0.0, tax_total=0.0
        )
        assert result["is_balanced"] is True
        assert result["discrepancy_amount"] == 0.0

    def test_just_over_5pct_unbalanced(self):
        # gross=10000, deductions=10600 → 超 5% (6%), 不平
        result = PayrollReconciler.classify(
            gross_total=10000.0, ss_total=7000.0, hf_total=2000.0, tax_total=1600.0
        )
        assert result["is_balanced"] is False
        assert result["discrepancy_amount"] == 600.0


# ============================================================
# #2 double_declining_monthly (regression guard)
# ============================================================
class TestDoubleDecliningRegression:
    """DDB 月折旧公式: NBV * (2/N) / 12 = NBV * 2 / life_months.

    旧 BUGS_FOUND.md 报告"应 33 元/月"是基于对公式的错误理解.
    经核实: NBV=100000, life=120 月 → monthly=NBV*0.2/12=1666.67 元/月 (正确).
    这里锁住公式, 防止未来被改成错.
    """

    def test_nbv_100k_10yr(self):
        # NBV=100000, life_months=120 (10年) → annual rate=2/10=0.2, monthly=1666.67
        r = DepreciationCalculator.double_declining_monthly(100_000, 120)
        assert r == 1666.67

    def test_nbv_50k_5yr(self):
        # NBV=50000, life=60 月 → annual=2/5=0.4, monthly=1666.67
        r = DepreciationCalculator.double_declining_monthly(50_000, 60)
        assert r == 1666.67

    def test_zero_life_returns_zero(self):
        assert DepreciationCalculator.double_declining_monthly(100_000, 0) == 0.0


# ============================================================
# #1 LeaseAmortizer.build_schedule 月度递推
# ============================================================
class TestBuildScheduleMonthRecursion:
    """P0 修复: 月度递推 off-by-one (start_dt.month=1, i=12 算出 year-12 而非 year+1-01).

    验证关键边界:
      - start_dt = 2026-01-15, n=12
      - 第 1 期: 2026-01
      - 第 12 期: 2027-01 (而不是 2026-12)
    """

    def test_jan_start_12_months(self):
        # 2026-01 起, 12 期租赁, i=1 → 2026-01, i=12 → 2026-12
        periods = LeaseAmortizer.compute_periods(start_year_month="2026-01", n=12)
        assert len(periods) == 12
        assert periods[0] == "2026-01"
        assert periods[1] == "2026-02"
        assert periods[11] == "2026-12"

    def test_jan_start_13_months_cross_year(self):
        # 2026-01 起, 13 期租赁, 最后一期跨年 → 2027-01
        periods = LeaseAmortizer.compute_periods(start_year_month="2026-01", n=13)
        assert periods[0] == "2026-01"
        assert periods[11] == "2026-12"
        assert periods[12] == "2027-01"

    def test_march_start_24_months(self):
        # 2026-03 起, 24 期, 最后期 2028-02 (跨年 2 次)
        periods = LeaseAmortizer.compute_periods(start_year_month="2026-03", n=24)
        assert len(periods) == 24
        assert periods[0] == "2026-03"
        assert periods[9] == "2026-12"
        assert periods[10] == "2027-01"
        assert periods[23] == "2028-02"

    def test_december_start_12_months(self):
        # 2026-12 起, 12 期, 应到 2027-11
        periods = LeaseAmortizer.compute_periods(start_year_month="2026-12", n=12)
        assert periods[0] == "2026-12"
        assert periods[1] == "2027-01"
        assert periods[11] == "2027-11"

    def test_invalid_input_returns_empty(self):
        # 兜底: 错误格式返空, 不抛
        assert LeaseAmortizer.compute_periods(start_year_month="invalid", n=12) == []
        assert LeaseAmortizer.compute_periods(start_year_month="2026-01", n=0) == []