"""Pydantic schemas for the sales-ledger API.

These are intentionally separate from `db_models.py` (SQLAlchemy ORM) and
`audit.py` (existing Pydantic schemas) to keep the new module self-contained.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- documents ----------------------------------------------------


class SalesDocumentResponse(BaseModel):
    id: int
    project_id: int
    filename: str
    doc_type: str
    note: Optional[str] = None
    uploaded_at: datetime

    model_config = {"from_attributes": True}


# ---------- sales records ------------------------------------------------


class SalesRecordBase(BaseModel):
    contract_no: str = ""
    customer_name: str
    product_code: str
    product_name: Optional[str] = ""
    # 销售发票与税务
    invoice_no: Optional[str] = None
    currency: Optional[str] = "CNY"
    tax_rate: float = 0.0
    tax_amount: float = 0.0
    gross_amount: float = 0.0
    # 数量与金额
    quantity: float = 0
    unit_price: float = 0
    revenue_amount: float = 0
    cost_amount: float = 0
    # 直接费用
    shipping_fee: float = 0
    customs_fee: float = 0
    other_direct_fee: float = 0
    # 退换货 / 折扣 / 返利
    return_amount: float = 0.0
    discount_amount: float = 0.0
    rebate_amount: float = 0.0
    # 时间
    ship_date: Optional[date] = None
    receipt_date: Optional[date] = None
    revenue_confirm_date: Optional[date] = None
    # 函证
    confirmation_status: Optional[str] = "未发函"
    confirmation_ref: Optional[str] = None
    confirmation_diff: float = 0.0
    # 溯源
    source: Optional[str] = None
    confidence: float = 1.0


class SalesRecordCreate(SalesRecordBase):
    project_id: int
    document_id: Optional[int] = None


class SalesRecordUpdate(BaseModel):
    contract_no: Optional[str] = None
    customer_name: Optional[str] = None
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    invoice_no: Optional[str] = None
    currency: Optional[str] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    gross_amount: Optional[float] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    revenue_amount: Optional[float] = None
    cost_amount: Optional[float] = None
    shipping_fee: Optional[float] = None
    customs_fee: Optional[float] = None
    other_direct_fee: Optional[float] = None
    return_amount: Optional[float] = None
    discount_amount: Optional[float] = None
    rebate_amount: Optional[float] = None
    ship_date: Optional[date] = None
    receipt_date: Optional[date] = None
    revenue_confirm_date: Optional[date] = None
    confirmation_status: Optional[str] = None
    confirmation_ref: Optional[str] = None
    confirmation_diff: Optional[float] = None
    source: Optional[str] = None
    is_verified: Optional[bool] = None


class SalesRecordResponse(SalesRecordBase):
    id: int
    project_id: int
    document_id: Optional[int] = None
    is_verified: bool = False
    created_at: datetime
    updated_at: datetime

    # Derived (computed on demand)
    gross_profit: Optional[float] = None
    gross_margin: Optional[float] = None

    model_config = {"from_attributes": True}


# ---------- synthesis / analysis ----------------------------------------


class SynthesisRequest(BaseModel):
    project_id: int
    document_ids: list[int] = Field(default_factory=list)
    extra_hint: str = ""


class SynthesisErrorItem(BaseModel):
    """P0-13: 单行 AI 抽取失败的明细 — 供前端 '待复核' 列表使用."""

    idx: int = Field(..., description="原始 batch 中的索引位置")
    row_summary: str = Field(..., description="失败行的精简摘要 (最多 200 字符)")
    error: str = Field(..., description="校验错误详情")


class SynthesisResponse(BaseModel):
    project_id: int
    synthesized_count: int
    records: list[SalesRecordResponse]
    # P0-13: 部分失败时, 把失败行 + 错误信息回传前端, 不再整批 500
    error_count: int = 0
    errors: list[SynthesisErrorItem] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    project_id: int
    period_end: Optional[date] = None
    cut_off_window_days: int = 10
    price_volatility_pct: float = 0.20
    run_industry_benchmark: bool = False
    industry: str = ""


class AnalysisResponse(BaseModel):
    project_id: int
    summary: dict[str, Any]
    by_customer: list[dict[str, Any]]
    by_product: list[dict[str, Any]]
    by_month: list[dict[str, Any]]
    by_customer_product_month: list[dict[str, Any]]
    cut_off_alerts: list[dict[str, Any]]
    price_volatility_alerts: list[dict[str, Any]]
    inventory_recon: list[dict[str, Any]] = Field(default_factory=list)
    # New (incremental patch)
    confirmation_coverage: list[dict[str, Any]] = Field(default_factory=list)
    dso_by_customer: list[dict[str, Any]] = Field(default_factory=list)
    return_discount_impact: list[dict[str, Any]] = Field(default_factory=list)
    recognition_timing_diff: list[dict[str, Any]] = Field(default_factory=list)
    industry_benchmark: Optional[dict[str, Any]] = None
