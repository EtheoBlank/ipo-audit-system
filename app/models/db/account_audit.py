"""长期资产发生额审定 ORM (Pack A — 用户特别要求).

需求原话:
  "长期资产审定数的发生额也要是审定的, 这个需要在底稿里加一下,
   就是长期资产这几个科目不是期初期末确定审定数就好了, 发生额也需要是审定数。"

含义: 对长期资产科目 (固定资产 / 在建工程 / 无形资产 / 长期股权投资 / 商誉
/ 使用权资产 / 投资性房地产 / 长期待摊费用 等), 底稿里:
  - 期初余额: 出审定数 + 审计调整 (原有功能, 走 AccountBalance)
  - 期末余额: 出审定数 + 审计调整 (原有功能, 走 AccountBalance)
  - 本期借方发生额: **逐笔出审定数 + 审计调整** (新增功能, 走本表 + ChronologicalAccount)
  - 本期贷方发生额: **逐笔出审定数 + 审计调整** (新增功能, 走本表 + ChronologicalAccount)

每一笔来自序时账的发生额, 在审定时由审计师录入 audited_amount, 系统自动:
  - adjustment = audited - book
  - 在底稿生成时校验恒等式: 期初审定 + 借方审定 - 贷方审定 = 期末审定
  - 不平时高亮提示
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    "AccountMovementAudit",
    "LongTermAssetScopeOverride",
    # 长期资产科目默认前缀清单 (会计科目准则)
    "DEFAULT_LONG_TERM_ASSET_PREFIXES",
    # 状态
    "MOVEMENT_AUDIT_STATUS_PENDING",
    "MOVEMENT_AUDIT_STATUS_AUDITED",
    "MOVEMENT_AUDIT_STATUS_DISPUTED",
    "MOVEMENT_AUDIT_STATUS_SKIPPED",
    "ALL_MOVEMENT_AUDIT_STATUSES",
    # 方向
    "MOVEMENT_DIRECTION_DEBIT",
    "MOVEMENT_DIRECTION_CREDIT",
]


def _utcnow() -> datetime:
    """与 db_models 一致 — naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# === 默认长期资产科目前缀清单 ===
# 来自《企业会计准则——应用指南》(2006/CAS 30 + 2017 修订 + CAS 21 新租赁)
# 用户可在前端 / 配置里追加 / 排除 (例如递延所得税资产)
DEFAULT_LONG_TERM_ASSET_PREFIXES: tuple[str, ...] = (
    "1501",  # 债权投资
    "1502",  # 债权投资减值准备
    "1503",  # 其他债权投资
    "1511",  # 长期股权投资
    "1512",  # 长期股权投资减值准备
    "1513",  # 长期债权投资
    "1521",  # 持有至到期投资 (旧)
    "1522",  # 持有至到期投资减值准备 (旧)
    "1523",  # 长期应收款
    "1531",  # 长期债券投资
    "1601",  # 固定资产
    "1602",  # 累计折旧
    "1603",  # 固定资产减值准备
    "1604",  # 在建工程
    "1605",  # 工程物资
    "1606",  # 固定资产清理
    "1621",  # 生产性生物资产
    "1622",  # 生产性生物资产累计折旧
    "1623",  # 公益性生物资产
    "1631",  # 油气资产
    "1632",  # 累计折耗
    "1701",  # 无形资产
    "1702",  # 累计摊销
    "1703",  # 无形资产减值准备
    "1711",  # 商誉
    "1801",  # 长期待摊费用
    "1810",  # 投资性房地产
    "1811",  # 投资性房地产累计折旧 (摊销)
    "1812",  # 投资性房地产减值准备
    "1821",  # 使用权资产 (CAS 21)
    "1822",  # 使用权资产累计折旧
    "1901",  # 递延所得税资产 (默认包含, 用户可在前端取消)
)


# === 发生额审定状态 ===
MOVEMENT_AUDIT_STATUS_PENDING = "pending"      # 待审定 (初始)
MOVEMENT_AUDIT_STATUS_AUDITED = "audited"      # 已审定
MOVEMENT_AUDIT_STATUS_DISPUTED = "disputed"    # 有争议, 需复核
MOVEMENT_AUDIT_STATUS_SKIPPED = "skipped"      # 排除 (例如重大错报无关)
ALL_MOVEMENT_AUDIT_STATUSES = [
    MOVEMENT_AUDIT_STATUS_PENDING,
    MOVEMENT_AUDIT_STATUS_AUDITED,
    MOVEMENT_AUDIT_STATUS_DISPUTED,
    MOVEMENT_AUDIT_STATUS_SKIPPED,
]


# === 借贷方向 ===
MOVEMENT_DIRECTION_DEBIT = "debit"
MOVEMENT_DIRECTION_CREDIT = "credit"


class AccountMovementAudit(Base):
    """长期资产科目本期发生额审定明细 (一笔凭证一行).

    与 ``ChronologicalAccount`` 的关系: 软关联 (voucher_no + line_no + account_code
    复合定位), 不强制 FK — 因为审计调整后底稿可能基于不同版本的序时账,
    导致序时账数据被替换时审定记录仍要保留。
    """
    __tablename__ = "account_movement_audits"
    __table_args__ = (
        # 同一项目同一凭证同一行同一科目同一方向只能审定一次
        UniqueConstraint(
            "project_id",
            "account_code",
            "voucher_no",
            "voucher_line_no",
            "direction",
            name="uq_movement_audit_voucher_line",
        ),
        Index("ix_movement_audit_project_account", "project_id", "account_code"),
        Index("ix_movement_audit_period", "project_id", "period_end"),
        Index("ix_movement_audit_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    # 业务定位
    account_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_end: Mapped[str] = mapped_column(String(20), nullable=False)  # YYYY-MM-DD

    voucher_date: Mapped[str] = mapped_column(String(20), nullable=False)  # YYYY-MM-DD
    voucher_no: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    voucher_line_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # debit / credit

    # 摘要与对方科目 (从序时账冗余, 方便底稿展示而不 join)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    counter_account: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    auxiliary_accounting: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # 核心三栏
    book_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    audited_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    adjustment_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # 审计意见
    adjustment_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    working_paper_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 状态 + 审定人 (User 删除后保留冗余)
    status: Mapped[str] = mapped_column(
        String(20), default=MOVEMENT_AUDIT_STATUS_PENDING, nullable=False, index=True
    )
    audited_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    audited_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    audited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class LongTermAssetScopeOverride(Base):
    """项目级"长期资产科目范围"覆盖.

    用途: 默认前缀清单是会计准则通用的, 但具体项目里:
      - 行业差异 (例如能源公司额外把 ``6201`` 主营成本里某些资本化项计入)
      - 用户偏好 (递延所得税资产想排除)

    每条记录 = 一次"加" 或 "减" 一个科目前缀
    """
    __tablename__ = "long_term_asset_scope_overrides"
    __table_args__ = (
        UniqueConstraint("project_id", "account_prefix", name="uq_lta_scope_project_prefix"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    account_prefix: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # include / exclude
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
