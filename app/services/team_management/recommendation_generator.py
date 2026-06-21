"""管理建议生成器 — 根据项目进度数据 + 日报 + 卡点 + 人员负载调用 AI 输出可执行建议。

降级原则同前两个 generator。
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
class RecommendationContext:
    """生成管理建议所需的上下文。"""

    project_id: int
    project_name: str
    completion_rate: float  # 项目整体完成率 0-1
    blocked_count: int
    critical_blockers: int
    overdue_items: int  # 逾期未完成的任务数
    members_load: list[dict[str, Any]] = field(
        default_factory=list
    )  # [{name, level, completion_rate, hours_logged, open_blockers}]
    recent_blockers: list[dict[str, Any]] = field(
        default_factory=list
    )  # [{title, severity, days_open}]
    recent_summaries: list[str] = field(default_factory=list)  # 来自日报的摘要
    period_start: Optional[str] = None
    period_end: Optional[str] = None


@dataclass
class RecommendationResult:
    """生成结果。"""

    findings: list[dict[str, Any]] = field(
        default_factory=list
    )  # [{category, severity, finding, evidence}]
    priority_actions: list[dict[str, Any]] = field(
        default_factory=list
    )  # [{action, owner, deadline, rationale}]
    recommendations: str = ""  # Markdown 长文
    ai_enabled: bool = False
    ai_raw: Optional[dict[str, Any]] = None


# ============================================================
#  兜底建议
# ============================================================


def _fallback_recommendations(ctx: RecommendationContext) -> RecommendationResult:
    findings: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    md_lines: list[str] = [f"## {ctx.project_name} 管理建议（规则评估）", ""]

    # 1) 卡点
    if ctx.critical_blockers > 0:
        findings.append(
            {
                "category": "进度风险",
                "severity": "critical",
                "finding": f"存在 {ctx.critical_blockers} 项紧急卡点未解决",
                "evidence": f"紧急卡点 {ctx.critical_blockers} / 总开放卡点 {ctx.blocked_count}",
            }
        )
        actions.append(
            {
                "action": "召开紧急卡点清障会",
                "owner": "项目负责人",
                "deadline": "本周内",
                "rationale": "紧急卡点会显著拖累整体进度",
            }
        )
        md_lines.append("- ⚠️ **紧急卡点**：存在需立即介入的卡点，建议本周内召开清障会。")
    elif ctx.blocked_count > 0:
        md_lines.append(f"- ⚠️ 仍有 {ctx.blocked_count} 项卡点待处理，请持续跟进。")

    # 2) 完成率
    if ctx.completion_rate < 0.3:
        findings.append(
            {
                "category": "进度滞后",
                "severity": "high",
                "finding": f"项目完成率仅 {ctx.completion_rate:.0%}",
                "evidence": f"完成率 {ctx.completion_rate:.0%} < 30%",
            }
        )
        actions.append(
            {
                "action": "重新审视工作计划优先级，集中资源攻克关键路径",
                "owner": "项目负责人",
                "deadline": "本周末",
                "rationale": "完成率偏低，需调整任务结构",
            }
        )
        md_lines.append(
            f"- 📉 **进度滞后**：完成率仅 {ctx.completion_rate:.0%}，建议聚焦关键路径。"
        )
    elif ctx.completion_rate > 0.8:
        md_lines.append(f"- ✅ 进度良好，完成率 {ctx.completion_rate:.0%}。")

    # 3) 人员负载
    heavy = [m for m in ctx.members_load if (m.get("hours_logged_7d") or 0) > 50]
    light = [m for m in ctx.members_load if (m.get("hours_logged_7d") or 0) < 8]
    if heavy and light:
        findings.append(
            {
                "category": "资源分配",
                "severity": "medium",
                "finding": "人员负载不均：部分成员过载，部分闲置",
                "evidence": f"高负载 {len(heavy)} 人 vs 闲置 {len(light)} 人",
            }
        )
        actions.append(
            {
                "action": "将低负载人员的待办任务重新分配",
                "owner": "项目经理",
                "deadline": "下次例会",
                "rationale": "平衡负载、避免关键人 burnout",
            }
        )
        md_lines.append("- 👥 人员负载不均，建议重新分配待办任务。")
    if heavy:
        names = "、".join(m.get("name", "?") for m in heavy[:3])
        md_lines.append(f"- 🏋️ 高负载关注：{names}（7 天工时 > 50h），关注 burnout 风险。")

    # 4) 逾期
    if ctx.overdue_items > 0:
        findings.append(
            {
                "category": "进度风险",
                "severity": "high",
                "finding": f"{ctx.overdue_items} 项任务已逾期",
                "evidence": "due_date < today 且 status != done",
            }
        )
        actions.append(
            {
                "action": "拉清单逐项确认：能否关闭 / 重新分派 / 调整截止日",
                "owner": "项目经理",
                "deadline": "本周内",
                "rationale": "逾期任务需明确处置方案",
            }
        )
        md_lines.append(f"- ⏰ 逾期任务 {ctx.overdue_items} 项，需明确处置。")

    if not findings:
        findings.append(
            {
                "category": "综合",
                "severity": "info",
                "finding": "项目整体运行平稳，规则层未发现明显异常",
                "evidence": "完成率与卡点数据在健康区间",
            }
        )
        md_lines.append("- 项目整体运行平稳，规则层未发现明显异常。")

    if not actions:
        actions.append(
            {
                "action": "按现行节奏继续推进，下周例会复盘",
                "owner": "项目负责人",
                "deadline": "下次例会前",
                "rationale": "无明显异常，维持节奏",
            }
        )

    return RecommendationResult(
        findings=findings,
        priority_actions=actions,
        recommendations="\n".join(md_lines),
        ai_enabled=False,
    )


# ============================================================
#  主类
# ============================================================


class ManagementRecommendationGenerator:
    """管理建议生成器。"""

    def __init__(self, deepseek: Optional[DeepSeekClient] = None) -> None:
        self.deepseek = deepseek or DeepSeekClient(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_API_BASE,
            model=settings.DEEPSEEK_MODEL,
        )

    async def generate(self, ctx: RecommendationContext) -> RecommendationResult:
        if not self.deepseek.is_configured:
            return _fallback_recommendations(ctx)

        try:
            ai_result = await self.deepseek.chat_json(
                system=self._system_prompt(),
                user=self._build_prompt(ctx),
                temperature=0.3,
                max_tokens=2000,
            )
            parsed = self._parse_ai(ai_result)
            if parsed.findings or parsed.priority_actions or parsed.recommendations:
                return parsed
        except DeepSeekError as exc:
            logger.exception("AI 生成管理建议失败，使用规则评估: %s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("AI 生成管理建议时发生未知异常，使用规则评估")
        return _fallback_recommendations(ctx)

    def _system_prompt(self) -> str:
        return (
            "你是资深 IPO 审计合伙人，需要根据项目进度数据 + 人员负载 + 卡点 + 日报摘要"
            "为项目负责人输出可执行的管理建议。\n"
            "要求：\n"
            "1) findings：3-6 条关键发现，每条带 category / severity / finding / evidence；\n"
            "2) priority_actions：3-5 条优先行动，每条带 action / owner / deadline / rationale；\n"
            "3) recommendations：Markdown 总结（200-500 字），可直接发给项目负责人；\n"
            "4) 重点关注：进度滞后、卡点积压、人员负载失衡、关键路径风险；\n"
            "5) 输出严格 JSON：{findings: [...], priority_actions: [...], recommendations: '...'}。"
        )

    def _build_prompt(self, ctx: RecommendationContext) -> str:
        members = json.dumps(ctx.members_load, ensure_ascii=False, indent=2)
        blockers = json.dumps(ctx.recent_blockers[:10], ensure_ascii=False, indent=2)
        # P0 安全: 截断每条日报摘要 + 限制总条数
        safe_summaries = [
            (s or "")[:120].replace(chr(10), " ")
            for s in ctx.recent_summaries[-15:]
        ]
        summaries = "\n".join(f"- {s}" for s in safe_summaries) or "(无)"
        period = f"{ctx.period_start or '?'} ~ {ctx.period_end or '?'}"
        return (
            f"### 项目\n- {ctx.project_name} (id={ctx.project_id})\n- 周期: {period}\n\n"
            f"### 整体进度\n- 完成率: {ctx.completion_rate:.0%}\n"
            f"- 开放卡点: {ctx.blocked_count} (紧急: {ctx.critical_blockers})\n"
            f"- 逾期任务: {ctx.overdue_items}\n\n"
            f"### 人员负载\n{members}\n\n"
            f"### 最近卡点\n{blockers}\n\n"
            f"### 日报摘要\n{summaries}\n\n"
            f"请按 system 中要求输出 JSON。"
        )

    def _parse_ai(self, result: dict[str, Any]) -> RecommendationResult:
        def _list_of_dict(key: str) -> list[dict[str, Any]]:
            val = result.get(key) or []
            if not isinstance(val, list):
                return []
            return [x for x in val if isinstance(x, dict)]

        return RecommendationResult(
            findings=_list_of_dict("findings"),
            priority_actions=_list_of_dict("priority_actions"),
            recommendations=str(result.get("recommendations", "")).strip(),
            ai_enabled=True,
            ai_raw=result,
        )


# 全局单例
management_recommendation_generator = ManagementRecommendationGenerator()
