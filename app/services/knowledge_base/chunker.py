"""中文友好的文本切块 (chunker).

策略：
  1. 输入：``ParsedSegment`` 序列 (段落 + 章/节/页 元数据)
  2. 把段落按字符数累积到接近 ``chunk_size`` 后切一刀
  3. 相邻 chunk 之间保留 ``overlap`` 个字符的重叠，保证语义不被截断

为什么不直接按句号切？— 中文书里大段引用、公式表格混杂，句号粒度偏小，反而
让单个 chunk 信息密度不够；段落级 + 字数阈值在实务案例检索里效果更稳。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from app.services.knowledge_base.document_loader import ParsedSegment


@dataclass
class TextChunk:
    chunk_index: int
    content: str
    chapter: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    char_count: int = 0


def chunk_segments(
    segments: List[ParsedSegment],
    chunk_size: int = 600,
    overlap: int = 80,
) -> List[TextChunk]:
    """把段落序列合并/切分为 chunk。

    Args:
        segments: ``document_loader.load_document`` 的输出
        chunk_size: 单 chunk 目标字符数
        overlap: 与上一个 chunk 重叠的尾部字符数 (用于保留上下文)

    Returns:
        TextChunk 列表，按出现顺序编号
    """
    chunks: List[TextChunk] = []
    if not segments:
        return chunks

    buf: List[str] = []
    buf_len = 0
    current_chapter = segments[0].chapter
    current_section = segments[0].section
    current_page = segments[0].page

    def _flush(
        next_chapter: Optional[str], next_section: Optional[str], next_page: Optional[int]
    ) -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        content = "\n".join(buf).strip()
        if not content:
            buf, buf_len = [], 0
            return
        chunks.append(
            TextChunk(
                chunk_index=len(chunks),
                content=content,
                chapter=current_chapter,
                section=current_section,
                page=current_page,
                char_count=len(content),
            )
        )
        # 保留尾部 overlap 字符进入下一个 chunk
        tail = content[-overlap:] if overlap and len(content) > overlap else ""
        buf = [tail] if tail else []
        buf_len = len(tail)

    for seg in segments:
        # 章节切换 → 强制 flush，避免一个 chunk 跨章
        if (seg.chapter and seg.chapter != current_chapter) or (
            seg.section and seg.section != current_section
        ):
            _flush(seg.chapter, seg.section, seg.page)
            current_chapter = seg.chapter or current_chapter
            current_section = seg.section
            current_page = seg.page

        # 单段过长 — 按硬阈值再切
        if len(seg.text) > chunk_size * 1.5:
            _flush(seg.chapter, seg.section, seg.page)
            for piece in _split_long_paragraph(seg.text, chunk_size, overlap):
                chunks.append(
                    TextChunk(
                        chunk_index=len(chunks),
                        content=piece,
                        chapter=current_chapter,
                        section=current_section,
                        page=seg.page or current_page,
                        char_count=len(piece),
                    )
                )
            continue

        if buf_len + len(seg.text) > chunk_size and buf_len > 0:
            _flush(seg.chapter, seg.section, seg.page)
        buf.append(seg.text)
        buf_len += len(seg.text)
        if seg.page is not None:
            current_page = seg.page

    _flush(None, None, None)
    return chunks


def _split_long_paragraph(text: str, chunk_size: int, overlap: int) -> List[str]:
    """按句号 / 分号 / 换行兜底切长段。"""
    sentences = re.split(r"(?<=[。！？!?；;])", text)
    out: List[str] = []
    buf: List[str] = []
    buf_len = 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if buf_len + len(s) > chunk_size and buf:
            out.append("".join(buf))
            tail = (out[-1][-overlap:]) if overlap else ""
            buf = [tail] if tail else []
            buf_len = len(tail)
        buf.append(s)
        buf_len += len(s)
    if buf:
        out.append("".join(buf))
    return out


# ----------------------------------------------------------------------
# 关键词提取 (简易) — 给 chunk 标关键词，加速过滤
# ----------------------------------------------------------------------

_STOPWORDS = set(
    "的 了 是 在 和 或 与 也 都 及 等 等等 一 一个 这 那 这些 那些 我 你 他 我们 你们 他们 "
    "因 因为 所以 但是 然而 而且 并且 如果 那么 这样 那样 即 应 应当 应该 可以 不能 不会 "
    "本 本节 本章 本款 本文 上述 下列 其中 其 此 该 该项 该等 即将 业已 仅 较 较为".split()
)


def extract_keywords(text: str, top_k: int = 8) -> List[str]:
    """非常轻量的中文关键词抽取 — 2-4 字词频统计。

    不上 jieba 是为了避免新增依赖；遇到大量专业语料时检索效果靠语义向量补足。
    """
    text = re.sub(r"[^一-龥A-Za-z0-9]+", " ", text)
    grams: dict[str, int] = {}
    tokens = text.split()
    for tok in tokens:
        if 2 <= len(tok) <= 12 and tok not in _STOPWORDS:
            # 英文/数字直接计 + 中文 2-4 字滑窗
            if re.fullmatch(r"[A-Za-z0-9]+", tok):
                grams[tok] = grams.get(tok, 0) + 1
                continue
            for n in (2, 3, 4):
                for i in range(0, len(tok) - n + 1):
                    g = tok[i : i + n]
                    if g in _STOPWORDS:
                        continue
                    grams[g] = grams.get(g, 0) + 1
    return [w for w, _ in sorted(grams.items(), key=lambda x: -x[1])[:top_k]]
