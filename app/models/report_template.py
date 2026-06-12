"""Pydantic schemas for Report Template module."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.models.db.report_template import (
    ALL_REPORT_TYPES,
    REPORT_FORMAT_DOCX,
    REPORT_FORMAT_XLSX,
    REPORT_FORMAT_PDF,
)


class ReportTemplateBase(BaseModel):
    template_code: str = Field(..., min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_.\-]+$")
    template_name: str = Field(..., min_length=1, max_length=200)
    report_type: str
    version: str = Field(default="v1", max_length=20)
    output_format: str = Field(default=REPORT_FORMAT_DOCX)
    description: Optional[str] = None

    @field_validator("report_type")
    @classmethod
    def _type_known(cls, v: str) -> str:
        if v not in ALL_REPORT_TYPES:
            raise ValueError(f"report_type 必须是 {ALL_REPORT_TYPES} 之一")
        return v

    @field_validator("output_format")
    @classmethod
    def _format_known(cls, v: str) -> str:
        if v not in {REPORT_FORMAT_DOCX, REPORT_FORMAT_XLSX, REPORT_FORMAT_PDF}:
            raise ValueError("output_format 仅支持 docx/xlsx/pdf")
        return v


class ReportTemplateCreate(ReportTemplateBase):
    firm_id: Optional[int] = None


class ReportTemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ReportTemplateResponse(ReportTemplateBase):
    id: int
    firm_id: Optional[int] = None
    template_filename: str
    template_size: int
    template_sha256: Optional[str] = None
    placeholder_schema: Optional[str] = None
    is_active: bool
    is_builtin: bool
    created_by_user_id: Optional[int] = None
    created_by_display: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ReportTemplateListResponse(BaseModel):
    total: int
    items: List[ReportTemplateResponse]


class ReportRenderRequest(BaseModel):
    template_id: int
    project_id: Optional[int] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    output_filename: Optional[str] = None


class ReportRenderHistoryResponse(BaseModel):
    id: int
    template_id: int
    project_id: Optional[int] = None
    output_filename: str
    output_size: int
    success: bool
    error_msg: Optional[str] = None
    rendered_by_user_id: Optional[int] = None
    rendered_by_display: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TemplateAnalyzeResponse(BaseModel):
    """探测出来的 placeholder 列表 (用户上传后预览)."""
    placeholders: List[str]
    duplicates: List[str]
    unknown_tags: List[str]
    is_valid: bool
    suggested_context_keys: Dict[str, str] = Field(default_factory=dict)


__all__ = [
    "ReportTemplateBase",
    "ReportTemplateCreate",
    "ReportTemplateUpdate",
    "ReportTemplateResponse",
    "ReportTemplateListResponse",
    "ReportRenderRequest",
    "ReportRenderHistoryResponse",
    "TemplateAnalyzeResponse",
]
