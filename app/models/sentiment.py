"""舆情跟踪 Pydantic schemas.

所有 schema 集中在这里, 避免污染 db_models.py.
模式:
    - Base / Create / Update / Response (与项目惯例一致, 见 app/models/audit.py)
    - ORM → Schema 用 model_config = {"from_attributes": True}
    - 中文 description
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
#  SentimentSubject — 搜索别名
# ============================================================


class SentimentSubjectBase(BaseModel):
    alias_type: str = Field("company", description="company / brand / product / person / domain")
    alias_value: str = Field(..., max_length=200, description="搜索别名值")
    match_mode: str = Field("contains", description="exact / contains / regex")
    is_primary: bool = Field(False)
    weight: int = Field(10, ge=0, le=100)
    is_active: bool = Field(True)
    note: Optional[str] = Field(None, max_length=500)


class SentimentSubjectCreate(SentimentSubjectBase):
    project_id: int


class SentimentSubjectUpdate(BaseModel):
    alias_type: Optional[str] = None
    alias_value: Optional[str] = Field(None, max_length=200)
    match_mode: Optional[str] = None
    is_primary: Optional[bool] = None
    weight: Optional[int] = Field(None, ge=0, le=100)
    is_active: Optional[bool] = None
    note: Optional[str] = None


class SentimentSubjectResponse(SentimentSubjectBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    created_at: datetime
    updated_at: datetime


# ============================================================
#  SentimentSource — 信源 (管理员可启停, 用户不能改结构)
# ============================================================


class SentimentSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    provider_type: str
    display_name: str
    base_url: Optional[str] = None
    is_paid: bool
    api_key_ref: Optional[str] = None
    is_enabled: bool
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_count: int
    last_error: Optional[str] = None
    created_at: datetime


class SentimentSourceToggle(BaseModel):
    is_enabled: bool


# ============================================================
#  SentimentEvent — 舆情事件
# ============================================================


class SentimentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    source_id: Optional[int] = None
    source_code: Optional[str] = None
    event_kind: Optional[str] = None
    severity: str
    review_status: str
    title: str
    url: Optional[str] = None
    publisher: Optional[str] = None
    publish_date: Optional[str] = None
    content_text: str
    content_hash: str
    matched_alias: Optional[str] = None
    attached_briefing_id: Optional[int] = None
    fetched_at: datetime
    created_at: datetime


class SentimentEventImport(BaseModel):
    """手工录入事件."""

    project_id: int
    title: str = Field(..., min_length=1)
    content_text: str = Field("", max_length=8000)
    url: Optional[str] = None
    publisher: Optional[str] = None
    publish_date: Optional[str] = None
    severity: str = Field("info", description="info/notice/warn/critical")
    event_kind: Optional[str] = None


# ============================================================
#  SentimentDailyBriefing — 每日简报
# ============================================================


class SentimentBriefingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    briefing_date: str
    title: str
    ai_summary: Optional[str] = None
    event_snapshot_json: Optional[str] = None
    risk_assessment_json: Optional[str] = None
    audit_verification_json: Optional[str] = None
    is_locked: bool
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    status: str
    submitted_at: Optional[datetime] = None
    submitted_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_comment: Optional[str] = None
    verification_failed: bool
    verification_message: Optional[str] = None
    event_count: int
    word_report_path: Optional[str] = None
    word_report_sha256: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SentimentBriefingGenerateRequest(BaseModel):
    project_id: int
    briefing_date: Optional[str] = None  # 默认今天
    force: bool = Field(False, description="True 时强制重新生成 (忽略 detector 幂等)")


class SentimentBriefingReviewRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    comment: Optional[str] = Field(None, max_length=2000)


class SentimentBriefingRejectRequest(SentimentBriefingReviewRequest):
    comment: str = Field(..., min_length=1, description="驳回必须填写意见")


class SentimentBriefingReviseRequest(BaseModel):
    reviser: str = Field(..., min_length=1, max_length=100)
    change_note: Optional[str] = None


# ============================================================
#  SentimentQuarterlyReport — 季度跟踪报告
# ============================================================


class SentimentQuarterlyReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    period_type: str
    fiscal_year: int
    period_end: str
    title: str
    trigger_type: Optional[str] = None
    daily_briefing_window_start: Optional[str] = None
    daily_briefing_window_end: Optional[str] = None
    referenced_briefing_ids_json: Optional[str] = None
    referenced_event_ids_json: Optional[str] = None
    financial_input_json: Optional[str] = None
    financial_input_source: Optional[str] = None
    financial_input_verified_by: Optional[str] = None
    financial_input_verified_at: Optional[datetime] = None
    ai_report_md: Optional[str] = None
    ai_report_verification_json: Optional[str] = None
    content_snapshot: Optional[str] = None
    amount_snapshot: Optional[str] = None
    word_report_path: Optional[str] = None
    word_report_sha256: Optional[str] = None
    is_locked: bool
    verification_failed: bool
    verification_message: Optional[str] = None
    status: str
    submitted_at: Optional[datetime] = None
    submitted_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SentimentQuarterlyCreateRequest(BaseModel):
    project_id: int
    period_type: str = Field(..., description="Q1/H1/Q3/ANNUAL")
    fiscal_year: int
    trigger_type: str = Field("manual")


class SentimentQuarterlyFinancialInput(BaseModel):
    """季报关键数据录入. 8 个必填字段 + 签名."""

    revenue: float = Field(..., description="营业收入 (元)")
    net_profit: float = Field(..., description="净利润 (元)")
    non_recurring_pnl: float = Field(..., description="扣非净利润 (元)")
    gross_margin: float = Field(..., description="毛利率 (%, 0-100)")
    yoy_revenue: float = Field(..., description="营收同比 (%, 正负)")
    yoy_net_profit: float = Field(..., description="净利同比 (%, 正负)")
    total_assets: float = Field(..., description="期末总资产 (元)")
    operating_cash_flow: float = Field(..., description="经营现金流净额 (元)")
    verified_by: str = Field(..., min_length=1, max_length=100, description="审计师签名 (必填)")
    note: Optional[str] = None


class SentimentQuarterlyReviewRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    comment: Optional[str] = Field(None, max_length=2000)


class SentimentQuarterlyRejectRequest(SentimentQuarterlyReviewRequest):
    comment: str = Field(..., min_length=1)


# ============================================================
#  SentimentNotification — 站内通知
# ============================================================


class SentimentNotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: Optional[int] = None
    notification_type: str
    title: str
    body: Optional[str] = None
    link_url: Optional[str] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime


# ============================================================
#  通用响应
# ============================================================


class SentimentScanRequest(BaseModel):
    project_id: Optional[int] = None
    source_codes: Optional[list[str]] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
