"""Pydantic schemas for the inventory module."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def _validate_finite_nonneg(v: float, name: str) -> float:
    if v is None:
        return v
    if not math.isfinite(v):
        raise ValueError(f"{name} 必须为有限数值 (不接受 NaN / inf)")
    if v < 0:
        raise ValueError(f"{name} 不能为负数")
    return v


# ---- 收发存 ------------------------------------------------------------


class InventoryMovementResponse(BaseModel):
    id: int
    project_id: int
    material_code: str
    material_name: str
    category: Optional[str] = None
    spec: Optional[str] = None
    unit: Optional[str] = None
    warehouse: Optional[str] = None
    batch_no: Optional[str] = None
    inbound_date: Optional[datetime] = None
    period_end: str
    is_prior_year: bool = False
    opening_qty: float = 0.0
    opening_amount: float = 0.0
    inbound_qty: float = 0.0
    inbound_amount: float = 0.0
    outbound_qty: float = 0.0
    outbound_amount: float = 0.0
    ending_qty: float = 0.0
    ending_amount: float = 0.0
    unit_cost: float = 0.0

    model_config = {"from_attributes": True}


class InventoryImportResponse(BaseModel):
    project_id: int
    period_end: str
    is_prior_year: bool
    imported_count: int
    total_ending_amount: float


# ---- 盘点用表 ----------------------------------------------------------


class CountSheetGenerateRequest(BaseModel):
    period_end: Optional[date] = None
    coverage_threshold: float = Field(0.80, ge=0.0, le=1.0)
    b_sample_ratio: float = Field(0.20, ge=0.0, le=1.0)
    c_sample_ratio: float = Field(0.05, ge=0.0, le=1.0)
    high_value_warehouses: list[str] = Field(default_factory=list)
    must_include_categories: list[str] = Field(default_factory=list)
    must_include_codes: list[str] = Field(default_factory=list)
    min_unit_amount: float = Field(0.0, ge=0.0)
    random_seed: int = 42
    persist: bool = True  # 是否落库（False 仅预览）
    plan_id: Optional[int] = None  # 关联到哪个盘点计划
    force_overwrite_counted: bool = False  # True 时连已回填的实盘数也一并清空（默认 False 保留）
    # 重要性水平：单条 ≥ 该值的物料强制入 A（如税前利润 ×5%）
    materiality: float = Field(0.0, ge=0.0)
    # B 类抽样方法：mus（按金额加权，推荐）/ random
    b_sample_method: str = Field("mus", pattern="^(mus|random)$")
    # 反向抽盘（物→账）的比例；默认 5%
    reverse_sample_ratio: float = Field(0.05, ge=0.0, le=0.5)


class CountSheetSimulateRequest(BaseModel):
    """Compare multiple thresholds — used by the interactive page."""

    period_end: Optional[date] = None
    thresholds: list[float] = Field(default_factory=lambda: [0.7, 0.8, 0.9])
    b_sample_ratio: float = 0.20
    c_sample_ratio: float = 0.05


class CountSheetRowResponse(BaseModel):
    id: int
    project_id: int
    plan_id: Optional[int] = None
    material_code: str
    material_name: str
    category: Optional[str] = None
    warehouse: Optional[str] = None
    batch_no: Optional[str] = None
    unit: Optional[str] = None
    book_qty: float
    book_unit_cost: float
    book_amount: float
    counted_qty: Optional[float] = None
    sample_tier: str
    sample_reason: Optional[str] = None
    coverage_rank: int
    counted_at: Optional[datetime] = None
    counted_by: Optional[str] = None
    remark: Optional[str] = None

    model_config = {"from_attributes": True}


class CountSheetGenerateResponse(BaseModel):
    project_id: int
    total_amount: float
    covered_amount: float
    coverage_ratio: float
    total_items: int
    selected_items: int
    tier_summary: dict[str, dict[str, Any]]
    strategy_desc: str
    rows: list[CountSheetRowResponse] = Field(default_factory=list)


# ---- 盘点计划 ----------------------------------------------------------


class CountPlanGenerateRequest(BaseModel):
    period_end: Optional[date] = None
    industry: Optional[str] = None  # 不传则用 Project.industry
    count_days_before: int = 0
    count_days_after: int = 2
    team: list[dict[str, str]] = Field(default_factory=list)


class CountPlanReviseRequest(BaseModel):
    instruction: str = Field(..., description="用户对话式修改指令")


class CountPlanResponse(BaseModel):
    id: int
    project_id: int
    title: str
    industry: Optional[str] = None
    period_end: str
    count_date_start: Optional[str] = None
    count_date_end: Optional[str] = None
    objectives: Optional[str] = None
    scope: Optional[str] = None
    team: Optional[str] = None  # JSON string
    procedures: Optional[str] = None
    special_notes: Optional[str] = None
    risks: Optional[str] = None
    revision_log: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- 照片回填 ----------------------------------------------------------


class CountPhotoUploadResponse(BaseModel):
    photo_id: int
    project_id: int
    ocr_engine: str
    parsed_row_count: int
    matched_count: int
    unmatched_count: int
    counted_by: Optional[str] = None
    counted_at: Optional[datetime] = None
    unmatched_rows: list[dict[str, Any]] = Field(default_factory=list)


class CompletionStatsResponse(BaseModel):
    overall: dict[str, Any]
    by_warehouse: list[dict[str, Any]]
    differences: list[dict[str, Any]]
    differences_major: list[dict[str, Any]] = Field(default_factory=list)
    differences_minor: list[dict[str, Any]] = Field(default_factory=list)
    difference_summary: dict[str, Any]
    uncovered: list[dict[str, Any]] = Field(default_factory=list)


# ---- 库龄 / 跌价 -------------------------------------------------------


class ImpairmentComputeRequest(BaseModel):
    period_end: Optional[date] = None
    use_sales_for_nrv: bool = True
    # 注意：若不显式传 sell_cost_rate（保留 None），后端会按 Project.industry 自动选默认
    sell_cost_rate: Optional[float] = Field(None, ge=0.0, le=0.50)
    # 完工口径：原材料/在产品的 NRV 要扣这部分（占售价的比例）。默认 0 = 不启用完工口径
    completion_cost_rate: float = Field(0.0, ge=0.0, le=0.90)
    manual_nrv: dict[str, float] = Field(default_factory=dict, max_length=10_000)
    persist: bool = True
    include_reversal: bool = True  # 自动结合上年期初已计提做跌价转回

    @field_validator("manual_nrv")
    @classmethod
    def _check_manual_nrv(cls, v: dict[str, float]) -> dict[str, float]:
        for code, price in v.items():
            if not isinstance(code, str) or not (0 < len(code) <= 64):
                raise ValueError(f"物料编码长度必须在 1-64：{code!r}")
            if not math.isfinite(price) or price < 0:
                raise ValueError(f"物料 {code} 的市价必须为非负有限数值")
        return v


class ImpairmentRowResponse(BaseModel):
    material_code: str
    material_name: str
    category: Optional[str] = None
    period_end: str
    ending_qty: float
    book_unit_cost: float
    book_amount: float
    age_le_90: float
    age_91_180: float
    age_181_365: float
    age_366_730: float
    age_gt_730: float
    weighted_avg_age: float
    nrv_unit_price: Optional[float] = None
    nrv_source: Optional[str] = None
    nrv_amount: float
    estimated_sell_cost: float
    impairment_current: float
    impairment_opening: float
    impairment_reversal: float
    impairment_provision: float
    net_impairment_change: float
    method: str
    note: Optional[str] = None
    # 转回拆分
    reversal_to_cogs: float = 0.0
    reversal_to_loss: float = 0.0


class ImpairmentComputeResponse(BaseModel):
    project_id: int
    summary: dict[str, float]
    rows: list[ImpairmentRowResponse] = Field(default_factory=list)


class PriorImpairmentUpload(BaseModel):
    """{material_code: 上年期末已计提跌价金额}"""

    items: dict[str, float] = Field(..., max_length=10_000)

    @field_validator("items")
    @classmethod
    def _check_items(cls, v: dict[str, float]) -> dict[str, float]:
        for code, amount in v.items():
            if not isinstance(code, str) or not (0 < len(code) <= 64):
                raise ValueError(f"物料编码长度必须在 1-64：{code!r}")
            if not math.isfinite(amount) or amount < 0:
                raise ValueError(f"物料 {code} 的金额必须为非负有限数值")
        return v


# ---- 物料编码跨年映射 -------------------------------------------------


class CodeMappingItem(BaseModel):
    old_code: str = Field(..., min_length=1, max_length=100)
    new_code: str = Field(..., min_length=1, max_length=100)
    note: Optional[str] = None


class CodeMappingUploadRequest(BaseModel):
    items: list[CodeMappingItem] = Field(..., max_length=10_000)
    replace: bool = True  # True = 覆盖项目下全部映射；False = 增量追加


class CodeMappingResponse(BaseModel):
    id: int
    project_id: int
    old_code: str
    new_code: str
    note: Optional[str] = None

    model_config = {"from_attributes": True}
