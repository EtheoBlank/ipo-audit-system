"""Pack D — 一档剩余 + 三档 IPO 专属 ORM.

一档剩余:
  - Phase 16: 内控穿行测试 (InternalControlCycle / ICRiskControl / ICWalkthrough /
    ICWalkthroughStep / ICSamplingResult)
  - Phase 17: 跨期调整 + 合同资产/负债 (RevenueCutoffTest / ContractAssetLiability /
    JournalEntryDraft)

三档 IPO 专属:
  - 招股书勾稽 (Prospectus / ProspectusKeyMetric / ReconciliationFinding)
  - 三年一期对比 (PeriodComparisonReport)
  - 客户/供应商重叠 (CustomerSupplierOverlap)
  - 可比公司 (PeerCompany / PeerCompanyMetric)
  - 反馈意见 (FeedbackLetter / FeedbackQuestion / FeedbackResponse)
  - 申报材料 (SubmissionChecklistItem)
"""

from __future__ import annotations

from datetime import datetime, timezone
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


__all__ = [
    # Phase 16
    "InternalControlCycle",
    "ICRiskControl",
    "ICWalkthrough",
    "ICWalkthroughStep",
    "ICSamplingResult",
    # Phase 17
    "RevenueCutoffTest",
    "ContractAssetLiability",
    "JournalEntryDraft",
    # 招股书勾稽
    "Prospectus",
    "ProspectusKeyMetric",
    "ReconciliationFinding",
    # 三年一期对比
    "PeriodComparisonReport",
    # 客户/供应商重叠
    "CustomerSupplierOverlap",
    # 可比公司
    "PeerCompany",
    "PeerCompanyMetric",
    # 反馈意见
    "FeedbackLetter",
    "FeedbackQuestion",
    "FeedbackResponse",
    # 申报材料
    "SubmissionChecklistItem",
]


# ============================================================
#  Phase 16 — 内控穿行测试
# ============================================================


class InternalControlCycle(Base):
    """6 大循环 (销售/采购/存货/薪酬/财务报告/IT) + 自定义."""

    __tablename__ = "ipo_ic_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    cycle_code: Mapped[str] = mapped_column(String(40), nullable=False)
    # sales / procurement / inventory / payroll / financial_reporting / it / custom
    cycle_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ICRiskControl(Base):
    """风险-控制矩阵 (RCM)."""

    __tablename__ = "ipo_ic_risk_controls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cycle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_ic_cycles.id"), nullable=False, index=True
    )
    risk_no: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_description: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    control_no: Mapped[str] = mapped_column(String(40), nullable=False)
    control_description: Mapped[str] = mapped_column(Text, nullable=False)
    control_type: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    # preventive / detective; manual / automated
    control_frequency: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    test_assertion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ICWalkthrough(Base):
    __tablename__ = "ipo_ic_walkthroughs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    cycle_id: Mapped[int] = mapped_column(Integer, ForeignKey("ipo_ic_cycles.id"), nullable=False)
    walkthrough_no: Mapped[str] = mapped_column(String(60), nullable=False)
    walkthrough_date: Mapped[str] = mapped_column(String(20), nullable=False)
    sample_voucher_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sample_basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    overall_conclusion: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # effective / partial / ineffective
    flowchart_mermaid: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ICWalkthroughStep(Base):
    __tablename__ = "ipo_ic_walkthrough_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    walkthrough_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_ic_walkthroughs.id"), nullable=False, index=True
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    step_description: Mapped[str] = mapped_column(Text, nullable=False)
    document_evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    control_evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_result: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    issues_noted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ICSamplingResult(Base):
    __tablename__ = "ipo_ic_sampling_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    cycle_code: Mapped[str] = mapped_column(String(40), nullable=False)
    sampling_method: Mapped[str] = mapped_column(String(40), nullable=False)
    # top_n / random / cutoff / risk_based
    criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    sampled_voucher_nos: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  Phase 17 — 跨期调整 + 合同资产/负债
# ============================================================


class RevenueCutoffTest(Base):
    __tablename__ = "ipo_revenue_cutoff_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    period_end: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    sales_record_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cutoff_judgement: Mapped[str] = mapped_column(String(40), nullable=False)
    # early / late / normal
    ship_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    revenue_confirm_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    diff_days: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    adjustment_proposed: Mapped[float] = mapped_column(Float, default=0.0)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ContractAssetLiability(Base):
    __tablename__ = "ipo_contract_asset_liabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    contract_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    performance_obligation: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    period_end: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    # contract_asset (已履约未开票) / contract_liability (已收款未履约)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class JournalEntryDraft(Base):
    """跨期调整分录草稿 — 审计师复核后接受落入序时账."""

    __tablename__ = "ipo_journal_entry_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    source_module: Mapped[str] = mapped_column(String(50), nullable=False)
    # revenue_cutoff / contract_asset / depreciation / impairment / 等
    period_end: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    debit_account_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    debit_amount: Mapped[float] = mapped_column(Float, default=0.0)
    credit_account_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    credit_amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    # proposed / accepted / rejected / modified
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decided_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  招股书勾稽
# ============================================================


class Prospectus(Base):
    __tablename__ = "ipo_prospectuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    upload_date: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ProspectusKeyMetric(Base):
    """招股书关键数据点 (毛利率/产能利用率/前五客户占比 等)."""

    __tablename__ = "ipo_prospectus_key_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    prospectus_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_prospectuses.id"), nullable=False, index=True
    )
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_label: Mapped[str] = mapped_column(String(40), nullable=False)
    # 2024 / 2023 / 2022 / 2024H1 等
    prospectus_value: Mapped[float] = mapped_column(Float, default=0.0)
    system_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="元")
    diff_amount: Mapped[float] = mapped_column(Float, default=0.0)
    diff_pct: Mapped[float] = mapped_column(Float, default=0.0)
    is_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ReconciliationFinding(Base):
    __tablename__ = "ipo_reconciliation_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    prospectus_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ipo_prospectuses.id"), nullable=True
    )
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="warn", nullable=False)
    # critical / warn / notice
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  三年一期对比
# ============================================================


class PeriodComparisonReport(Base):
    """三年一期对比表 — 资产负债表 / 利润表 / 现金流量表."""

    __tablename__ = "ipo_period_comparison_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # balance_sheet / income_statement / cash_flow / ratios
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    value_period_1: Mapped[float] = mapped_column(Float, default=0.0)  # 报告期 -3
    value_period_2: Mapped[float] = mapped_column(Float, default=0.0)  # 报告期 -2
    value_period_3: Mapped[float] = mapped_column(Float, default=0.0)  # 报告期 -1
    value_period_h1: Mapped[float] = mapped_column(Float, default=0.0)  # 一期 (半年)
    yoy_change_pct: Mapped[float] = mapped_column(Float, default=0.0)
    anomaly_flag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ai_explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  客户 / 供应商重叠
# ============================================================


class CustomerSupplierOverlap(Base):
    """客户即供应商 / 客户与供应商重叠检测."""

    __tablename__ = "ipo_customer_supplier_overlaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unified_credit_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    match_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # exact_credit_code / fuzzy_name
    fuzzy_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    customer_sales: Mapped[float] = mapped_column(Float, default=0.0)
    supplier_purchases: Mapped[float] = mapped_column(Float, default=0.0)
    is_related_party: Mapped[bool] = mapped_column(Boolean, default=False)
    explanation_required: Mapped[bool] = mapped_column(Boolean, default=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  可比公司
# ============================================================


class PeerCompany(Base):
    __tablename__ = "ipo_peer_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    stock_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    short_name: Mapped[str] = mapped_column(String(100), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    industry_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    main_business: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    market_cap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PeerCompanyMetric(Base):
    """可比公司的年度财务指标 (毛利率/净利率/ROE/营收增速 等)."""

    __tablename__ = "ipo_peer_company_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    peer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_peer_companies.id"), nullable=False, index=True
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(20), default="%")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  反馈意见 / 问询函
# ============================================================


class FeedbackLetter(Base):
    __tablename__ = "ipo_feedback_letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    letter_no: Mapped[str] = mapped_column(String(100), nullable=False)
    issuer: Mapped[str] = mapped_column(String(100), nullable=False)
    # CSRC / SSE / SZSE / BSE / other
    issue_date: Mapped[str] = mapped_column(String(20), nullable=False)
    received_date: Mapped[str] = mapped_column(String(20), nullable=False)
    reply_deadline: Mapped[str] = mapped_column(String(20), nullable=False)
    sla_days: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(20), default="open")
    # open / in_progress / replied / closed
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class FeedbackQuestion(Base):
    __tablename__ = "ipo_feedback_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    letter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_feedback_letters.id"), nullable=False, index=True
    )
    question_no: Mapped[str] = mapped_column(String(40), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    related_module: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # revenue / related_party / inventory / 内控 / 等
    severity: Mapped[str] = mapped_column(String(20), default="warn")
    status: Mapped[str] = mapped_column(String(20), default="open")
    assigned_to_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class FeedbackResponse(Base):
    __tablename__ = "ipo_feedback_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    question_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_feedback_questions.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


# ============================================================
#  申报材料完整性
# ============================================================


class SubmissionChecklistItem(Base):
    __tablename__ = "ipo_submission_checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    board_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # main_board / chinext / sse_star / bse
    item_code: Mapped[str] = mapped_column(String(80), nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    is_uploaded: Mapped[bool] = mapped_column(Boolean, default=False)
    upload_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    uploaded_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
