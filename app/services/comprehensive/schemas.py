"""Pydantic schemas for comprehensive workpaper system."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 字段类型
FieldType = Literal[
    "text", "text_long", "number", "percent", "date", "choice", "boolean"
]

# 填充来源前缀
SourcePrefix = Literal["workpaper", "rule", "web_search", "human_qa", "calculated"]


class TemplateField(BaseModel):
    """模板中一个待填字段的描述。"""

    field_id: str = Field(..., description="字段唯一 ID，小写字母/数字/下划线")
    label: str = Field(..., description="字段中文名")
    type: FieldType = Field("text", description="字段类型")
    source: str = Field(..., description="填充来源，格式: <prefix>:<path>")
    required: bool = Field(False, description="是否必填")
    hint: Optional[str] = Field(None, description="填写提示")
    options: Optional[list[str]] = Field(None, description="choice 类型的可选项")
    cell_ref: str = Field(..., description="单元格引用，如 '应收账款!A5'")
    name_range: Optional[str] = Field(None, description="命名区域名（如有）")
    sheet: str = Field(..., description="所属工作表")
    row: int = Field(..., description="行号（1-based）")
    column: int = Field(..., description="列号（1-based）")

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        """校验 source 形如 'workpaper:xxx' / 'human_qa' / 'calculated:xxx'。"""
        valid_prefixes = ("workpaper:", "rule:", "web_search:", "human_qa", "calculated:")
        if not any(v == p.rstrip(":") or v.startswith(p) for p in valid_prefixes):
            raise ValueError(
                f"source 必须以以下前缀之一开头: {valid_prefixes}，实际为 '{v}'"
            )
        return v

    @field_validator("options", mode="before")
    @classmethod
    def _split_options(cls, v):
        """支持 '_meta' 表中 'a,b,c' 字符串或 list。"""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


class TemplateSchema(BaseModel):
    """解析后的综合底稿模板。"""

    template_id: str
    template_name: str
    version: str
    firm_id: str
    industry: Optional[str] = None
    audit_period: Optional[str] = None
    required_workpapers: list[str] = Field(default_factory=list)
    manual_ref: Optional[str] = None
    fields: list[TemplateField] = Field(default_factory=list)
    sheets: list[str] = Field(default_factory=list)

    def get_field(self, field_id: str) -> Optional[TemplateField]:
        """按 ID 查找字段。"""
        for f in self.fields:
            if f.field_id == field_id:
                return f
        return None

    def fields_by_source(self, prefix: str) -> list[TemplateField]:
        """按 source 前缀过滤（如 'workpaper:' / 'human_qa'）。"""
        if prefix == "human_qa":
            return [f for f in self.fields if f.source == "human_qa"]
        return [f for f in self.fields if f.source.startswith(prefix)]


class FillResult(BaseModel):
    """单个字段的填充结果。"""

    field_id: str
    value: object
    source_used: str  # 实际采用的来源描述
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    citation: Optional[str] = None  # 引用/依据


class FillReport(BaseModel):
    """整份综合底稿的填充报告。"""

    template_id: str
    total_fields: int
    filled: int
    pending: int
    results: list[FillResult]
    open_questions: list["PendingQuestion"] = Field(default_factory=list)


class PendingQuestion(BaseModel):
    """需要人类回答的问题。"""

    question_id: str
    field_ids: list[str]  # 同一问题可对应多个字段（合并提问）
    prompt: str
    context: str
    topic: str  # 主题分类，用于聚类展示
    options: Optional[list[str]] = None
