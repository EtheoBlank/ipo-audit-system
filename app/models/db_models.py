"""SQLAlchemy database models for IPO Audit System."""
from datetime import datetime
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