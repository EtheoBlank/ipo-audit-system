"""Work plan generator — 根据公司信息 + 已导入账套 + 人员清单调用 AI 生成 IPO 审计工作计划。

设计原则（与 ``audit_note_generator`` 一致）：
  - AI 不可用时返回结构化骨架，永不抛 500
  - DeepSeek JSON mode 失败 → 退回内置 IPO 审计阶段标准模板
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db_models import (
    AccountBalance,
    Project,
    TeamMember,
    MEMBER_LEVEL_LABELS,
)
from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)


# ============================================================
#  上下文 / 结果
# ============================================================


@dataclass
class WorkPlanContext:
    """生成工作计划所需的上下文。"""

    project_id: int
    project_name: str
    company_name: str
    industry: Optional[str] = None
    fiscal_year: Optional[int] = None
    # 导入概况
    account_count: int = 0  # 科目余额条数
    voucher_count: int = 0  # 序时账条数
    bank_statement_count: int = 0  # 银行对账单条数
    # 人员清单 — id + 名称 + 级别 + 特长
    members: list[dict[str, Any]] = field(default_factory=list)
    # 是否已有已存在 active 计划
    has_active_plan: bool = False


@dataclass
class WorkPlanGenerated:
    """AI 生成结果。"""

    name: str
    items: list[
        dict[str, Any]
    ]  # [{title, description, related_module, priority, estimated_hours, recommended_level}]
    prompt_used: str
    ai_enabled: bool
    ai_raw: Optional[dict[str, Any]] = None


# ============================================================
#  模板（AI 不可用时的兜底）
# ============================================================


# IPO 审计典型工作阶段 → 任务清单的兜底模板
# 工时与人员级别按"四大 IPO 实务"校准：
#   风险评估/控制测试 = manager 主导，senior_manager 复核
#   实质性程序 = senior_auditor / auditor 执行
#   底稿复核 = senior_manager 一级 + lead 终审
#   监盘/盘点 = senior_auditor 现场 + manager 复核
_FALLBACK_PLAN_TEMPLATES: list[dict[str, Any]] = [
    {
        "title": "风险评估与审计策略制定",
        "description": "了解被审计单位及其环境，识别重大错报风险（含舞弊风险），制定总体审计策略与具体审计计划。",
        "related_module": "底稿",
        "priority": "high",
        "estimated_hours": 80.0,
        "recommended_level": "senior_manager",
    },
    {
        "title": "控制测试设计与执行",
        "description": "对关键业务流程（销售/采购/存货/资金/薪酬）设计与执行控制测试，评价控制运行有效性。",
        "related_module": "底稿",
        "priority": "high",
        "estimated_hours": 60.0,
        "recommended_level": "manager",
    },
    {
        "title": "货币资金实质性程序",
        "description": "银行存款余额调节表检查、银行函证（按财政部模板）、大额流水测试、库存现金监盘、截止性测试。",
        "related_module": "底稿",
        "priority": "high",
        "estimated_hours": 24.0,
        "recommended_level": "senior_auditor",
    },
    {
        "title": "应收账款函证 + 客户走访",
        "description": "按 CSA 1311/1502/1504 要求生成函证清单并发出；跟进回函、统计差异、替代程序。",
        "related_module": "函证",
        "priority": "high",
        "estimated_hours": 32.0,
        "recommended_level": "senior_auditor",
    },
    {
        "title": "存货监盘（盘点现场）",
        "description": "盘点计划编制、现场监盘（按行业特征选点）、抽盘复核、盘点差异汇总与调整建议。",
        "related_module": "盘点",
        "priority": "high",
        "estimated_hours": 48.0,
        "recommended_level": "senior_auditor",
    },
    {
        "title": "收入截止性与真实性测试",
        "description": "期末前后若干天的销售交易双向截止测试；毛利率与同行业对比；收入循环穿行测试。",
        "related_module": "销售",
        "priority": "high",
        "estimated_hours": 28.0,
        "recommended_level": "senior_auditor",
    },
    {
        "title": "采购与付款循环测试",
        "description": "抽样测试采购订单、收货单、发票三单匹配，付款审批与付款记录核验，关联方采购识别。",
        "related_module": "底稿",
        "priority": "medium",
        "estimated_hours": 24.0,
        "recommended_level": "auditor",
    },
    {
        "title": "成本核算与毛利率分析",
        "description": "复核成本归集与分配逻辑（直接材料/直接人工/制造费用），分析毛利率波动原因，与同行业对比。",
        "related_module": "底稿",
        "priority": "medium",
        "estimated_hours": 16.0,
        "recommended_level": "auditor",
    },
    {
        "title": "合同条款复核 (CAS 14 五步法)",
        "description": "抽样重大销售/采购合同，按 CAS 14 五步法识别履约义务、控制权转移时点、可变对价。",
        "related_module": "合同",
        "priority": "medium",
        "estimated_hours": 20.0,
        "recommended_level": "senior_auditor",
    },
    {
        "title": "金融工具与长期股权投资",
        "description": "复核金融资产分类与计量（CAS 22）、长期股权投资权益法/成本法核算、合并范围判断。",
        "related_module": "底稿",
        "priority": "medium",
        "estimated_hours": 24.0,
        "recommended_level": "senior_auditor",
    },
    {
        "title": "关联方与关联交易",
        "description": "获取关联方清单，抽样测试关联交易公允性、披露完整性、资金占用风险。",
        "related_module": "底稿",
        "priority": "high",
        "estimated_hours": 20.0,
        "recommended_level": "manager",
    },
    {
        "title": "底稿一级复核",
        "description": "项目经理 + 高级经理对全部底稿做一级复核，识别重大事项与未解决差异。",
        "related_module": "底稿",
        "priority": "high",
        "estimated_hours": 60.0,
        "recommended_level": "senior_manager",
    },
    {
        "title": "项目终审与质控复核",
        "description": "项目合伙人终审，质控合伙人独立复核，关键判断事项复核与文档化。",
        "related_module": "底稿",
        "priority": "high",
        "estimated_hours": 24.0,
        "recommended_level": "lead",
    },
]


# ============================================================
#  主类
# ============================================================


class WorkPlanGenerator:
    """AI 工作计划生成器。"""

    def __init__(self, deepseek: Optional[DeepSeekClient] = None) -> None:
        self.deepseek = deepseek or DeepSeekClient(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_API_BASE,
            model=settings.DEEPSEEK_MODEL,
        )

    # ------------------------------------------------------------
    #  入口
    # ------------------------------------------------------------

    async def generate(self, ctx: WorkPlanContext) -> WorkPlanGenerated:
        """生成工作计划。AI 失败 → 退回标准模板。"""
        prompt = self._build_prompt(ctx)

        if self.deepseek.is_configured:
            try:
                ai_result = await self.deepseek.chat_json(
                    system=self._system_prompt(),
                    user=prompt,
                    temperature=0.3,
                    max_tokens=3500,
                )
                items = self._parse_ai_items(ai_result)
                if items:
                    return WorkPlanGenerated(
                        name=ai_result.get("plan_name") or f"{ctx.company_name} IPO 审计计划",
                        items=items,
                        prompt_used=prompt,
                        ai_enabled=True,
                        ai_raw=ai_result,
                    )
                logger.warning("AI 返回 items 为空，使用兜底模板")
            except DeepSeekError as exc:
                logger.exception("AI 生成工作计划失败，使用兜底模板: %s", exc)
            except Exception:  # noqa: BLE001
                logger.exception("AI 生成工作计划时发生未知异常，使用兜底模板")

        return self._fallback_plan(ctx, prompt)

    # ------------------------------------------------------------
    #  AI prompt
    # ------------------------------------------------------------

    def _system_prompt(self) -> str:
        return (
            "你是一位资深 IPO 审计合伙人，专长 IPO 项目整体工作规划与人员调度。"
            "你需要根据给定的公司信息、账套导入规模、人员级别清单，"
            "输出一份完整、可执行的 IPO 审计工作计划 JSON。\n"
            "要求：\n"
            "1) 覆盖 IPO 审计各阶段：风险评估/控制测试/实质性程序/底稿复核/质控复核；\n"
            "2) 任务粒度应可由 1-2 名审计员在 1-3 天内完成；\n"
            "3) 每项任务给出 priority(high/medium/low)、estimated_hours、recommended_level、"
            "related_module(底稿/函证/盘点/销售/合同/监管/其他)；\n"
            "4) 总工时建议与项目组人数匹配；\n"
            "5) 输出严格 JSON：{plan_name, items: [{title, description, related_module, "
            "priority, estimated_hours, recommended_level}]}。"
        )

    def _build_prompt(self, ctx: WorkPlanContext) -> str:
        members_block = (
            "\n".join(
                f"- id={m.get('id')}, name={m.get('full_name')}, level={m.get('level')}, "
                f"specialties={m.get('specialties') or '未指定'}"
                for m in ctx.members
            )
            or "- (项目组尚未分配人员)"
        )

        return (
            f"### 项目信息\n"
            f"- 项目名: {ctx.project_name}\n"
            f"- 被审计单位: {ctx.company_name}\n"
            f"- 行业: {ctx.industry or '未指定'}\n"
            f"- 审计年度: {ctx.fiscal_year or '未指定'}\n"
            f"- 科目余额条数: {ctx.account_count}\n"
            f"- 序时账条数: {ctx.voucher_count}\n"
            f"- 银行对账单条数: {ctx.bank_statement_count}\n\n"
            f"### 项目组人员\n{members_block}\n\n"
            f"### 要求\n"
            f"基于上述信息输出一份 JSON：{{plan_name, items: [...]}}。\n"
            f"任务覆盖 IPO 审计关键阶段，按人员级别匹配任务复杂度。"
        )

    def _parse_ai_items(self, ai_result: dict[str, Any]) -> list[dict[str, Any]]:
        items = ai_result.get("items") or []
        valid_modules = {"底稿", "函证", "盘点", "销售", "合同", "监管", "其他"}
        valid_priorities = {"high", "medium", "low"}
        valid_levels = set(MEMBER_LEVEL_LABELS.keys())

        out: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title", "")).strip()
            if not title:
                continue
            related = str(raw.get("related_module", "其他")).strip() or "其他"
            if related not in valid_modules:
                related = "其他"
            priority = str(raw.get("priority", "medium")).lower()
            if priority not in valid_priorities:
                priority = "medium"
            try:
                hours = float(raw.get("estimated_hours", 0) or 0)
            except (TypeError, ValueError):
                hours = 0.0
            level = str(raw.get("recommended_level", "auditor")).lower().strip()
            if level not in valid_levels:
                level = "auditor"
            out.append(
                {
                    "title": title,
                    "description": str(raw.get("description", "")).strip(),
                    "related_module": related,
                    "priority": priority,
                    "estimated_hours": max(0.0, hours),
                    "recommended_level": level,
                }
            )
        return out

    # ------------------------------------------------------------
    #  兜底
    # ------------------------------------------------------------

    def _fallback_plan(self, ctx: WorkPlanContext, prompt: str) -> WorkPlanGenerated:
        items = [dict(t) for t in _FALLBACK_PLAN_TEMPLATES]
        # 根据实际导入数据量微调工时：规模大 → 系数提高
        scale = 1.0
        if ctx.account_count >= 500 or ctx.voucher_count >= 10000:
            scale = 1.5
        elif ctx.account_count >= 200 or ctx.voucher_count >= 5000:
            scale = 1.2
        for it in items:
            it["estimated_hours"] = round(float(it["estimated_hours"]) * scale, 1)
        return WorkPlanGenerated(
            name=f"{ctx.company_name} IPO 审计计划 (标准模板)",
            items=items,
            prompt_used=prompt,
            ai_enabled=False,
            ai_raw=None,
        )

    # ------------------------------------------------------------
    #  上下文构建
    # ------------------------------------------------------------

    async def build_context(self, db: AsyncSession, project_id: int) -> WorkPlanContext:
        """从数据库读取项目信息 + 账套导入规模 + 已分配人员，构建上下文。

        性能注意：用 SQL `func.count()` 聚合，避免把大表的所有 id 拉回 Python
        再 len()（IPO 大项目导入 10 万条序时账时会 OOM）。
        """
        from sqlalchemy import func
        from app.models.db_models import (
            ChronologicalAccount,
            BankStatement,
            ProjectAssignment,
            MEMBER_STATUS_ACTIVE,
        )

        proj = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if not proj:
            raise ValueError(f"项目不存在: {project_id}")

        ab_count = (
            await db.execute(
                select(func.count(AccountBalance.id)).where(AccountBalance.project_id == project_id)
            )
        ).scalar() or 0
        ca_count = (
            await db.execute(
                select(func.count(ChronologicalAccount.id)).where(
                    ChronologicalAccount.project_id == project_id
                )
            )
        ).scalar() or 0
        bs_count = (
            await db.execute(
                select(func.count(BankStatement.id)).where(BankStatement.project_id == project_id)
            )
        ).scalar() or 0

        # 只列在岗人员，避免离职/暂离人员被推荐
        members_q = await db.execute(
            select(TeamMember, ProjectAssignment)
            .join(ProjectAssignment, ProjectAssignment.member_id == TeamMember.id)
            .where(
                ProjectAssignment.project_id == project_id,
                TeamMember.status == MEMBER_STATUS_ACTIVE,
            )
            .order_by(TeamMember.level.desc())
        )
        members_data: list[dict[str, Any]] = []
        for member, _assign in members_q.all():
            members_data.append(
                {
                    "id": member.id,
                    "full_name": member.full_name,
                    "level": member.level,
                    "specialties": member.specialties,
                }
            )

        return WorkPlanContext(
            project_id=project_id,
            project_name=proj.name,
            company_name=proj.company_name,
            industry=proj.industry,
            fiscal_year=proj.fiscal_year,
            account_count=int(ab_count),
            voucher_count=int(ca_count),
            bank_statement_count=int(bs_count),
            members=members_data,
        )


# 全局单例
work_plan_generator = WorkPlanGenerator()
