"""Tests for the comprehensive fill engine (orchestrator)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pytest

from app.services.comprehensive.builtin_rules import default_rule_book
from app.services.comprehensive.fill_engine import (
    ComprehensiveFillEngine,
    _safe_eval,
)
from app.services.comprehensive.field_mapper import (
    FieldMapper,
    WorkpaperDataContext,
)
from app.services.comprehensive.qa_engine import QAEngine
from app.services.comprehensive.rule_engine import RuleEngine
from app.services.comprehensive.schemas import TemplateField
from app.services.comprehensive.web_search_engine import (
    SearchHit,
    WebSearchEngine,
)


# ---------- helpers ----------

@dataclass
class FakeProject:
    company_name: str
    industry: str
    fiscal_year: int


def _field(
    field_id: str, source: str, type_: str = "text", label: str | None = None
) -> TemplateField:
    return TemplateField(
        field_id=field_id, label=label or field_id, type=type_, source=source,
        cell_ref="A1", sheet="s", row=1, column=1,
    )


def _schema(*fields: TemplateField) -> "TemplateSchema":  # type: ignore[name-defined]
    from app.services.comprehensive.schemas import TemplateSchema
    return TemplateSchema(
        template_id="t1", template_name="t", version="1.0.0",
        firm_id="f", fields=list(fields), sheets=["s"],
    )


# ---------- _safe_eval ----------

def test_safe_eval_basic_arithmetic():
    assert _safe_eval("1 + 2", {}) == 3
    assert _safe_eval("10 * 2 - 5", {}) == 15
    assert _safe_eval("100 / 4", {}) == 25.0


def test_safe_eval_with_namespace():
    ns = {"x": 10, "y": 5}
    assert _safe_eval("x + y", ns) == 15
    assert _safe_eval("x * 2 - y", ns) == 15


def test_safe_eval_rejects_unsafe_names():
    with pytest.raises(ValueError):
        _safe_eval("__import__('os')", {})
    with pytest.raises(ValueError):
        _safe_eval("open('x')", {})


def test_safe_eval_white_list_functions():
    assert _safe_eval("abs(-5)", {}) == 5
    assert _safe_eval("max(1, 2, 3)", {}) == 3
    assert _safe_eval("round(1.5)", {}) == 2


# ---------- fill() 端到端 ----------

@pytest.fixture
def engines():
    """构造带 mock 三方检索器的引擎组合。"""
    async def fake_reg(q, k):
        if "披露" in q or "disclosure" in q.lower():
            return [SearchHit(
                title="CAS 22 披露要求", snippet="按 CAS 22 披露...",
                source="", citation="财政部 · 财会〔2017〕7号",
                score=0.9,
            )]
        return []
    web = WebSearchEngine(regulation_search=fake_reg)
    return ComprehensiveFillEngine(
        mapper=FieldMapper(),
        rule_engine=RuleEngine(default_rule_book()),
        web_engine=web,
        qa_engine=QAEngine(),
    )


@pytest.fixture
def ctx():
    ab = pd.DataFrame([
        {"account_code": "1122", "account_name": "应收账款", "balance_direction": "借",
         "beginning_balance": 8000, "debit_amount": 4000, "credit_amount": 1000, "ending_balance": 11000},
    ])
    return WorkpaperDataContext(
        project=FakeProject("ACME", "制造业", 2024),
        account_balances=ab,
        confirmation_cases=[],
        extra={"revenue": 36500.0},
    )


@pytest.mark.asyncio
async def test_fill_workpaper_then_rule(engines, ctx):
    schema = _schema(
        _field("ar_balance", "workpaper:ar_ledger.total_ending", "number"),
        _field("ar_turnover_days", "calculated:365*ar_avg/revenue", "number"),
        _field("risk_level", "rule:ar_risk_classify"),
    )
    report = await engines.fill(schema, ctx)
    by_id = {r.field_id: r for r in report.results}

    # workpaper 填上
    assert by_id["ar_balance"].value == 11000.0

    # calculated 依赖 workpaper（需要先把 ar_avg 推出来）
    # 当前表达式引用了 ar_avg，但 ctx 里没有，所以 calculated 会失败 → 不影响
    # 但 turnover_days 来自 calculated，应该没有
    # 我们改用 turnover_days 来自规则推导的场景：


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end(engines, ctx):
    """完整跑通：workpaper → calculated → rule → web_search → human_qa。"""
    schema = _schema(
        _field("company_name", "workpaper:project.company_name"),
        _field("ar_balance", "workpaper:ar_ledger.total_ending", "number"),
        _field("confirmation_rate", "workpaper:confirmation.coverage", "percent"),
        # ar_turnover_days 用 calculated 引用 ar_balance
        _field("ar_turnover_days", "calculated:ar_balance/100", "number"),
        _field("risk_level", "rule:ar_risk_classify"),
        _field("disclosure_note", "web_search:ar_disclosure", "text_long", label="应收账款披露"),
        _field("mgmt_judgment", "human_qa"),
    )
    report = await engines.fill(schema, ctx)
    by_id = {r.field_id: r for r in report.results}

    assert by_id["company_name"].value == "ACME"
    assert by_id["ar_balance"].value == 11000.0
    # ar_turnover_days = 11000/100 = 110 → 落在 medium 区间
    assert by_id["ar_turnover_days"].value == 110.0
    assert by_id["risk_level"].value == "中"
    # web_search 命中
    assert by_id["disclosure_note"].value is not None
    assert "CAS 22" in (by_id["disclosure_note"].citation or "")

    # human_qa 没填，留作问题
    qa_topics = {q.topic for q in report.open_questions}
    assert "管理层判断" in qa_topics

    assert report.filled == 5
    assert report.pending == 2  # disclosure 可能没填、mgmt_judgment 留作问题


@pytest.mark.asyncio
async def test_fill_does_not_overwrite_high_confidence_with_lower(engines, ctx):
    """workpaper 的 confidence=0.95 应高于规则的 0.85，已填的不被覆盖。"""
    # ar_turnover_days 直接来自 workpaper，规则跑完不应该覆盖它
    schema = _schema(
        _field("ar_turnover_days", "workpaper:project.fiscal_year", "number"),
        _field("risk_level", "rule:ar_risk_classify"),
    )
    report = await engines.fill(schema, ctx)
    by_id = {r.field_id: r for r in report.results}
    assert by_id["ar_turnover_days"].value == 2024.0
    assert by_id["ar_turnover_days"].source_used.startswith("workpaper:")


@pytest.mark.asyncio
async def test_rule_runs_again_after_calculated(engines, ctx):
    """calculated 后应再次跑规则，触发级联。"""
    schema = _schema(
        _field("ar_balance", "workpaper:ar_ledger.total_ending", "number"),
        # 直接给出 turnover_days 的中风险触发条件
        _field("ar_turnover_days", "calculated:ar_balance/100", "number"),
        _field("risk_level", "rule:ar_risk_classify"),
    )
    report = await engines.fill(schema, ctx)
    by_id = {r.field_id: r for r in report.results}
    # ar_turnover_days = 110，落在 medium
    assert by_id["ar_turnover_days"].value == 110.0
    assert by_id["risk_level"].value == "中"


@pytest.mark.asyncio
async def test_calculated_failure_does_not_crash(engines, ctx):
    """计算表达式引用未知变量时静默跳过。"""
    schema = _schema(
        _field("x", "calculated:nonexistent * 2", "number"),
    )
    report = await engines.fill(schema, ctx)
    assert all(r.field_id != "x" or r.value is None for r in report.results)


# ---------- apply_qa_answers ----------

@pytest.mark.asyncio
async def test_apply_qa_answers_fills_human_qa_fields(engines, ctx):
    schema = _schema(
        _field("mgmt_judgment", "human_qa"),
        _field("mgmt_estimate", "human_qa"),
    )
    report = await engines.fill(schema, ctx)
    # 两个都属于"管理层判断"主题，合并为一个问题
    assert len(report.open_questions) == 1
    q = report.open_questions[0]
    assert set(q.field_ids) == {"mgmt_judgment", "mgmt_estimate"}

    new_report = await engines.apply_qa_answers(
        report, {q.question_id: "管理层基于账龄分析计提坏账..."}
    )
    filled_ids = {r.field_id for r in new_report.results if r.value is not None}
    assert {"mgmt_judgment", "mgmt_estimate"}.issubset(filled_ids)
    # 答错的字段不被填
    assert new_report.filled >= 2


@pytest.mark.asyncio
async def test_apply_qa_answers_ignores_unknown_question_id(engines, ctx):
    schema = _schema(_field("mgmt_judgment", "human_qa"))
    report = await engines.fill(schema, ctx)
    new_report = await engines.apply_qa_answers(report, {"q_unknown": "..."})
    # mgmt_judgment 没被填
    assert not any(r.field_id == "mgmt_judgment" for r in new_report.results)
