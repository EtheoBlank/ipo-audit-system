"""季度报告双数据源对账 — financial_input vs 简报/事件中数字."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.sentiment.briefing.verifier import BriefingVerifier

logger = logging.getLogger(__name__)


# Round 35 P0: 百分比形 fallback 通用 subject — 未知字段名 / None 时用.
_GENERIC_SUBJECT_TOKENS: tuple[str, ...] = (
    "毛利率", "营收", "营业收入", "净利", "净利润", "同比", "环比",
    "增长率", "增幅", "占比", "费用率", "负债率", "总资产", "现金流",
    "gross", "margin", "yoy", "revenue", "profit",
)


@dataclass
class ConsistencyFlag:
    """一对数字的比对结果."""

    financial_field: str
    financial_value: Any
    matched_in: str  # "events" / "briefings" / "none"
    matched_value: Any
    consistent: bool
    note: str = ""


@dataclass
class QuarterlyVerificationReport:
    passed: bool
    consistency_flags: list[ConsistencyFlag] = field(default_factory=list)
    briefing_verify_report: Optional[Any] = None  # 引用 BriefingVerifier 产物
    issue_count: int = 0
    error_count: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "consistency_flags": [
                {
                    "financial_field": c.financial_field,
                    "financial_value": c.financial_value,
                    "matched_in": c.matched_in,
                    "matched_value": c.matched_value,
                    "consistent": c.consistent,
                    "note": c.note,
                }
                for c in self.consistency_flags
            ],
            "briefing_verify_report": (
                self.briefing_verify_report.to_dict()
                if self.briefing_verify_report is not None
                else None
            ),
            "issue_count": self.issue_count,
            "error_count": self.error_count,
            "note": self.note,
        }


class QuarterlyVerifier:
    """双数据源对账: financial_input 中的每个数值字段 vs 简报/事件原文.

    通过判定:
        - 字段在简报/事件原文中能找到一致值 → consistent
        - 字段在简报/事件原文中未出现 → "无舆情印证" (不视为错误, 仅标注)
        - 字段在简报/事件原文中出现, 但值与 financial_input 不符 → 不一致 (error)
    """

    def __init__(self) -> None:
        self.briefing_verifier = BriefingVerifier()

    def verify(
        self,
        markdown: str,
        financial_input: dict,
        events: list[dict],
        briefings: list[dict],
    ) -> QuarterlyVerificationReport:
        flags: list[ConsistencyFlag] = []

        # 1) 对每个 financial 字段, 扫描所有 events + briefings 文本, 看是否有提及
        events_text = self._flatten_texts(events, fields=["title", "content_text"])
        briefings_text = self._flatten_texts(
            briefings, fields=["ai_summary", "audit_verification_json"]
        )

        for field_name, value in financial_input.items():
            if value is None:
                continue
            if not isinstance(value, (int, float, str)):
                continue
            # 数字字段才比 — 透传 field_name, 让百分比形 subject 匹配走严格路径
            if isinstance(value, (int, float)):
                matched_in, matched_val, note = self._find_value(
                    value, events_text, briefings_text, field_name=field_name,
                )
            else:
                # 字符串字段 (例如 "增长" / "下降") 粗略包含
                if str(value) in events_text or str(value) in briefings_text:
                    matched_in = "events" if str(value) in events_text else "briefings"
                    matched_val = value
                    note = "字符串一致"
                else:
                    matched_in, matched_val, note = "none", None, "无舆情印证"

            consistent = matched_in != "mismatch"
            flags.append(
                ConsistencyFlag(
                    financial_field=field_name,
                    financial_value=value,
                    matched_in=matched_in,
                    matched_value=matched_val,
                    consistent=consistent,
                    note=note,
                )
            )

        # 2) 简报侧的常规校验 (复用 BriefingVerifier)
        ev_dicts = [
            {
                "id": e.get("id"),
                "title": e.get("title", ""),
                "content_text": e.get("content_text", ""),
                "publisher": e.get("publisher", ""),
                "publish_date": e.get("publish_date", ""),
            }
            for e in events
        ]
        brief_verify = self.briefing_verifier.verify(markdown, ev_dicts) if markdown else None

        # 3) 汇总
        error_count = sum(1 for c in flags if not c.consistent)
        if brief_verify is not None:
            error_count += brief_verify.error_count

        return QuarterlyVerificationReport(
            passed=(error_count == 0),
            consistency_flags=flags,
            briefing_verify_report=brief_verify,
            issue_count=len(flags) + (brief_verify.issue_count if brief_verify else 0),
            error_count=error_count,
            note="由 QuarterlyVerifier 双数据源对账 + BriefingVerifier 联合校验",
        )

    def _flatten_texts(self, items: list[dict], fields: list[str]) -> str:
        out: list[str] = []
        for item in items:
            for f in fields:
                v = item.get(f) or ""
                if isinstance(v, str):
                    out.append(v)
        return "\n".join(out)

    def _find_value(
        self, value, events_text: str, briefings_text: str,
        *, field_name: Optional[str] = None,
    ):
        """在 events/briefings 文本里找 value 出现.

        Round 35 P0: 旧版百分比形 {f*100:.1f}% 会无条件命中任何 "50.0%" 字样,
        即使该字样在讨论完全不相关的事项 (毛利率 50% vs 营收 50%). 例:
            financial_input = {"gross_margin": 0.5}
            events_text = "营收同比增长 50.0%, 净利大幅提升"
        旧版: 错配 events="营收..." (matched_value="50.0%"), 一致性假阳性.
        修复:
            1. verify() 循环透传 ``field_name``, 百分比形必须 subject 上下文匹配
            2. subject 关键词从 field_name 启发 (gross_margin→毛利率/margin, ...)
            3. 纯数值形 (千分位 / 浮点 / 整数) 不强加 subject, 因金额在上下文中天然区分
        """
        forms: list[tuple[str, bool]] = []  # (form, is_percentage)
        if isinstance(value, (int, float)):
            f = float(value)
            # 多种表示形式: 原始 / 带千分位 / 不带千分位 / 小数
            if isinstance(value, int):
                forms.extend([(str(value), False), (f"{value:,}", False)])
            else:
                forms.extend([
                    (f"{f:g}", False),
                    (f"{f:,.2f}", False),
                    (f"{f:.2f}", False),
                    (f"{f:,}", False),
                ])
            # 百分比 (0~1 之间) — 标 pct: 前缀, 需 subject 上下文匹配
            if 0 < f < 1:
                forms.append((f"pct:{f * 100:.2f}%", True))
                forms.append((f"pct:{f * 100:.1f}%", True))
                forms.append((f"pct:{f * 100:.0f}%", True))
            # 100 倍 (被当作 %)
            forms.append((f"pct:{f * 100:.1f}%", True))
            # 整数部分 + 浮点表示
            if f >= 1:
                forms.append((f"{f:.0f}", False))
                forms.append((f"{f:,.0f}", False))
        else:
            forms.append((str(value), False))
        # 去重
        seen = set()
        uniq: list[tuple[str, bool]] = []
        for form, is_pct in forms:
            if form in seen:
                continue
            seen.add(form)
            uniq.append((form, is_pct))

        # 第一轮: 纯数值形 (整数 / 千分位 / 浮点) — 不强加 subject
        for form, is_pct in uniq:
            if is_pct:
                continue
            if form in events_text:
                return "events", form, f"事件文本中匹配 {form}"
        for form, is_pct in uniq:
            if is_pct:
                continue
            if form in briefings_text:
                return "briefings", form, f"简报文本中匹配 {form}"

        # 第二轮: 百分比形 — 必须 subject 上下文匹配 (避免假阳性)
        field_subjects = self._field_subject_tokens(field_name)
        for form, is_pct in uniq:
            if not is_pct:
                continue
            bare = form[len("pct:"):]  # 去掉前缀
            for src_name, src_text in (("events", events_text), ("briefings", briefings_text)):
                if bare not in src_text:
                    continue
                if self._has_subject_context(src_text, bare, field_subjects):
                    return src_name, bare, f"{src_name}文本中百分比匹配 {bare} 且 subject 上下文一致"
        return "none", None, "无舆情印证 (不视为错误)"

    @staticmethod
    def _field_subject_tokens(field_name: Optional[str]) -> tuple[str, ...]:
        """根据 financial_input 字段名返回该字段专属的 subject token.

        - gross_margin → 毛利率 / margin
        - revenue / yoy_revenue → 营收 / 营业收入 / revenue
        - net_profit / yoy_net_profit → 净利 / 净利润 / profit
        - 总资产 / 现金流 → 对应中文 token
        - 未知字段: 返通用 tuple (含多种, 避免过度严格漏匹配)
        """
        if not field_name:
            return _GENERIC_SUBJECT_TOKENS
        f = field_name.lower()
        if "gross_margin" in f or "margin" in f:
            return ("毛利率", "毛利", "margin", "gross")
        if "revenue" in f or "yoy_revenue" in f:
            return ("营收", "营业收入", "revenue", "同比", "yoy")
        if "net_profit" in f or "yoy_net_profit" in f:
            return ("净利", "净利润", "扣非", "profit")
        if "non_recurring" in f:
            return ("扣非", "非经常性损益", "non_recurring")
        if "total_assets" in f:
            return ("总资产", "资产总额", "assets")
        if "cash_flow" in f or "operating_cash" in f:
            return ("现金流", "经营活动现金流", "cash_flow")
        return _GENERIC_SUBJECT_TOKENS

    @classmethod
    def _has_subject_context(
        cls, text: str, needle: str, subjects: tuple[str, ...], window: int = 80,
    ) -> bool:
        """在 text 中所有 needle 出现位置, 任意一个 ±window 内出现任一 subject token 则 True."""
        if not text or not needle or not subjects:
            return False
        n = len(needle)
        i = 0
        text_lower = text.lower()
        while True:
            idx = text.find(needle, i)
            if idx < 0:
                return False
            lo = max(0, idx - window)
            hi = min(len(text), idx + n + window)
            chunk_lower = text_lower[lo:hi]
            if any(tok.lower() in chunk_lower for tok in subjects):
                return True
            i = idx + n
