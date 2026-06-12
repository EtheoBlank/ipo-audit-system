"""Tests for the rule engine."""
from __future__ import annotations

import pytest

from app.services.comprehensive.builtin_rules import default_rule_book
from app.services.comprehensive.rule_engine import (
    Rule,
    RuleAction,
    RuleBook,
    RuleCondition,
    RuleEngine,
)
from app.services.comprehensive.schemas import TemplateField


def _field(field_id: str, source: str) -> TemplateField:
    return TemplateField(
        field_id=field_id, label=field_id, type="text", source=source,
        cell_ref="A1", sheet="s", row=1, column=1,
    )


# ---------- RuleCondition ----------

def test_condition_numeric_gt():
    c = RuleCondition(field="x", op=">", value=10)
    assert c.evaluate({"x": 11}) is True
    assert c.evaluate({"x": 10}) is False
    assert c.evaluate({"x": 5}) is False


def test_condition_between():
    c = RuleCondition(field="x", op="between", value=[1, 5])
    assert c.evaluate({"x": 3}) is True
    assert c.evaluate({"x": 1}) is True
    assert c.evaluate({"x": 5}) is True
    assert c.evaluate({"x": 0}) is False
    assert c.evaluate({"x": 6}) is False


def test_condition_in_and_not_in():
    c_in = RuleCondition(field="x", op="in", value=["A", "B"])
    assert c_in.evaluate({"x": "A"}) is True
    assert c_in.evaluate({"x": "C"}) is False

    c_nin = RuleCondition(field="x", op="not_in", value=["A", "B"])
    assert c_nin.evaluate({"x": "C"}) is True
    assert c_nin.evaluate({"x": "A"}) is False


def test_condition_is_none():
    c = RuleCondition(field="x", op="is_none", value=None)
    assert c.evaluate({"x": None}) is True
    assert c.evaluate({"x": 0}) is False


def test_condition_null_actual_disables_comparison():
    c = RuleCondition(field="x", op=">", value=5)
    assert c.evaluate({"x": None}) is False
    assert c.evaluate({}) is False  # missing key


def test_condition_type_mismatch_returns_false():
    c = RuleCondition(field="x", op=">", value=5)
    # 字符串不能与 5 比较
    assert c.evaluate({"x": "abc"}) is False


def test_condition_dotted_path():
    c = RuleCondition(field="project.fiscal_year", op=">=", value=2024)
    assert c.evaluate({"project": {"fiscal_year": 2024}}) is True
    assert c.evaluate({"project": {"fiscal_year": 2023}}) is False


# ---------- Rule / RuleBook ----------

def test_rule_matches_all_conditions():
    rule = Rule(
        id="r1", target_field="risk_level",
        conditions=[
            RuleCondition(field="turnover", op=">", value=100),
            RuleCondition(field="ar_balance", op=">", value=0),
        ],
        action=RuleAction(value="高"),
    )
    assert rule.matches({"turnover": 150, "ar_balance": 1000}) is True
    assert rule.matches({"turnover": 50, "ar_balance": 1000}) is False
    assert rule.matches({"turnover": 150, "ar_balance": 0}) is False


def test_rulebook_for_field_sorted_by_priority():
    book = RuleBook(rules=[
        Rule(id="a", target_field="x", priority=1, action=RuleAction(value="low")),
        Rule(id="b", target_field="x", priority=10, action=RuleAction(value="high")),
        Rule(id="c", target_field="y", priority=5, action=RuleAction(value="other")),
    ])
    xs = book.for_field("x")
    assert [r.id for r in xs] == ["b", "a"]
    assert book.for_field("z") == []


# ---------- RuleEngine ----------

def test_engine_picks_highest_priority_matching_rule():
    book = default_rule_book()
    engine = RuleEngine(book)
    field = _field("risk_level", "rule:ar_risk_classify")

    # ar_turnover_days = 60：high 需 >120 不命中，medium 需 [90,120] 不命中，
    # low 仅要求 is_not_none 命中。优先级 10 < 30 < 20，但仅 low 满足。
    r_low = engine.evaluate_field(field, {"ar_turnover_days": 60})
    assert r_low is not None
    assert r_low.value == "低"
    assert r_low.source_used == "rule:ar_risk_low_turnover"

    r_mid = engine.evaluate_field(field, {"ar_turnover_days": 100})
    assert r_mid.value == "中"
    assert r_mid.source_used == "rule:ar_risk_medium_turnover"

    r_high = engine.evaluate_field(field, {"ar_turnover_days": 150})
    assert r_high.value == "高"
    assert r_high.source_used == "rule:ar_risk_high_turnover"


def test_engine_returns_none_when_no_match():
    # 用一个没有规则的字段
    field = _field("not_in_book", "rule:xxx")
    engine = RuleEngine(RuleBook())
    assert engine.evaluate_field(field, {}) is None


def test_engine_evaluate_all_only_processes_rule_source():
    engine = RuleEngine(default_rule_book())
    fields = [
        _field("risk_level", "rule:ar_risk_classify"),
        _field("ar_balance", "workpaper:ar_ledger.total_ending"),
        _field("disclosure_note", "rule:confirmation_check"),
    ]
    ctx = {"ar_turnover_days": 150, "confirmation_rate": 0.3}
    results = engine.evaluate_all(fields, ctx)
    assert {r.field_id for r in results} == {"risk_level", "disclosure_note"}
    assert any(r.value == "高" for r in results)


def test_engine_load_yaml(tmp_path):
    yml = tmp_path / "rules.yaml"
    yml.write_text(
        """
rules:
  - id: test_rule
    description: 测试规则
    target_field: foo
    priority: 5
    conditions:
      - field: bar
        op: "=="
        value: 42
    action:
      value: "matched"
      citation: "yaml 加载测试"
      confidence: 0.7
        """,
        encoding="utf-8",
    )
    engine = RuleEngine()
    engine.load_yaml(yml)
    f = _field("foo", "rule:test")
    assert engine.evaluate_field(f, {"bar": 42}).value == "matched"
    assert engine.evaluate_field(f, {"bar": 0}) is None


def test_engine_ignores_unknown_op():
    # 测试 op 非法时 Pydantic 校验失败
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RuleCondition(field="x", op="bogop", value=1)
