"""综合底稿自动填写引擎（编排器）。

把字段映射引擎、规则引擎、网络核查引擎、问答引擎串起来，按以下顺序
填充一个综合底稿模板：

  1. workpaper:    从基础底稿直接抽取（最高置信度）
  2. rule:         用审计手册规则推导
  3. web_search:   联网/知识库检索权威信息
  4. calculated:   在前述填充结果之上执行表达式
  5. human_qa:     无法自动填的字段聚类为问题，留待人工

最终产出 ``FillReport``，可被：
  - 前端：用于一次性问答界面 + 预览
  - Excel 写入器：把 values 写回模板
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from app.services.comprehensive.field_mapper import (
    FieldMapper,
    WorkpaperDataContext,
)
from app.services.comprehensive.qa_engine import QAEngine
from app.services.comprehensive.rule_engine import RuleEngine
from app.services.comprehensive.schemas import (
    FillReport,
    FillResult,
    TemplateSchema,
)
from app.services.comprehensive.web_search_engine import WebSearchEngine

logger = logging.getLogger(__name__)


# ============================== 表达式求值 ==============================

# 允许的内置函数白名单（const tuple，便于 AST 节点直接比对）
_SAFE_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": sum,
    "len": len,
    "int": int,
    "float": float,
    "pow": pow,
}

# 拒绝的 AST 节点（语句级 / 属性反射 / 海龟式逃逸）
_FORBIDDEN_NODES = (
    ast.Call,  # 任意函数调用都禁止（仅白名单可放行）
    ast.Attribute,  # 属性访问（避免 x.__class__ 等逃逸）
    ast.Subscript,  # 下标
    ast.Lambda,  # 匿名函数
    ast.FunctionDef,
    ast.ClassDef,
    ast.AsyncFunctionDef,  # 嵌套定义
    ast.Import,
    ast.ImportFrom,
    ast.Starred,
    ast.Yield,
    ast.YieldFrom,
    ast.NamedExpr,  # walrus :=
    # 任何 comprehension 内的循环
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.Raise,
    ast.Global,
    ast.Nonlocal,
    ast.IfExp,  # 表达式级 if-else 也禁（避免写花式逻辑）
    ast.Dict,
    ast.Set,  # 容器字面量（与本场景无关）
)


class _SafeEvalError(ValueError):
    """表达式求值被拒绝（安全策略）。"""


def _safe_eval(expr: str, namespace: dict[str, Any]) -> Any:
    """AST 解析的受限表达式求值。

    允许：
      - 数字字面量（int/float）
      - 字符串/列表/元组字面量（仅用于简单数据）
      - 命名空间中的变量
      - 一元/二元算术运算
      - 比较运算

    拒绝：
      - 函数调用、属性访问、下标、lambda、def、class、import、
        推导式、循环、if-else、:= 等
    """
    if not expr or not isinstance(expr, str):
        return None
    if len(expr) > 2000:
        raise _SafeEvalError("表达式过长（>2000 字符）")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise _SafeEvalError(f"表达式语法错误: {exc}") from exc

    # 静态分析：拒绝任何禁止节点（Call 单独处理，区分白名单函数与危险调用）
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # 仅允许：调用 _SAFE_FUNCTIONS 内的函数
            func = node.func
            if isinstance(func, ast.Name) and func.id in _SAFE_FUNCTIONS:
                # 校验所有参数为合法字面量/变量
                for arg in node.args:
                    if not isinstance(
                        arg,
                        (ast.Constant, ast.Name, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp),
                    ):
                        raise _SafeEvalError(
                            f"白名单函数的参数仅支持字面量/变量/表达式: {type(arg).__name__}"
                        )
                continue
            raise _SafeEvalError(f"禁止函数调用: {type(func).__name__}")
        if isinstance(node, _FORBIDDEN_NODES):
            raise _SafeEvalError(f"禁止使用的语法: {type(node).__name__}")
        if isinstance(node, ast.Name) and not _is_safe_name(node.id, namespace):
            raise _SafeEvalError(f"未授权标识符: {node.id}")
    # ast.parse(mode="eval") 顶层是 Expression，body 是合法表达式节点
    return _eval_node(tree.body, namespace)


def _is_safe_name(name: str, namespace: dict[str, Any]) -> bool:
    """标识符是否在白名单内。"""
    if not name.isidentifier():
        return False
    if name.startswith("__"):
        return False
    return name in namespace or name in _SAFE_FUNCTIONS


def _eval_node(node: ast.AST, namespace: dict[str, Any]) -> Any:
    """递归求值（仅支持叶子节点集）。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool)) or node.value is None:
            return node.value
        raise _SafeEvalError(f"不支持的字面量类型: {type(node.value).__name__}")
    if isinstance(node, ast.Name):
        if not _is_safe_name(node.id, namespace):
            raise _SafeEvalError(f"未授权标识符: {node.id}")
        if node.id in namespace:
            return namespace[node.id]
        if node.id in _SAFE_FUNCTIONS:
            return _SAFE_FUNCTIONS[node.id]
        raise _SafeEvalError(f"未定义变量: {node.id}")
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, namespace)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.Not):
            return not operand
        raise _SafeEvalError(f"不支持的一元运算: {type(node.op).__name__}")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, namespace)
        right = _eval_node(node.right, namespace)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            result = left / right
        elif isinstance(node.op, ast.FloorDiv):
            result = left // right
        elif isinstance(node.op, ast.Mod):
            result = left % right
        elif isinstance(node.op, ast.Pow):
            result = left**right
        else:
            raise _SafeEvalError(f"不支持的二元运算: {type(node.op).__name__}")
        # P0 资源: 限制数值结果大小, 防内存爆 (pow(2, 1<<10000) 之类)
        if isinstance(result, (int, float)) and abs(result) > 1e15:
            raise ValueError(f"表达式结果过大: {result}")
        return result
    if isinstance(node, ast.Compare):
        # 仅支持单层比较 (a < b)
        left = _eval_node(node.left, namespace)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, namespace)
            if not _apply_compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, namespace) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise _SafeEvalError(f"不支持的布尔运算: {type(node.op).__name__}")
    if isinstance(node, ast.Call):
        # 仅白名单函数（外层已校验）
        func = node.func
        if isinstance(func, ast.Name) and func.id in _SAFE_FUNCTIONS:
            args = [_eval_node(a, namespace) for a in node.args]
            return _SAFE_FUNCTIONS[func.id](*args)
        raise _SafeEvalError("禁止函数调用")
    raise _SafeEvalError(f"不支持的节点: {type(node).__name__}")


def _apply_compare(op: ast.AST, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise _SafeEvalError(f"不支持的比较运算: {type(op).__name__}")


# ============================== 编排器 ==============================


def _SOURCES_ALL(_f: Any) -> bool:
    """默认 predicate: 所有字段都参与 (workpaper/rule 跨所有 source)."""
    return True


# 单个阶段: predicate 决定哪些字段参与, runner 真正执行填充逻辑.
StageRunner = Callable[["_FillState"], Awaitable[None]]
Predicate = Callable[[Any], bool]


@dataclass
class _Stage:
    name: str
    predicate: Predicate  # 字段级 filter
    runner: StageRunner  # 实际填充/汇总逻辑

    async def run(self, state: "_FillState") -> None:
        await self.runner(state)


@dataclass
class _FillState:
    """fill 阶段的共享状态 (filled / context / engine / 当前 predicate / 阶段产物)."""

    schema: Any  # TemplateSchema
    ctx: Any  # WorkpaperDataContext
    engine: "ComprehensiveFillEngine"
    filled: dict[str, Any]  # field_id -> FillResult
    context: dict[str, Any]  # 命名空间 (filled 的扁平 view)
    predicate: Predicate = _SOURCES_ALL  # 当前阶段的字段过滤
    questions: list[Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.questions is None:
            self.questions = []


# ---------- 阶段 runner (签名统一: 只接 _FillState) ----------

async def _run_workpaper(state: _FillState) -> None:
    """阶段 1: 从基础底稿直接抽取 (最高置信度)."""
    for r in state.engine.mapper.map_all(state.schema.fields, state.ctx):
        if r.value is not None:
            state.filled[r.field_id] = r
            state.context[r.field_id] = r.value


async def _run_rule(state: _FillState) -> None:
    """阶段 2/5: 规则引擎 — 仅在更高置信度时覆盖."""
    for r in state.engine.rules.evaluate_all(state.schema.fields, state.context):
        if r.field_id not in state.filled or state.filled[r.field_id].confidence < r.confidence:
            state.filled[r.field_id] = r
            state.context[r.field_id] = r.value


async def _run_web_search(state: _FillState) -> None:
    """阶段 3: 联网/知识库检索 (predicate 已限定 source=web_search:*)."""
    for f in state.schema.fields:
        if not state.predicate(f):
            continue
        if f.field_id in state.filled:
            continue
        r = await state.engine.web.fill_field(f, state.context)
        if r.value is not None:
            state.filled[r.field_id] = r
            state.context[r.field_id] = r.value


async def _run_calculated(state: _FillState) -> None:
    """阶段 4: 在前面填充之上求值 (predicate 已限定 source=calculated:*)."""
    for f in state.schema.fields:
        if not state.predicate(f):
            continue
        expr = f.source.split(":", 1)[1]
        try:
            value = _safe_eval(expr, state.context)
        except ValueError as exc:
            logger.warning("字段 '%s' 表达式求值失败: %s", f.field_id, exc)
            continue
        if value is None:
            continue
        state.filled[f.field_id] = FillResult(
            field_id=f.field_id,
            value=value,
            source_used=f"calculated:{expr}",
            confidence=0.99,
            citation=f"表达式: {expr}",
        )
        state.context[f.field_id] = value


async def _run_open_questions(state: _FillState) -> None:
    """阶段 6: 聚类未填字段为问号, 留待人工."""
    state.questions = await state.engine.qa.generate_questions(
        fields=state.schema.fields,
        filled_field_ids={fid for fid, r in state.filled.items() if r.value is not None},
        context={
            "company_name": getattr(state.ctx.project, "company_name", None),
            "audit_period": getattr(state.ctx.project, "fiscal_year", None),
            "industry": getattr(state.ctx.project, "industry", None),
        },
    )


# ---------- 公共 API ----------


class ComprehensiveFillEngine:
    """综合底稿自动填写编排器。"""

    def __init__(
        self,
        mapper: Optional[FieldMapper] = None,
        rule_engine: Optional[RuleEngine] = None,
        web_engine: Optional[WebSearchEngine] = None,
        qa_engine: Optional[QAEngine] = None,
    ):
        self.mapper = mapper or FieldMapper()
        self.rules = rule_engine or RuleEngine()
        self.web = web_engine or WebSearchEngine()
        self.qa = qa_engine or QAEngine()

    async def fill(
        self,
        schema: TemplateSchema,
        ctx: WorkpaperDataContext,
    ) -> FillReport:
        """对一份模板跑完整填充流程。

        顺序（按声明的 ``self._stages``）：
          workpaper → rule → web_search → calculated → rule-again → open_questions
        新增阶段只改 ``_stages`` 配置，不动主循环。
        """
        # context 既是"已填值字典"，也是 calculated 表达式的命名空间
        # 预先把 ctx.extra 注入，使 calculated: 365*ar_balance/revenue 能找到 revenue
        context: dict[str, Any] = dict(getattr(ctx, "extra", {}) or {})
        filled: dict[str, FillResult] = {}
        state = _FillState(
            schema=schema,
            ctx=ctx,
            engine=self,
            filled=filled,
            context=context,
        )
        for stage in self._stages():
            state.predicate = stage.predicate
            await stage.run(state)

        total = len(schema.fields)
        filled_count = sum(1 for r in filled.values() if r.value is not None)
        return FillReport(
            template_id=schema.template_id,
            total_fields=total,
            filled=filled_count,
            pending=total - filled_count,
            results=list(filled.values()),
            open_questions=state.questions,
        )

    def _stages(self) -> list["_Stage"]:
        """声明式阶段列表。新增阶段：写一个 _Stage 进来即可。"""
        return [
            _Stage("workpaper", _SOURCES_ALL, _run_workpaper),
            _Stage("rule", _SOURCES_ALL, _run_rule),
            _Stage("web_search", lambda f: f.source.startswith("web_search:"), _run_web_search),
            _Stage("calculated", lambda f: f.source.startswith("calculated:"), _run_calculated),
            _Stage("rule_again", _SOURCES_ALL, _run_rule),
            _Stage("open_questions", _SOURCES_ALL, _run_open_questions),
        ]

    async def apply_qa_answers(
        self,
        report: FillReport,
        answers: dict[str, str],
    ) -> FillReport:
        """把用户对若干问题的回答合并到 report 中。"""
        for q in report.open_questions:
            ans = answers.get(q.question_id)
            if not ans:
                continue
            values = await self.qa.apply_answer(q, ans)
            for fid, val in values.items():
                report.results.append(
                    FillResult(
                        field_id=fid,
                        value=val,
                        source_used=f"human_qa:{q.question_id}",
                        confidence=1.0,
                        citation=f"问题 '{q.topic}' 的人工回答",
                    )
                )
        report.filled = sum(1 for r in report.results if r.value is not None)
        report.pending = report.total_fields - report.filled
        return report
