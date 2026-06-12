"""Markdown → .docx — 照搬 app.services.report_generator.ComprehensiveReportGenerator 风格.

差异:
- 接受 markdown 字符串, 简单逐段解析
- 落盘路径: settings.SENTIMENT_OUTPUT_DIR / "briefings" / "{project_id}_{date}.docx"
- 返回 (file_path, sha256)
"""
from __future__ import annotations

import hashlib
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Optional

from docx import Document
from docx.shared import Pt

from app.core.config import settings

logger = logging.getLogger(__name__)


class BriefingWordExporter:
    """简报 Markdown → .docx.

    极简实现: 把 Markdown 按行处理
        - # / ## / ### → heading
        - | col | col |  → table (简单支持)
        - 1. / 2.       → numbered list
        - - item        → bullet
        - 其它          → paragraph
    不追求完整 Markdown 渲染, 但能稳定呈现 4 轮 LLM 的产物.
    """

    def __init__(self) -> None:
        self.output_dir = settings.SENTIMENT_OUTPUT_DIR / "briefings"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(
        self,
        project_id: int,
        briefing_date: str,
        company_name: str,
        markdown: str,
        *,
        version_no: Optional[int] = None,
    ) -> tuple[Path, str]:
        """生成 .docx, 落盘, 返回 (path, sha256)."""
        doc = Document()
        # 默认中文字体
        style = doc.styles["Normal"]
        style.font.size = Pt(10.5)
        style.font.name = "Microsoft YaHei"

        # 顶部标题
        doc.add_heading(f"{company_name} {briefing_date} 舆情简报", level=0)

        self._render_markdown_into_doc(doc, markdown)

        # 落盘
        suffix = f"_v{version_no}" if version_no and version_no > 1 else ""
        fname = f"project_{project_id}_{briefing_date}{suffix}.docx"
        path = self.output_dir / fname
        doc.save(str(path))

        # sha256
        digest = self._sha256_file(path)
        logger.info("BriefingWordExporter: %s -> %s (sha256=%s)", company_name, path, digest[:12])
        return path, digest

    def _render_markdown_into_doc(self, doc: Document, markdown: str) -> None:
        lines = markdown.splitlines()
        i = 0
        in_table = False
        table_buf: list[list[str]] = []

        def flush_table() -> None:
            nonlocal table_buf, in_table
            if not table_buf:
                in_table = False
                return
            cols = max(len(r) for r in table_buf)
            t = doc.add_table(rows=len(table_buf), cols=cols)
            t.style = "Table Grid"
            for r_idx, row in enumerate(table_buf):
                for c_idx in range(cols):
                    val = row[c_idx] if c_idx < len(row) else ""
                    t.cell(r_idx, c_idx).text = val
            table_buf = []
            in_table = False
            doc.add_paragraph("")  # 表后空行

        while i < len(lines):
            line = lines[i].rstrip()
            stripped = line.strip()

            # 表格行
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                # 跳过分隔行 | --- | --- |
                if all(re.match(r"^[-:\s]+$", c) for c in cells):
                    i += 1
                    continue
                table_buf.append(cells)
                in_table = True
                i += 1
                continue
            else:
                if in_table:
                    flush_table()

            # 标题
            m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if m:
                level = min(len(m.group(1)), 9)
                doc.add_heading(m.group(2), level=level)
                i += 1
                continue

            # 引用块
            if stripped.startswith(">"):
                doc.add_paragraph(stripped.lstrip(">").strip(), style="Intense Quote")
                i += 1
                continue

            # 数字列表
            m = re.match(r"^(\d+)\.\s+(.*)$", stripped)
            if m:
                doc.add_paragraph(f"{m.group(1)}. {m.group(2)}", style="List Number")
                i += 1
                continue

            # 无序列表
            if stripped.startswith("- "):
                doc.add_paragraph(stripped[2:], style="List Bullet")
                i += 1
                continue

            # 空行
            if not stripped:
                doc.add_paragraph("")
                i += 1
                continue

            # 普通段落
            doc.add_paragraph(stripped)
            i += 1

        if in_table:
            flush_table()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
