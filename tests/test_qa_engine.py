"""Tests for the Q&A engine."""
from __future__ import annotations

import pytest

from app.services.comprehensive.qa_engine import (
    QAEngine,
    classify_topic,
)
from app.services.comprehensive.schemas import TemplateField


def _field(field_id: str, source: str = "human_qa", required: bool = True,
           hint: str | None = None, label: str | None = None) -> TemplateField:
    return TemplateField(
        field_id=field_id, label=label or field_id, type="text_long",
        source=source, required=required, hint=hint,
        cell_ref="A1", sheet="s", row=1, column=1,
    )


# ---------- classify_topic ----------

def test_classify_topic_by_prefix():
    assert classify_topic(_field("mgmt_judgment")) == "管理层判断"
    assert classify_topic(_field("disclosure_ar")) == "披露事项"
    assert classify_topic(_field("risk_overall")) == "风险评估"
    assert classify_topic(_field("policy_revenue")) == "会计政策"
    assert classify_topic(_field("subsequent_events")) == "期后事项"
    assert classify_topic(_field("related_party_tx")) == "关联方"
    assert classify_topic(_field("foo")) == "其他补充"


# ---------- generate_questions ----------

@pytest.mark.asyncio
async def test_generate_no_pending_when_all_filled():
    fields = [_field("a"), _field("b")]
    qa = QAEngine()
    qs = await qa.generate_questions(fields, filled_field_ids={"a", "b"}, context={})
    assert qs == []


@pytest.mark.asyncio
async def test_generate_merges_same_topic_into_one_question():
    fields = [
        _field("mgmt_ar", label="应收账款判断"),
        _field("mgmt_inv", label="存货判断"),
        _field("mgmt_rev", label="收入判断"),
    ]
    qa = QAEngine()
    qs = await qa.generate_questions(fields, filled_field_ids=set(), context={})
    # 三个都属于"管理层判断"主题，应合并为 1 个问题
    assert len(qs) == 1
    assert qs[0].topic == "管理层判断"
    assert set(qs[0].field_ids) == {"mgmt_ar", "mgmt_inv", "mgmt_rev"}


@pytest.mark.asyncio
async def test_generate_separates_different_topics():
    fields = [
        _field("mgmt_judgment"),
        _field("disclosure_note"),
        _field("risk_overall"),
    ]
    qa = QAEngine()
    qs = await qa.generate_questions(fields, filled_field_ids=set(), context={})
    topics = {q.topic for q in qs}
    assert topics == {"管理层判断", "披露事项", "风险评估"}
    # 每个问题都只对应一个 field_id
    for q in qs:
        assert len(q.field_ids) == 1


@pytest.mark.asyncio
async def test_generate_respects_max_questions():
    fields = [_field(f"topic_{i}_x") for i in range(20)]  # 全部"其他补充"但只有 1 主题
    qa = QAEngine(max_questions_per_round=3)
    qs = await qa.generate_questions(fields, filled_field_ids=set(), context={})
    assert len(qs) <= 3


@pytest.mark.asyncio
async def test_generate_includes_context_in_prompt():
    fields = [_field("mgmt_judgment")]
    qa = QAEngine()
    qs = await qa.generate_questions(
        fields, filled_field_ids=set(),
        context={"company_name": "ACME", "audit_period": "2024", "industry": "制造业"},
    )
    assert "ACME" in qs[0].context
    assert "2024" in qs[0].context
    assert "制造业" in qs[0].context
    assert "管理层判断" in qs[0].prompt


@pytest.mark.asyncio
async def test_generate_skips_non_human_qa_when_not_required():
    fields = [
        _field("optional", source="rule:xxx", required=False),
        _field("workpaper_optional", source="workpaper:xxx", required=False),
        _field("mgmt_required", source="human_qa", required=True),
    ]
    qa = QAEngine()
    qs = await qa.generate_questions(fields, filled_field_ids=set(), context={})
    # 必填的 human_qa 留下；非必填且不是 human_qa 的也跳过
    assert len(qs) == 1
    assert qs[0].field_ids == ["mgmt_required"]


# ---------- apply_answer ----------

@pytest.mark.asyncio
async def test_apply_answer_fans_out_to_all_fields():
    from app.services.comprehensive.schemas import PendingQuestion

    q = PendingQuestion(
        question_id="q1",
        field_ids=["a", "b", "c"],
        prompt="...",
        context="...",
        topic="管理层判断",
    )
    qa = QAEngine()
    result = await qa.apply_answer(q, "管理层说明文字...")
    assert result == {"a": "管理层说明文字...", "b": "管理层说明文字...", "c": "管理层说明文字..."}


# ---------- LLM 集成（可选） ----------

@pytest.mark.asyncio
async def test_llm_generator_used_when_provided():
    captured = {}

    async def llm(prompt, ctx):
        captured["prompt"] = prompt
        return "LLM 生成的问题"

    fields = [_field("mgmt_a"), _field("mgmt_b")]
    qa = QAEngine(llm_generator=llm)
    qs = await qa.generate_questions(fields, set(), context={"company_name": "ACME"})
    assert qs[0].prompt == "LLM 生成的问题"
    # LLM 收到的 prompt 包含主题/字段/上下文
    assert "管理层判断" in captured["prompt"]
    assert "ACME" in captured["prompt"]  # 来自 ctx_str


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_template():
    async def bad_llm(prompt, ctx):
        raise RuntimeError("LLM down")

    fields = [_field("mgmt_a")]
    qa = QAEngine(llm_generator=bad_llm)
    qs = await qa.generate_questions(fields, set(), context={})
    # 回退到模板化问题
    assert "管理层判断" in qs[0].prompt
