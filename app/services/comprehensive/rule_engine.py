"""审计手册规则引擎。

把 `audit-procedures.md` 等审计手册中的"如果…则…"经验沉淀为结构化规则，
在综合底稿自动填充时按数据状态触发并产生结论。

特性：
- 声明式：规则用 Pydantic 模型描述，可从 YAML/JSON 加载，便于事务所维护
- 可组合：规则可级联（A 规则产生 risk_level，B 规则根据 risk_level 触发）
- 可追溯：每条结论都附 rule_id 与条件命中说明
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, field_validator

from app.services.comprehensive.schemas import FillResult, TemplateField

logger = logging.getLogger(__name__)


# ============================== 规则定义 ==============================

Operator = Literal[
    "==", "!=", ">", "<", ">=", "<=",
    "in", "not_in", "between",
    "is_none", "is_not_none", "contains", "starts_with",
]


class RuleCondition(BaseModel):
    """单条条件：``field op value``。"""

    field: str = Field(..., description="被比较的字段 ID 或数据路径")
    op: Operator
    value: Any = None

    def evaluate(self, context: dict[str, Any]) -> bool:
        """基于当前已填值上下文判断条件是否成立。"""
        actual = self._lookup(context, self.field)
        try:
            if self.op == "is_none":
                return actual is None
            if self.op == "is_not_none":
                return actual is not None
            if self.op == "in":
                return actual in (self.value or [])
            if self.op == "not_in":
                return actual not in (self.value or [])
            if self.op == "between":
                lo, hi = self.value
                return actual is not None and lo <= actual <= hi
            if self.op == "contains":
                return actual is not None and self.value in actual
            if self.op == "starts_with":
                return actual is not None and str(actual).startswith(str(self.value))
            if actual is None:
                return False
            return {
                "==": actual == self.value,
                "!=": actual != self.value,
                ">": actual > self.value,
                "<": actual < self.value,
                ">=": actual >= self.value,
                "<=": actual <= self.value,
            }[self.op]
        except TypeError:
            # 不同类型不能比较
            return False

    @staticmethod
    def _lookup(context: dict[str, Any], key: str) -> Any:
        """支持 'a.b.c' 点号路径。"""
        cur: Any = context
        for k in key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                cur = getattr(cur, k, None)
            if cur is None:
                return None
        return cur


class RuleAction(BaseModel):
    """条件成立时执行的动作。"""

    value: Any = Field(..., description="写入目标字段的值")
    citation: Optional[str] = Field(None, description="引用/依据说明")
    confidence: float = Field(0.85, ge=0.0, le=1.0)


class Rule(BaseModel):
    """一条规则。"""

    id: str
    description: Optional[str] = None
    target_field: str = Field(..., description="规则产出的字段 ID（与模板 _meta 中的 field_id 对齐）")
    conditions: list[RuleCondition] = Field(default_factory=list)
    action: RuleAction
    priority: int = Field(0, description="数值越大越优先；同字段多规则命中时取最高优先级")

    def matches(self, context: dict[str, Any]) -> bool:
        return all(c.evaluate(context) for c in self.conditions)


class RuleBook(BaseModel):
    """一组规则。"""

    rules: list[Rule] = Field(default_factory=list)

    def for_field(self, field_id: str) -> list[Rule]:
        return sorted(
            [r for r in self.rules if r.target_field == field_id],
            key=lambda r: r.priority,
            reverse=True,
        )


# ============================== 引擎 ==============================

class RuleEngine:
    """规则引擎。"""

    def __init__(self, book: Optional[RuleBook] = None):
        self.book = book or RuleBook()

    def load_yaml(self, path: Union[str, Path]) -> None:
        """从 YAML 加载规则到 book 中（追加，不覆盖）。"""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        rules_raw = data.get("rules", [])
        for r in rules_raw:
            self.book.rules.append(Rule(**r))
        logger.info("从 %s 加载 %d 条规则", path, len(rules_raw))

    def add(self, rule: Rule) -> None:
        self.book.rules.append(rule)

    def evaluate_field(
        self,
        field_def: TemplateField,
        context: dict[str, Any],
    ) -> Optional[FillResult]:
        """评估某个字段的所有规则，返回按优先级最高命中的结果。"""
        candidates = [r for r in self.book.for_field(field_def.field_id) if r.matches(context)]
        if not candidates:
            return None
        rule = candidates[0]
        return FillResult(
            field_id=field_def.field_id,
            value=rule.action.value,
            source_used=f"rule:{rule.id}",
            confidence=rule.action.confidence,
            citation=rule.action.citation or rule.description or rule.id,
        )

    def evaluate_all(
        self,
        fields: list[TemplateField],
        context: dict[str, Any],
    ) -> list[FillResult]:
        """对一批字段跑规则；按字段分别处理。"""
        results: list[FillResult] = []
        for f in fields:
            if not f.source.startswith("rule:"):
                continue
            r = self.evaluate_field(f, context)
            if r is not None:
                results.append(r)
        return results
