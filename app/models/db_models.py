"""SQLAlchemy database models for IPO Audit System."""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Float, Integer, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Project(Base):
    """审计项目表"""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联关系
    account_balances: Mapped[list["AccountBalance"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    chronological_accounts: Mapped[list["ChronologicalAccount"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    bank_statements: Mapped[list["BankStatement"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sales_documents: Mapped[list["SalesDocument"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sales_records: Mapped[list["SalesRecord"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    contracts: Mapped[list["ContractDocument"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class AccountBalance(Base):
    """科目余额表"""
    __tablename__ = "account_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    account_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    balance_direction: Mapped[str] = mapped_column(String(10), nullable=False)  # 借/贷
    beginning_balance: Mapped[float] = mapped_column(Float, default=0)
    debit_amount: Mapped[float] = mapped_column(Float, default=0)
    credit_amount: Mapped[float] = mapped_column(Float, default=0)
    ending_balance: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 关联关系
    project: Mapped["Project"] = relationship(back_populates="account_balances")


class ChronologicalAccount(Base):
    """序时账"""
    __tablename__ = "chronological_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    voucher_date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    voucher_no: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    account_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    debit_amount: Mapped[float] = mapped_column(Float, default=0)
    credit_amount: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    auxiliary_accounting: Mapped[str] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 关联关系
    project: Mapped["Project"] = relationship(back_populates="chronological_accounts")


class BankStatement(Base):
    """银行对账单"""
    __tablename__ = "bank_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    statement_date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    voucher_no: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    debit_amount: Mapped[float] = mapped_column(Float, default=0)
    credit_amount: Mapped[float] = mapped_column(Float, default=0)
    balance: Mapped[float] = mapped_column(Float, default=0)
    bank_account: Mapped[str] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 关联关系
    project: Mapped["Project"] = relationship(back_populates="bank_statements")


class RegulatoryCase(Base):
    """监管案例库"""
    __tablename__ = "regulatory_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_no: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    case_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # 问询函/处罚决定
    source: Mapped[str] = mapped_column(String(100), nullable=False)  # 证监会/交易所
    publish_date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    key_words: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditRisk(Base):
    """审计风险记录"""
    __tablename__ = "audit_risks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)  # 高/中/低
    risk_description: Mapped[str] = mapped_column(Text, nullable=False)
    affected_accounts: Mapped[str] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str] = mapped_column(Text, nullable=True)
    related_case_id: Mapped[int] = mapped_column(Integer, ForeignKey("regulatory_cases.id"), nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # 关联关系
    project: Mapped["Project"] = relationship(back_populates=None)
    related_case: Mapped["RegulatoryCase"] = relationship(back_populates=None)


class SalesDocument(Base):
    """用户上传的原始销售文档（销售合同/发票/发货单/报关单等）。
    解析后的纯文本/表格内容存于 raw_text，供 AI 抽取。
    """
    __tablename__ = "sales_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)  # docx / pdf / xlsx
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="sales_documents")
    records: Mapped[list["SalesRecord"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class SalesRecord(Base):
    """销售清单行（AI 合成后入库，可由审计师在前端核对修改）。
    字段对应"销售清单"底稿要求：金额、发货/确认时间、数量/单价、产品编号、成本、可直接对应销售费用。
    """
    __tablename__ = "sales_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    document_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("sales_documents.id"), nullable=True)

    # 业务主标识
    contract_no: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 销售发票与税务 (新增 — 增值税底稿闭环)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(10), default="CNY", nullable=True)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)         # 税率，如 0.13
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)       # 税额
    gross_amount: Mapped[float] = mapped_column(Float, default=0.0)     # 价税合计 (revenue + tax)

    # 数量与金额
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit_price: Mapped[float] = mapped_column(Float, default=0)         # 不含税单价
    revenue_amount: Mapped[float] = mapped_column(Float, default=0)     # 不含税收入金额
    cost_amount: Mapped[float] = mapped_column(Float, default=0)        # 对应成本（用于毛利率分析）

    # 与销售直接对应的费用
    shipping_fee: Mapped[float] = mapped_column(Float, default=0)       # 运费
    customs_fee: Mapped[float] = mapped_column(Float, default=0)        # 报关费
    other_direct_fee: Mapped[float] = mapped_column(Float, default=0)   # 其他直接费用

    # 退换货 / 折扣 / 返利 (新增 — 毛利真实性)
    return_amount: Mapped[float] = mapped_column(Float, default=0.0)    # 退货冲减金额
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 折扣折让
    rebate_amount: Mapped[float] = mapped_column(Float, default=0.0)    # 销售返利

    # 时间
    ship_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    receipt_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)  # 新增: 签收/验收日
    revenue_confirm_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    # 函证状态 (新增 — 审计轨迹闭环)
    confirmation_status: Mapped[Optional[str]] = mapped_column(String(20), default="未发函", nullable=True)  # 未发函/已发函/已回函/未回函/作废
    confirmation_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confirmation_diff: Mapped[float] = mapped_column(Float, default=0.0)

    # 溯源
    source: Mapped[str] = mapped_column(String(255), nullable=True)     # 来源文档名 / 备注
    confidence: Mapped[float] = mapped_column(Float, default=1.0)       # AI 合成置信度（0-1）
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)   # 人工核对标志

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="sales_records")
    document: Mapped[Optional["SalesDocument"]] = relationship(back_populates="records")


class ContractDocument(Base):
    """收入合同（图片/PDF/扫描件）+ OCR 文本 + 要点 / CAS 14 五步法分析。"""
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    # 原始信息
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)  # image/jpeg, image/png, application/pdf
    ocr_engine: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # paddleocr / easyocr / tesseract / manual
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # AI 抽取：基础 7 字段 (JSON 字符串)
    key_points: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # AI 抽取：CAS 14 五步法 (JSON 字符串)
    five_step_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 风险扫描结论
    risk_flags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="contracts")