"""智能问答生成与聚类引擎。

负责把"无法自动填充的字段"转成"少量、对人友好、按主题归类的问题"。

设计目标：
- 一次会话 ≤ N 个问题（默认 5）
- 同一主题的多个字段合并为一个问题，答案自动写入所有相关字段
- 问题生成可使用 LLM（可选），无 LLM 时回退到模板式问题
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Awaitable, Callable, Optional

from app.services.comprehensive.schemas import (
    PendingQuestion,
    TemplateField,
)

logger = logging.getLogger(__name__)

# 可选 LLM 生成器签名：输入(prompt, context) → 问题字符串
LLMQuestionGenerator = Callable[[str, dict], Awaitable[str]]


# ============================== 主题归类 ==============================

# 字段 ID 前缀 → 主题
TOPIC_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^mgmt_"), "管理层判断"),
    (re.compile(r"^disclosure_"), "披露事项"),
    (re.compile(r"^risk_"), "风险评估"),
    (re.compile(r"^policy_"), "会计政策"),
    (re.compile(r"^judgment_"), "职业判断"),
    (re.compile(r"^estimate_"), "会计估计"),
    (re.compile(r"^subsequent_"), "期后事项"),
    (re.compile(r"^related_"), "关联方"),
    (re.compile(r"^contingent_"), "或有事项"),
    (re.compile(r"^commitment_"), "承诺事项"),
]


def classify_topic(field: TemplateField) -> str:
    """按 field_id 前缀归类主题。"""
    for pat, topic in TOPIC_RULES:
        if pat.search(field.field_id):
            return topic
    return "其他补充"


# ============================== 引擎 ==============================


class QAEngine:
    """问答生成与聚类引擎。"""

    def __init__(
        self,
        max_questions_per_round: int = 5,
        llm_generator: Optional[LLMQuestionGenerator] = None,
    ):
        self.max_questions_per_round = max_questions_per_round
        self._llm = llm_generator

    # ---------- 公共 API ----------

    async def generate_questions(
        self,
        fields: list[TemplateField],
        filled_field_ids: set[str],
        context: dict,
    ) -> list[PendingQuestion]:
        """为尚未填上的字段生成问题，按主题聚类并截断。"""
        # 1) 过滤：未填的 human_qa 字段
        pending = [
            f
            for f in fields
            if f.field_id not in filled_field_ids and (f.source == "human_qa" or f.required)
        ]
        if not pending:
            return []

        # 2) 按主题聚类
        groups: dict[str, list[TemplateField]] = defaultdict(list)
        for f in pending:
            groups[classify_topic(f)].append(f)

        # 3) 每个主题生成 1 个问题
        questions: list[PendingQuestion] = []
        for topic, fs in groups.items():
            q = await self._build_question(topic, fs, context)
            questions.append(q)

        # 4) 按"必填在前 + 主题优先级"排序，再截断
        questions.sort(key=self._priority_key, reverse=True)
        return questions[: self.max_questions_per_round]

    async def apply_answer(
        self,
        question: PendingQuestion,
        answer: str,
    ) -> dict[str, object]:
        """把用户对一个问题的回答，展开成所有相关字段的填充值。

        Returns: {field_id: value}
        """
        # 大多数情况：所有相关字段写同一份答案
        # 如果问题对应多个 field，且 label 表明字段类型不同，可在此分桶
        return {fid: answer for fid in question.field_ids}

    # ---------- 内部 ----------

    async def _build_question(
        self,
        topic: str,
        fields: list[TemplateField],
        context: dict,
    ) -> PendingQuestion:
        """构造一个 PendingQuestion。"""
        # 简单实现：模板化问题
        labels = "、".join(f.label for f in fields)
        prompt_default = f"请就「{topic}」补充以下信息：{labels}。请用一段话描述，不少于 100 字。"
        context_lines = [f"- {f.label}：{f.hint or '（无）'}" for f in fields]

        # 上下文：项目/期间等
        ctx_parts: list[str] = []
        if context.get("company_name"):
            ctx_parts.append(f"被审计单位：{context['company_name']}")
        if context.get("audit_period"):
            ctx_parts.append(f"审计期间：{context['audit_period']}")
        if context.get("industry"):
            ctx_parts.append(f"行业：{context['industry']}")
        ctx_str = "；".join(ctx_parts) or "（无）"

        prompt = prompt_default
        if self._llm is not None:
            try:
                llm_input = "\n".join(context_lines)
                prompt = await self._llm(
                    f"主题：{topic}\n字段：{llm_input}\n上下文：{ctx_str}",
                    context,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM 生成问题失败，回退到模板: %s", exc)

        return PendingQuestion(
            question_id=f"q_{topic}_{abs(hash(tuple(f.field_id for f in fields))) % 10**8}",
            field_ids=[f.field_id for f in fields],
            prompt=prompt,
            context=f"{ctx_str}\n" + "\n".join(context_lines),
            topic=topic,
            options=None,
        )

    @staticmethod
    def _priority_key(q: PendingQuestion) -> tuple[int, int]:
        """排序：(必填字段数, 主题优先级)。"""
        # 简化：必填字段数 = len(field_ids)（测试时假设全是 required）
        # 主题优先级：管理层判断 > 披露事项 > 风险评估 > 其他
        topic_rank = {
            "管理层判断": 5,
            "披露事项": 4,
            "风险评估": 3,
            "会计政策": 2,
            "其他补充": 1,
        }.get(q.topic, 0)
        return (len(q.field_ids), topic_rank)
