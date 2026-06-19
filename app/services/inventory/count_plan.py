"""Industry-aware inventory count-plan generator.

Two-stage:
1. Built-in templates by industry — works fully offline.
2. DeepSeek refinement (optional) — when an API key is configured, the AI
   tailors the procedures / special_notes / risks to the specific company
   and lets the user further edit through dialog.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError


def _try_parse_date(s: Any) -> Optional[date]:
    """P0-2: AI 返回的日期字段容错解析.

    支持格式:
      - ISO: "2024-12-30"
      - 斜杠: "2024/12/30"
      - 中文: "12月30日" (默认 2024 年, 当前业务场景是 2024 年度盘点)

    无法解析返回 None (不抛), 由调用方决定如何 fallback.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # 1) ISO
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError) as exc:
        # 不抛给上层 (fallback 流程), 但留痕便于排查脏数据
        logger.debug("count_plan: ISO date 解析失败 input=%r exc=%s", s, exc)
    # 2) YYYY/MM/DD 或 YYYY/M/D
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # 3) M月D日 (中文, 默认 2024 年)
    m = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if m:
        try:
            return date(2024, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None

logger = logging.getLogger(__name__)


# ---- 行业模板 ----------------------------------------------------------
# 每个键是 ``Project.industry`` 的常见值；命中后填充计划骨架。
INDUSTRY_TEMPLATES: dict[str, dict[str, Any]] = {
    "制造业": {
        "scope": "原材料库 / 半成品库 / 成品库 / 委外库 / 在途库 / 退货待处理库",
        "procedures": (
            "1) 监盘前一日切断收发料单据并加盖『盘点截止』戳；\n"
            "2) 现场对账面数与实物数双向抽盘（账→物 + 物→账）；\n"
            "3) 关注在产品折合系数与工艺路线匹配；\n"
            "4) 委外加工材料发函至受托方并取得受托方盘点表；\n"
            "5) 长龄、呆滞、变质物料单独列示并拍照存证。"
        ),
        "special_notes": (
            "- 注意三阶段切割：原材料入库截止 / 生产领料截止 / 成品入库与发货截止；\n"
            "- 在产品需取得当日工序进度报告，按工时折合 OR 投料法估算完工度；\n"
            "- 委外材料 = 账面期末『发出材料』，需收回受托方对账单。"
        ),
        "risks": (
            "- 提前确认收入导致成品/发出商品差异；\n"
            "- 在产品完工度高估导致成本提前结转；\n"
            "- 委外材料长期挂账无回收。"
        ),
    },
    "医药生物": {
        "scope": "原料药库 / 中间体库 / 成品库 / 冷链库 / GMP 受控库 / 待检/隔离库 / 退货销毁区",
        "procedures": (
            "1) 监盘需经 QA 同意，进入受控区按 GMP 更衣；\n"
            "2) 冷链按温区分别盘点并记录温湿度；\n"
            "3) 取效期临近 / 已过效期 / 待检 / 隔离 / 待销毁的批次单独表；\n"
            "4) 拍照保留批号、效期、CoA 编号；\n"
            "5) 关注国家管制药品（精麻、放射性、特殊药品）双人双锁双签。"
        ),
        "special_notes": (
            "- 冷链温度记录单需作为监盘附件；\n"
            "- 效期 ≤ 6 个月物料必须 100% 盘点，并由公司销售/质量部门书面评估"
            "其可变现净值（NRV = 估计售价 - 销售费用 - 税费）后再判断是否计提跌价，"
            "不可仅凭『临近效期』直接全额计提；\n"
            "- 特殊管制药品按 GSP 双人复核；\n"
            "- 受检/隔离库的物品禁止移动，仅做账面核对。"
        ),
        "risks": (
            "- 过期/近效期未及时减值；\n"
            "- 受控区进出不规范导致样品丢失；\n"
            "- 冷链中断导致整批报废未入账。"
        ),
    },
    "零售": {
        "scope": "门店货架 / 仓配中心 (DC) / 在途 / 退货返厂 / 临期专区",
        "procedures": (
            "1) 选择营业前 / 闭店后窗口期，避免高峰漏盘；\n"
            "2) PDA / RFID 扫描盘点，导出原始数据；\n"
            "3) 抽样若干门店现场监盘 + 视频抽看其余门店；\n"
            "4) 损耗品、临期品单独列表并比对 POS 报损单；\n"
            "5) 在途与 DC 关注调拨单一致性。"
        ),
        "special_notes": (
            "- 重视周转快 SKU 与高单价 SKU 双口径覆盖；\n"
            "- 区分自有库存与代销/寄售库存（不应计入主体存货）；\n"
            "- 关注短保食品/化妆品的临期减值。"
        ),
        "risks": (
            "- 多门店漏盘导致整体盘亏；\n"
            "- 代销/联营商品被错计入存货；\n"
            "- POS 损耗调整随意化，账实不符。"
        ),
    },
    "信息技术": {
        "scope": "硬件成品库 / 原器件库 / 软件载体 / 设备样机 / 在途及客户端寄存",
        "procedures": (
            "1) 对硬件按序列号清单 100% 监盘；\n"
            "2) 关注用户端寄存/演示机/借用机的归属与剩余使用期；\n"
            "3) 长期未发的旧型号机型单独提示技术减值；\n"
            "4) 软件载体（光盘/U盘/激活码）按批次清点。"
        ),
        "special_notes": (
            "- 序列号一物一码，是否唯一可追溯是关键；\n"
            "- 客户端寄存设备需取得客户回函或租赁合同确认；\n"
            "- 老型号、停产料件需技术部出具技术减值意见。"
        ),
        "risks": (
            "- 客户端寄存设备实际已售未确认；\n"
            "- 技术迭代导致旧型号大额减值滞后；\n"
            "- 演示/借用机长期挂账未追回。"
        ),
    },
    "化工": {
        "scope": "原料库 / 中间罐区 / 成品罐 / 包装库 / 危化品专库",
        "procedures": (
            "1) 液体罐区采用尺测 + 比重换算 + 温度修正；\n"
            "2) 危化品按危险等级隔离盘点，备防护用品；\n"
            "3) 取罐区液位计读数 + 容积换算表 + 实测样品比重三方核对；\n"
            "4) 现场拍照液位计、温度计、量程标尺。"
        ),
        "special_notes": (
            "- 罐区采用液位×密度，需取得校准过的换算表；\n"
            "- 易燃易爆品禁止使用非防爆电子设备；\n"
            "- 危废按环保部门要求与正常存货分别盘点。"
        ),
        "risks": (
            "- 液位换算误差导致大额体积差异；\n"
            "- 危化品账实差异引发监管风险；\n"
            "- 包装规格混乱导致重复入账。"
        ),
    },
    "建筑施工": {
        "scope": "周转材料 / 工程物资 / 临设 / 项目工地暂存",
        "procedures": (
            "1) 现场监盘按项目部分别盘点；\n"
            "2) 周转材料区分租入/自有/已摊销；\n"
            "3) 异地工地采用视频监盘+项目部回函+第三方监理签字。"
        ),
        "special_notes": (
            "- 多项目分散，重点关注完工已退场临设的归集；\n"
            "- 周转材料按摊销期已 ≤ 0 仍有库存的需提示报废。"
        ),
        "risks": ("- 项目部账外存货；\n- 周转材料计提摊销与库存不匹配。"),
    },
    "默认": {
        "scope": "全部存货仓库（原材料 / 在产品 / 库存商品 / 委外 / 在途）",
        "procedures": (
            "1) 监盘前一日下达截止指令；\n"
            "2) 双盲双盘 + 第三方监督；\n"
            "3) 关注长龄/呆滞/受损物料；\n"
            "4) 关注委外、在途、寄售等特殊存货归属。"
        ),
        "special_notes": "依据公司实际仓储情况补充。",
        "risks": "账实差异、归属不清、长龄减值不足。",
    },
}


@dataclass
class CountPlanDraft:
    title: str
    industry: str
    period_end: str  # YYYY-MM-DD
    count_date_start: str
    count_date_end: str
    objectives: str
    scope: str
    team: list[dict[str, str]]  # [{name, role, contact}]
    procedures: str
    special_notes: str
    risks: str
    revision_log: list[dict[str, str]] = field(default_factory=list)

    def to_db_kwargs(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "industry": self.industry,
            "period_end": self.period_end,
            "count_date_start": self.count_date_start,
            "count_date_end": self.count_date_end,
            "objectives": self.objectives,
            "scope": self.scope,
            "team": json.dumps(self.team, ensure_ascii=False),
            "procedures": self.procedures,
            "special_notes": self.special_notes,
            "risks": self.risks,
            "revision_log": json.dumps(self.revision_log, ensure_ascii=False),
        }


class CountPlanGenerator:
    """Generate / revise an inventory-count plan."""

    def __init__(self, client: Optional[DeepSeekClient] = None):
        self.client = client

    # ---- baseline (offline) ---------------------------------------------

    @staticmethod
    def _pick_template(industry: str) -> dict[str, Any]:
        if not industry:
            return INDUSTRY_TEMPLATES["默认"]
        # 包含匹配，e.g. "电子制造业" 命中 "制造业"
        for key, tpl in INDUSTRY_TEMPLATES.items():
            if key in industry or industry in key:
                return tpl
        return INDUSTRY_TEMPLATES["默认"]

    def baseline(
        self,
        *,
        company_name: str,
        industry: str,
        period_end: date,
        count_days_before: int = 0,
        count_days_after: int = 2,
        team: Optional[list[dict[str, str]]] = None,
    ) -> CountPlanDraft:
        tpl = self._pick_template(industry)
        start = period_end - timedelta(days=count_days_before)
        end = period_end + timedelta(days=count_days_after)
        return CountPlanDraft(
            title=f"{company_name} {period_end.year} 年度存货监盘计划",
            industry=industry or "默认",
            period_end=period_end.isoformat(),
            count_date_start=start.isoformat(),
            count_date_end=end.isoformat(),
            objectives=(
                "1) 通过对存货实物的盘点，验证账面期末存货数量、金额的真实性、完整性；\n"
                "2) 识别存货归属（自有/受托/寄售）；\n"
                "3) 关注长龄/呆滞/损毁/过期物料并评估跌价；\n"
                "4) 取得监盘程序的审计证据并形成监盘表/底稿。"
            ),
            scope=tpl["scope"],
            team=team
            or [
                {"name": "审计经理", "role": "现场负责人", "contact": ""},
                {"name": "审计员 A", "role": "账→物核对", "contact": ""},
                {"name": "审计员 B", "role": "物→账核对", "contact": ""},
                {"name": "客户仓管", "role": "陪同清点", "contact": ""},
            ],
            procedures=tpl["procedures"],
            special_notes=tpl["special_notes"],
            risks=tpl["risks"],
        )

    # ---- AI refinement / dialog ----------------------------------------

    SYS_PROMPT = (
        "你是 IPO 审计经理，专注存货监盘计划制定。基于给定的公司信息、行业、"
        "草案与用户最新反馈，返回更新后的监盘计划。必须严格输出 JSON："
        '{"title":...,"objectives":...,"scope":...,"procedures":...,'
        '"special_notes":...,"risks":...,"team":[{"name":...,'
        '"role":...,"contact":...}],"count_date_start":"YYYY-MM-DD",'
        '"count_date_end":"YYYY-MM-DD","change_summary":"..."}。'
        "保留原草案中未被用户改动的部分，只覆盖被指示修改的字段。"
    )

    async def revise(
        self,
        draft: CountPlanDraft,
        user_instruction: str,
        company_name: str = "",
    ) -> CountPlanDraft:
        """Apply a user instruction. Falls back to a no-op log entry if no AI."""
        if not user_instruction.strip():
            return draft

        if not (self.client and self.client.is_configured):
            # 无 AI → 把用户指令原样记入 revision_log，并把它追加到 special_notes
            draft.revision_log.append(
                {
                    "instruction": user_instruction,
                    "applied": "未启用 AI；用户原始指令已追加到『特殊事项』。",
                }
            )
            draft.special_notes = (draft.special_notes or "") + f"\n[用户补充] {user_instruction}"
            return draft

        user_payload = json.dumps(
            {
                "company": company_name,
                "industry": draft.industry,
                "draft": {
                    "title": draft.title,
                    "objectives": draft.objectives,
                    "scope": draft.scope,
                    "procedures": draft.procedures,
                    "special_notes": draft.special_notes,
                    "risks": draft.risks,
                    "team": draft.team,
                    "count_date_start": draft.count_date_start,
                    "count_date_end": draft.count_date_end,
                },
                "instruction": user_instruction,
            },
            ensure_ascii=False,
        )

        try:
            result = await self.client.chat_json(
                system=self.SYS_PROMPT,
                user=user_payload,
                temperature=0.2,
            )
        except DeepSeekError as exc:
            logger.warning("CountPlan AI revise failed: %s", exc)
            draft.revision_log.append(
                {
                    "instruction": user_instruction,
                    "applied": f"AI 调用失败：{exc}；指令已追加到『特殊事项』。",
                }
            )
            draft.special_notes = (draft.special_notes or "") + f"\n[用户补充] {user_instruction}"
            return draft

        # P0-2: 日期字段单独解析, 解析失败保留旧值 + 写 revision_log warning.
        # 不静默吞错, 否则下游 date.fromisoformat 会 500.
        date_warnings: list[str] = []
        for date_field in ("count_date_start", "count_date_end"):
            raw = result.get(date_field)
            if not raw:
                continue
            parsed = _try_parse_date(raw)
            if parsed is None:
                date_warnings.append(
                    f"{date_field}='{raw}' 无法解析为日期, 保留旧值"
                )
                continue
            setattr(draft, date_field, parsed.isoformat())

        for k in (
            "title",
            "objectives",
            "scope",
            "procedures",
            "special_notes",
            "risks",
        ):
            if result.get(k):
                setattr(draft, k, str(result[k]))
        if isinstance(result.get("team"), list):
            draft.team = result["team"]
        applied_msg = str(result.get("change_summary", "AI 已按指令调整"))
        if date_warnings:
            applied_msg += "；警告: " + "; ".join(date_warnings)
        draft.revision_log.append(
            {
                "instruction": user_instruction,
                "applied": applied_msg,
            }
        )
        return draft
