"""SQLAlchemy database models for IPO Audit System.

模块化拆分: 新增的 ORM (Pack A/B/C/D 等) 一律放到 ``app/models/db/<module>.py``
子文件里, 本文件在顶部统一 ``from app.models.db import *`` 聚合, 保证:
  - 现有 ``from app.models.db_models import X`` 调用 100% 兼容
  - 新表通过子模块 ``__all__`` 透出后 ``Base.metadata`` 完整收齐
  - 单文件不再无限膨胀

老模型 (Project / AccountBalance / ConfirmationCase / Sentiment* / TeamMember
等) 暂时仍在本文件里, 后续重构按需迁移; 迁移时只需把类剪到子文件、
本文件保留 import 即可, 调用方无感知。
"""

from datetime import datetime
from app.utils.datetime_helpers import utc_now
from typing import Optional
from sqlalchemy import (
    String,
    Text,
    Float,
    Integer,
    DateTime,
    ForeignKey,
    Boolean,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

# 子模块聚合 — 新增模块在 app/models/db/__init__.py 加一行即可
from app.models.db import *  # noqa: F401, F403


class Project(Base):
    """审计项目表"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active")

    # === 多租户硬隔离 (Pack A.2 — Roadmap "跨事务所多租户硬隔离") ===
    # Project 是所有业务数据的入口表; 其他表都通过 project_id 外键挂在这里,
    # 所以只要保证"用户只能访问 firm_id 匹配的 Project", 就实现了租户级数据隔离.
    # firm_id 可空 — 老数据 / AUTH_ENABLED=false 时无所属事务所, 视作全局可见.
    firm_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("auth_firms.id"), nullable=True, index=True
    )

    # === 舆情跟踪扩展字段 (v0.2 追加，全 nullable，旧数据零迁移) ===
    stock_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    stock_short_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    legal_representative: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    actual_controller: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    industry_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    unified_credit_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, unique=True
    )
    registered_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    keywords_extra: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # 审计师手填搜索别名（换行分隔）
    directors_supervisors_executives: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # 董监高 JSON
    search_priority: Mapped[Optional[str]] = mapped_column(
        String(10), default="normal", nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # 关联关系
    account_balances: Mapped[list["AccountBalance"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    chronological_accounts: Mapped[list["ChronologicalAccount"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    bank_statements: Mapped[list["BankStatement"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    sales_documents: Mapped[list["SalesDocument"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    sales_records: Mapped[list["SalesRecord"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    contracts: Mapped[list["ContractDocument"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    inventory_count_plans: Mapped[list["InventoryCountPlan"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    inventory_count_sheets: Mapped[list["InventoryCountSheet"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    inventory_impairments: Mapped[list["InventoryImpairment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    confirmation_cases: Mapped[list["ConfirmationCase"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    # 舆情跟踪关联
    sentiment_subjects: Mapped[list["SentimentSubject"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    sentiment_events: Mapped[list["SentimentEvent"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    daily_briefings: Mapped[list["SentimentDailyBriefing"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    quarterly_reports: Mapped[list["SentimentQuarterlyReport"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    # 项目组管理关联
    project_assignments: Mapped[list["ProjectAssignment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    work_plans: Mapped[list["WorkPlan"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    daily_reports: Mapped[list["DailyReport"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    blockers: Mapped[list["Blocker"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    progress_snapshots: Mapped[list["ProgressSnapshot"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    management_recommendations: Mapped[list["ManagementRecommendation"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # 关联关系
    project: Mapped["Project"] = relationship(back_populates="chronological_accounts")

    # P1 性能 (2026-06-19): 相关方推断 + 科目聚合常用 WHERE project_id + account_code
    __table_args__ = (
        Index("ix_chrono_project_account", "project_id", "account_code"),
        Index("ix_chrono_project_aux", "project_id", "auxiliary_accounting"),
    )


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # 关联关系
    project: Mapped["Project"] = relationship(back_populates="bank_statements")


class RegulatoryCase(Base):
    """监管案例库"""

    __tablename__ = "regulatory_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_no: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    case_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # 问询函/处罚决定
    source: Mapped[str] = mapped_column(String(100), nullable=False)  # 证监会/交易所
    publish_date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    key_words: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


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
    related_case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("regulatory_cases.id"), nullable=True
    )
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # 关联关系
    project: Mapped["Project"] = relationship()
    related_case: Mapped["RegulatoryCase"] = relationship()


class SalesDocument(Base):
    """用户上传的原始销售文档（销售合同/发票/发货单/报关单等）。
    解析后的纯文本/表格内容存于 raw_text，供 AI 抽取。
    """

    __tablename__ = "sales_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)  # docx / pdf / xlsx
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped["Project"] = relationship(back_populates="sales_documents")
    records: Mapped[list["SalesRecord"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class SalesRecord(Base):
    """销售清单行（AI 合成后入库，可由审计师在前端核对修改）。
    字段对应"销售清单"底稿要求：金额、发货/确认时间、数量/单价、产品编号、成本、可直接对应销售费用。
    """

    __tablename__ = "sales_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    document_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sales_documents.id"), nullable=True
    )

    # 业务主标识
    contract_no: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 销售发票与税务 (新增 — 增值税底稿闭环)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(10), default="CNY", nullable=True)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 税率，如 0.13
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 税额
    gross_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 价税合计 (revenue + tax)

    # 数量与金额
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit_price: Mapped[float] = mapped_column(Float, default=0)  # 不含税单价
    revenue_amount: Mapped[float] = mapped_column(Float, default=0)  # 不含税收入金额
    cost_amount: Mapped[float] = mapped_column(Float, default=0)  # 对应成本（用于毛利率分析）

    # 与销售直接对应的费用
    shipping_fee: Mapped[float] = mapped_column(Float, default=0)  # 运费
    customs_fee: Mapped[float] = mapped_column(Float, default=0)  # 报关费
    other_direct_fee: Mapped[float] = mapped_column(Float, default=0)  # 其他直接费用

    # 退换货 / 折扣 / 返利 (新增 — 毛利真实性)
    return_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 退货冲减金额
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 折扣折让
    rebate_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 销售返利

    # 时间
    ship_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    receipt_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, index=True, nullable=True
    )  # 新增: 签收/验收日
    revenue_confirm_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    # 函证状态 (新增 — 审计轨迹闭环)
    confirmation_status: Mapped[Optional[str]] = mapped_column(
        String(20), default="未发函", nullable=True
    )  # 未发函/已发函/已回函/未回函/作废
    confirmation_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confirmation_diff: Mapped[float] = mapped_column(Float, default=0.0)

    # 溯源
    source: Mapped[str] = mapped_column(String(255), nullable=True)  # 来源文档名 / 备注
    confidence: Mapped[float] = mapped_column(Float, default=1.0)  # AI 合成置信度（0-1）
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)  # 人工核对标志

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="sales_records")
    document: Mapped[Optional["SalesDocument"]] = relationship(back_populates="records")

    # P1 性能 (2026-06-19): 客户聚合 + 关联方推断 GROUP BY customer_name 提速
    __table_args__ = (
        Index("ix_sales_project_customer", "project_id", "customer_name"),
        Index("ix_sales_project_ship", "project_id", "ship_date"),
    )


class ContractDocument(Base):
    """收入合同（图片/PDF/扫描件）+ OCR 文本 + 要点 / CAS 14 五步法分析。"""

    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    # 原始信息
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # image/jpeg, image/png, application/pdf
    ocr_engine: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # paddleocr / easyocr / tesseract / manual
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # AI 抽取：基础 7 字段 (JSON 字符串)
    key_points: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # AI 抽取：CAS 14 五步法 (JSON 字符串)
    five_step_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 风险扫描结论
    risk_flags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="contracts")


# ============================================================
#  收发存 / 盘点 / 跌价（成本相关）
# ============================================================


class InventoryMovement(Base):
    """收发存明细表 (Inventory Movement / In-Out-Balance Ledger)。

    一行 = 一个物料 × 一个仓库 × 一个批次（可选）的期间记录。
    支持按月/季度/年导入；如果 ERP 只给汇总数，可只用期初/期末/本期入出。
    """

    __tablename__ = "inventory_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    # 物料基本信息
    material_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    material_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )  # 原材料/在产品/库存商品/低值易耗品
    spec: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # 规格型号
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 计量单位

    # 仓储信息
    warehouse: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)  # 仓库
    batch_no: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )  # 批次号
    inbound_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )  # 该批次入库日

    # 期间口径
    period_end: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 报告期截止日 YYYY-MM-DD
    is_prior_year: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否上年同期数据

    # 期初
    opening_qty: Mapped[float] = mapped_column(Float, default=0.0)
    opening_amount: Mapped[float] = mapped_column(Float, default=0.0)

    # 本期入库 / 出库
    inbound_qty: Mapped[float] = mapped_column(Float, default=0.0)
    inbound_amount: Mapped[float] = mapped_column(Float, default=0.0)
    outbound_qty: Mapped[float] = mapped_column(Float, default=0.0)
    outbound_amount: Mapped[float] = mapped_column(Float, default=0.0)

    # 期末
    ending_qty: Mapped[float] = mapped_column(Float, default=0.0)
    ending_amount: Mapped[float] = mapped_column(Float, default=0.0)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)  # 期末加权平均成本单价

    source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped["Project"] = relationship(back_populates="inventory_movements")


class InventoryCountPlan(Base):
    """存货盘点计划 (Inventory Count Plan)。

    包含时间表 / 人员 / 行业化特殊事项；可由 AI 初稿、用户对话式修改。
    """

    __tablename__ = "inventory_count_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="存货监盘计划")
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    period_end: Mapped[str] = mapped_column(String(20), nullable=False)  # 盘点基准日
    count_date_start: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    count_date_end: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # 行业特点 / 注意事项 / 团队安排 (Markdown 或 JSON 字符串)
    objectives: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 监盘目标
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 监盘范围（仓库列表）
    team: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 监盘小组（JSON）
    procedures: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 监盘程序
    special_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 行业化特殊事项
    risks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 重大风险

    revision_log: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON 数组：每次用户对话修改

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="inventory_count_plans")
    sheets: Mapped[list["InventoryCountSheet"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class InventoryCountSheet(Base):
    """存货盘点用表（一行 = 一个被抽中的盘点物料）。

    生成口径：金额优先 + 阈值覆盖；包含账面数 / 留空的实盘列。
    """

    __tablename__ = "inventory_count_sheets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    plan_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("inventory_count_plans.id"), nullable=True
    )

    material_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    material_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    warehouse: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    batch_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # 账面数
    book_qty: Mapped[float] = mapped_column(Float, default=0.0)
    book_unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    book_amount: Mapped[float] = mapped_column(Float, default=0.0)

    # 实盘数 (审计师在现场填写)
    counted_qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 抽样信息
    sample_tier: Mapped[str] = mapped_column(String(20), default="A")  # A=全盘, B=抽样, C=覆盖性抽
    sample_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    coverage_rank: Mapped[int] = mapped_column(Integer, default=0)  # 排名（金额降序）

    counted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    counted_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    remark: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="inventory_count_sheets")
    plan: Mapped[Optional["InventoryCountPlan"]] = relationship(back_populates="sheets")


class InventoryImpairment(Base):
    """存货跌价 / 库龄分析结果 (Inventory Impairment & Aging)。

    每个物料一行：保存 FIFO 推算的库龄分层、NRV、跌价准备、本年计提/转回。
    """

    __tablename__ = "inventory_impairments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    material_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    material_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    period_end: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # 期末账面
    ending_qty: Mapped[float] = mapped_column(Float, default=0.0)
    book_unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    book_amount: Mapped[float] = mapped_column(Float, default=0.0)

    # 库龄分层金额 (FIFO 推算)
    age_le_90: Mapped[float] = mapped_column(Float, default=0.0)
    age_91_180: Mapped[float] = mapped_column(Float, default=0.0)
    age_181_365: Mapped[float] = mapped_column(Float, default=0.0)
    age_366_730: Mapped[float] = mapped_column(Float, default=0.0)
    age_gt_730: Mapped[float] = mapped_column(Float, default=0.0)
    weighted_avg_age: Mapped[float] = mapped_column(Float, default=0.0)  # 加权平均库龄(天)

    # NRV / 跌价
    nrv_unit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 可变现净值单价
    nrv_source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 销售清单/手工/外部询价
    nrv_amount: Mapped[float] = mapped_column(Float, default=0.0)  # = ending_qty * nrv_unit_price
    estimated_sell_cost: Mapped[float] = mapped_column(Float, default=0.0)  # 销售费用 + 税费
    impairment_current: Mapped[float] = mapped_column(
        Float, default=0.0
    )  # 本期末应计提跌价 (按物料)

    # 期初跌价 / 转回 / 本年计提
    impairment_opening: Mapped[float] = mapped_column(
        Float, default=0.0
    )  # 上年末（即本年期初）跌价
    impairment_reversal: Mapped[float] = mapped_column(Float, default=0.0)  # 本期跌价转回
    impairment_provision: Mapped[float] = mapped_column(Float, default=0.0)  # 本期新增计提
    net_impairment_change: Mapped[float] = mapped_column(Float, default=0.0)  # provision - reversal

    # 转回拆分（CAS 1 第 21 条）：已售出部分应"转销营业成本"，仍在库部分才"转回资产减值损失"
    reversal_to_cogs: Mapped[float] = mapped_column(Float, default=0.0)
    reversal_to_loss: Mapped[float] = mapped_column(Float, default=0.0)

    method: Mapped[str] = mapped_column(String(20), default="aging")  # aging / nrv / combined
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped["Project"] = relationship(back_populates="inventory_impairments")


class InventoryCodeMapping(Base):
    """物料编码跨年映射 (旧→新)。

    场景：上年物料编码 "OLD-A-001" 在本年度 ERP 升级后改成 "NEW-XYZ"，
    跌价转回时按上年编码找不到 → 损益表少一笔。该表用于在 compute 时
    把上年 prior_impairments 的 key 翻译为本年编码。
    """

    __tablename__ = "inventory_code_mappings"
    __table_args__ = (
        UniqueConstraint("project_id", "old_code", name="uq_code_mapping_project_old"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    old_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    new_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


# ============================================================
#  函证 (External Confirmation) — 银行/客户/供应商/其他往来
# ============================================================


# ---- 函证对象类型 ------------------------------------------------------

PARTY_TYPE_BANK = "bank"  # 银行询证函
PARTY_TYPE_CUSTOMER = "customer"  # 客户询证函（应收账款）
PARTY_TYPE_SUPPLIER = "supplier"  # 供应商询证函（应付账款）
PARTY_TYPE_OTHER_RECEIVABLE = "other_recv"  # 其他应收款
PARTY_TYPE_OTHER_PAYABLE = "other_pay"  # 其他应付款
PARTY_TYPE_LOAN = "loan"  # 贷款 / 借款
PARTY_TYPE_INVESTMENT = "investment"  # 投资 / 投资款
PARTY_TYPE_REGULATOR = "regulator"  # 监管机构询证（如保税区/海关）
PARTY_TYPE_LITIGATION = "litigation"  # 诉讼对手方
PARTY_TYPE_OTHER = "other"  # 其他自定义


PARTY_TYPE_LABELS: dict[str, str] = {
    PARTY_TYPE_BANK: "银行",
    PARTY_TYPE_CUSTOMER: "客户",
    PARTY_TYPE_SUPPLIER: "供应商",
    PARTY_TYPE_OTHER_RECEIVABLE: "其他应收款对方",
    PARTY_TYPE_OTHER_PAYABLE: "其他应付款对方",
    PARTY_TYPE_LOAN: "贷款机构",
    PARTY_TYPE_INVESTMENT: "投资 / 被投资单位",
    PARTY_TYPE_REGULATOR: "监管机构",
    PARTY_TYPE_LITIGATION: "诉讼对手方",
    PARTY_TYPE_OTHER: "其他",
}


# ---- 函证状态机 -------------------------------------------------------

# 选样阶段
ITEM_STATUS_DRAFT = "draft"  # 草稿（统计表生成后未确认）
ITEM_STATUS_CONFIRMED = "confirmed"  # 已确定发函（进入发函阶段）
ITEM_STATUS_SENT = "sent"  # 已发函（发函日期已锁定）
ITEM_STATUS_RESPONDED = "responded"  # 已回函
ITEM_STATUS_PARTIAL = "partial"  # 部分相符回函
ITEM_STATUS_NO_REPLY = "no_reply"  # 未回函（已发函但到期未回）
ITEM_STATUS_REJECTED = "rejected"  # 拒函
ITEM_STATUS_MISMATCH = "mismatch"  # 不符
ITEM_STATUS_VOIDED = "voided"  # 作废

ITEM_STATUS_LABELS: dict[str, str] = {
    ITEM_STATUS_DRAFT: "草稿",
    ITEM_STATUS_CONFIRMED: "已确定发函",
    ITEM_STATUS_SENT: "已发函",
    ITEM_STATUS_RESPONDED: "已回函（相符）",
    ITEM_STATUS_PARTIAL: "部分相符",
    ITEM_STATUS_MISMATCH: "不符",
    ITEM_STATUS_NO_REPLY: "未回函",
    ITEM_STATUS_REJECTED: "拒函",
    ITEM_STATUS_VOIDED: "作废",
}


# 回函差异状态
RESPONSE_MATCH = "match"  # 相符
RESPONSE_PARTIAL = "partial"  # 部分相符
RESPONSE_MISMATCH = "mismatch"  # 不符
RESPONSE_REJECT = "reject"  # 拒函
RESPONSE_UNCLEAR = "unclear"  # 不清楚 / 模糊

RESPONSE_STATUS_LABELS: dict[str, str] = {
    RESPONSE_MATCH: "相符",
    RESPONSE_PARTIAL: "部分相符",
    RESPONSE_MISMATCH: "不符",
    RESPONSE_REJECT: "拒函",
    RESPONSE_UNCLEAR: "待人工核对",
}


class ConfirmationCase(Base):
    """函证案卷（一份函证统计表 / 一个项目 × 一个期间 × 一个发函批次）。

    一旦「确定发函」(confirm) 后即锁定：发函日期、内容快照、金额快照不可再修改。
    避免后续账套数据更新导致多版本混乱——这是审计函证管理的核心约束。
    """

    __tablename__ = "confirmation_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    case_name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_end: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 报告期截止日 YYYY-MM-DD
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 是否锁定：true 之后不能修改 items / letter 的金额与发函日期
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lock_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 生成 / 审计
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    generated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # 关联
    project: Mapped["Project"] = relationship(back_populates="confirmation_cases")
    items: Mapped[list["ConfirmationItem"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    letters: Mapped[list["ConfirmationLetter"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class ConfirmationItem(Base):
    """函证对象（一份统计表中的一行 = 一个被函证方）。

    函证对象可对应多个函证项 (subject_matters JSON)，如银行询证函同时
    函证『存款余额+贷款+票据+担保』等；客户/供应商询证函同时函证
    『余额+本期发生额+票据背书+关键合同条款』。

    一旦所在 case 锁定，本行的金额快照由 ConfirmationLetter.amount_snapshot
    固化；本表自身的 book_balance 仅作历史参考。
    """

    __tablename__ = "confirmation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("confirmation_cases.id"), nullable=False, index=True
    )

    # 函证方
    party_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    party_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    party_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )  # 银行账号/客户编号
    contact_person: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contact_info: Mapped[Optional[str]] = mapped_column(
        String(300), nullable=True
    )  # 地址/电话/邮箱

    # 我方核算信息
    account_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    account_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    book_balance: Mapped[float] = mapped_column(Float, default=0.0)  # 账面余额
    book_balance_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # 函证项 (JSON 数组)
    #   银行:   ["存款余额","贷款余额","银行承兑汇票","保函/担保","信用证","委托贷款"...]
    #   客户:   ["应收账款余额","本期销售额","本期回款额","已背书票据","关键合同条款","在执行订单"...]
    #   供应商: ["应付账款余额","本期采购额","本期付款额","已背书票据","关键合同条款"...]
    subject_matters: Mapped[str] = mapped_column(Text, default="[]")  # JSON 字符串

    # 金额汇总（多函证项时的合计）— 用 lock 时固化
    total_confirm_amount: Mapped[float] = mapped_column(
        Float, default=0.0
    )  # 发函时需要对方确认的合计

    # 快照字段 (锁定时固化, 后续不可改)
    subject_matters_snapshot: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # send_letter 时固化
    total_confirm_amount_snapshot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    book_balance_snapshot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 选样
    selection_method: Mapped[str] = mapped_column(String(30), default="auto")  # auto/manual
    selection_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    importance: Mapped[str] = mapped_column(String(10), default="B")  # A=必发 / B=抽样 / C=补充

    # 乐观锁版本号
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # 状态
    status: Mapped[str] = mapped_column(String(20), default=ITEM_STATUS_DRAFT, index=True)
    # 关联发函/回函 ID — 用普通外键字段, 不在 Item 端反向配对 relationship, 避免循环
    sent_letter_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("confirmation_letters.id"), nullable=True, index=True
    )
    response_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("confirmation_responses.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    case: Mapped["ConfirmationCase"] = relationship(back_populates="items")


class ConfirmationLetter(Base):
    """发函记录 — 一旦发出，发函日期 + 内容快照 + 金额快照全部锁定。

    `content_snapshot` / `amount_snapshot` 是 JSON 字符串，保存发出那一刻的
    完整内容；后续账套数据变化不影响已发函。
    """

    __tablename__ = "confirmation_letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("confirmation_cases.id"), nullable=False, index=True
    )
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("confirmation_items.id"), nullable=False, index=True
    )

    letter_no: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    letter_type: Mapped[str] = mapped_column(String(30), nullable=False)  # 同 party_type
    template_id: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # bank_official / customer_std / supplier_std ...
    seq: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )  # 同 case+item 下的发函序号

    # 锁定字段 (发函日期确定后不能改)
    sent_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    sent_method: Mapped[str] = mapped_column(
        String(30), default="邮寄"
    )  # 邮寄/电子邮件/跟函/电邮+邮寄
    sent_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sender_firm: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    recipient: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    recipient_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    courier_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # 快递单号

    # 锁定快照 (JSON)
    content_snapshot: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # 函证正文(文本/Markdown)
    amount_snapshot: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # {subject: amount, ...} 锁定金额
    file_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )  # 生成的 docx/pdf 路径
    file_format: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # docx/pdf

    # 状态
    letter_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    #   draft / sent / recalled / voided

    expected_reply_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    case: Mapped["ConfirmationCase"] = relationship(back_populates="letters")
    item: Mapped["ConfirmationItem"] = relationship(foreign_keys=[item_id])
    # 不在 Letter 端反向配对 ConfirmationResponse, 避免 Item->Letter->Response 三向循环
    # ConfirmationResponse.letter_id 是普通外键, 通过 letter_id 反查


class ConfirmationResponse(Base):
    """回函记录 — 与发函一一对应。

    收到回函时录入：可手工录入，也可上传回函照片自动 OCR + AI 解析。
    """

    __tablename__ = "confirmation_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    letter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("confirmation_letters.id"), nullable=False, unique=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 历次覆写 +1

    received_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    response_method: Mapped[str] = mapped_column(
        String(30), default="纸质原件"
    )  # 纸质原件/扫描件/电邮/传真
    response_status: Mapped[str] = mapped_column(String(20), default=RESPONSE_UNCLEAR, index=True)

    # 回函核心结论
    amount_confirmed: Mapped[float] = mapped_column(Float, default=0.0)  # 对方确认金额
    amount_difference: Mapped[float] = mapped_column(Float, default=0.0)  # 差异 = 对方 - 我方
    difference_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 多函证项的明细 (JSON: {subject: {confirmed, difference, note}})
    subjects_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # OCR / AI
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # OCR 原始文本
    ai_extracted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AI 抽取结果 JSON
    is_manually_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    auditor_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    letter: Mapped["ConfirmationLetter"] = relationship(foreign_keys=[letter_id])
    photos: Mapped[list["ConfirmationResponsePhoto"]] = relationship(
        back_populates="response", cascade="all, delete-orphan"
    )


class ConfirmationResponsePhoto(Base):
    """回函照片 — 纸质回函拍照后上传 → OCR + AI 解析 → 回填到 ConfirmationResponse。"""

    __tablename__ = "confirmation_response_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    response_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("confirmation_responses.id"), nullable=False, index=True
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    ocr_engine: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ocr_text: Mapped[str] = mapped_column(Text, default="")

    # AI 解析结果
    parsed_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    match_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending/parsed/matched/failed
    matched_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    matched_subjects: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 数组

    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    response: Mapped["ConfirmationResponse"] = relationship(back_populates="photos")


# 回到原 InventoryCountPhoto 段


class InventoryCountPhoto(Base):
    """盘点照片：现场盘点用表拍照原件 + OCR + AI 整理结果。

    审计师把盘点完毕、已填写实盘数的纸质表拍照上传，系统 OCR + AI 解析手写
    "实盘数量" 列，回填到 InventoryCountSheet.counted_qty。每张照片可能覆盖
    多行（解析结果以 JSON 数组保存在 matched_rows）。
    """

    __tablename__ = "inventory_count_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    plan_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("inventory_count_plans.id"), nullable=True
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # image/jpeg, image/png, application/pdf
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)  # 存盘相对路径
    ocr_engine: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # paddleocr / easyocr / tesseract / manual
    ocr_text: Mapped[str] = mapped_column(Text, default="")

    # AI 解析结果 (JSON 字符串)
    parsed_rows: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # [{material_code, counted_qty, ...}]
    matched_count: Mapped[int] = mapped_column(Integer, default=0)  # 成功回填的行数
    unmatched_count: Mapped[int] = mapped_column(Integer, default=0)  # 未匹配的行数
    counted_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    counted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ============================================================
#  法律法规库 (Regulations Library)
#  自动抓取证监会 / 财政部 / 国家税务总局 / 外管局 / 人民银行
#  的政策、准则、规章、问答口径，并提供本地搜索 + 收藏。
# ============================================================


class Regulation(Base):
    """法律法规 / 政策文件 / 准则解释。

    覆盖范围 (source 字段):
      - CSRC   证监会 (含发行部、上市部、稽查局等的规范性文件)
      - MOF    财政部 / 会计司 (CAS 准则、应用指南、问答、解释)
      - STA    国家税务总局 (增值税/所得税/印花税公告与问答)
      - SAFE   国家外汇管理局
      - PBOC   中国人民银行
      - LOCAL  地方财政局 / 地方税务局 (按 issuing_authority 标识省市)
      - OTHER  其他 (行业自律组织、注协等)
    """

    __tablename__ = "regulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 来源与分类
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    issuing_authority: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    # 法律 / 行政法规 / 部门规章 / 规范性文件 / 准则 / 应用指南 / 问答 / 公告 / 通知

    # 文件元信息
    title: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    document_no: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    publish_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    effective_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    expire_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_effective: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # 正文 / 摘要 / 关键词
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    full_text: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 逗号分隔
    attachments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 数组

    # 内容指纹（去重）
    content_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

    # 元数据
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    favorites: Mapped[list["RegulationFavorite"]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan"
    )


class RegulationFavorite(Base):
    """法规收藏 — 项目级 / 全局 (project_id=NULL)。"""

    __tablename__ = "regulation_favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    regulation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("regulations.id"), nullable=False, index=True
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    tag: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    regulation: Mapped["Regulation"] = relationship(back_populates="favorites")


# ============================================================
#  自助知识库 (Knowledge Base)
#  用户上传实务书籍 / 笔记 / 案例集，系统切块 + 向量化，
#  在生成审计说明时检索相似案例供 AI 参考。
# ============================================================


class KnowledgeBook(Base):
    """知识库书籍 / 文档 (一本书 = 一份资料)。

    支持 PDF / EPUB / DOCX / Markdown / TXT。
    """

    __tablename__ = "knowledge_books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    author: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    isbn: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 文件信息
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf/epub/docx/txt/md
    file_size: Mapped[int] = mapped_column(Integer, default=0)

    # 分类标签
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    # 审计实务 / 会计准则 / 税务实务 / 内控 / 案例集 / 行业研究 / 其他
    tags: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 逗号分隔
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 索引状态
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending / parsing / indexing / ready / failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    total_chars: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 嵌入元数据
    embedding_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # TF-IDF 词表状态 (JSON: vocab + idf), 用于跨书共享词表
    tfidf_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # P0 (2026-06-19): 多租户隔离 — 知识库可能含客户内部资料 / 风险点备忘,
    # 之前任意登录用户可读别所上传的, 现按 firm_id 过滤
    firm_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base):
    """知识库文本片段 (一本书会被切成 N 个 chunk)。

    embedding 字段以 JSON 字符串保存浮点数组 (维度由 embedding_dim 决定)。
    SQLite 上没有原生向量类型，检索时一次性读入内存计算 cosine — 对万级 chunk
    场景足够；后续如有需要可换 FAISS / sqlite-vss。
    """

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("knowledge_books.id"), nullable=False, index=True
    )

    # 定位信息
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chapter: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    section: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 文本内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 向量 (JSON 字符串)
    embedding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    book: Mapped["KnowledgeBook"] = relationship(back_populates="chunks")


class KnowledgeRetrievalLog(Base):
    """知识库检索历史 — 便于回溯 "这条审计说明依据了哪些案例"。"""

    __tablename__ = "knowledge_retrieval_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 触发位置：account_code/template_type/risk_id 等

    top_chunk_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 数组
    top_scores: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 数组
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


# ============================================================
#  舆情跟踪 (Sentiment Tracking) — 每天舆情 + 季报跟踪报告
#  v0.2 新增模块：8 张新表 + 5 组状态机常量
# ============================================================


# ---- 舆情事件严重度 ----------------------------------------------------
SENTIMENT_SEVERITY_INFO = "info"  # 一般资讯（参考性）
SENTIMENT_SEVERITY_NOTICE = "notice"  # 关注（需审计师过目）
SENTIMENT_SEVERITY_WARN = "warn"  # 警示（潜在风险）
SENTIMENT_SEVERITY_CRITICAL = "critical"  # 重大风险（领导必看）

SENTIMENT_SEVERITY_LABELS: dict[str, str] = {
    SENTIMENT_SEVERITY_INFO: "一般",
    SENTIMENT_SEVERITY_NOTICE: "关注",
    SENTIMENT_SEVERITY_WARN: "警示",
    SENTIMENT_SEVERITY_CRITICAL: "重大",
}


# ---- 舆情事件来源类型 --------------------------------------------------
SENTIMENT_SOURCE_REGULATOR = "regulator"  # 监管/交易所/工商
SENTIMENT_SOURCE_NEWS = "news"  # 财经新闻媒体
SENTIMENT_SOURCE_ANNOUNCE = "announce"  # 公司公告/招股书
SENTIMENT_SOURCE_RSS = "rss"  # RSS 订阅
SENTIMENT_SOURCE_PAID_API = "paid_api"  # 付费搜索 API
SENTIMENT_SOURCE_MANUAL = "manual"  # 审计师手工录入
SENTIMENT_SOURCE_OTHER = "other"

SENTIMENT_SOURCE_TYPE_LABELS: dict[str, str] = {
    SENTIMENT_SOURCE_REGULATOR: "监管/公告",
    SENTIMENT_SOURCE_NEWS: "新闻媒体",
    SENTIMENT_SOURCE_ANNOUNCE: "公司公告",
    SENTIMENT_SOURCE_RSS: "RSS 订阅",
    SENTIMENT_SOURCE_PAID_API: "付费 API",
    SENTIMENT_SOURCE_MANUAL: "手工录入",
    SENTIMENT_SOURCE_OTHER: "其他",
}


# ---- 事件审核状态 -------------------------------------------------------
SENTIMENT_EVENT_STATUS_UNREAD = "unread"  # 未读
SENTIMENT_EVENT_STATUS_READ = "read"  # 已读
SENTIMENT_EVENT_STATUS_IGNORED = "ignored"  # 已忽略
SENTIMENT_EVENT_STATUS_ATTACHED = "attached"  # 已挂入某简报

SENTIMENT_EVENT_STATUS_LABELS: dict[str, str] = {
    SENTIMENT_EVENT_STATUS_UNREAD: "未读",
    SENTIMENT_EVENT_STATUS_READ: "已读",
    SENTIMENT_EVENT_STATUS_IGNORED: "已忽略",
    SENTIMENT_EVENT_STATUS_ATTACHED: "已入简报",
}


# ---- 信源注册表状态 ----------------------------------------------------
SENTIMENT_SOURCE_STATUS_SUCCESS = "success"
SENTIMENT_SOURCE_STATUS_PARTIAL = "partial"
SENTIMENT_SOURCE_STATUS_FAILED = "failed"
SENTIMENT_SOURCE_STATUS_SKIPPED = "skipped"  # 付费源无 key 时
SENTIMENT_SOURCE_STATUS_DISABLED = "disabled"

SENTIMENT_SOURCE_STATUS_LABELS: dict[str, str] = {
    SENTIMENT_SOURCE_STATUS_SUCCESS: "成功",
    SENTIMENT_SOURCE_STATUS_PARTIAL: "部分成功",
    SENTIMENT_SOURCE_STATUS_FAILED: "失败",
    SENTIMENT_SOURCE_STATUS_SKIPPED: "跳过（无 key）",
    SENTIMENT_SOURCE_STATUS_DISABLED: "已停用",
}


# ---- 简报/报告审阅状态机 ------------------------------------------------
SENTIMENT_DOC_STATUS_DRAFT = "draft"  # 草稿（AI 跑完待人工核对）
SENTIMENT_DOC_STATUS_REVIEW = "review"  # 已提交审阅
SENTIMENT_DOC_STATUS_APPROVED = "approved"  # 已批准
SENTIMENT_DOC_STATUS_REJECTED = "rejected"  # 已驳回
SENTIMENT_DOC_STATUS_FROZEN = "frozen"  # 锁定快照（与 is_locked 联动）

SENTIMENT_DOC_STATUS_LABELS: dict[str, str] = {
    SENTIMENT_DOC_STATUS_DRAFT: "草稿",
    SENTIMENT_DOC_STATUS_REVIEW: "审阅中",
    SENTIMENT_DOC_STATUS_APPROVED: "已批准",
    SENTIMENT_DOC_STATUS_REJECTED: "已驳回",
    SENTIMENT_DOC_STATUS_FROZEN: "已锁定",
}

# 合法状态流转图：key = 当前状态，value = 允许的下一状态集合
SENTIMENT_DOC_STATUS_TRANSITIONS: dict[str, set[str]] = {
    SENTIMENT_DOC_STATUS_DRAFT: {
        SENTIMENT_DOC_STATUS_REVIEW,
        SENTIMENT_DOC_STATUS_FROZEN,
        SENTIMENT_DOC_STATUS_REJECTED,
    },
    SENTIMENT_DOC_STATUS_REVIEW: {
        SENTIMENT_DOC_STATUS_APPROVED,
        SENTIMENT_DOC_STATUS_REJECTED,
        SENTIMENT_DOC_STATUS_DRAFT,
    },
    SENTIMENT_DOC_STATUS_APPROVED: {SENTIMENT_DOC_STATUS_FROZEN},
    SENTIMENT_DOC_STATUS_REJECTED: {SENTIMENT_DOC_STATUS_DRAFT, SENTIMENT_DOC_STATUS_REVIEW},
    SENTIMENT_DOC_STATUS_FROZEN: set(),  # 冻结后任何状态都不允许直转
}


# ---- 季度报告期次类型 ---------------------------------------------------
SENTIMENT_PERIOD_TYPE_Q1 = "Q1"
SENTIMENT_PERIOD_TYPE_H1 = "H1"  # 半年报
SENTIMENT_PERIOD_TYPE_Q3 = "Q3"
SENTIMENT_PERIOD_TYPE_ANNUAL = "ANNUAL"

SENTIMENT_PERIOD_TYPE_LABELS: dict[str, str] = {
    SENTIMENT_PERIOD_TYPE_Q1: "第一季度",
    SENTIMENT_PERIOD_TYPE_H1: "半年度",
    SENTIMENT_PERIOD_TYPE_Q3: "第三季度",
    SENTIMENT_PERIOD_TYPE_ANNUAL: "年度",
}


# ---- 站内通知类型 ------------------------------------------------------
SENTIMENT_NOTIFY_NEW_EVENT = "new_event"
SENTIMENT_NOTIFY_BRIEFING_READY = "briefing_ready"
SENTIMENT_NOTIFY_BRIEFING_REJECTED = "briefing_rejected"
SENTIMENT_NOTIFY_REPORT_READY = "report_ready"
SENTIMENT_NOTIFY_REPORT_APPROVED = "report_approved"
SENTIMENT_NOTIFY_REPORT_REJECTED = "report_rejected"
SENTIMENT_NOTIFY_SCAN_FAILED = "scan_failed"

SENTIMENT_NOTIFY_TYPE_LABELS: dict[str, str] = {
    SENTIMENT_NOTIFY_NEW_EVENT: "新舆情事件",
    SENTIMENT_NOTIFY_BRIEFING_READY: "简报待审阅",
    SENTIMENT_NOTIFY_BRIEFING_REJECTED: "简报已驳回",
    SENTIMENT_NOTIFY_REPORT_READY: "季报待审阅",
    SENTIMENT_NOTIFY_REPORT_APPROVED: "季报已批准",
    SENTIMENT_NOTIFY_REPORT_REJECTED: "季报已驳回",
    SENTIMENT_NOTIFY_SCAN_FAILED: "扫描任务异常",
}


# ---- 异常类 -------------------------------------------------------------
class SentimentError(Exception):
    """舆情模块基础异常。"""


class PaidSourceMissingKey(SentimentError):
    """付费信源未配置 API Key。"""


class NoLlmConfigured(SentimentError):
    """未配置任何 LLM（DeepSeek / MiniMax 都没有 API Key）。"""


class IllegalStateTransition(SentimentError):
    """简报/报告状态机非法流转。"""


class VerificationFailed(SentimentError):
    """简报/报告校验未通过，禁止进入审阅流。"""


# ---- ORM 模型 -----------------------------------------------------------


class SentimentSubject(Base):
    """舆情搜索主体别名 — 一个客户（Project）可配置多个搜索词。

    alias_type 取值: company / brand / product / person / domain
    match_mode 取值: exact / contains / regex
    weight 用于多别名同时出现时取最高权重。
    """

    __tablename__ = "sentiment_subjects"
    __table_args__ = (
        UniqueConstraint("project_id", "alias_value", name="uq_sentiment_subjects_project_alias"),
        Index("ix_sentiment_subjects_project", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    alias_type: Mapped[str] = mapped_column(String(20), default="company", nullable=False)
    alias_value: Mapped[str] = mapped_column(String(200), nullable=False)
    match_mode: Mapped[str] = mapped_column(String(10), default="contains", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="sentiment_subjects")


class SentimentSource(Base):
    """舆情信源注册表（免费/付费统一）。

    provider_type 取值: free_rss / free_scrape / paid_api / manual
    is_paid=True 时 scheduler 会检查 api_key_ref 对应的 settings 字段非空才执行。
    """

    __tablename__ = "sentiment_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    provider_type: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    api_key_ref: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 例: TAVILY_API_KEY
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 抓取参数 JSON
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    last_run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class SentimentEvent(Base):
    """舆情原始事件 — 去重粒度。

    content_hash = SHA256(source_code|title|url|publish_date)，唯一索引（防重抓）。
    """

    __tablename__ = "sentiment_events"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_sentiment_events_content_hash"),
        Index("ix_sentiment_events_project_date", "project_id", "publish_date"),
        Index(
            "ix_sentiment_events_project_severity_status", "project_id", "severity", "review_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    source_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sentiment_sources.id"), nullable=True
    )
    source_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)

    event_kind: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True, index=True
    )  # 处罚/问询/公告/...
    severity: Mapped[str] = mapped_column(
        String(20), default=SENTIMENT_SEVERITY_INFO, nullable=False, index=True
    )
    review_status: Mapped[str] = mapped_column(
        String(20), default=SENTIMENT_EVENT_STATUS_UNREAD, nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    publish_date: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )  # YYYY-MM-DD
    content_text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 限 8K
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    matched_alias: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    raw_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 原始 JSON

    attached_briefing_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sentiment_daily_briefings.id"), nullable=True, index=True
    )

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="sentiment_events")
    source: Mapped[Optional["SentimentSource"]] = relationship()
    attached_briefing: Mapped[Optional["SentimentDailyBriefing"]] = relationship(
        foreign_keys=[attached_briefing_id]
    )


class SentimentDailyBriefing(Base):
    """每日舆情简报 — 锁 + 快照。

    参照 ConfirmationCase.is_locked 模式：is_locked=True 后 ai_summary/event_snapshot_json 不可改；
    修订必须新建 SentimentDailyBriefingRevision。
    状态机：draft → review → approved/frozen，rejected → draft/review。
    """

    __tablename__ = "sentiment_daily_briefings"
    __table_args__ = (
        UniqueConstraint("project_id", "briefing_date", name="uq_sentiment_briefings_project_date"),
        Index("ix_sentiment_briefings_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    briefing_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # AI 生成内容快照（锁定后不可改）
    event_snapshot_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_assessment_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audit_verification_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 锁定
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lock_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Word 文档
    word_report_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    word_report_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # 校验
    verification_failed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    verification_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 审阅流
    status: Mapped[str] = mapped_column(
        String(20), default=SENTIMENT_DOC_STATUS_DRAFT, nullable=False, index=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="daily_briefings")
    revisions: Mapped[list["SentimentDailyBriefingRevision"]] = relationship(
        back_populates="briefing",
        cascade="all, delete-orphan",
        order_by="SentimentDailyBriefingRevision.version_no",
    )


class SentimentDailyBriefingRevision(Base):
    """每日简报版本历史 — 任何对已锁简报的修订都新建一行。"""

    __tablename__ = "sentiment_daily_briefing_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    briefing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sentiment_daily_briefings.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    briefing: Mapped["SentimentDailyBriefing"] = relationship(back_populates="revisions")


class SentimentQuarterlyReport(Base):
    """季度跟踪报告 — 季报发布后触发，结合窗口期简报集 + 季报数据生成。"""

    __tablename__ = "sentiment_quarterly_reports"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "period_type", "fiscal_year", name="uq_sentiment_quarterly_project_period"
        ),
        Index("ix_sentiment_quarterly_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    period_type: Mapped[str] = mapped_column(String(10), nullable=False)  # Q1/H1/Q3/ANNUAL
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # 触发
    trigger_type: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # manual / financials_uploaded / scheduled
    trigger_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sentiment_events.id"), nullable=True
    )

    # 输入窗口
    daily_briefing_window_start: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    daily_briefing_window_end: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    referenced_briefing_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    referenced_event_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 财务输入
    financial_input_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    financial_input_source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # manual / uploaded_pdf / uploaded_excel
    financial_input_verified_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    financial_input_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # AI 产物
    ai_report_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_report_verification_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 锁定快照（与 ConfirmationLetter.content_snapshot 同模式）
    content_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    amount_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Word 文档
    word_report_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    word_report_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # 校验
    verification_failed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    verification_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 锁定 + 审阅
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lock_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), default=SENTIMENT_DOC_STATUS_DRAFT, nullable=False, index=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="quarterly_reports")
    revisions: Mapped[list["SentimentQuarterlyReportRevision"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="SentimentQuarterlyReportRevision.version_no",
    )


class SentimentQuarterlyReportRevision(Base):
    """季度报告版本历史。"""

    __tablename__ = "sentiment_quarterly_report_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sentiment_quarterly_reports.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    amount_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    report: Mapped["SentimentQuarterlyReport"] = relationship(back_populates="revisions")


class SentimentNotification(Base):
    """站内通知 — 驱动红点。"""

    __tablename__ = "sentiment_notifications"
    __table_args__ = (Index("ix_sentiment_notifications_project_read", "project_id", "is_read"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    link_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


# ============================================================
#  项目组管理 (Team Management) — 人员 / 计划 / 会议 / 日报 / 卡点 / 建议
# ============================================================


# ---- 人员级别 -------------------------------------------------------

MEMBER_LEVEL_LEAD = "lead"  # 项目负责人 / 合伙人
MEMBER_LEVEL_SENIOR_MANAGER = "senior_manager"  # 高级经理
MEMBER_LEVEL_MANAGER = "manager"  # 经理
MEMBER_LEVEL_SENIOR_AUDITOR = "senior_auditor"  # 高级审计员
MEMBER_LEVEL_AUDITOR = "auditor"  # 审计员
MEMBER_LEVEL_INTERN = "intern"  # 实习生

MEMBER_LEVEL_LABELS: dict[str, str] = {
    MEMBER_LEVEL_LEAD: "项目负责人",
    MEMBER_LEVEL_SENIOR_MANAGER: "高级经理",
    MEMBER_LEVEL_MANAGER: "经理",
    MEMBER_LEVEL_SENIOR_AUDITOR: "高级审计员",
    MEMBER_LEVEL_AUDITOR: "审计员",
    MEMBER_LEVEL_INTERN: "实习生",
}

MEMBER_LEVEL_ORDER: dict[str, int] = {
    MEMBER_LEVEL_INTERN: 0,
    MEMBER_LEVEL_AUDITOR: 1,
    MEMBER_LEVEL_SENIOR_AUDITOR: 2,
    MEMBER_LEVEL_MANAGER: 3,
    MEMBER_LEVEL_SENIOR_MANAGER: 4,
    MEMBER_LEVEL_LEAD: 5,
}

# 人员状态
MEMBER_STATUS_ACTIVE = "active"
MEMBER_STATUS_INACTIVE = "inactive"
MEMBER_STATUS_LABELS: dict[str, str] = {
    MEMBER_STATUS_ACTIVE: "在职",
    MEMBER_STATUS_INACTIVE: "离职/暂离",
}

# ---- 任务状态 -------------------------------------------------------

TASK_STATUS_PENDING = "pending"  # 待办
TASK_STATUS_IN_PROGRESS = "in_progress"  # 进行中
TASK_STATUS_BLOCKED = "blocked"  # 卡点阻塞
TASK_STATUS_DONE = "done"  # 完成
TASK_STATUS_CANCELLED = "cancelled"  # 取消

TASK_STATUS_LABELS: dict[str, str] = {
    TASK_STATUS_PENDING: "待办",
    TASK_STATUS_IN_PROGRESS: "进行中",
    TASK_STATUS_BLOCKED: "阻塞",
    TASK_STATUS_DONE: "已完成",
    TASK_STATUS_CANCELLED: "已取消",
}

# 任务优先级
TASK_PRIORITY_HIGH = "high"
TASK_PRIORITY_MEDIUM = "medium"
TASK_PRIORITY_LOW = "low"
TASK_PRIORITY_LABELS: dict[str, str] = {
    TASK_PRIORITY_HIGH: "高",
    TASK_PRIORITY_MEDIUM: "中",
    TASK_PRIORITY_LOW: "低",
}

# 计划状态
WORK_PLAN_STATUS_DRAFT = "draft"  # 草稿（AI 已生成但未确认）
WORK_PLAN_STATUS_ACTIVE = "active"  # 进行中
WORK_PLAN_STATUS_COMPLETED = "completed"  # 已完成
WORK_PLAN_STATUS_ARCHIVED = "archived"  # 归档

WORK_PLAN_STATUS_LABELS: dict[str, str] = {
    WORK_PLAN_STATUS_DRAFT: "草稿",
    WORK_PLAN_STATUS_ACTIVE: "执行中",
    WORK_PLAN_STATUS_COMPLETED: "已完成",
    WORK_PLAN_STATUS_ARCHIVED: "已归档",
}

# ---- 会议 ----------------------------------------------------------

MEETING_TYPE_DAILY = "daily"  # 站会
MEETING_TYPE_WEEKLY = "weekly"  # 周会
MEETING_TYPE_KICKOFF = "kickoff"  # 启动会
MEETING_TYPE_REVIEW = "review"  # 复核会
MEETING_TYPE_ADHOC = "adhoc"  # 临时

MEETING_TYPE_LABELS: dict[str, str] = {
    MEETING_TYPE_DAILY: "站会",
    MEETING_TYPE_WEEKLY: "周会",
    MEETING_TYPE_KICKOFF: "启动会",
    MEETING_TYPE_REVIEW: "复核会",
    MEETING_TYPE_ADHOC: "临时会议",
}

MEETING_STATUS_SCHEDULED = "scheduled"
MEETING_STATUS_ONGOING = "ongoing"
MEETING_STATUS_COMPLETED = "completed"
MEETING_STATUS_CANCELLED = "cancelled"
MEETING_STATUS_LABELS: dict[str, str] = {
    MEETING_STATUS_SCHEDULED: "已排期",
    MEETING_STATUS_ONGOING: "进行中",
    MEETING_STATUS_COMPLETED: "已结束",
    MEETING_STATUS_CANCELLED: "已取消",
}

# ---- 卡点 ----------------------------------------------------------

BLOCKER_SEVERITY_LOW = "low"
BLOCKER_SEVERITY_MEDIUM = "medium"
BLOCKER_SEVERITY_HIGH = "high"
BLOCKER_SEVERITY_CRITICAL = "critical"
BLOCKER_SEVERITY_LABELS: dict[str, str] = {
    BLOCKER_SEVERITY_LOW: "低",
    BLOCKER_SEVERITY_MEDIUM: "中",
    BLOCKER_SEVERITY_HIGH: "高",
    BLOCKER_SEVERITY_CRITICAL: "紧急",
}

BLOCKER_STATUS_OPEN = "open"
BLOCKER_STATUS_IN_PROGRESS = "in_progress"
BLOCKER_STATUS_RESOLVED = "resolved"
BLOCKER_STATUS_ESCALATED = "escalated"
BLOCKER_STATUS_LABELS: dict[str, str] = {
    BLOCKER_STATUS_OPEN: "待处理",
    BLOCKER_STATUS_IN_PROGRESS: "处理中",
    BLOCKER_STATUS_RESOLVED: "已解决",
    BLOCKER_STATUS_ESCALATED: "已升级",
}

# ---- 项目内角色 ---------------------------------------------------

PROJECT_ROLE_LEAD = "lead"
PROJECT_ROLE_DEPUTY = "deputy"
PROJECT_ROLE_REVIEWER = "reviewer"
PROJECT_ROLE_MEMBER = "member"
PROJECT_ROLE_LABELS: dict[str, str] = {
    PROJECT_ROLE_LEAD: "项目负责人",
    PROJECT_ROLE_DEPUTY: "副负责人",
    PROJECT_ROLE_REVIEWER: "复核人",
    PROJECT_ROLE_MEMBER: "组员",
}


# ---- 数据导入事件（用于触发 AI 计划生成） -------------------------

IMPORT_KIND_ACCOUNT_BALANCES = "account_balances"
IMPORT_KIND_CHRONOLOGICAL = "chronological_accounts"
IMPORT_KIND_BANK_STATEMENTS = "bank_statements"
IMPORT_KIND_LABELS: dict[str, str] = {
    IMPORT_KIND_ACCOUNT_BALANCES: "科目余额表",
    IMPORT_KIND_CHRONOLOGICAL: "序时账",
    IMPORT_KIND_BANK_STATEMENTS: "银行对账单",
}


# ============================================================
#  ORM 模型
# ============================================================


class TeamMember(Base):
    """项目组成员（全局人员库）。"""

    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    level: Mapped[str] = mapped_column(String(50), default=MEMBER_LEVEL_AUDITOR, nullable=False)
    # 特长/擅长领域 (JSON 字符串数组), 例如 ["收入循环", "存货盘点"]
    specialties: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MEMBER_STATUS_ACTIVE, nullable=False)
    joined_at: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # 入职日期 YYYY-MM-DD
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    # 关系
    # NOTE: 不在 TeamMember 上挂 cascade="all, delete-orphan" —
    # 一旦删除成员，级联会静默删除该成员所有 ProjectAssignment / WorkPlanItem /
    # DailyReport / Blocker，破坏审计轨迹。删除成员前应先做软删除
    # (status='inactive') 或显式清理关联数据。
    assignments: Mapped[list["ProjectAssignment"]] = relationship(back_populates="member")
    work_plan_items: Mapped[list["WorkPlanItem"]] = relationship(back_populates="assignee")
    daily_reports: Mapped[list["DailyReport"]] = relationship(back_populates="member")
    blockers: Mapped[list["Blocker"]] = relationship(back_populates="raised_by")


class ProjectAssignment(Base):
    """项目人员分配 — 多对多 + 项目内角色。"""

    __tablename__ = "project_assignments"
    __table_args__ = (
        UniqueConstraint("project_id", "member_id", name="uq_assignment_project_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team_members.id"), nullable=False, index=True
    )
    role_in_project: Mapped[str] = mapped_column(
        String(50), default=PROJECT_ROLE_MEMBER, nullable=False
    )
    hourly_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    workload_pct: Mapped[float] = mapped_column(Float, default=100.0)  # 投入百分比
    start_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    end_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped["Project"] = relationship(back_populates="project_assignments")
    member: Mapped["TeamMember"] = relationship(back_populates="assignments")


class WorkPlan(Base):
    """项目工作计划主表 — 一份计划对应一组 WorkPlanItem。"""

    __tablename__ = "work_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=WORK_PLAN_STATUS_DRAFT, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    generated_by: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # "ai" / "manual:<user>"
    total_estimated_hours: Mapped[float] = mapped_column(Float, default=0.0)
    ai_prompt_used: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # 记录 AI 用了什么 prompt
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    items: Mapped[list["WorkPlanItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="WorkPlanItem.sort_order"
    )
    project: Mapped["Project"] = relationship(back_populates="work_plans")


class WorkPlanItem(Base):
    """工作计划任务项 — 分配到具体人员。"""

    __tablename__ = "work_plan_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_plans.id"), nullable=False, index=True
    )
    member_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("team_members.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 关联的 IPO 审计模块: 底稿 / 函证 / 盘点 / 销售 / 合同 / 监管 / 其他
    related_module: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default=TASK_PRIORITY_MEDIUM, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TASK_STATUS_PENDING, nullable=False)
    estimated_hours: Mapped[float] = mapped_column(Float, default=0.0)
    actual_hours: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    due_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    parent_item_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("work_plan_items.id"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 建议的人员级别（AI 给出，分配时作为参考）
    recommended_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    plan: Mapped["WorkPlan"] = relationship(back_populates="items")
    assignee: Mapped[Optional["TeamMember"]] = relationship(back_populates="work_plan_items")
    # 自引用：父子任务
    children: Mapped[list["WorkPlanItem"]] = relationship(
        "WorkPlanItem",
        back_populates="parent",
        cascade="all, delete-orphan",
        remote_side="WorkPlanItem.parent_item_id",
    )
    parent: Mapped[Optional["WorkPlanItem"]] = relationship(
        "WorkPlanItem", back_populates="children", remote_side="WorkPlanItem.id"
    )


class Meeting(Base):
    """项目会议 — 站会 / 周会 / 启动会 / 复核会。"""

    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    meeting_type: Mapped[str] = mapped_column(
        String(20), default=MEETING_TYPE_WEEKLY, nullable=False
    )
    scheduled_at: Mapped[str] = mapped_column(String(20), nullable=False)  # YYYY-MM-DD HH:MM
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    agenda: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=MEETING_STATUS_SCHEDULED, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    record: Mapped[Optional["MeetingRecord"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan", uselist=False
    )
    project: Mapped["Project"] = relationship(back_populates="meetings")


class MeetingRecord(Base):
    """会议纪要 — AI 同步给出质量评分。"""

    __tablename__ = "meeting_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    meeting_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("meetings.id"), nullable=False, unique=True, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 决策事项 — JSON 数组 [{decision: "...", owner: "..."}]
    decisions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 行动项 — JSON 数组 [{action: "...", owner: "...", due: "..."}]
    action_items: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 与会人 — JSON 数组 ["张三", "李四"]
    attendees: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # AI 评估
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0-100
    ai_assessment: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON: {strengths, weaknesses, suggestions}
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    meeting: Mapped["Meeting"] = relationship(back_populates="record")


class DailyReport(Base):
    """每日工作汇报。"""

    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team_members.id"), nullable=False, index=True
    )
    report_date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # YYYY-MM-DD
    completed_work: Mapped[str] = mapped_column(Text, nullable=False)  # 已完成
    in_progress_work: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 进行中
    blockers_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 卡点摘要
    next_day_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 次日计划
    hours_logged: Mapped[float] = mapped_column(Float, default=0.0)  # 实际工时
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    member: Mapped["TeamMember"] = relationship(back_populates="daily_reports")
    project: Mapped["Project"] = relationship(back_populates="daily_reports")


class Blocker(Base):
    """项目卡点 / 阻碍事项。"""

    __tablename__ = "blockers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team_members.id"), nullable=False, index=True
    )
    related_task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("work_plan_items.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(
        String(20), default=BLOCKER_SEVERITY_MEDIUM, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default=BLOCKER_STATUS_OPEN, nullable=False)
    raised_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # SLA 跟踪
    sla_deadline: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    raised_by: Mapped["TeamMember"] = relationship(back_populates="blockers")
    project: Mapped["Project"] = relationship(back_populates="blockers")


class ProgressSnapshot(Base):
    """项目 / 人员进度快照 — 每日聚合写入用于历史趋势。"""

    __tablename__ = "progress_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    member_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("team_members.id"), nullable=True, index=True
    )
    snapshot_date: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    in_progress_items: Mapped[int] = mapped_column(Integer, default=0)
    blocked_items: Mapped[int] = mapped_column(Integer, default=0)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0)
    hours_done: Mapped[float] = mapped_column(Float, default=0.0)
    hours_remaining: Mapped[float] = mapped_column(Float, default=0.0)
    open_blockers: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped["Project"] = relationship(back_populates="progress_snapshots")


class ManagementRecommendation(Base):
    """AI 周期性生成的管理建议 — 提交给项目负责人确认。"""

    __tablename__ = "management_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    period_start: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # 建议覆盖的开始日期
    period_end: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 结束日期
    # 关键发现 — JSON 数组 [{category, severity, finding, evidence}]
    findings: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 优先行动 — JSON 数组 [{action, owner, deadline, rationale}]
    priority_actions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 建议 — Markdown 长文
    recommendations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 原始 AI 输出
    # 负责人确认
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confirmed_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )  # P1 (2026-06-19): 强审计追溯, 不依赖 free-form 文本
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 项目负责人对建议的备注
    manager_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # P0 (2026-06-19, round25 #14): manager_notes 的 sha256 hex 前 8 位
    # 与 confirmed_by_user_id 联合形成完整审计追溯, 验证后续 notes 内容是否被改.
    notes_hash: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)

    project: Mapped["Project"] = relationship(back_populates="management_recommendations")


# ============================================================
# 综合底稿自动生成 — 多所模板管理与历史底稿库
# ============================================================


class FirmTemplate(Base):
    """事务所综合底稿模板（多所隔离 + 版本管理）。

    - ``firm_id`` 用字符串（每家事务所唯一代码）
    - 同一 (firm_id, template_id) 可有多个 version，按 published_at 排序取最新
    - ``template_bytes`` 直接存 .xlsx 二进制，避免模板文件散落磁盘
    """

    __tablename__ = "firm_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    firm_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    template_name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    audit_period: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 二进制 .xlsx 模板
    template_bytes: Mapped[bytes] = mapped_column(nullable=False)
    # 模板字段定义快照（JSON），便于跨 session 复用，避免每次解析
    field_schema_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 元数据
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    __table_args__ = (
        UniqueConstraint("firm_id", "template_id", "version", name="uq_firm_template_version"),
        Index("ix_firm_template_lookup", "firm_id", "template_id", "is_active"),
    )


class HistoricalWorkpaper(Base):
    """事务所历史综合底稿（脱敏后入库，作为第 4 类信息源）。

    脱敏原则：
    - 移除公司名、客户名、供应商名、人名（统一替换为 ``<ENT_{i}>``）
    - 保留所有金额、比例、日期、文本结构
    - 保留 ``_meta`` 字段定义，使历史底稿可被同模板复用
    """

    __tablename__ = "historical_workpapers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    firm_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    project_fiscal_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 脱敏后的 .xlsx 字节
    anonymized_bytes: Mapped[bytes] = mapped_column(nullable=False)
    # 抽取的文本片段（用于全文搜索）
    text_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 关键结论（管理层判断、披露事项等长文本）
    key_findings: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 来源项目（脱敏前）
    source_project_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
