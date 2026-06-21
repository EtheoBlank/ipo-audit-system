"""文档解析器 — 把书籍 / 文档转为带定位信息的段落列表。

返回结构：``List[ParsedSegment]``，每个 segment 包含：
  - text:    段落文本
  - chapter: 章节 (如果能识别)
  - section: 节 (如果能识别)
  - page:    页码 (PDF / EPUB 有，DOCX 没有)

设计原则：**任何依赖都标记为 optional**。pdfplumber / python-docx / ebooklib
都已经在项目里或可单独装；缺少时返回一个友好的错误，而不是让整个服务起不来。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedSegment:
    text: str
    chapter: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None


# ----------------------------------------------------------------------
# 章节识别 (中文)
# ----------------------------------------------------------------------


_CHAPTER_PAT = re.compile(r"^\s*(第[一二三四五六七八九十百零〇\d]+[章篇编]\s*[^\n]{0,40})")
_SECTION_PAT = re.compile(r"^\s*(第[一二三四五六七八九十百零〇\d]+节\s*[^\n]{0,40})")


def _scan_heading(line: str) -> tuple[Optional[str], Optional[str]]:
    """返回 (chapter, section) — 若该行是章/节标题。"""
    if not line:
        return None, None
    m_chap = _CHAPTER_PAT.match(line)
    if m_chap:
        return m_chap.group(1).strip(), None
    m_sec = _SECTION_PAT.match(line)
    if m_sec:
        return None, m_sec.group(1).strip()
    return None, None


# ----------------------------------------------------------------------
# 各格式 Loader
# ----------------------------------------------------------------------


def _load_txt(path: Path) -> List[ParsedSegment]:
    """TXT / MD 通用：按双换行切大段。"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    segments: List[ParsedSegment] = []
    current_chapter: Optional[str] = None
    current_section: Optional[str] = None
    for paragraph in re.split(r"\n\s*\n", text):
        p = paragraph.strip()
        if not p:
            continue
        first_line = p.split("\n", 1)[0]
        chap, sec = _scan_heading(first_line)
        if chap:
            current_chapter = chap
            current_section = None
        if sec:
            current_section = sec
        segments.append(
            ParsedSegment(
                text=p,
                chapter=current_chapter,
                section=current_section,
            )
        )
    return segments


def _load_pdf(path: Path) -> List[ParsedSegment]:
    """PDF — 通过 pdfplumber 逐页提取文本，再按段落切。"""
    try:
        import pdfplumber  # 已在 deps
    except ImportError as e:
        raise RuntimeError("PDF 解析需要 pdfplumber，请 `uv add pdfplumber`") from e

    segments: List[ParsedSegment] = []
    current_chapter: Optional[str] = None
    current_section: Optional[str] = None
    with pdfplumber.open(str(path)) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                # round 36 P1: debug 留不下 traceback, 改 exception (INFO 级以免漏)
                logger.exception("PDF page %d 抽取失败，跳过", idx)
                continue
            for paragraph in re.split(r"\n\s*\n", page_text):
                p = paragraph.strip()
                if not p:
                    continue
                first_line = p.split("\n", 1)[0]
                chap, sec = _scan_heading(first_line)
                if chap:
                    current_chapter = chap
                    current_section = None
                if sec:
                    current_section = sec
                segments.append(
                    ParsedSegment(
                        text=p,
                        chapter=current_chapter,
                        section=current_section,
                        page=idx,
                    )
                )
    return segments


def _load_docx(path: Path) -> List[ParsedSegment]:
    """Word 文档 — python-docx 已在 deps。"""
    try:
        from docx import Document  # python-docx
    except ImportError as e:
        raise RuntimeError("DOCX 解析需要 python-docx，请 `uv add python-docx`") from e

    doc = Document(str(path))
    segments: List[ParsedSegment] = []
    current_chapter: Optional[str] = None
    current_section: Optional[str] = None
    buf: List[str] = []

    def _flush():
        if not buf:
            return
        text = "\n".join(buf).strip()
        if text:
            segments.append(
                ParsedSegment(
                    text=text,
                    chapter=current_chapter,
                    section=current_section,
                )
            )
        buf.clear()

    for para in doc.paragraphs:
        style = (para.style.name or "").lower() if para.style else ""
        text = (para.text or "").strip()
        if not text:
            _flush()
            continue
        chap, sec = _scan_heading(text)
        if chap or "heading 1" in style or "标题 1" in style:
            _flush()
            current_chapter = chap or text
            current_section = None
            continue
        if sec or "heading 2" in style or "标题 2" in style:
            _flush()
            current_section = sec or text
            continue
        buf.append(text)
    _flush()
    return segments


def _load_epub(path: Path) -> List[ParsedSegment]:
    """EPUB — 依赖 ebooklib + BeautifulSoup。"""
    try:
        import ebooklib  # type: ignore[import-not-found]
        from ebooklib import epub  # type: ignore[import-not-found]
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise RuntimeError("EPUB 解析需要 ebooklib，请 `uv add ebooklib`") from e

    book = epub.read_epub(str(path))
    segments: List[ParsedSegment] = []
    current_chapter: Optional[str] = None
    current_section: Optional[str] = None
    for idx, item in enumerate(book.get_items(), start=1):
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content(), "lxml")
        # 优先 h1 / h2 当章节
        h1 = soup.find(["h1"])
        if h1:
            current_chapter = h1.get_text(strip=True)
            current_section = None
        for h2 in soup.find_all(["h2", "h3"]):
            current_section = h2.get_text(strip=True)
            break
        for p in soup.find_all(["p", "div"]):
            text = p.get_text(strip=True)
            if len(text) < 10:
                continue
            chap, sec = _scan_heading(text)
            if chap:
                current_chapter = chap
                current_section = None
            elif sec:
                current_section = sec
            segments.append(
                ParsedSegment(
                    text=text,
                    chapter=current_chapter,
                    section=current_section,
                    page=idx,
                )
            )
    return segments


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------


def load_document(path: Path) -> List[ParsedSegment]:
    """根据扩展名分派到对应 loader，并做基础清洗。"""
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"txt", "md", "markdown"}:
        segs = _load_txt(path)
    elif suffix == "pdf":
        segs = _load_pdf(path)
    elif suffix in {"docx"}:
        segs = _load_docx(path)
    elif suffix == "epub":
        segs = _load_epub(path)
    else:
        raise ValueError(f"不支持的文档类型: .{suffix} (支持 pdf / epub / docx / txt / md)")

    # 去掉过短 / 纯页眉页脚
    cleaned = []
    for s in segs:
        text = re.sub(r"\s+", " ", s.text).strip()
        if len(text) < 8:
            continue
        s.text = text
        cleaned.append(s)
    logger.info("文档 %s 解析为 %d 段", path.name, len(cleaned))
    return cleaned


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix in {"md", "markdown"}:
        return "md"
    return suffix
