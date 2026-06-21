"""内置规则测试 — P0 测试空白 (2026-06-19).

app/services/comprehensive/builtin_rules.py::default_rule_book()
此前 0 直接测试. 规则引擎触发后会影响风险评级 + 披露说明,
任何规则缺失会让 qa_engine / rule_engine 漏掉关键审计提示.
"""
from __future__ import annotations

import pytest

from app.services.comprehensive.builtin_rules import default_rule_book
from app.services.comprehensive.rule_engine import RuleEngine


class TestBuiltinRuleBook:
    """P0 业务正确性 — 内置规则数量 / 字段完整性."""

    def test_default_book_loads(self):
        rb = default_rule_book()
        assert rb is not None
        assert hasattr(rb, "rules")
        assert len(rb.rules) >= 4, "内置规则数量 < 4, 可能规则被误删"

    def test_all_rules_have_required_fields(self):
        rb = default_rule_book()
        for rule in rb.rules:
            assert rule.id, f"规则缺 id: {rule}"
            assert rule.description, f"规则 {rule.id} 缺描述"
            assert rule.target_field, f"规则 {rule.id} 缺 target_field"
            assert len(rule.conditions) >= 1, f"规则 {rule.id} 无条件"
            assert rule.action.value, f"规则 {rule.id} 无 action.value"

    def test_priority_is_numeric(self):
        rb = default_rule_book()
        for rule in rb.rules:
            assert isinstance(rule.priority, int), f"规则 {rule.id} priority 非 int"
            assert 0 <= rule.priority <= 100, f"规则 {rule.id} priority 越界"

    def test_rule_ids_unique(self):
        rb = default_rule_book()
        ids = [r.id for r in rb.rules]
        assert len(ids) == len(set(ids)), f"重复 id: {ids}"

    def test_ar_risk_high_turnover_rule_present(self):
        # 关键规则 1: 应收账款周转天数 > 120 → 高风险
        rb = default_rule_book()
        ar_rules = [r for r in rb.rules if "ar" in r.id and "high" in r.id]
        assert ar_rules, "缺 ar_risk_high_turnover 规则"
        rule = ar_rules[0]
        cond = rule.conditions[0]
        assert cond.field == "ar_turnover_days"
        assert cond.op == ">"
        assert cond.value == 120

    def test_confirmation_low_coverage_rule_present(self):
        # 关键规则 2: 函证覆盖率 < 50%
        rb = default_rule_book()
        rules = [r for r in rb.rules if r.id == "confirmation_low_coverage"]
        assert rules, "缺 confirmation_low_coverage 规则"
        rule = rules[0]
        cond = rule.conditions[0]
        assert cond.field == "confirmation_rate"
        assert cond.op == "<"
        assert cond.value == 0.5


class TestBuiltinRulesEvaluation:
    """P0 业务正确性 — 规则引擎能跑通内置规则."""

    def test_engine_can_initialize_with_default_book(self):
        rb = default_rule_book()
        engine = RuleEngine(rb)
        assert engine is not None
        # book.for_field(field_id) 不抛错
        for ftype in ("risk_level", "disclosure_note"):
            matched = rb.for_field(ftype)
            assert isinstance(matched, list)

    def test_high_ar_turnover_matches_high_rule(self):
        # 应收账款周转天数 150 应命中 high 规则 (条件 > 120)
        # 注意: 引擎按 rule.target_field 匹配 field.field_id,
        # 所以 field_id 要用 "risk_level" 而不是规则 id
        from app.services.comprehensive.schemas import TemplateField

        rb = default_rule_book()
        engine = RuleEngine(rb)
        field = TemplateField(
            field_id="risk_level",
            label="风险等级",
            type="choice",
            source="rule:ar_risk_high_turnover",
            sheet="S", row=1, column=1, cell_ref="S!A1",
        )
        ctx = {"ar_turnover_days": 150.0}
        results = engine.evaluate_all([field], ctx)
        # 至少有一个 FillResult 的 value == "高"
        matched = [r for r in results if r.value == "高"]
        assert matched, "周转 150 天应触发高风险规则"

    def test_low_ar_turnover_matches_low_rule(self):
        from app.services.comprehensive.schemas import TemplateField

        rb = default_rule_book()
        engine = RuleEngine(rb)
        field = TemplateField(
            field_id="risk_level",
            label="风险等级",
            type="choice",
            source="rule:ar_risk_low_turnover",
            sheet="S", row=1, column=1, cell_ref="S!A1",
        )
        ctx = {"ar_turnover_days": 30.0}
        results = engine.evaluate_all([field], ctx)
        matched = [r for r in results if r.value == "低"]
        assert matched, "周转 30 天应触发低风险规则"