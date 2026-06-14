"""关联方专项 ORM (Pack B).

IPO 最大雷区. 横跨主数据 / 识别引擎 / 交易公允性 / 资金占用 / 同业竞争 / 披露核查.

为什么独立大模块: 关联方信息分散在工商登记 + 董监高 + 序时账摘要 + 客户/供应商
主数据 + 招股书披露, 需要专门的引擎做交叉匹配 + diff 检测.
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
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


__all__ = [
    "RelatedParty",
    "RelatedPartyRelation",
    "RelatedPartyKeyPerson",
    "RelatedPartyTransaction",
    "RelatedPartyCapitalOccupation",
    "PeerCompetitionAssessment",
    "ProspectusDisclosureGap",
    # 关联方类型
    "RP_TYPE_CONTROLLING_SHAREHOLDER",
    "RP_TYPE_ACTUAL_CONTROLLER",
    "RP_TYPE_DIRECTOR_OR_SENIOR",
    "RP_TYPE_KEY_MANAGEMENT",
    "RP_TYPE_5PCT_SHAREHOLDER",
    "RP_TYPE_FAMILY_MEMBER",
    "RP_TYPE_CONTROLLED_ENTITY",
    "RP_TYPE_JOINT_CONTROLLED_ENTITY",
    "RP_TYPE_SIGNIFICANT_INFLUENCE",
    "RP_TYPE_OTHER",
    "ALL_RP_TYPES",
    # 来源
    "RP_SOURCE_MANUAL",
    "RP_SOURCE_INDUSTRY_DB",
    "RP_SOURCE_PROSPECTUS",
    "RP_SOURCE_CHRONO_SCAN",
    "RP_SOURCE_CUSTOMER_OVERLAP",
    "RP_SOURCE_AI",
    # 交易类型
    "RPT_TYPE_SALES",
    "RPT_TYPE_PURCHASE",
    "RPT_TYPE_LOAN_RECEIVABLE",
    "RPT_TYPE_LOAN_PAYABLE",
    "RPT_TYPE_GUARANTEE",
    "RPT_TYPE_LEASE",
    "RPT_TYPE_SERVICE",
    "RPT_TYPE_SHARED_RESOURCE",
    "RPT_TYPE_ASSET_TRANSFER",
    "RPT_TYPE_OTHER",
    # 披露 diff 状态
    "DISCLOSURE_GAP_CRITICAL",
    "DISCLOSURE_GAP_REVIEW",
    "DISCLOSURE_GAP_OK",
]


# === 关联方类型 (来自《企业会计准则第 36 号 — 关联方披露》) ===
RP_TYPE_CONTROLLING_SHAREHOLDER = "controlling_shareholder"  # 控股股东
RP_TYPE_ACTUAL_CONTROLLER = "actual_controller"  # 实际控制人
RP_TYPE_DIRECTOR_OR_SENIOR = "director_or_senior"  # 董监高
RP_TYPE_KEY_MANAGEMENT = "key_management"  # 关键管理人员
RP_TYPE_5PCT_SHAREHOLDER = "shareholder_5pct"  # 持股 5%+ 股东
RP_TYPE_FAMILY_MEMBER = "family_member"  # 与上述有关系的家庭成员
RP_TYPE_CONTROLLED_ENTITY = "controlled_entity"  # 受控制的企业
RP_TYPE_JOINT_CONTROLLED_ENTITY = "joint_controlled_entity"  # 共同控制的企业
RP_TYPE_SIGNIFICANT_INFLUENCE = "significant_influence"  # 重大影响关系企业
RP_TYPE_OTHER = "other"

ALL_RP_TYPES = [
    RP_TYPE_CONTROLLING_SHAREHOLDER,
    RP_TYPE_ACTUAL_CONTROLLER,
    RP_TYPE_DIRECTOR_OR_SENIOR,
    RP_TYPE_KEY_MANAGEMENT,
    RP_TYPE_5PCT_SHAREHOLDER,
    RP_TYPE_FAMILY_MEMBER,
    RP_TYPE_CONTROLLED_ENTITY,
    RP_TYPE_JOINT_CONTROLLED_ENTITY,
    RP_TYPE_SIGNIFICANT_INFLUENCE,
    RP_TYPE_OTHER,
]


# === 来源 ===
RP_SOURCE_MANUAL = "manual"
RP_SOURCE_INDUSTRY_DB = "industry_db"  # 工商登记 / 天眼查
RP_SOURCE_PROSPECTUS = "prospectus"  # 招股书已披露
RP_SOURCE_CHRONO_SCAN = "chronological_scan"  # 序时账摘要扫描出来的
RP_SOURCE_CUSTOMER_OVERLAP = "customer_overlap"  # 客户/供应商重叠
RP_SOURCE_AI = "ai_inferred"  # AI 推断


# === 关联交易类型 ===
RPT_TYPE_SALES = "sales"  # 销售
RPT_TYPE_PURCHASE = "purchase"  # 采购
RPT_TYPE_LOAN_RECEIVABLE = "loan_receivable"  # 资金拆出
RPT_TYPE_LOAN_PAYABLE = "loan_payable"  # 资金拆入
RPT_TYPE_GUARANTEE = "guarantee"  # 担保
RPT_TYPE_LEASE = "lease"  # 租赁
RPT_TYPE_SERVICE = "service"  # 服务
RPT_TYPE_SHARED_RESOURCE = "shared_resource"  # 共用资源
RPT_TYPE_ASSET_TRANSFER = "asset_transfer"  # 资产转让
RPT_TYPE_OTHER = "other"


# === 披露 diff 状态 ===
DISCLOSURE_GAP_CRITICAL = "critical"  # 系统识别但招股书未披露 — 必须处理
DISCLOSURE_GAP_REVIEW = "review"  # 招股书披露但系统未识别 — 可能误报, 需复核
DISCLOSURE_GAP_OK = "ok"  # 一致


class RelatedParty(Base):
    """关联方主数据 (公司 / 自然人 / 其他实体)."""

    __tablename__ = "related_parties"
    __table_args__ = (
        Index("ix_rp_project_type", "project_id", "party_type"),
        Index("ix_rp_credit_code", "unified_credit_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    party_kind: Mapped[str] = mapped_column(String(20), default="entity", nullable=False)
    # entity / person — 公司还是自然人

    party_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # 控股股东 / 实控人 / 董监高 / 关键管理人员 / 5%+股东 / 家庭成员 / 受控制 / 共同控制 / 重大影响

    # 公司字段 (party_kind=entity)
    unified_credit_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True
    )
    registered_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    legal_representative: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    registered_capital: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    business_scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    establishment_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # 自然人字段 (party_kind=person)
    id_number_masked: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # 脱敏存储
    position: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    holding_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0-100

    # 关系链 (人话描述: "实控人张三 → 其配偶李四 → 持股 80% 的乙公司")
    relation_chain: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 识别元数据
    source: Mapped[str] = mapped_column(String(40), default=RP_SOURCE_MANUAL, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)  # 0-1
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # AI 推断的需人工 confirm 才算正式关联方

    # 招股书披露
    is_disclosed_in_prospectus: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prospectus_section_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class RelatedPartyRelation(Base):
    """关联方之间的关系图 (用于股权穿透 + 血缘穿透)."""

    __tablename__ = "related_party_relations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "party_a_id",
            "party_b_id",
            "relation_type",
            name="uq_rp_relation",
        ),
        Index("ix_rp_relation_a", "party_a_id"),
        Index("ix_rp_relation_b", "party_b_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    party_a_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("related_parties.id"), nullable=False
    )
    party_b_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("related_parties.id"), nullable=False
    )

    relation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # 持股 / 配偶 / 父母 / 子女 / 兄弟姐妹 / 同事 / 同学 / 任职 / 共同控制 / 受控制

    holding_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 持股关系才填
    since_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    until_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class RelatedPartyKeyPerson(Base):
    """关联方关键人员明细 (例如关联方公司的董监高 + 持股个人)."""

    __tablename__ = "related_party_key_persons"
    __table_args__ = (Index("ix_rp_keyperson_party", "party_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    party_id: Mapped[int] = mapped_column(Integer, ForeignKey("related_parties.id"), nullable=False)

    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    id_number_masked: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    position: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    holding_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    family_members: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class RelatedPartyTransaction(Base):
    """关联方交易明细 (从序时账 / 合同 / 销售清单等汇总)."""

    __tablename__ = "related_party_transactions"
    __table_args__ = (
        Index("ix_rpt_project_party", "project_id", "party_id"),
        Index("ix_rpt_period", "project_id", "period_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    party_id: Mapped[int] = mapped_column(Integer, ForeignKey("related_parties.id"), nullable=False)

    transaction_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    period_start: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    period_end: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)

    amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="CNY", nullable=False)

    pricing_basis: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 市场公允价 / 成本加成 / 协议定价 / 其他

    # 公允性测试
    similar_market_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fairness_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # 0-100, 越接近 100 越公允
    is_fair: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    fairness_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 溯源
    source_voucher_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_contract_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_doc_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class RelatedPartyCapitalOccupation(Base):
    """关联方资金占用穿行表."""

    __tablename__ = "related_party_capital_occupations"
    __table_args__ = (Index("ix_rpco_project_party", "project_id", "party_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    party_id: Mapped[int] = mapped_column(Integer, ForeignKey("related_parties.id"), nullable=False)

    occupation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # 资金占用 / 担保占用 / 其他

    period_start: Mapped[str] = mapped_column(String(20), nullable=False)
    period_end: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    opening_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    debit_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    credit_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ending_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    max_occupation_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_occupation_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    cleanup_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    # pending / partial / cleared
    cleanup_voucher_refs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PeerCompetitionAssessment(Base):
    """同业竞争评估 — 关联方经营范围与发行人主业重合度."""

    __tablename__ = "peer_competition_assessments"
    __table_args__ = (Index("ix_pca_project_party", "project_id", "party_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    party_id: Mapped[int] = mapped_column(Integer, ForeignKey("related_parties.id"), nullable=False)

    overlap_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0-100
    overlap_keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    risk_level: Mapped[str] = mapped_column(String(20), default="low", nullable=False)
    # low / medium / high / critical

    solution_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # 关联方剥离 / 注销 / 承诺函 / 其他
    solution_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    solution_doc_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    assessed_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assessed_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    assessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ProspectusDisclosureGap(Base):
    """招股书关联方披露 diff (系统识别 vs 招股书已披露)."""

    __tablename__ = "prospectus_disclosure_gaps"
    __table_args__ = (Index("ix_pdg_project_status", "project_id", "gap_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    party_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("related_parties.id"), nullable=True
    )

    gap_status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # critical (系统识别但未披露) / review (披露但未识别) / ok

    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    in_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    in_prospectus: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prospectus_section_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    transaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    suggested_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
