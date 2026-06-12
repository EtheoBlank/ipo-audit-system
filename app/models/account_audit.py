"""Pydantic schemas for AccountMovementAudit (长期资产发生额审定)."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.models.db.account_audit import (
    MOVEMENT_AUDIT_STATUS_PENDING,
    ALL_MOVEMENT_AUDIT_STATUSES,
    MOVEMENT_DIRECTION_DEBIT,
    MOVEMENT_DIRECTION_CREDIT,
)


def _validate_finite_amount(v: float) -> float:
    if v is None or not math.isfinite(float(v)):
        raise ValueError("金额必须是有限数")
    return float(v)


# ============================================================
#  发生额审定行 (单笔凭证)
# ============================================================


class MovementAuditRowBase(BaseModel):
    account_code: str = Field(..., min_length=1, max_length=50)
    account_name: str = Field(..., min_length=1, max_length=200)
    period_end: str = Field(..., min_length=8, max_length=20)  # YYYY-MM-DD
    voucher_date: str = Field(..., min_length=8, max_length=20)
    voucher_no: str = Field(..., min_length=1, max_length=50)
    voucher_line_no: int = Field(default=1, ge=1)
    direction: str = Field(..., pattern=r"^(debit|credit)$")
    summary: Optional[str] = None
    counter_account: Optional[str] = Field(None, max_length=100)
    auxiliary_accounting: Optional[str] = Field(None, max_length=200)


class MovementAuditRowResponse(MovementAuditRowBase):
    id: int
    project_id: int
    book_amount: float
    audited_amount: float
    adjustment_amount: float
    adjustment_reason: Optional[str] = None
    working_paper_ref: Optional[str] = None
    note: Optional[str] = None
    status: str
    audited_by_user_id: Optional[int] = None
    audited_by_display: Optional[str] = None
    audited_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MovementAuditUpdate(BaseModel):
    """单行审定 — PUT /movements/{id}/audit."""
    audited_amount: float
    adjustment_reason: Optional[str] = Field(None, max_length=2000)
    working_paper_ref: Optional[str] = Field(None, max_length=100)
    note: Optional[str] = None
    status: Optional[str] = None  # 默认会自动设为 audited

    @field_validator("audited_amount")
    @classmethod
    def _amount_finite(cls, v: float) -> float:
        return _validate_finite_amount(v)

    @field_validator("status")
    @classmethod
    def _status_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALL_MOVEMENT_AUDIT_STATUSES:
            raise ValueError(f"status 必须是 {ALL_MOVEMENT_AUDIT_STATUSES} 之一")
        return v


class MovementAuditBulkItem(BaseModel):
    """批量上传 (Excel 导入) 单条."""
    account_code: str
    voucher_no: str
    voucher_line_no: int = 1
    direction: str = Field(..., pattern=r"^(debit|credit)$")
    audited_amount: float
    adjustment_reason: Optional[str] = None
    working_paper_ref: Optional[str] = None
    note: Optional[str] = None

    @field_validator("audited_amount")
    @classmethod
    def _amount_finite(cls, v: float) -> float:
        return _validate_finite_amount(v)


class MovementAuditBulkRequest(BaseModel):
    period_end: str
    rows: List[MovementAuditBulkItem]


class MovementAuditBulkResponse(BaseModel):
    matched: int
    updated: int
    not_found: int
    errors: List[str] = Field(default_factory=list)


class MovementAuditDisputeRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


# ============================================================
#  发生额审定汇总 (给底稿用)
# ============================================================


class AccountAuditSummary(BaseModel):
    """单科目本期发生额审定汇总."""
    project_id: int
    account_code: str
    account_name: str
    period_end: str
    is_long_term_asset: bool

    # 期初 / 期末 走 AccountBalance (原有), 这里也带上方便底稿恒等式
    beginning_balance_book: float = 0.0
    beginning_balance_audited: float = 0.0
    beginning_balance_adjustment: float = 0.0

    debit_book_total: float = 0.0
    debit_audited_total: float = 0.0
    debit_adjustment_total: float = 0.0
    debit_pending_count: int = 0
    debit_audited_count: int = 0
    debit_disputed_count: int = 0
    debit_total_count: int = 0

    credit_book_total: float = 0.0
    credit_audited_total: float = 0.0
    credit_adjustment_total: float = 0.0
    credit_pending_count: int = 0
    credit_audited_count: int = 0
    credit_disputed_count: int = 0
    credit_total_count: int = 0

    ending_balance_book: float = 0.0
    ending_balance_audited: float = 0.0
    ending_balance_adjustment: float = 0.0

    identity_check_book: float = 0.0       # 期初 + 借 - 贷 - 期末 (账面)
    identity_check_audited: float = 0.0    # 期初(审定) + 借(审定) - 贷(审定) - 期末(审定)
    is_balanced: bool = True
    notes: Optional[str] = None


class AccountAuditOverview(BaseModel):
    """项目级长期资产科目审定总览."""
    project_id: int
    period_end: str
    total_accounts: int
    accounts_fully_audited: int
    accounts_with_pending: int
    accounts_with_dispute: int
    accounts_unbalanced: int
    accounts: List[AccountAuditSummary]


class MovementListQuery(BaseModel):
    period_end: Optional[str] = None
    direction: Optional[str] = None  # debit / credit
    status: Optional[str] = None
    voucher_no: Optional[str] = None
    keyword: Optional[str] = None
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class MovementListResponse(BaseModel):
    total: int
    items: List[MovementAuditRowResponse]


# ============================================================
#  长期资产范围覆盖
# ============================================================


class ScopeOverrideCreate(BaseModel):
    account_prefix: str = Field(..., min_length=1, max_length=50, pattern=r"^[0-9A-Za-z]+$")
    action: str = Field(..., pattern=r"^(include|exclude)$")
    reason: Optional[str] = None


class ScopeOverrideResponse(ScopeOverrideCreate):
    id: int
    project_id: int
    created_by_user_id: Optional[int] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class EffectivePrefixesResponse(BaseModel):
    """前端展示: 默认前缀 + 用户覆盖, 计算后生效的科目前缀集合."""
    default_prefixes: List[str]
    project_includes: List[str]
    project_excludes: List[str]
    effective_prefixes: List[str]


__all__ = [
    "MovementAuditRowBase",
    "MovementAuditRowResponse",
    "MovementAuditUpdate",
    "MovementAuditBulkItem",
    "MovementAuditBulkRequest",
    "MovementAuditBulkResponse",
    "MovementAuditDisputeRequest",
    "AccountAuditSummary",
    "AccountAuditOverview",
    "MovementListQuery",
    "MovementListResponse",
    "ScopeOverrideCreate",
    "ScopeOverrideResponse",
    "EffectivePrefixesResponse",
    "MOVEMENT_DIRECTION_DEBIT",
    "MOVEMENT_DIRECTION_CREDIT",
]
