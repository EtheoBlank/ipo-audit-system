"""季度报告 .docx 导出 — 复用简报 word_exporter 的 Markdown 解析."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services.sentiment.briefing.word_exporter import BriefingWordExporter

logger = logging.getLogger(__name__)


class QuarterlyReportWordExporter:
    """季度报告 Markdown → .docx. 复用 BriefingWordExporter 的 Markdown 解析."""

    def __init__(self) -> None:
        self.output_dir = settings.SENTIMENT_OUTPUT_DIR / "quarterly"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._delegated = BriefingWordExporter()

    def export(
        self,
        project_id: int,
        period_type: str,
        fiscal_year: int,
        company_name: str,
        markdown: str,
        *,
        version_no: Optional[int] = None,
    ) -> tuple[Path, str]:
        from app.models.db_models import SENTIMENT_PERIOD_TYPE_LABELS
        label = SENTIMENT_PERIOD_TYPE_LABELS.get(period_type, period_type)
        # 把 markdown 灌给 delegated, 但文件名前缀用 project_id+period
        suffix = f"_v{version_no}" if version_no and version_no > 1 else ""
        # 直接调 delegated.export 但路径会被它写到 briefings/ 下 — 我们要重定向
        # 简化: 自己写一个最小 export
        from docx import Document
        from docx.shared import Pt
        doc = Document()
        style = doc.styles["Normal"]
        style.font.size = Pt(10.5)
        style.font.name = "Microsoft YaHei"
        doc.add_heading(f"{company_name} {fiscal_year} {label} 跟踪报告", level=0)
        # 复用 markdown 解析
        self._delegated._render_markdown_into_doc(doc, markdown)
        fname = f"project_{project_id}_{period_type}_{fiscal_year}{suffix}.docx"
        path = self.output_dir / fname
        doc.save(str(path))
        digest = self._sha256_file(path)
        logger.info("QuarterlyReportWordExporter: %s -> %s (sha256=%s)", company_name, path, digest[:12])
        return path, digest

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
