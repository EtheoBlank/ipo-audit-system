"""Tests for the web search engine."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.comprehensive.schemas import TemplateField
from app.services.comprehensive.web_search_engine import (
    SearchHit,
    WebSearchEngine,
)


def _field(field_id: str, source: str, hint: str | None = None) -> TemplateField:
    return TemplateField(
        field_id=field_id, label=field_id, type="text_long", source=source,
        hint=hint, cell_ref="A1", sheet="s", row=1, column=1,
    )


# ---------- helpers ----------

async def _fake_reg(query: str, top_k: int) -> list[SearchHit]:
    if "应收账款" in query:
        return [SearchHit(
            title="CAS 22 — 金融工具确认和计量",
            snippet="应收账款减值的披露要求...",
            source="",
            citation="财政部 · 财会〔2017〕7号 · 2017-03-31",
            score=0.92,
        )]
    return []


async def _fake_kb(query: str, top_k: int) -> list[SearchHit]:
    if "披露" in query:
        return [SearchHit(
            title="IPO 审计实务指南",
            snippet="应收账款的披露口径...",
            source="",
            citation="《IPO 审计实务指南》第 120 页",
            score=0.85,
        )]
    return []


async def _fake_web(query: str, top_k: int) -> list[SearchHit]:
    return [SearchHit(
        title="证监会 — 上市公司信息披露",
        snippet="...",
        source="",
        citation="https://example.com/csrc",
        score=0.6,
    )]


# ---------- search() ----------

@pytest.mark.asyncio
async def test_search_merges_three_sources():
    engine = WebSearchEngine(
        regulation_search=_fake_reg,
        kb_search=_fake_kb,
        web_search=_fake_web,
    )
    # 三个 query_id 都不命中具体关键词，但 web_search 兜底
    hits = await engine.search("应收账款 披露", top_k=5)
    sources = {h.source for h in hits}
    assert "regulation" in sources
    assert "knowledge_base" in sources
    assert "web" in sources
    # 排序按 score 降序
    assert hits[0].score >= hits[-1].score


@pytest.mark.asyncio
async def test_search_dedup_by_title():
    async def _dupe(query, k):
        return [
            SearchHit(title="A", snippet="x", source="", citation="1", score=0.9),
            SearchHit(title="A", snippet="y", source="", citation="2", score=0.8),
            SearchHit(title="B", snippet="z", source="", citation="3", score=0.7),
        ]
    engine = WebSearchEngine(regulation_search=_dupe)
    hits = await engine.search("any", top_k=5)
    assert len(hits) == 2  # 标题 A 去重


@pytest.mark.asyncio
async def test_search_handles_failing_source_gracefully():
    async def _bad(query, k):
        raise RuntimeError("boom")
    engine = WebSearchEngine(
        regulation_search=_bad,
        kb_search=_fake_kb,
    )
    hits = await engine.search("披露", top_k=5)
    # 异常被吞掉，kb 的结果保留
    assert any(h.source == "knowledge_base" for h in hits)


@pytest.mark.asyncio
async def test_search_no_sources_returns_empty():
    engine = WebSearchEngine()
    assert await engine.search("anything") == []


# ---------- fill_field() ----------

@pytest.mark.asyncio
async def test_fill_field_uses_top_hit():
    engine = WebSearchEngine(
        regulation_search=_fake_reg,
        kb_search=_fake_kb,
        web_search=_fake_web,
    )
    f = _field("disclosure_note", "web_search:csrc_ar_disclosure",
               hint="应收账款披露要求")
    ctx = {"industry": "制造业"}
    r = await engine.fill_field(f, ctx)
    assert r.value is not None
    # 最高分的是 regulation
    assert r.source_used == "web_search:csrc_ar_disclosure"
    assert r.confidence > 0
    assert "CAS 22" in (r.citation or "")


@pytest.mark.asyncio
async def test_fill_field_returns_no_hit_when_nothing_matches():
    async def empty(query, k):
        return []
    engine = WebSearchEngine(
        regulation_search=empty, kb_search=empty, web_search=empty,
    )
    f = _field("x", "web_search:zzz")
    r = await engine.fill_field(f, {})
    assert r.value is None
    assert r.confidence == 0.0
    assert "未检索到" in (r.citation or "")


@pytest.mark.asyncio
async def test_fill_field_rejects_non_web_source():
    engine = WebSearchEngine()
    f = _field("x", "human_qa")
    r = await engine.fill_field(f, {})
    assert r.value is None
    assert "非 web_search" in (r.citation or "")


@pytest.mark.asyncio
async def test_query_includes_hint_and_industry():
    captured = []

    async def _capture(query, k):
        captured.append(query)
        return []
    engine = WebSearchEngine(regulation_search=_capture)
    f = _field("disclosure_note", "web_search:abc", hint="披露要求")
    await engine.fill_field(f, {"industry": "金融业"})
    assert "披露要求" in captured[0]
    assert "金融业" in captured[0]
    assert "abc" in captured[0]
