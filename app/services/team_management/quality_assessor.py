"""会议纪要质量评估 — AI 根据纪要内容、决策、行动项打分并给出改进建议。

降级模式与 ``work_plan_generator`` 一致：AI 不可用时给出基于规则的结构化评估。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import settings
from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)


# ============================================================
#  上下文 / 结果
# ============================================================


@dataclass
class MeetingQualityContext:
    """评估所需上下文。"""

    meeting_title: str
    meeting_type: str
    content: str
    decisions: list[dict[str, Any]] = field(default_factory=list)
    action_items: list[dict[str, Any]] = field(default_factory=list)
    attendees: list[str] = field(default_factory=list)


@dataclass
class MeetingQualityResult:
    """评估结果。"""

    quality_score: float  # 0-100
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    ai_enabled: bool = False
    ai_raw: Optional[dict[str, Any]] = None


# ============================================================
#  规则化兜底评估
# ============================================================


_KEY_ACTION_HINTS = ("需要", "建议", "决定", "明确", "要求", "跟进", "落实", "完成")
_LOW_QUALITY_HINTS = (
    "稍后再说",
    "下次再讨论",
    "看一下",
    "再想想",
    "回头再说",
)


def _fallback_assessment(ctx: MeetingQualityContext) -> MeetingQualityResult:
    """基于规则打分 — AI 不可用时使用，绝不抛异常。"""
    score = 50.0
    strengths: list[str] = []
    weaknesses: list[str] = []
    suggestions: list[str] = []

    content = (ctx.content or "").strip()
    content_len = len(content)

    # 1) 纪要长度
    if content_len >= 800:
        score += 10
        strengths.append("纪要内容详实，信息完整")
    elif content_len >= 300:
        score += 5
    else:
        score -= 10
        weaknesses.append("纪要过于简短，可能遗漏关键讨论")
        suggestions.append("补充关键讨论点、决策依据与不同意见")

    # 2) 决策事项
    if ctx.decisions:
        score += min(15, len(ctx.decisions) * 4)
        strengths.append(f"形成 {len(ctx.decisions)} 项明确决策")
        # 检查每项决策是否同时有 owner + deadline (审计可追溯性)
        missing_owner = [
            i
            for i, d in enumerate(ctx.decisions)
            if not (isinstance(d, dict) and (d.get("owner") or d.get("负责人")))
        ]
        missing_due = [
            i
            for i, d in enumerate(ctx.decisions)
            if not (isinstance(d, dict) and (d.get("due") or d.get("deadline") or d.get("截止日")))
        ]
        if missing_owner:
            score -= 5
            weaknesses.append(f"{len(missing_owner)}/{len(ctx.decisions)} 项决策缺少 owner")
            suggestions.append("为每项决策指定 owner（责任人）")
        if missing_due:
            score -= 3
            weaknesses.append(f"{len(missing_due)}/{len(ctx.decisions)} 项决策缺少 deadline")
            suggestions.append("为每项决策指定截止日")
    else:
        weaknesses.append("未提炼决策事项")
        suggestions.append("会后请用『会议决定』开头逐条列出本次拍板事项")

    # 3) 行动项 — 同样检查 owner / due
    if ctx.action_items:
        score += min(15, len(ctx.action_items) * 3)
        strengths.append(f"列出 {len(ctx.action_items)} 项行动项")
        missing_action_owner = [
            i
            for i, a in enumerate(ctx.action_items)
            if not (isinstance(a, dict) and (a.get("owner") or a.get("负责人")))
        ]
        missing_action_due = [
            i
            for i, a in enumerate(ctx.action_items)
            if not (isinstance(a, dict) and (a.get("due") or a.get("deadline") or a.get("截止日")))
        ]
        if missing_action_owner:
            score -= 4
            weaknesses.append(f"{len(missing_action_owner)}/{len(ctx.action_items)} 项行动缺 owner")
        if missing_action_due:
            score -= 4
            weaknesses.append(f"{len(missing_action_due)}/{len(ctx.action_items)} 项行动缺截止日")
            suggestions.append("每项行动补 owner / due 才算闭环")
    else:
        weaknesses.append("无行动项，跟进可能脱节")
        suggestions.append("用『行动项』清单：动作 + 责任人 + 截止日")

    # 4) 与会人
    if ctx.attendees and len(ctx.attendees) >= 3:
        score += 5
        strengths.append(f"{len(ctx.attendees)} 人参与，覆盖关键角色")
    elif not ctx.attendees:
        weaknesses.append("未列与会人，无法追溯责任")
        suggestions.append("在纪要开头列明所有与会人及角色")

    # 5) 行动关键词 / 低质量关键词
    if any(h in content for h in _KEY_ACTION_HINTS):
        score += 5
    if any(h in content for h in _LOW_QUALITY_HINTS):
        score -= 10
        weaknesses.append("出现『稍后再议』等模糊措辞，决策不够明确")
        suggestions.append("将模糊表述转成『谁、何时、做何事』")

    # 限幅
    score = max(0.0, min(100.0, score))

    if not strengths:
        strengths.append("(AI 未启用，仅根据规则评估，建议补充更详实的纪要)")
    if not weaknesses:
        weaknesses.append("(规则未发现明显问题)")

    return MeetingQualityResult(
        quality_score=round(score, 1),
        strengths=strengths,
        weaknesses=weaknesses,
        suggestions=suggestions,
        ai_enabled=False,
    )


# ============================================================
#  主类
# ============================================================


class MeetingQualityAssessor:
    """会议纪要质量评估。"""

    def __init__(self, deepseek: Optional[DeepSeekClient] = None) -> None:
        self.deepseek = deepseek or DeepSeekClient(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_API_BASE,
            model=settings.DEEPSEEK_MODEL,
        )

    async def assess(self, ctx: MeetingQualityContext) -> MeetingQualityResult:
        if not self.deepseek.is_configured:
            return _fallback_assessment(ctx)

        try:
            ai_result = await self.deepseek.chat_json(
                system=self._system_prompt(),
                user=self._build_prompt(ctx),
                temperature=0.2,
                max_tokens=1500,
            )
            return self._parse_ai_result(ai_result)
        except DeepSeekError as exc:
            logger.exception("AI 评估会议纪要失败，使用规则评估: %s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("AI 评估会议纪要时发生未知异常，使用规则评估")
        return _fallback_assessment(ctx)

    # ------------------------------------------------------------
    #  prompt
    # ------------------------------------------------------------

    def _system_prompt(self) -> str:
        return (
            "你是一位资深 IPO 审计项目质量复核合伙人，"
            "负责审计团队内部会议的纪要质量评分 (0-100)。\n"
            "评估维度：\n"
            "1) 内容完整性：是否覆盖关键讨论、决策、争议点；\n"
            "2) 决策清晰度：是否明确『谁/何时/做何事』；\n"
            "3) 行动项质量：是否有 owner / deadline / 可验证的产出；\n"
            "4) 跟进机制：是否给下次会议提供 input。\n"
            "输出严格 JSON：{quality_score(0-100), strengths: [...], "
            "weaknesses: [...], suggestions: [...]}。"
        )

    def _build_prompt(self, ctx: MeetingQualityContext) -> str:
        decisions = json.dumps(ctx.decisions, ensure_ascii=False, indent=2)
        actions = json.dumps(ctx.action_items, ensure_ascii=False, indent=2)
        attendees = ", ".join(ctx.attendees) or "(未提供)"
        return (
            f"### 会议基本信息\n"
            f"- 标题: {ctx.meeting_title}\n"
            f"- 类型: {ctx.meeting_type}\n"
            f"- 与会人: {attendees}\n\n"
            f"### 纪要正文\n{ctx.content}\n\n"
            f"### 决策事项\n{decisions}\n\n"
            f"### 行动项\n{actions}\n\n"
            f"请按 system 中的要求评估并输出 JSON。"
        )

    def _parse_ai_result(self, result: dict[str, Any]) -> MeetingQualityResult:
        try:
            score = float(result.get("quality_score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(100.0, score))

        def _list_of_str(key: str) -> list[str]:
            val = result.get(key) or []
            if not isinstance(val, list):
                return []
            return [str(x) for x in val if str(x).strip()]

        return MeetingQualityResult(
            quality_score=round(score, 1),
            strengths=_list_of_str("strengths"),
            weaknesses=_list_of_str("weaknesses"),
            suggestions=_list_of_str("suggestions"),
            ai_enabled=True,
            ai_raw=result,
        )


# 全局单例
meeting_quality_assessor = MeetingQualityAssessor()
