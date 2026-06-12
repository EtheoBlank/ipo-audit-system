"""4 轮 LLM 协议 — 提取 / 自检 / 挑刺 / 拼装.

设计目标 (用户硬性要求):
    - 用词精准: 不允许"严重"/"恶劣"/"暴雷"/"崩塌"等情绪词
    - 数据反复核实: 每个事实必须 [事件#N] 引用; 数字必须与原文一致
    - 供领导审阅: 4 轮产物分别落库, 审计师/领导可逐条对照

第 1 轮 (extract): 输入事件 JSON, 输出 key_facts/severity_breakdown/watch_list
第 2 轮 (self_check): 同对话, 让模型自检每个 key_fact 能否在原文找到完全一致字符
第 3 轮 (adversarial): 扮演挑刺审计师, 列出第 1-2 轮中所有不实/夸大/双关/暧昧的措辞
                    — 这一轮不写入最终简报, 但产物落 audit_verification_json 供领导审阅
第 4 轮 (compose): 输入第 1+2 轮安全事实 + 第 3 轮挑刺结论, 输出最终 Markdown 简报
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import settings
from app.services.sentiment.llm_client import LlmClientFactory

logger = logging.getLogger(__name__)


# ============================================================
#  Prompt 模板 (集中维护)
# ============================================================

# 禁用情绪词 — 简报里出现这些词会被 verifier 标红
BANNED_WORDS = ["严重", "恶劣", "暴雷", "崩塌", "惊天", "血亏", "惨烈", "崩盘", "重磅"]


EXTRACT_SYSTEM = """你是 IPO 审计舆情分析师, 你的唯一任务是从原始事件中提取关键事实.

【铁律】
1. 严禁评价、推测、演绎 — 只提取
2. 每个事实必须以 event_id 引用, 不允许 "据报道" / "有消息称" 等模糊措辞
3. 所有数字、日期、金额、百分比必须与原文逐字一致, 不一致则不写
4. 引用公告文号/监管函号必须与原文逐字一致
5. 禁用情绪词: 严重 / 恶劣 / 暴雷 / 崩塌 / 惊天 / 血亏 / 惨烈 / 崩盘 / 重磅

【输出 JSON Schema】
{
  "key_facts": [
    {"event_id": <int>, "fact": "<短句, 不超 60 字>", "quote": "<原文片段, 5-100 字>",
     "publish_date": "YYYY-MM-DD", "severity": "info|notice|warn|critical"}
  ],
  "severity_breakdown": {"info": <int>, "notice": <int>, "warn": <int>, "critical": <int>},
  "watch_list": [{"event_id": <int>, "reason": "<为何需要持续关注, 30 字内>"}],
  "tone_words_used": ["<自报: 本轮用过的情绪词, 应为空>"]
}
"""

EXTRACT_USER_TEMPLATE = """项目: {company_name} ({project_id})
简报日期: {briefing_date}
事件清单 (JSON):
{events_json}

请按 schema 输出 JSON."""


SELF_CHECK_SYSTEM = """【独立视角】你是独立的复核员, 没有上下文, 只基于以下 JSON 判断. 不要假设你知道任何外部信息.

你的任务: 逐条复核第 1 轮 (extract) 的 key_facts, 校验字符级一致性.

【任务】逐条检查 key_facts:
- fact 描述能否在 quote 中找到完全一致的字符 (字符级匹配, 不允许改写)
- 数字、日期、金额、百分比、监管文号是否与 quote 逐字一致
- 是否包含禁用情绪词 (严重/恶劣/暴雷/崩塌/惊天/血亏/惨烈/崩盘/重磅)
- quote 字段是否在原始 events 文本中可被找到 (字符串 substring 匹配)

【输出 JSON Schema】
{
  "safe_facts": [
    {"event_id": <int>, "verified": true|false, "issue": "<不通过原因, 验证通过则空>"}
  ],
  "removed_facts": [{"event_id": <int>, "original_fact": "<被剔除的原 fact>"}]
}
"""


ADVERSARIAL_SYSTEM = """【独立视角】你是独立的挑刺审计师, 没有上下文, 只基于以下 JSON 判断. 不要假设你知道任何外部信息.

你的任务: 找出上一轮 (含自检) 中所有可能不实、夸大、双关、暧昧的措辞.

【任务】
- 逐条检查 key_facts: 用词是否夸大? 是否暗含因果/推测? 是否使用了双关语?
- 检查 watch_list: reason 是否引用了事实? 是否暗示未来走向?
- 列出具体问题与原文对比

【输出 JSON Schema】
{
  "critiques": [
    {"target_event_id": <int>, "target_text": "<被质疑的原文>", "issue": "<具体问题>"}
  ],
  "overall_risk_summary": "<5-15 字, 中性, 不评价, 只描述信息密度>"
}
"""


COMPOSE_SYSTEM = """你是 IPO 审计舆情简报编辑. 综合 4 类输入生成最终简报 Markdown.

【强约束 — 违反任一条则视为不合格】
1. 每个事实以 "[事件#N]" 引用 — 没有引用的事实不要写
2. 数字 / 金额 / 百分比 / 日期与原文逐字一致 — 不一致则删去该数字
3. 监管文号 / 公告文号逐字一致
4. 严禁使用: 严重 / 恶劣 / 暴雷 / 崩塌 / 惊天 / 血亏 / 惨烈 / 崩盘 / 重磅 / 触目惊心 / 惊心动魄
5. 对尚未证实 / 正在调查 / 待回复的事项, 用 "尚待核实"
6. 不评价公司前景 / 不预测股价 / 不下结论 ("利好" / "利空" / "值得期待" 全部禁用)
7. 全篇不使用感叹号, 不使用反问句

【输出 Markdown 模板】
# {company_name} {briefing_date} 舆情简报

## 一、关键事实 ({n} 条)
1. [事件#1] <事实> (来源: <publisher>, <publish_date>)
2. [事件#2] <事实> (来源: <publisher>, <publish_date>)
...

## 二、严重度统计
| 等级 | 数量 |
| --- | --- |
| 重大 (critical) | n |
| 警示 (warn) | n |
| 关注 (notice) | n |
| 一般 (info) | n |

## 三、需持续关注 ({n} 条)
- [事件#N] <原因> (来源: <publisher>)

## 四、数据核实说明
<逐条说明: 哪些数字与原文一致, 哪些被剔除, 剔除原因>

> 本简报由系统基于 {n} 条舆情事件自动生成, 经 4 轮 LLM 协议与独立校验器交叉核实, 供审计师与领导审阅. 所有事实可点击原文链接核对.
"""


# ============================================================
#  数据类 — 4 轮产物的内存表示
# ============================================================


@dataclass
class ExtractionResult:
    key_facts: list[dict] = field(default_factory=list)
    severity_breakdown: dict = field(default_factory=dict)
    watch_list: list[dict] = field(default_factory=list)
    tone_words_used: list[str] = field(default_factory=list)


@dataclass
class SelfCheckResult:
    safe_facts: list[dict] = field(default_factory=list)
    removed_facts: list[dict] = field(default_factory=list)


@dataclass
class AdversarialResult:
    critiques: list[dict] = field(default_factory=list)
    overall_risk_summary: str = ""


@dataclass
class BriefingContent:
    """4 轮协议最终产物."""
    markdown: str
    extraction: ExtractionResult
    self_check: SelfCheckResult
    adversarial: AdversarialResult
    safe_fact_event_ids: list[int]      # 用于 verifier 二次校验
    event_snapshot: list[dict]          # 当日事件精简 (入库用)
    raw_input_events: list[dict] = field(default_factory=list)  # 原始事件, verifier 也要用


# ============================================================
#  4 轮协议主类
# ============================================================


class BriefingGenerator:
    """执行 4 轮 LLM 协议, 输出 BriefingContent.

    用法::

        gen = BriefingGenerator()
        content = await gen.generate(
            company_name="ACME 公司",
            project_id=1,
            briefing_date="2025-06-12",
            events=[{...}, ...],  # dict 列表, 含 id/title/content_text/publisher/publish_date/severity
        )
    """

    def __init__(self) -> None:
        self.client = LlmClientFactory.preferred()
        self.temperature = settings.SENTIMENT_LLM_TEMPERATURE
        self.max_tokens = settings.SENTIMENT_LLM_MAX_TOKENS
        self.verify_temperature = settings.SENTIMENT_VERIFY_LLM_TEMPERATURE

    async def generate(
        self,
        company_name: str,
        project_id: int,
        briefing_date: str,
        events: list[dict],
    ) -> BriefingContent:
        if not events:
            raise ValueError("events 不能为空 (调用前应先过 detector)")

        # 准备事件 JSON (只取 LLM 需要的字段)
        slim_events = [
            {
                "id": e.get("id"),
                "title": e.get("title", ""),
                "content_text": e.get("content_text", "")[:2000],  # 限 2K 避免 prompt 爆
                "publisher": e.get("publisher", ""),
                "publish_date": e.get("publish_date", ""),
                "severity": e.get("severity", "info"),
                "url": e.get("url"),
            }
            for e in events
        ]
        events_json = json.dumps(slim_events, ensure_ascii=False, indent=2)

        # 第 1 轮: 提取
        extract_user = EXTRACT_USER_TEMPLATE.format(
            company_name=company_name,
            project_id=project_id,
            briefing_date=briefing_date,
            events_json=events_json,
        )
        r1 = await self.client.chat_json(
            EXTRACT_SYSTEM, extract_user,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        extraction = self._parse_extraction(r1)

        # 第 2 轮: 自检
        r2 = await self.client.chat_json(
            SELF_CHECK_SYSTEM,
            f"第 1 轮 key_facts: {json.dumps(extraction.key_facts, ensure_ascii=False)}\n"
            f"原始 events: {events_json}",
            temperature=self.verify_temperature,
            max_tokens=self.max_tokens,
        )
        self_check = self._parse_self_check(r2)

        # 第 3 轮: 挑刺
        r3 = await self.client.chat_json(
            ADVERSARIAL_SYSTEM,
            f"key_facts (第1轮): {json.dumps(extraction.key_facts, ensure_ascii=False)}\n"
            f"safe_facts (第2轮): {json.dumps(self_check.safe_facts, ensure_ascii=False)}\n"
            f"原始 events: {events_json}",
            temperature=self.verify_temperature,
            max_tokens=self.max_tokens,
        )
        adversarial = self._parse_adversarial(r3)

        # 第 4 轮: 拼装
        safe_ids = [f["event_id"] for f in self_check.safe_facts if f.get("verified")]
        safe_facts = [f for f in extraction.key_facts if f.get("event_id") in safe_ids]
        watch_list = [w for w in extraction.watch_list if w.get("event_id") in safe_ids]

        compose_user = (
            f"公司: {company_name}\n"
            f"日期: {briefing_date}\n"
            f"safe_facts: {json.dumps(safe_facts, ensure_ascii=False)}\n"
            f"watch_list: {json.dumps(watch_list, ensure_ascii=False)}\n"
            f"severity_breakdown: {json.dumps(extraction.severity_breakdown, ensure_ascii=False)}\n"
            f"removed_facts (含原因): {json.dumps(self_check.removed_facts, ensure_ascii=False)}\n"
            f"adversarial_critiques: {json.dumps(adversarial.critiques, ensure_ascii=False)}\n"
            f"\n请按 COMPOSE_SYSTEM 的 Markdown 模板输出. "
            f"第四节『数据核实说明』必须把 removed_facts 全部列出, 注明剔除原因. "
            f"其余章节只引用 safe_facts 中的 event_id. "
        )
        r4 = await self.client.chat_json(
            COMPOSE_SYSTEM, compose_user,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        # r4 期望是 {"markdown": "..."} 或直接是 markdown 字符串
        markdown = self._parse_compose(r4, company_name, briefing_date, len(safe_facts))

        # 情绪词后置硬过滤 — 即使 LLM 不听话, 也兜底
        markdown = self._strip_banned_words(markdown)

        return BriefingContent(
            markdown=markdown,
            extraction=extraction,
            self_check=self_check,
            adversarial=adversarial,
            safe_fact_event_ids=safe_ids,
            event_snapshot=[
                {
                    "id": e.get("id"),
                    "title": e.get("title", ""),
                    "severity": e.get("severity", "info"),
                    "publisher": e.get("publisher", ""),
                    "publish_date": e.get("publish_date", ""),
                    "url": e.get("url"),
                    "summary_one_line": (e.get("content_text", "") or "")[:200],
                }
                for e in events
            ],
            raw_input_events=events,
        )

    # ---- 解析 --------------------------------------------------------

    def _parse_extraction(self, r: dict) -> ExtractionResult:
        return ExtractionResult(
            key_facts=r.get("key_facts") or [],
            severity_breakdown=r.get("severity_breakdown") or {},
            watch_list=r.get("watch_list") or [],
            tone_words_used=r.get("tone_words_used") or [],
        )

    def _parse_self_check(self, r: dict) -> SelfCheckResult:
        return SelfCheckResult(
            safe_facts=r.get("safe_facts") or [],
            removed_facts=r.get("removed_facts") or [],
        )

    def _parse_adversarial(self, r: dict) -> AdversarialResult:
        return AdversarialResult(
            critiques=r.get("critiques") or [],
            overall_risk_summary=r.get("overall_risk_summary") or "",
        )

    def _parse_compose(self, r: Any, company_name: str, briefing_date: str, n_safe: int) -> str:
        """解析第 4 轮产物. 可能是 dict {"markdown": "..."} 或 直接字符串."""
        if isinstance(r, dict):
            md = r.get("markdown") or r.get("content") or r.get("report") or ""
        else:
            md = str(r)
        if not md.strip().startswith("#"):
            # 容错: 包一层标题
            md = f"# {company_name} {briefing_date} 舆情简报\n\n" + md
        return md

    @staticmethod
    def _strip_banned_words(text: str) -> str:
        """后置硬过滤: 出现 BANNED_WORDS 任一即替换为 ***"""
        import re
        out = text
        for w in BANNED_WORDS:
            out = re.sub(re.escape(w), "***", out)
        return out
