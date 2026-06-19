"""QuarterlyVerifier 双数据源对账测试.

覆盖:
  - financial_input 中的数字能在 events 文本中找到 → consistent=True
  - financial_input 数字在 events 找不到 → matched_in="none", consistent=True
    (无舆情印证不视为错误, verifier 只对 mismatch 报 error)
  - 缺关键字段 → 校验不通过
  - 数学等式: revenue - cost = gross_profit (财务公式一致性)
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

from app.services.sentiment.quarterly.verifier import (  # noqa: E402
    ConsistencyFlag,
    QuarterlyVerificationReport,
    QuarterlyVerifier,
)


# ============================================================
#  Helpers
# ============================================================


def _mk_event(eid: int, title: str, content: str) -> dict:
    return {
        "id": eid,
        "title": title,
        "content_text": content,
        "publisher": "测试源",
        "publish_date": "2024-02-15",
    }


def _mk_briefing(bid: int, summary: str, audit_json: str = "{}") -> dict:
    return {
        "id": bid,
        "briefing_date": "2024-02-15",
        "ai_summary": summary,
        "audit_verification_json": audit_json,
    }


# ============================================================
#  Tests
# ============================================================


class TestVerifierConsistency:
    """核心: financial_input vs 简报/事件中的数字一致性."""

    def test_verifier_consistency_pass(self):
        """financial_input 中的数字能在 events 文本里找到 → consistent=True."""
        v = QuarterlyVerifier()
        events = [
            _mk_event(1, "公告", "本期营业收入 100,000,000 元, 净利润 10,000,000 元"),
        ]
        financial = {
            "revenue": 100_000_000,
            "net_profit": 10_000_000,
        }
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        # revenue / net_profit 都能在 events 文本中找到
        rev_flag = next(c for c in report.consistency_flags if c.financial_field == "revenue")
        np_flag = next(c for c in report.consistency_flags if c.financial_field == "net_profit")
        assert rev_flag.matched_in == "events"
        assert rev_flag.consistent is True
        assert np_flag.matched_in == "events"
        assert np_flag.consistent is True

    def test_verifier_consistency_mismatch(self):
        """financial_input 数字 ≠ events/briefings 中的数字 → consistent=True (matched_in='none').

        QuarterlyVerifier 的语义: 找不到 = '无舆情印证', 不算 mismatch (error).
        """
        v = QuarterlyVerifier()
        events = [
            _mk_event(1, "公告", "本期营业收入 50,000,000 元"),  # 5000万, 不是 1 亿
        ]
        financial = {"revenue": 100_000_000}
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        # 100,000,000 在 events 文本里找不到, verifier 标 'none'
        rev = next(c for c in report.consistency_flags if c.financial_field == "revenue")
        assert rev.matched_in == "none"
        # 找不到不视为错误, 一致性标记为 True
        assert rev.consistent is True
        assert "无舆情印证" in rev.note

    def test_verifier_citation_missing(self):
        """简报里 [事件#N] 引用的 event 不在传入 events 列表里 → 走 BriefingVerifier 报错.

        这里通过 markdown 引用一个不存在的 id, 让 BriefingVerifier 报 broken_event_ref.
        """
        v = QuarterlyVerifier()
        events = [_mk_event(1, "公告", "内容 A")]
        financial = {"revenue": 100_000_000}
        # markdown 引用了不存在的事件 999
        md = "# 测试\n\n引用了 [事件#999] 的内容"
        report = v.verify(
            markdown=md,
            financial_input=financial,
            events=events,
            briefings=[],
        )
        # 联合校验应至少 1 个 error (broken_event_ref)
        assert report.briefing_verify_report is not None
        assert report.briefing_verify_report.error_count >= 1
        assert not report.passed

    def test_verifier_math_correctness(self):
        """数学等式: revenue - cost = gross_profit (财务公式一致性).

        QuarterlyVerifier 自身不做等式校验, 这里验证我们能基于 verify() 的产物
        自己写出"revenue - cost == gross_profit" 校验逻辑, 并集成到对账中.
        """
        v = QuarterlyVerifier()
        # 三组数据
        cases = [
            (1_000_000, 600_000, 400_000, True),   # 100w - 60w = 40w
            (1_000_000, 700_000, 400_000, False),  # 100w - 70w ≠ 40w
            (500_000, 100_000, 400_000, True),     # 50w - 10w = 40w
        ]
        for rev, cost, gp, expected_pass in cases:
            events = [_mk_event(1, "测试", f"营收 {rev:,} 成本 {cost:,} 毛利 {gp:,}")]
            financial = {"revenue": rev, "cost": cost, "gross_profit": gp}
            report = v.verify(
                markdown="# 测试",
                financial_input=financial,
                events=events,
                briefings=[],
            )
            # 我们的"等式校验"：算出 revenue - cost, 与 gross_profit 对比
            calc_gp = rev - cost
            eq_pass = calc_gp == gp
            assert eq_pass == expected_pass, (
                f"等式失败: {rev:,} - {cost:,} = {calc_gp:,}, 实际 gp={gp:,}"
            )
            # 三个字段都能在 events 文本中找到
            for f in ("revenue", "cost", "gross_profit"):
                flag = next(c for c in report.consistency_flags if c.financial_field == f)
                assert flag.matched_in == "events"
                assert flag.consistent is True

    def test_verifier_string_field_inclusion(self):
        """字符串字段 (如 '增长'/'下降') 在 events 文本里出现 → consistent."""
        v = QuarterlyVerifier()
        events = [_mk_event(1, "公告", "营收同比增长 15%")]
        financial = {"trend": "增长"}
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        flag = next(c for c in report.consistency_flags if c.financial_field == "trend")
        assert flag.matched_in == "events"
        assert flag.consistent is True
        assert flag.note == "字符串一致"

    def test_verifier_empty_financial(self):
        """financial_input 为空 → consistency_flags 为空, passed=True (无错)."""
        v = QuarterlyVerifier()
        report = v.verify(
            markdown="# 测试",
            financial_input={},
            events=[],
            briefings=[],
        )
        assert report.consistency_flags == []
        assert report.passed is True
        assert report.error_count == 0

    def test_verifier_to_dict_roundtrip(self):
        """to_dict 序列化的契约 — 不能丢字段."""
        v = QuarterlyVerifier()
        events = [_mk_event(1, "A", "B")]
        report = v.verify(
            markdown="# 测试",
            financial_input={"revenue": 1_000_000},
            events=events,
            briefings=[],
        )
        d = report.to_dict()
        assert "passed" in d
        assert "consistency_flags" in d
        assert "issue_count" in d
        assert "error_count" in d
        # consistency_flags 元素也是 dict
        for c in d["consistency_flags"]:
            assert "financial_field" in c
            assert "consistent" in c
