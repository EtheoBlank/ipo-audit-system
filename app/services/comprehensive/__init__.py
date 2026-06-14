"""Comprehensive workpaper auto-generation subpackage.

将"基础底稿 + 审计手册 + 联网核查 + 一次性问答"四路数据自动汇入综合底稿 Excel。
模板规范见 `docs/COMPREHENSIVE_WORKPAPER_TEMPLATE_SPEC.md`。
"""

from app.services.comprehensive.field_mapper import (
    DataPath,
    FieldMapper,
    MappingError,
    WorkpaperDataContext,
    parse_workpaper_source,
)
from app.services.comprehensive.schemas import (
    FillReport,
    FillResult,
    PendingQuestion,
    TemplateField,
    TemplateSchema,
)
from app.services.comprehensive.template_parser import TemplateParseError, TemplateParser

__all__ = [
    "DataPath",
    "FieldMapper",
    "FillReport",
    "FillResult",
    "MappingError",
    "PendingQuestion",
    "TemplateField",
    "TemplateParseError",
    "TemplateParser",
    "TemplateSchema",
    "WorkpaperDataContext",
    "parse_workpaper_source",
]
