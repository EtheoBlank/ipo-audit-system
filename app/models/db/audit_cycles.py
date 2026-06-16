"""Pack C — 10 个审计循环 ORM 汇总.

每个循环只建最核心的表 (主表 + 1-2 个明细表), 服务层逻辑放在
``app/services/audit_cycles/<module>.py``.

10 个循环:
  1. payables (应付循环)
  2. expenses (费用循环)
  3. payroll (薪酬循环)
  4. fixed_assets (固定资产 + 在建工程, 长期资产发生额审定联动)
  5. intangible_assets (无形资产 + 研发资本化)
  6. long_term_investment (长投 + 合并报表)
  7. leases (租赁 CAS 21)
  8. income_tax (所得税重算)
  9. accounting_estimates (重要会计估计 — ECL 三阶段等)
 10. subsequent_events (后续期间事项)
"""

from __future__ import annotations

from datetime import datetime
from app.utils.datetime_helpers import utc_now
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


__all__ = [
    # Payables
    "Supplier",
    "PayableAging",
    # Expenses
    "ExpenseRecord",
    "ExpenseAnomalyFlag",
    # Payroll
    "PayrollRecord",
    "PayrollReconciliation",
    # Fixed Assets
    "FixedAsset",
    "DepreciationRecalc",
    "ConstructionInProgress",
    # Intangible
    "IntangibleAsset",
    "RDCapitalizationAssessment",
    # Long-term Investment
    "LongTermInvestment",
    "GoodwillImpairmentTest",
    # Leases (CAS 21)
    "LeaseContract",
    "LeaseAmortizationSchedule",
    # Income Tax
    "IncomeTaxReconciliation",
    "DeferredTaxItem",
    # Accounting Estimates
    "ECLAssessment",
    "AssetImpairmentTest",
    "ProvisionEstimate",
    # Subsequent Events
    "SubsequentEvent",
    "GoingConcernAssessment",
]


# ============================================================
#  1. PAYABLES (应付循环)
# ============================================================


class Supplier(Base):
    __tablename__ = "ac_suppliers"
    __table_args__ = (Index("ix_supplier_project_code", "project_id", "supplier_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    supplier_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unified_credit_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True
    )
    contact: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    payment_terms: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_related_party: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PayableAging(Base):
    __tablename__ = "ac_payable_agings"
    __table_args__ = (Index("ix_payable_aging_project_period", "project_id", "period_end"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    supplier_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ac_suppliers.id"), nullable=True
    )
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_end: Mapped[str] = mapped_column(String(20), nullable=False)
    # 5 个账龄区间
    amount_0_30: Mapped[float] = mapped_column(Float, default=0.0)
    amount_31_90: Mapped[float] = mapped_column(Float, default=0.0)
    amount_91_180: Mapped[float] = mapped_column(Float, default=0.0)
    amount_181_365: Mapped[float] = mapped_column(Float, default=0.0)
    amount_over_365: Mapped[float] = mapped_column(Float, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    risk_flag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  2. EXPENSES (费用循环)
# ============================================================


class ExpenseRecord(Base):
    __tablename__ = "ac_expense_records"
    __table_args__ = (
        Index("ix_expense_project_account", "project_id", "account_code"),
        Index("ix_expense_voucher", "project_id", "voucher_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    voucher_date: Mapped[str] = mapped_column(String(20), nullable=False)
    voucher_no: Mapped[str] = mapped_column(String(50), nullable=False)
    account_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    expense_category: Mapped[str] = mapped_column(String(50), nullable=False)
    # 职工薪酬 / 差旅 / 折旧摊销 / 办公 / 业务招待 / 广告宣传 / 咨询 / 研发投入 等
    amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_related_party: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ExpenseAnomalyFlag(Base):
    __tablename__ = "ac_expense_anomaly_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    expense_record_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ac_expense_records.id"), nullable=True
    )
    anomaly_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 整数报销 / 节假日报销 / 业务招待超 60% / 1‰ / 关联方支付
    severity: Mapped[str] = mapped_column(String(20), default="warn", nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  3. PAYROLL (薪酬循环)
# ============================================================


class PayrollRecord(Base):
    __tablename__ = "ac_payroll_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    period_yyyymm: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    employee_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    employee_name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    position: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    gross_salary: Mapped[float] = mapped_column(Float, default=0.0)
    social_security: Mapped[float] = mapped_column(Float, default=0.0)
    housing_fund: Mapped[float] = mapped_column(Float, default=0.0)
    income_tax: Mapped[float] = mapped_column(Float, default=0.0)
    net_salary: Mapped[float] = mapped_column(Float, default=0.0)
    is_senior_executive: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PayrollReconciliation(Base):
    """工资 vs 社保 vs 公积金 vs 个税 四表勾稽."""

    __tablename__ = "ac_payroll_reconciliations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    period_yyyymm: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    payroll_total: Mapped[float] = mapped_column(Float, default=0.0)
    social_security_total: Mapped[float] = mapped_column(Float, default=0.0)
    housing_fund_total: Mapped[float] = mapped_column(Float, default=0.0)
    income_tax_total: Mapped[float] = mapped_column(Float, default=0.0)
    discrepancy_amount: Mapped[float] = mapped_column(Float, default=0.0)
    is_balanced: Mapped[bool] = mapped_column(Boolean, default=True)
    discrepancy_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  4. FIXED ASSETS (固定资产 + 在建工程, 长期资产发生额审定联动)
# ============================================================


class FixedAsset(Base):
    __tablename__ = "ac_fixed_assets"
    __table_args__ = (Index("ix_fa_project_code", "project_id", "asset_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    asset_code: Mapped[str] = mapped_column(String(80), nullable=False)
    asset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    original_cost: Mapped[float] = mapped_column(Float, default=0.0)
    accumulated_depreciation: Mapped[float] = mapped_column(Float, default=0.0)
    impairment_provision: Mapped[float] = mapped_column(Float, default=0.0)
    net_book_value: Mapped[float] = mapped_column(Float, default=0.0)
    purchase_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    useful_life_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salvage_rate: Mapped[float] = mapped_column(Float, default=0.05)
    depreciation_method: Mapped[str] = mapped_column(
        String(40), default="straight_line", nullable=False
    )
    # straight_line / double_declining / sum_of_years / units_of_production
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    in_use: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class DepreciationRecalc(Base):
    __tablename__ = "ac_depreciation_recalcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("ac_fixed_assets.id"), nullable=False)
    period_yyyymm: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    book_depreciation: Mapped[float] = mapped_column(Float, default=0.0)
    recalc_depreciation: Mapped[float] = mapped_column(Float, default=0.0)
    diff_amount: Mapped[float] = mapped_column(Float, default=0.0)
    diff_pct: Mapped[float] = mapped_column(Float, default=0.0)
    has_material_diff: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ConstructionInProgress(Base):
    __tablename__ = "ac_construction_in_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    started_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    expected_completion_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    cumulative_cost: Mapped[float] = mapped_column(Float, default=0.0)
    transfer_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    transfer_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    transfer_evidence_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  5. INTANGIBLE ASSETS (无形资产 + 研发资本化)
# ============================================================


class IntangibleAsset(Base):
    __tablename__ = "ac_intangible_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    asset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    # 专利 / 商标 / 软件 / 土地使用权 / 客户关系 / 自研技术 等
    original_cost: Mapped[float] = mapped_column(Float, default=0.0)
    accumulated_amortization: Mapped[float] = mapped_column(Float, default=0.0)
    impairment_provision: Mapped[float] = mapped_column(Float, default=0.0)
    useful_life_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    acquired_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_internally_developed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class RDCapitalizationAssessment(Base):
    """CAS 6 五项条件评估."""

    __tablename__ = "ac_rd_capitalization_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    rd_project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_yyyymm: Mapped[str] = mapped_column(String(7), nullable=False)
    # CAS 6 五项条件
    technical_feasibility: Mapped[bool] = mapped_column(Boolean, default=False)
    intent_to_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    ability_to_use_or_sell: Mapped[bool] = mapped_column(Boolean, default=False)
    future_economic_benefit: Mapped[bool] = mapped_column(Boolean, default=False)
    resources_sufficient: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_measurable: Mapped[bool] = mapped_column(Boolean, default=False)
    all_conditions_met: Mapped[bool] = mapped_column(Boolean, default=False)
    capitalized_amount: Mapped[float] = mapped_column(Float, default=0.0)
    expensed_amount: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  6. LONG-TERM INVESTMENT (长投 + 合并 + 商誉减值)
# ============================================================


class LongTermInvestment(Base):
    __tablename__ = "ac_long_term_investments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    investee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # cost / equity
    holding_pct: Mapped[float] = mapped_column(Float, default=0.0)
    has_significant_influence: Mapped[bool] = mapped_column(Boolean, default=False)
    original_cost: Mapped[float] = mapped_column(Float, default=0.0)
    cumulative_adjustment: Mapped[float] = mapped_column(Float, default=0.0)
    investment_income: Mapped[float] = mapped_column(Float, default=0.0)
    impairment_provision: Mapped[float] = mapped_column(Float, default=0.0)
    book_value: Mapped[float] = mapped_column(Float, default=0.0)
    is_consolidated: Mapped[bool] = mapped_column(Boolean, default=False)
    goodwill_amount: Mapped[float] = mapped_column(Float, default=0.0)
    acquired_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class GoodwillImpairmentTest(Base):
    """商誉减值测试 — 5/8 年现金流折现."""

    __tablename__ = "ac_goodwill_impairment_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    investment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ac_long_term_investments.id"), nullable=False
    )
    test_date: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_group_name: Mapped[str] = mapped_column(String(200), nullable=False)
    book_value_with_goodwill: Mapped[float] = mapped_column(Float, default=0.0)
    recoverable_amount: Mapped[float] = mapped_column(Float, default=0.0)
    fair_value_less_costs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    value_in_use: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    forecast_years: Mapped[int] = mapped_column(Integer, default=5)
    discount_rate: Mapped[float] = mapped_column(Float, default=0.10)
    impairment_required: Mapped[float] = mapped_column(Float, default=0.0)
    method_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cashflow_table_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  7. LEASES (CAS 21 新租赁准则)
# ============================================================


class LeaseContract(Base):
    __tablename__ = "ac_lease_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    contract_no: Mapped[str] = mapped_column(String(100), nullable=False)
    lessor: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_description: Mapped[str] = mapped_column(String(500), nullable=False)
    commencement_date: Mapped[str] = mapped_column(String(20), nullable=False)
    lease_term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_frequency: Mapped[str] = mapped_column(String(20), default="monthly")
    fixed_payment: Mapped[float] = mapped_column(Float, default=0.0)
    discount_rate: Mapped[float] = mapped_column(Float, default=0.05)
    is_short_term: Mapped[bool] = mapped_column(Boolean, default=False)
    is_low_value: Mapped[bool] = mapped_column(Boolean, default=False)
    use_simplified: Mapped[bool] = mapped_column(Boolean, default=False)
    rou_asset_initial: Mapped[float] = mapped_column(Float, default=0.0)
    lease_liability_initial: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class LeaseAmortizationSchedule(Base):
    __tablename__ = "ac_lease_amortization_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    contract_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ac_lease_contracts.id"), nullable=False, index=True
    )
    period_yyyymm: Mapped[str] = mapped_column(String(7), nullable=False)
    payment: Mapped[float] = mapped_column(Float, default=0.0)
    interest_expense: Mapped[float] = mapped_column(Float, default=0.0)
    principal_reduction: Mapped[float] = mapped_column(Float, default=0.0)
    rou_depreciation: Mapped[float] = mapped_column(Float, default=0.0)
    liability_balance: Mapped[float] = mapped_column(Float, default=0.0)
    rou_balance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  8. INCOME TAX (所得税重算)
# ============================================================


class IncomeTaxReconciliation(Base):
    __tablename__ = "ac_income_tax_reconciliations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    pretax_profit: Mapped[float] = mapped_column(Float, default=0.0)
    permanent_diff_total: Mapped[float] = mapped_column(Float, default=0.0)
    temporary_diff_total: Mapped[float] = mapped_column(Float, default=0.0)
    taxable_income: Mapped[float] = mapped_column(Float, default=0.0)
    nominal_rate: Mapped[float] = mapped_column(Float, default=0.25)
    effective_rate: Mapped[float] = mapped_column(Float, default=0.25)
    current_tax: Mapped[float] = mapped_column(Float, default=0.0)
    deferred_tax_change: Mapped[float] = mapped_column(Float, default=0.0)
    losses_carried_forward_used: Mapped[float] = mapped_column(Float, default=0.0)
    bridging_table_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class DeferredTaxItem(Base):
    __tablename__ = "ac_deferred_tax_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_dta: Mapped[bool] = mapped_column(Boolean, default=True)  # True=DTA, False=DTL
    temporary_diff: Mapped[float] = mapped_column(Float, default=0.0)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.25)
    deferred_tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    recognition_basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  9. ACCOUNTING ESTIMATES (重要会计估计 — ECL/资产减值/预计负债)
# ============================================================


class ECLAssessment(Base):
    """坏账 ECL 三阶段评估."""

    __tablename__ = "ac_ecl_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    period_end: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    receivable_balance: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[int] = mapped_column(Integer, default=1)  # 1 / 2 / 3
    pd_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 违约概率
    lgd_rate: Mapped[float] = mapped_column(Float, default=0.45)  # 违约损失率
    ead_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 风险敞口
    ecl_amount: Mapped[float] = mapped_column(Float, default=0.0)
    methodology_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class AssetImpairmentTest(Base):
    __tablename__ = "ac_asset_impairment_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    asset_category: Mapped[str] = mapped_column(String(80), nullable=False)
    asset_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    test_date: Mapped[str] = mapped_column(String(20), nullable=False)
    book_value: Mapped[float] = mapped_column(Float, default=0.0)
    recoverable_amount: Mapped[float] = mapped_column(Float, default=0.0)
    impairment_required: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ProvisionEstimate(Base):
    __tablename__ = "ac_provision_estimates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    provision_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 诉讼 / 产品质量保证 / 重组 / 其他
    description: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_amount: Mapped[float] = mapped_column(Float, default=0.0)
    probability: Mapped[float] = mapped_column(Float, default=0.0)  # 0-1
    booking_status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending / booked / disclosed_only / no_action
    period_end: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  10. SUBSEQUENT EVENTS (后续期间)
# ============================================================


class SubsequentEvent(Base):
    __tablename__ = "ac_subsequent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    balance_sheet_date: Mapped[str] = mapped_column(String(20), nullable=False)
    event_date: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # adjusting / non_adjusting
    description: Mapped[str] = mapped_column(Text, nullable=False)
    financial_impact_amount: Mapped[float] = mapped_column(Float, default=0.0)
    adjustment_made: Mapped[bool] = mapped_column(Boolean, default=False)
    disclosure_required: Mapped[bool] = mapped_column(Boolean, default=False)
    disclosure_section: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    evidence_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class GoingConcernAssessment(Base):
    """持续经营能力评估 (12 个月偿债压力测试)."""

    __tablename__ = "ac_going_concern_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    assessment_date: Mapped[str] = mapped_column(String(20), nullable=False)
    operating_cashflow_12m: Mapped[float] = mapped_column(Float, default=0.0)
    interest_expense_12m: Mapped[float] = mapped_column(Float, default=0.0)
    debt_due_12m: Mapped[float] = mapped_column(Float, default=0.0)
    available_credit_line: Mapped[float] = mapped_column(Float, default=0.0)
    cash_balance: Mapped[float] = mapped_column(Float, default=0.0)
    going_concern_risk_level: Mapped[str] = mapped_column(String(20), default="low")
    # low / medium / high / substantial_doubt
    mitigating_factors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conclusion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
