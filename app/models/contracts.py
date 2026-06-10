"""Pydantic schemas for the contract (收入合同) analysis module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- contract document -------------------------------------------


class ContractDocumentResponse(BaseModel):
    id: int
    project_id: int
    filename: str
    media_type: str
    ocr_engine: Optional[str] = None
    ocr_text: str = ""
    note: Optional[str] = None
    key_points: Optional[dict[str, Any]] = None
    five_step_analysis: Optional[dict[str, Any]] = None
    risk_flags: Optional[list[str]] = None
    uploaded_at: datetime
    analyzed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------- analysis request --------------------------------------------


class ContractAnalysisRequest(BaseModel):
    project_id: int
    contract_id: int
    run_key_points: bool = True
    run_five_step: bool = True


class ContractAnalysisResponse(BaseModel):
    contract_id: int
    project_id: int
    key_points: Optional[dict[str, Any]] = None
    five_step_analysis: Optional[dict[str, Any]] = None
    risk_flags: list[str] = Field(default_factory=list)
    analyzed_at: Optional[datetime] = None
