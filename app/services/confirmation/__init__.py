"""Confirmation (函证) sub-package.

Public surface:
- ``ConfirmationStatsBuilder``     — 从账套自动生成函证统计表
- ``ConfirmationLetterGenerator``  — 生成询证函 (docx/pdf)
- ``ConfirmationResponseProcessor``— 回函照片 OCR + AI 解析 + 回填
- ``ConfirmationExporter``         — 导出统计表 + 函证工作簿
"""

from app.services.confirmation.stats_builder import ConfirmationStatsBuilder
from app.services.confirmation.letter_generator import (
    ConfirmationLetterGenerator,
    LetterGenerationError,
)
from app.services.confirmation.response_processor import (
    ConfirmationResponseProcessor,
    ResponseParseError,
)
from app.services.confirmation.excel_exporter import ConfirmationExporter

__all__ = [
    "ConfirmationStatsBuilder",
    "ConfirmationLetterGenerator",
    "LetterGenerationError",
    "ConfirmationResponseProcessor",
    "ResponseParseError",
    "ConfirmationExporter",
]
