"""Pydantic schemas for Related Parties (Pack B)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.db.related_parties import (
    ALL_RP_TYPES,
    RP_SOURCE_MANUAL,
    DISCLOSURE_GAP_CRITICAL,
    DISCLOSURE_GAP_REVIEW,
    DISCLOSURE_GAP_OK,
)


# ============================================================
#  RelatedParty 主数据
# ============================================================


class RelatedPartyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    party_kind: str = Field(default="entity", pattern=r"^(entity|person)$")
    party_type: str
    unified_credit_code: Optional[str] = Field(None, max_length=50)
    registered_address: Optional[str] = Field(None, max_length=500)
    legal_representative: Optional[str] = Field(None, max_length=100)
    registered_capital: Optional[float] = None
    business_scope: Optional[str] = None
    establishment_date: Optional[str] = Field(None, max_length=20)
    id_number_masked: Optional[str] = Field(None, max_length=30)
    position: Optional[str] = Field(None, max_length=100)
    holding_pct: Optional[float] = Field(None, ge=0, le=100)
    relation_chain: Optional[str] = None
    source: str = Field(default=RP_SOURCE_MANUAL)
    confidence: float = Field(default=1.0, ge=0, le=1)
    is_disclosed_in_prospectus: bool = False
    prospectus_section_ref: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None

    @field_validator("party_type")
    @classmethod
    def _type_known(cls, v: str) -> str:
        if v not in ALL_RP_TYPES:
            raise ValueError(f"party_type 必须是 {ALL_RP_TYPES} 之一")
        return v


class RelatedPartyCreate(RelatedPartyBase):
    pass


class RelatedPartyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    party_type: Optional[str] = None
    unified_credit_code: Optional[str] = Field(None, max_length=50)
    holding_pct: Optional[float] = Field(None, ge=0, le=100)
    is_confirmed: Optional[bool] = None
    is_disclosed_in_prospectus: Optional[bool] = None
    relation_chain: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("party_type")
    @classmethod
    def _type_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALL_RP_TYPES:
            raise ValueError(f"party_type 必须是 {ALL_RP_TYPES} 之一")
        return v


class RelatedPartyResponse(RelatedPartyBase):
    id: int
    project_id: int
    is_confirmed: bool
    created_by_user_id: Optional[int] = None
    created_by_display: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RelatedPartyListResponse(BaseModel):
    total: int
    items: List[RelatedPartyResponse]


# ============================================================
#  关系图
# ============================================================


class RelationCreate(BaseModel):
    party_a_id: int
    party_b_id: int
    relation_type: str = Field(..., min_length=1, max_length=40)
    holding_pct: Optional[float] = Field(None, ge=0, le=100)
    since_date: Optional[str] = Field(None, max_length=20)
    until_date: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None


class RelationResponse(RelationCreate):
    id: int
    project_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ============================================================
#  识别引擎
# ============================================================


class DetectorRunRequest(BaseModel):
    project_id: int
    enable_chrono_scan: bool = True
    enable_customer_overlap: bool = True
    enable_prospectus_compare: bool = False
    enable_ai_inference: bool = Field(
        default=False,
        description="启用 DeepSeek AI 推断兜底通道. 需要 DEEPSEEK_API_KEY. "
        "AI 通道单独打分, 命中后仍走候选 → confirm 流程.",
    )
    ai_max_candidates: int = Field(
        default=30,
        ge=1,
        le=100,
        description="AI 通道最多返回多少候选 (防 token 爆炸).",
    )
    keywords_extra: Optional[List[str]] = None


class DetectorCandidate(BaseModel):
    name: str
    party_kind: str = "entity"
    party_type: str
    source: str
    confidence: float
    evidence: List[str] = Field(default_factory=list)
    suggested_relation: Optional[str] = None


class DetectorRunResponse(BaseModel):
    scanned_vouchers: int = 0
    scanned_customers: int = 0
    scanned_suppliers: int = 0
    new_candidates: int = 0
    candidates: List[DetectorCandidate] = Field(default_factory=list)
    ai_enabled: bool = False
    notes: Optional[str] = None


# ============================================================
#  关联交易
# ============================================================


class TransactionCreate(BaseModel):
    party_id: int
    transaction_type: str
    period_start: Optional[str] = Field(None, max_length=20)
    period_end: Optional[str] = Field(None, max_length=20)
    amount: float = 0.0
    currency: str = "CNY"
    pricing_basis: Optional[str] = Field(None, max_length=100)
    source_voucher_no: Optional[str] = Field(None, max_length=100)
    source_contract_no: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class TransactionResponse(TransactionCreate):
    id: int
    project_id: int
    similar_market_price: Optional[float] = None
    fairness_score: Optional[float] = None
    is_fair: Optional[bool] = None
    fairness_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class FairnessCheckRequest(BaseModel):
    transaction_ids: Optional[List[int]] = None
    party_id: Optional[int] = None
    period_end: Optional[str] = None


class FairnessCheckResponse(BaseModel):
    assessed: int = 0
    fair: int = 0
    not_fair: int = 0
    pending: int = 0
    avg_score: float = 0.0
    notes: Optional[str] = None


# ============================================================
#  资金占用
# ============================================================


class CapitalOccupationCreate(BaseModel):
    party_id: int
    occupation_type: str = Field(default="资金占用")
    period_start: str
    period_end: str
    opening_balance: float = 0.0
    debit_amount: float = 0.0
    credit_amount: float = 0.0
    ending_balance: float = 0.0
    max_occupation_amount: float = 0.0
    max_occupation_date: Optional[str] = None
    cleanup_status: str = Field(default="pending", pattern=r"^(pending|partial|cleared)$")
    notes: Optional[str] = None


class CapitalOccupationResponse(CapitalOccupationCreate):
    id: int
    project_id: int
    cleanup_voucher_refs: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ============================================================
#  同业竞争
# ============================================================


class PeerCompetitionAssessRequest(BaseModel):
    party_id: int
    issuer_business_keywords: List[str] = Field(default_factory=list)
    use_ai: bool = True


class PeerCompetitionResponse(BaseModel):
    id: int
    project_id: int
    party_id: int
    overlap_score: float
    overlap_keywords: Optional[str] = None
    risk_level: str
    solution_type: Optional[str] = None
    solution_detail: Optional[str] = None
    solution_doc_path: Optional[str] = None
    assessed_by_display: Optional[str] = None
    assessed_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ============================================================
#  披露 diff
# ============================================================


class DisclosureCheckRequest(BaseModel):
    project_id: int
    prospectus_party_names: List[str] = Field(default_factory=list)


class DisclosureGapResponse(BaseModel):
    id: int
    project_id: int
    party_id: Optional[int] = None
    gap_status: str
    party_name: str
    in_system: bool
    in_prospectus: bool
    prospectus_section_ref: Optional[str] = None
    transaction_count: int
    total_amount: float
    suggested_action: Optional[str] = None
    resolved: bool
    resolved_at: Optional[datetime] = None
    resolved_by_display: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DisclosureCheckResponse(BaseModel):
    system_only: List[DisclosureGapResponse] = Field(default_factory=list)
    prospectus_only: List[DisclosureGapResponse] = Field(default_factory=list)
    matched: int = 0
    total_critical: int = 0
    total_review: int = 0


# ============================================================
#  专项报告
# ============================================================


class RelatedPartyReportRequest(BaseModel):
    project_id: int
    period_end: str
    include_sections: List[str] = Field(
        default_factory=lambda: [
            "summary",
            "main_data",
            "transactions",
            "capital_occupation",
            "peer_competition",
            "disclosure_gap",
            "remediation",
        ]
    )
    output_format: str = Field(default="docx", pattern=r"^(docx|xlsx)$")


__all__ = [
    "RelatedPartyBase",
    "RelatedPartyCreate",
    "RelatedPartyUpdate",
    "RelatedPartyResponse",
    "RelatedPartyListResponse",
    "RelationCreate",
    "RelationResponse",
    "DetectorRunRequest",
    "DetectorCandidate",
    "DetectorRunResponse",
    "TransactionCreate",
    "TransactionResponse",
    "FairnessCheckRequest",
    "FairnessCheckResponse",
    "CapitalOccupationCreate",
    "CapitalOccupationResponse",
    "PeerCompetitionAssessRequest",
    "PeerCompetitionResponse",
    "DisclosureCheckRequest",
    "DisclosureGapResponse",
    "DisclosureCheckResponse",
    "RelatedPartyReportRequest",
    "DISCLOSURE_GAP_CRITICAL",
    "DISCLOSURE_GAP_REVIEW",
    "DISCLOSURE_GAP_OK",
]
