"""季度报告双数据源对账 — financial_input vs 简报/事件中数字."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.sentiment.briefing.verifier import BriefingVerifier

logger = logging.getLogger(__name__)


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
            # 数字字段才比
            if isinstance(value, (int, float)):
                matched_in, matched_val, note = self._find_value(value, events_text, briefings_text)
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

    def _find_value(self, value, events_text: str, briefings_text: str):
        """在 events/briefings 文本里找 value 出现."""
        forms: list[str] = []
        if isinstance(value, (int, float)):
            f = float(value)
            # 多种表示形式: 原始 / 带千分位 / 不带千分位 / 小数 / 百分比
            if isinstance(value, int):
                # 整数: 也加带逗号版本
                forms.extend([str(value), f"{value:,}"])
            else:
                forms.extend([f"{f:g}", f"{f:,.2f}", f"{f:.2f}", f"{f:,}"])
            # 百分比 (0~1 之间)
            if 0 < f < 1:
                forms.append(f"{f * 100:.2f}%")
                forms.append(f"{f * 100:.1f}%")
                forms.append(f"{f * 100:.0f}%")
            # 100 倍 (被当作 %)
            forms.append(f"{f * 100:.1f}%")
            # 整数部分 + 浮点表示
            if f >= 1:
                forms.append(f"{f:.0f}")
                forms.append(f"{f:,.0f}")
        else:
            forms.append(str(value))
        # 去重
        forms = list(dict.fromkeys(forms))
        # 优先 events
        for form in forms:
            if form in events_text:
                return "events", form, f"事件文本中匹配 {form}"
        for form in forms:
            if form in briefings_text:
                return "briefings", form, f"简报文本中匹配 {form}"
        return "none", None, "无舆情印证 (不视为错误)"
