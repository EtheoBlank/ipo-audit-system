"""网络核查引擎。

为综合底稿中 ``web_search:<query_id>`` 字段提供权威信息检索与填充。

按以下优先级查找：
1. **法规库**（CSRC / MOF / STA / SAFE / PBOC）— 来自 ``Regulation`` 表
2. **事务所知识库** — 来自 ``KnowledgeBaseService.search()``（基于书籍分块的向量/关键词检索）
3. **联网检索**（最后兜底）— 调用 ``WebSearch`` / ``WebFetch``

每条结果都附 ``citation`` 引用，确保审计员可追溯。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.services.comprehensive.schemas import FillResult, TemplateField

logger = logging.getLogger(__name__)


# ============================== 数据结构 ==============================


@dataclass
class SearchHit:
    """一次搜索的单条命中。"""

    title: str
    snippet: str  # 摘要/正文片段
    source: str  # 命中来源: "regulation" / "knowledge_base" / "web"
    citation: str  # 给审计员看的引用信息
    score: float = 1.0
    url: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


# 函数签名：输入 query 字符串，返回 List[SearchHit]（可异步）
RegulationSearchFn = Callable[[str, int], Awaitable[list[SearchHit]]]
KnowledgeBaseSearchFn = Callable[[str, int], Awaitable[list[SearchHit]]]
WebSearchFn = Callable[[str, int], Awaitable[list[SearchHit]]]


# ============================== 引擎 ==============================


class WebSearchError(Exception):
    """网络核查失败。"""


class WebSearchEngine:
    """网络核查引擎。"""

    def __init__(
        self,
        regulation_search: Optional[RegulationSearchFn] = None,
        kb_search: Optional[KnowledgeBaseSearchFn] = None,
        web_search: Optional[WebSearchFn] = None,
    ):
        # 默认三路都不可用时仍能跑（返回空命中）
        self._reg = regulation_search
        self._kb = kb_search
        self._web = web_search

    # ---------- 公共 API ----------

    async def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        """三路并发合并去重，按 score 排序。

        任一路异常被吞掉，其他路不受影响。
        """
        import asyncio

        tasks: list[tuple[str, Any]] = []
        for fn, label in (
            (self._reg, "regulation"),
            (self._kb, "knowledge_base"),
            (self._web, "web"),
        ):
            if fn is None:
                continue
            tasks.append((label, fn(query, top_k)))

        results = await asyncio.gather(*(t[1] for t in tasks), return_exceptions=True)
        hits: list[SearchHit] = []
        for (label, _), r in zip(tasks, results):
            if isinstance(r, Exception):
                logger.warning("[%s] 检索 '%s' 失败: %s", label, query, r)
                continue
            for h in r:
                if h.source == "":
                    h.source = label
                hits.append(h)
        hits.sort(key=lambda h: h.score, reverse=True)
        # 去重（按 title 简化去重）
        seen: set[str] = set()
        deduped: list[SearchHit] = []
        for h in hits:
            key = h.title.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(h)
        return deduped[:top_k]

    async def fill_field(
        self,
        field_def: TemplateField,
        context: dict[str, Any],
    ) -> FillResult:
        """为 ``web_search:<query_id>`` 字段生成 FillResult。"""
        if not field_def.source.startswith("web_search:"):
            return FillResult(
                field_id=field_def.field_id,
                value=None,
                source_used=field_def.source,
                confidence=0.0,
                citation="字段 source 非 web_search 前缀",
            )

        # 用 query_id + field_id + hint 拼出搜索词
        query = self._build_query(field_def, context)
        hits = await self.search(query, top_k=3)
        if not hits:
            return FillResult(
                field_id=field_def.field_id,
                value=None,
                source_used=f"web_search:{field_def.source.split(':', 1)[1]}",
                confidence=0.0,
                citation=f"未检索到 '{query}' 的权威信息，将由人工补全",
            )

        top = hits[0]
        return FillResult(
            field_id=field_def.field_id,
            value=top.snippet,
            source_used=f"web_search:{field_def.source.split(':', 1)[1]}",
            confidence=min(0.95, top.score),
            citation=f"[{top.source}] {top.title} — {top.citation}",
        )

    # ---------- helpers ----------

    @staticmethod
    def _build_query(field_def: TemplateField, context: dict[str, Any]) -> str:
        """根据 field 元信息 + 已填上下文拼出搜索词。"""
        query_id = field_def.source.split(":", 1)[1]
        parts: list[str] = [query_id, field_def.label]
        if field_def.hint:
            parts.append(field_def.hint)
        # 加入行业上下文（如果有）
        industry = context.get("industry")
        if industry:
            parts.append(str(industry))
        return " ".join(p for p in parts if p)


# ============================== 内置检索器 ==============================
# 这些函数会接入真实的 DB / 服务，对外暴露 SearchHit 列表。
# 设计为可注入，便于测试时 mock。


async def regulation_db_search(query: str, top_k: int) -> list[SearchHit]:
    """在 ``Regulation`` 表中按关键词搜索。

    实现要点：
      - 多关键词用 OR（任一命中即可），避免 AND 在中文短语下返回 0 行
      - 数量限制 + 简单打分（title 命中权重高）
    """
    try:
        from sqlalchemy import func, or_, select
        from app.core.database import AsyncSessionLocal
        from app.models.db_models import Regulation
    except ImportError:
        logger.debug("Regulation 模型不可用，跳过法规库检索")
        return []

    keywords = [w for w in query.split() if len(w) >= 2]
    if not keywords:
        return []

    hits: list[SearchHit] = []
    async with AsyncSessionLocal() as session:  # type: ignore[name-defined]
        # OR 命中：title / keywords / summary 任一含 keyword
        or_clauses = []
        for kw in keywords:
            or_clauses.append(Regulation.title.contains(kw))
            if hasattr(Regulation, "keywords") and Regulation.keywords is not None:
                or_clauses.append(Regulation.keywords.contains(kw))
        if not or_clauses:
            return []
        stmt = select(Regulation).where(or_(*or_clauses)).limit(top_k * 3)
        rows = (await session.execute(stmt)).scalars().all()

        # 简单打分：title 中命中数 × 3 + keywords 中命中数 × 2
        scored: list[tuple[float, Any]] = []
        for r in rows:
            title = r.title or ""
            kw_text = getattr(r, "keywords", None) or ""
            score = (
                sum(1 for kw in keywords if kw in title) * 3.0
                + sum(1 for kw in keywords if kw in kw_text) * 2.0
            )
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda t: t[0], reverse=True)
        for score, r in scored[:top_k]:
            text = getattr(r, "summary", None) or (r.full_text or "")[:200]
            hits.append(
                SearchHit(
                    title=r.title,
                    snippet=text,
                    source="regulation",
                    citation=f"{r.source} · {r.document_no or '—'} · {r.publish_date or '—'}",
                    url=r.source_url,
                    score=min(0.95, 0.5 + score / 10.0),
                    meta={
                        "issuing_authority": r.issuing_authority,
                        "category": r.category,
                    },
                )
            )
    return hits


async def knowledge_base_search(query: str, top_k: int) -> list[SearchHit]:
    """在事务所知识库（书籍分块）中搜索。"""
    try:
        from app.services.knowledge_base.service import KnowledgeBaseService
    except ImportError:
        return []

    try:
        svc = KnowledgeBaseService()
        rows = await svc.search(query=query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("知识库检索失败: %s", exc)
        return []

    hits: list[SearchHit] = []
    for row in rows or []:
        # KnowledgeBookChunk 的字段约定
        content = getattr(row, "content", "") or ""
        title = getattr(row, "book_title", None) or "知识库"
        page = getattr(row, "page", None)
        score = float(getattr(row, "score", 0.7) or 0.7)
        hits.append(
            SearchHit(
                title=title,
                snippet=content[:400],
                source="knowledge_base",
                citation=(f"{title} 第 {page} 页" if page else title),
                score=score,
            )
        )
    return hits


async def live_web_search(query: str, top_k: int) -> list[SearchHit]:
    """兜底：使用 WebSearch MCP 工具做实时检索。

    该函数依赖宿主环境的 WebSearch 工具，未启用时返回空。
    """
    # 注意：此函数不在常规 pytest 中运行（避免外部依赖）
    try:
        from app.core.mcp_clients import web_search  # type: ignore
    except ImportError:
        logger.debug("未配置 WebSearch MCP 客户端，跳过联网检索")
        return []

    try:
        results = await web_search(query, top_k=top_k)  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebSearch 失败: %s", exc)
        return []

    return [
        SearchHit(
            title=r.get("title", ""),
            snippet=r.get("snippet", ""),
            source="web",
            citation=r.get("url", ""),
            url=r.get("url"),
            score=float(r.get("score", 0.5) or 0.5),
        )
        for r in (results or [])
    ]


def default_web_search_engine() -> WebSearchEngine:
    """构造默认引擎，注入内置检索器。"""
    return WebSearchEngine(
        regulation_search=regulation_db_search,
        kb_search=knowledge_base_search,
        web_search=live_web_search,
    )
