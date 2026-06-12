"""简报独立校验器 — 数字 / 事件引用 回查原始 event.

与 LLM 的 4 轮协议解耦; 只看产物, 不信 LLM.
校验失败 → briefing.verification_failed=True, 禁止进入 review 状态.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# 数字/百分比/日期/金额 — 抽取模式
NUMERIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"\d{1,3}(?:,\d{3})+\.?\d*"),       # 1,234.56 / 1,234,567
    re.compile(r"\d+\.\d+%"),                       # 12.5%
    re.compile(r"\d+%"),                            # 12%
    re.compile(r"[\d.]+亿"),                         # 1.5亿
    re.compile(r"[\d.]+万"),                         # 200万
    re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),          # 2025-06-12
    re.compile(r"\d{4}年\d{1,2}月\d{1,2}日"),       # 2025年6月12日
]

# 引用模式: [事件#N] 或 事件#N
EVENT_REF_PATTERN = re.compile(r"\[?事件#(\d+)\]?")

# 监管文号 / 公告文号 (粗略: 含汉字 + 数字 + 字母)
DOC_NO_PATTERN = re.compile(r"[一-龥]{2,8}[\[\(（]?\d{4}[\]\)）]?号?(?:第?\d+号)?")


@dataclass
class Issue:
    """校验问题."""
    issue_type: str        # missing_event_ref / hallucinated_number / broken_event_ref / mood_word / unverified_fact
    event_id: Optional[int]
    detail: str
    severity: str = "warn"  # warn / error (error → verification_failed=True)


@dataclass
class VerificationReport:
    """校验产物 — 落库到 briefing.audit_verification_json."""
    passed: bool
    issue_count: int = 0
    error_count: int = 0
    warn_count: int = 0
    issues: list[Issue] = field(default_factory=list)
    fact_verification: list[dict] = field(default_factory=list)  # 每条 fact 的核实结果
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "issue_count": self.issue_count,
            "error_count": self.error_count,
            "warn_count": self.warn_count,
            "issues": [
                {"type": i.issue_type, "event_id": i.event_id, "detail": i.detail, "severity": i.severity}
                for i in self.issues
            ],
            "fact_verification": self.fact_verification,
            "note": self.note,
        }


class BriefingVerifier:
    """独立校验简报 Markdown 与原始 events 的一致性."""

    # 情绪词 — 与 generator 保持一致
    BANNED_WORDS = ["严重", "恶劣", "暴雷", "崩塌", "惊天", "血亏", "惨烈", "崩盘", "重磅"]

    def verify(
        self,
        markdown: str,
        raw_events: list[dict],
        safe_fact_event_ids: Optional[list[int]] = None,
        key_facts: Optional[list[dict]] = None,
    ) -> VerificationReport:
        """核心校验.

        Args:
            markdown: LLM 生成的最终简报 Markdown
            raw_events: 原始事件列表 (dict), 每条至少含 id/title/content_text/publisher
            safe_fact_event_ids: LLM 自检后"通过"的事件 id 列表; 若为 None 则视为全通过
            key_facts: 第 1 轮 LLM 提取的事实列表 (含 quote 字段).
                若提供, verifier 会校验每个 quote 是否在对应 event 的 content_text 中
                出现 (substring 匹配), 防止 LLM 数字碰巧匹配但事实是编造的情况.
        """
        safe_ids: Optional[set[int]] = (
            set(safe_fact_event_ids) if safe_fact_event_ids is not None else None
        )
        events_by_id = {e.get("id"): e for e in raw_events if e.get("id") is not None}

        issues: list[Issue] = []
        fact_verification: list[dict] = []

        # 1) 情绪词扫描
        for w in self.BANNED_WORDS:
            if w in markdown:
                issues.append(Issue(
                    issue_type="mood_word",
                    event_id=None,
                    detail=f"禁用情绪词『{w}』出现在简报正文中 (违反用词精准要求)",
                    severity="error",
                ))

        # 2) [事件#N] 引用校验
        ref_ids = set()
        for m in EVENT_REF_PATTERN.finditer(markdown):
            try:
                eid = int(m.group(1))
            except (TypeError, ValueError):
                continue
            ref_ids.add(eid)
            if eid not in events_by_id:
                issues.append(Issue(
                    issue_type="broken_event_ref",
                    event_id=eid,
                    detail=f"引用了不存在的事件 id={eid}",
                    severity="error",
                ))

        if not ref_ids and raw_events:
            # 简报里没有任何事件引用 → 强警告
            issues.append(Issue(
                issue_type="missing_event_ref",
                event_id=None,
                detail="简报正文未引用任何 [事件#N] 标记 (违反『每个事实必须引用』要求)",
                severity="error",
            ))

        # 3) 数字/日期/金额/文号 一致性
        # 思路: 把 markdown 中所有数字抽出, 再把所有被引用的 event 的原文数字合并成 union,
        # 差集就是 LLM 幻觉的嫌疑数字
        md_numbers = self._extract_numbers(markdown)
        all_referenced_content: list[str] = []
        for eid in ref_ids:
            ev = events_by_id.get(eid)
            if not ev:
                continue
            all_referenced_content.append((ev.get("content_text", "") or "") + " " + (ev.get("title", "") or ""))
        union_content_numbers: set[str] = set()
        for c in all_referenced_content:
            union_content_numbers |= self._extract_numbers(c)
        hallucinated = md_numbers - union_content_numbers
        if hallucinated:
            for n in sorted(hallucinated)[:10]:
                # 找出哪个 event 缺这个数字 (用于 fact_verification)
                missing_in = []
                for eid in ref_ids:
                    ev = events_by_id.get(eid)
                    if not ev:
                        continue
                    if n not in self._extract_numbers((ev.get("content_text", "") or "") + " " + (ev.get("title", "") or "")):
                        missing_in.append(eid)
                issues.append(Issue(
                    issue_type="hallucinated_number",
                    event_id=missing_in[0] if missing_in else None,
                    detail=f"数字『{n}』在被引用事件 {missing_in or list(ref_ids)} 的原文中均不存在, 疑似 LLM 幻觉",
                    severity="error",
                ))
        # 记录 fact 核实结果
        for eid in ref_ids:
            ev = events_by_id.get(eid)
            if not ev:
                continue
            content = (ev.get("content_text", "") or "") + " " + (ev.get("title", "") or "")
            ev_numbers = self._extract_numbers(content)
            fact_verification.append({
                "event_id": eid,
                "matched_numbers": sorted(md_numbers & ev_numbers),
                "hallucinated_numbers": sorted(md_numbers - ev_numbers),
            })

        # 4) safe_fact_event_ids 与实际引用比对
        if safe_ids is not None:
            used_unsafe = ref_ids - safe_ids
            for eid in used_unsafe:
                issues.append(Issue(
                    issue_type="unverified_fact",
                    event_id=eid,
                    detail=f"事件 {eid} 出现在正文, 但 LLM 自检未将其标记为 verified",
                    severity="warn",
                ))

        # 5) quote 精确 substring 匹配 (P0 LLM F2 修复)
        # 防止 LLM 数字碰巧匹配但 fact 是编造的情况
        if key_facts:
            for fact in key_facts:
                eid = fact.get("event_id")
                quote = (fact.get("quote") or "").strip()
                if not quote or eid is None:
                    continue
                ev = events_by_id.get(eid)
                if not ev:
                    issues.append(Issue(
                        issue_type="unverified_fact",
                        event_id=eid,
                        detail=f"key_facts 引用了不存在的事件 id={eid}",
                        severity="warn",
                    ))
                    continue
                content = (ev.get("content_text", "") or "") + " " + (ev.get("title", "") or "")
                if quote not in content:
                    issues.append(Issue(
                        issue_type="quote_not_in_source",
                        event_id=eid,
                        detail=f"quote『{quote[:50]}...』在原文 (事件 {eid}) 中找不到完全一致的子串, 疑似 LLM 编造事实",
                        severity="error",
                    ))

        # 汇总
        errors = [i for i in issues if i.severity == "error"]
        warns = [i for i in issues if i.severity == "warn"]
        report = VerificationReport(
            passed=(len(errors) == 0),
            issue_count=len(issues),
            error_count=len(errors),
            warn_count=len(warns),
            issues=issues,
            fact_verification=fact_verification,
            note="由 BriefingVerifier 独立校验; 不依赖 LLM 自身判断",
        )
        return report

    def _extract_numbers(self, text: str) -> set[str]:
        if not text:
            return set()
        out: set[str] = set()
        for pat in NUMERIC_PATTERNS:
            for m in pat.finditer(text):
                out.add(m.group(0))
        return out
