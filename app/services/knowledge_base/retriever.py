"""检索器 (Retriever).

输入查询文本 → 编码 → 与库中 chunk 算 cosine → 关键词加权 → 返回 top-k。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import KnowledgeBook, KnowledgeChunk
from app.services.knowledge_base.embedder import cosine, json_to_vec

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: int
    book_id: int
    book_title: str
    chapter: Optional[str]
    section: Optional[str]
    page: Optional[int]
    content: str
    score: float
    semantic_score: float
    keyword_score: float


def _keyword_score(query: str, content: str, keywords: str | None) -> float:
    """关键词加权得分 — 命中率粗略估算。

    匹配条目：
      - query 整词出现
      - query 拆词后的子串出现
      - chunk.keywords 中包含的关键词
    """
    query = query.strip()
    if not query:
        return 0.0
    text = content.lower()
    score = 0.0
    if query.lower() in text:
        score += 1.0
    # 关键词分词 (空格/标点)
    tokens = [t for t in re.split(r"[\s,，。；;:：、/]+", query) if len(t) >= 2]
    if tokens:
        hits = sum(1 for t in tokens if t.lower() in text)
        score += 0.5 * (hits / len(tokens))
    if keywords:
        kw_list = [k for k in re.split(r"[,，;；\s]+", keywords) if k]
        if kw_list:
            kw_hits = sum(1 for k in kw_list if k.lower() in query.lower())
            score += 0.3 * (kw_hits / len(kw_list))
    return min(score, 2.0) / 2.0  # 归一化到 [0,1]


async def retrieve(
    db: AsyncSession,
    query: str,
    query_vector: Optional[List[float]] = None,
    top_k: int = 5,
    book_ids: Optional[Sequence[int]] = None,
    category: Optional[str] = None,
    firm_id: Optional[int] = None,
    min_score: float = 0.0,
    keyword_weight: float = 0.3,
) -> List[RetrievedChunk]:
    """在已索引的知识库中检索相似 chunk.

    Args:
        query:        原始查询文本 (用于关键词得分)
        query_vector: query 的向量；若 None 则只用关键词匹配
        top_k:        返回条数
        book_ids:     限定在哪几本书检索 (None = 全部)
        category:     按书的 category 过滤
        firm_id:      P0 (2026-06-19): 多租户隔离, 仅检索指定事务所的书
                     None 表示不限 (admin / 测试用); 普通用户应传自己 firm_id
        min_score:    分数低于此阈值的丢弃
        keyword_weight: 关键词得分在最终分数里的权重 (0~1)
    """
    stmt = select(KnowledgeChunk, KnowledgeBook).join(
        KnowledgeBook, KnowledgeChunk.book_id == KnowledgeBook.id
    )
    if book_ids:
        stmt = stmt.where(KnowledgeChunk.book_id.in_(list(book_ids)))
    if category:
        stmt = stmt.where(KnowledgeBook.category == category)
    if firm_id is not None:
        # P0 多租户: 仅查指定 firm 的书, 跨所不可见
        stmt = stmt.where(KnowledgeBook.firm_id == firm_id)
    rows = (await db.execute(stmt)).all()

    semantic_weight = 1.0 - keyword_weight
    scored: List[RetrievedChunk] = []
    for chunk, book in rows:
        sem = 0.0
        if query_vector is not None and chunk.embedding:
            sem = max(cosine(query_vector, json_to_vec(chunk.embedding)), 0.0)
        kw = _keyword_score(query, chunk.content, chunk.keywords)
        final = semantic_weight * sem + keyword_weight * kw
        if final < min_score:
            continue
        scored.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                book_id=book.id,
                book_title=book.title,
                chapter=chunk.chapter,
                section=chunk.section,
                page=chunk.page,
                content=chunk.content,
                score=round(final, 4),
                semantic_score=round(sem, 4),
                keyword_score=round(kw, 4),
            )
        )

    scored.sort(key=lambda x: -x.score)
    return scored[:top_k]
