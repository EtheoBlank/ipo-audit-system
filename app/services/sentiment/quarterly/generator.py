"""季度报告 4 轮 LLM 协议 — 与简报同结构, 多 1 个 financial_input 维度."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.core.config import settings
from app.services.sentiment.briefing.generator import (
    ADVERSARIAL_SYSTEM,
    SELF_CHECK_SYSTEM,
    BriefingGenerator,
)
from app.services.sentiment.llm_client import LlmClientFactory
from app.services.sentiment.quarterly.financial_input import (
    FinancialInput,
)

logger = logging.getLogger(__name__)


# ---- Prompt 模板 (与简报同骨架, 强化"双数据源对账") ----------------

Q_EXTRACT_SYSTEM = """你是 IPO 审计季度跟踪报告分析师. 你的任务是从窗口期内的舆情事件 + 季报数据中提取关键洞察.

【输入三部分】
1) 窗口期简报集 (每日简报)
2) 窗口期舆情事件 (event 列表)
3) 季报关键数据 (financial_input)

【铁律】
1. 严禁评价、推测、演绎 — 只提取
2. 每个事实必须以 event_id 引用, 不允许 "据报道" / "有消息称" 等模糊措辞
3. 季报数字与 financial_input 逐字一致; 舆情数字与原文逐字一致
4. 禁用情绪词: 严重 / 恶劣 / 暴雷 / 崩塌 / 惊天 / 血亏 / 惨烈 / 崩盘 / 重磅
5. 涉及业绩变脸 (净利同比转负、扣非大降) 时, 必须同时引用 financial_input 中的对应数字 + 至少 1 个舆情事件

【输出 JSON Schema】
{
  "key_findings": [
    {"event_id": <int|null>, "financial_field": "<financial_input key 或 null>",
     "finding": "<60 字内, 中性>", "severity": "info|notice|warn|critical"}
  ],
  "data_consistency_flags": [
    {"financial_field": "<key>", "financial_value": <val>, "claimed_in_event": <val|null>,
     "consistent": true|false, "note": "<差异说明>"}
  ],
  "severity_breakdown": {"info":n, "notice":n, "warn":n, "critical":n},
  "watch_list": [{"event_id": <int|null>, "reason": "<30 字内>"}]
}
"""

Q_COMPOSE_SYSTEM = """你是 IPO 审计季度跟踪报告编辑. 综合 4 类输入生成最终报告 Markdown.

【强约束】
1. 每个事实以 "[事件#N]" 或 "[财报#字段名]" 引用
2. 数字 / 金额 / 百分比与原文逐字一致
3. 严禁使用: 严重 / 恶劣 / 暴雷 / 崩塌 / 惊天 / 血亏 / 惨烈 / 崩盘 / 重磅
4. 对尚未证实 / 正在调查 / 待回复的事项, 用 "尚待核实"
5. 不评价公司前景 / 不预测股价 / 不下结论
6. 全篇不使用感叹号, 不使用反问句
7. 必须包含『双数据源对账』章节, 列出 financial_input vs 简报/事件中数字的一致性

【输出 Markdown 模板】
# {company_name} {fiscal_year} {period_label} 跟踪报告

## 一、本期核心发现 ({n} 条)
1. [事件#N] 或 [财报#字段] <事实>
...

## 二、季报关键数据
| 指标 | 数值 | 同比 |
| --- | --- | --- |
| 营业收入 | ¥xxx | +x% |
| 净利润 | ¥xxx | +x% |
| 扣非净利润 | ¥xxx | — |
| 毛利率 | x% | — |
| 期末总资产 | ¥xxx | — |
| 经营现金流 | ¥xxx | — |

## 三、双数据源对账 ({m} 项)
- [财报#字段] financial_input=X, 简报/事件中未出现 → 标记"无舆情印证"
- [财报#字段] financial_input=X, 简报#Y 引用 X → 标记"一致"
- [财报#字段] financial_input=X, 事件#N 引用 Y ≠ X → 标记"差异" + 注明

## 四、舆情窗口期回顾 ({k} 条事件 / {b} 份简报)
- 重大 (critical): n 条
- 警示 (warn): n 条
- 关注 (notice): n 条
- 一般 (info): n 条

## 五、需持续关注 ({w} 条)
...

## 六、数据核实说明
<逐条说明: 哪些数字与原文一致, 哪些被剔除>

> 本报告基于 {b} 份每日简报 + {k} 条舆情事件 + 季报数据自动生成, 经 4 轮 LLM 协议与双数据源对账校验, 供审计师与领导审阅. 所有事实可点击原文链接核对.
"""


# ---- 数据类 ------------------------------------------------------------


@dataclass
class QuarterlyExtraction:
    key_findings: list[dict] = field(default_factory=list)
    data_consistency_flags: list[dict] = field(default_factory=list)
    severity_breakdown: dict = field(default_factory=dict)
    watch_list: list[dict] = field(default_factory=list)


@dataclass
class QuarterlyReportContent:
    markdown: str
    extraction: QuarterlyExtraction
    self_check: dict
    adversarial: dict
    safe_finding_keys: list[str]  # 用于 verifier
    raw_input: dict  # 缓存原始输入, verifier 用


# ---- 主类 --------------------------------------------------------------


class QuarterlyReportGenerator:
    """4 轮 LLM 协议 — 与简报同骨架, 增强双数据源对账."""

    def __init__(self) -> None:
        self.client = LlmClientFactory.preferred()
        self.temperature = settings.SENTIMENT_LLM_TEMPERATURE
        self.max_tokens = settings.SENTIMENT_LLM_MAX_TOKENS
        self.verify_temperature = settings.SENTIMENT_VERIFY_LLM_TEMPERATURE

    async def generate(
        self,
        company_name: str,
        project_id: int,
        fiscal_year: int,
        period_type: str,
        period_end: str,
        financial_input: FinancialInput,
        briefings: list[
            dict
        ],  # [{id, briefing_date, ai_summary, severity_breakdown, audit_verification_json}]
        events: list[dict],  # [{id, title, content_text, severity, publish_date}]
    ) -> QuarterlyReportContent:
        # 第 1 轮: 提取 (含双数据源)
        r1_user = json.dumps(
            {
                "company_name": company_name,
                "project_id": project_id,
                "fiscal_year": fiscal_year,
                "period_type": period_type,
                "period_end": period_end,
                "financial_input": financial_input.data,
                "briefings": briefings[:30],  # 限 30 份简报, 避免 prompt 爆
                "events": [
                    {
                        "id": e.get("id"),
                        "title": e.get("title", ""),
                        "content_text": (e.get("content_text", "") or "")[:1500],
                        "severity": e.get("severity", "info"),
                        "publish_date": e.get("publish_date", ""),
                        "url": e.get("url"),
                    }
                    for e in events[:200]  # 限 200 条
                ],
            },
            ensure_ascii=False,
            default=str,
        )
        r1 = await self.client.chat_json(
            Q_EXTRACT_SYSTEM,
            r1_user,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        extraction = QuarterlyExtraction(
            key_findings=r1.get("key_findings") or [],
            data_consistency_flags=r1.get("data_consistency_flags") or [],
            severity_breakdown=r1.get("severity_breakdown") or {},
            watch_list=r1.get("watch_list") or [],
        )

        # 第 2 轮: 自检 (复用 SELF_CHECK_SYSTEM, 把 key_findings 当 fact 校验)
        r2 = await self.client.chat_json(
            SELF_CHECK_SYSTEM,
            f"第 1 轮 key_findings: {json.dumps(extraction.key_findings, ensure_ascii=False)}\n"
            f"原始 events: {json.dumps(events[:50], ensure_ascii=False, default=str)}",
            temperature=self.verify_temperature,
            max_tokens=self.max_tokens,
        )

        # 第 3 轮: 挑刺
        r3 = await self.client.chat_json(
            ADVERSARIAL_SYSTEM,
            f"key_findings: {json.dumps(extraction.key_findings, ensure_ascii=False)}\n"
            f"safe_facts: {json.dumps(r2.get('safe_facts') or [], ensure_ascii=False)}",
            temperature=self.verify_temperature,
            max_tokens=self.max_tokens,
        )

        # 第 4 轮: 拼装
        safe_keys = [
            f.get("financial_field") or f"event_{f.get('event_id')}"
            for f in (r2.get("safe_facts") or [])
            if isinstance(f, dict) and f.get("verified")
        ]
        r4 = await self.client.chat_json(
            Q_COMPOSE_SYSTEM,
            f"公司: {company_name}\n"
            f"年度: {fiscal_year} {period_type}\n"
            f"financial_input: {json.dumps(financial_input.data, ensure_ascii=False)}\n"
            f"key_findings: {json.dumps(extraction.key_findings, ensure_ascii=False)}\n"
            f"data_consistency_flags: {json.dumps(extraction.data_consistency_flags, ensure_ascii=False)}\n"
            f"watch_list: {json.dumps(extraction.watch_list, ensure_ascii=False)}\n"
            f"briefings_count: {len(briefings)}, events_count: {len(events)}\n"
            f"removed: {json.dumps(r2.get('removed_facts') or [], ensure_ascii=False)}\n"
            f"critiques: {json.dumps((r3 or {}).get('critiques') or [], ensure_ascii=False)}",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        # 解析 markdown
        if isinstance(r4, dict):
            md = r4.get("markdown") or r4.get("content") or r4.get("report") or ""
        else:
            md = str(r4)
        # 兜底标题
        if not md.strip().startswith("#"):
            from app.models.db_models import SENTIMENT_PERIOD_TYPE_LABELS

            label = SENTIMENT_PERIOD_TYPE_LABELS.get(period_type, period_type)
            md = f"# {company_name} {fiscal_year} {label} 跟踪报告\n\n" + md
        md = BriefingGenerator._strip_banned_words(md)

        return QuarterlyReportContent(
            markdown=md,
            extraction=extraction,
            self_check=r2,
            adversarial=r3,
            safe_finding_keys=safe_keys,
            raw_input={
                "financial_input": financial_input.data,
                "briefings": briefings,
                "events": events,
            },
        )
