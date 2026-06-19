"""QuarterlyReportGenerator (4 轮 LLM 协议) 测试.

覆盖:
  - 输入 events + financial → 返回 dict (含 summary / sections / citations)
  - 空 events → 降级输出
  - 引用抽取
  - AI 不可用 → 降级为 markdown 骨架

Mock 出 LlmClientFactory.preferred() 返回的 client, 不调真 DeepSeek.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# 在 import app 之前设环境变量
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

from app.services.sentiment.quarterly.financial_input import FinancialInput  # noqa: E402
from app.services.sentiment.quarterly.generator import (  # noqa: E402
    QuarterlyReportContent,
    QuarterlyReportGenerator,
)


# ============================================================
#  Mock LLM client
# ============================================================


def _mock_client(r1=None, r2=None, r3=None, r4=None) -> MagicMock:
    """构造 mock LlmClient, 4 轮对话分别返回指定 dict."""
    client = MagicMock()
    payloads = [r1, r2, r3, r4]
    call_log: list[dict] = []

    async def chat_json(system, user, **kwargs):
        call_log.append({"system_prefix": system[:30], "user_prefix": user[:50]})
        if payloads and payloads[0] is not None:
            return payloads.pop(0)
        return {}

    client.chat_json.side_effect = chat_json
    client._call_log = call_log
    return client


def _mock_financial_input() -> FinancialInput:
    return FinancialInput(
        data={
            "revenue": 100_000_000,
            "net_profit": 10_000_000,
            "non_recurring_pnl": 9_500_000,
            "gross_margin": 30.0,
            "yoy_revenue": 15.0,
            "yoy_net_profit": 8.0,
            "total_assets": 500_000_000,
            "operating_cash_flow": 12_000_000,
        },
        source="manual",
        verified_by="auditor1",
        verified_at="2024-04-30T00:00:00+00:00",
    )


# ============================================================
#  Tests
# ============================================================


class TestGeneratorReturnsContent:
    """正常路径: 4 轮 LLM 都返回有效 dict → QuarterlyReportContent."""

    @pytest.mark.asyncio
    async def test_generator_returns_quarterly_report_dict(self):
        """input events + financial → 返回 dataclass 含 markdown / extraction / safe_keys / raw_input."""
        r1 = {
            "key_findings": [
                {"event_id": 1, "financial_field": None, "finding": "公司公告营收 1 亿",
                 "severity": "info"},
                {"event_id": 2, "financial_field": "revenue",
                 "finding": "营收同比 +15%", "severity": "notice"},
            ],
            "data_consistency_flags": [
                {"financial_field": "revenue", "financial_value": 100_000_000,
                 "claimed_in_event": 100_000_000, "consistent": True, "note": "一致"},
            ],
            "severity_breakdown": {"info": 1, "notice": 1, "warn": 0, "critical": 0},
            "watch_list": [{"event_id": 2, "reason": "营收增长待持续观察"}],
        }
        r2 = {
            "safe_facts": [
                {"event_id": 1, "verified": True, "issue": ""},
                {"event_id": 2, "verified": True, "issue": ""},
            ],
            "removed_facts": [],
        }
        r3 = {"critiques": []}
        r4 = {"markdown": "# 测试公司 2024 Q1 跟踪报告\n\n## 一、核心发现\n无"}
        client = _mock_client(r1=r1, r2=r2, r3=r3, r4=r4)

        with patch(
            "app.services.sentiment.quarterly.generator.LlmClientFactory.preferred",
            return_value=client,
        ):
            gen = QuarterlyReportGenerator()
            fin = _mock_financial_input()
            events = [
                {"id": 1, "title": "公司公告", "content_text": "公司 2024 Q1 营收 1 亿",
                 "severity": "info", "publish_date": "2024-02-15"},
                {"id": 2, "title": "媒体", "content_text": "营收同比 +15%",
                 "severity": "notice", "publish_date": "2024-03-10"},
            ]
            briefings = [{"id": 100, "briefing_date": "2024-02-15", "ai_summary": "...", "audit_verification_json": "{}"}]

            result: QuarterlyReportContent = await gen.generate(
                company_name="测试公司",
                project_id=1,
                fiscal_year=2024,
                period_type="Q1",
                period_end="2024-03-31",
                financial_input=fin,
                briefings=briefings,
                events=events,
            )

        # 验证返回值是 dataclass
        assert isinstance(result, QuarterlyReportContent)
        # 1) markdown 字段
        assert result.markdown.startswith("# 测试公司 2024 Q1 跟踪报告")
        # 2) extraction 字段
        assert len(result.extraction.key_findings) == 2
        assert result.extraction.data_consistency_flags[0]["financial_field"] == "revenue"
        assert result.extraction.severity_breakdown["info"] == 1
        # 3) self_check / adversarial 字段
        assert result.self_check == r2
        assert result.adversarial == r3
        # 4) safe_finding_keys 来自 r2.safe_facts (LLM 自检后通过的事实),
        # key 形如 'event_N' 或 'financial_field' (verified=True)
        assert "event_1" in result.safe_finding_keys
        assert "event_2" in result.safe_finding_keys
        # 关键发现本身仍被记录在 extraction 中
        assert any(
            f.get("event_id") == 1 for f in result.extraction.key_findings
        )
        # 5) raw_input 缓存
        assert result.raw_input["financial_input"]["revenue"] == 100_000_000
        assert len(result.raw_input["events"]) == 2
        # 6) LLM 被调了 4 轮
        assert len(client._call_log) == 4

    @pytest.mark.asyncio
    async def test_generator_handles_no_events(self):
        """空 events + 空 briefings → 仍返回 markdown (降级), 不抛错."""
        client = _mock_client(
            r1={"key_findings": [], "data_consistency_flags": [],
                "severity_breakdown": {}, "watch_list": []},
            r2={"safe_facts": [], "removed_facts": []},
            r3={"critiques": []},
            # generator 兜底逻辑: 若 r4 本身没返回 markdown, 会用 company_name 拼标题
            r4={},  # 触发 fallback 拼标题
        )

        with patch(
            "app.services.sentiment.quarterly.generator.LlmClientFactory.preferred",
            return_value=client,
        ):
            gen = QuarterlyReportGenerator()
            result = await gen.generate(
                company_name="空数据公司",
                project_id=2,
                fiscal_year=2024,
                period_type="Q1",
                period_end="2024-03-31",
                financial_input=_mock_financial_input(),
                briefings=[],
                events=[],
            )

        # 即便没有数据, 仍有 markdown 骨架 (兜底标题用 company_name 拼)
        assert result.markdown
        assert "空数据公司" in result.markdown
        assert "2024 第一季度 跟踪报告" in result.markdown
        # 0 findings
        assert result.extraction.key_findings == []
        assert result.extraction.watch_list == []

    @pytest.mark.asyncio
    async def test_generator_citation_extraction(self):
        """引用抽取: r1 给出引用了 event_id 的 finding, safe_facts 标记 verified → safe_finding_keys 包含对应 key."""
        r1 = {
            "key_findings": [
                {"event_id": 42, "financial_field": None,
                 "finding": "重大事项公告", "severity": "critical"},
            ],
            "data_consistency_flags": [],
            "severity_breakdown": {"info": 0, "notice": 0, "warn": 0, "critical": 1},
            "watch_list": [],
        }
        r2 = {
            "safe_facts": [
                {"event_id": 42, "verified": True, "issue": ""},
            ],
            "removed_facts": [],
        }
        r3 = {"critiques": []}
        r4 = {"markdown": "# X 2024 Q1\n含 [事件#42] 引用"}
        client = _mock_client(r1=r1, r2=r2, r3=r3, r4=r4)

        with patch(
            "app.services.sentiment.quarterly.generator.LlmClientFactory.preferred",
            return_value=client,
        ):
            gen = QuarterlyReportGenerator()
            result = await gen.generate(
                company_name="X",
                project_id=1,
                fiscal_year=2024,
                period_type="Q1",
                period_end="2024-03-31",
                financial_input=_mock_financial_input(),
                briefings=[],
                events=[{"id": 42, "title": "重大事项", "content_text": "...",
                         "severity": "critical", "publish_date": "2024-03-01"}],
            )

        # safe_finding_keys 应该包含 event_42 (因为 verified=True 且 financial_field 为空)
        assert "event_42" in result.safe_finding_keys
        # extraction 关键发现
        assert result.extraction.severity_breakdown["critical"] == 1

    @pytest.mark.asyncio
    async def test_generator_disabled_fallback(self):
        """AI 不可用 (LlmClientFactory 抛 NoLlmConfigured) → QuarterlyReportGenerator
        构造时正确向上冒泡异常 (由调用方 wrap 降级).

        QuarterlyReportGenerator.__init__ 在 __init__ 阶段就调
        LlmClientFactory.preferred(), 拿不到 LLM 时抛 NoLlmConfigured.
        真实业务层 (callers) 通常会 try/except 兜底, 本测试验证信号正确.
        """
        from app.models.db_models import NoLlmConfigured

        def _raise():
            raise NoLlmConfigured("no LLM")

        with patch(
            "app.services.sentiment.quarterly.generator.LlmClientFactory.preferred",
            side_effect=_raise,
        ):
            # 构造器立即抛 — 表明无 LLM 时信号被正确传播
            with pytest.raises(NoLlmConfigured):
                QuarterlyReportGenerator()

    @pytest.mark.asyncio
    async def test_generator_chat_json_error_propagates(self):
        """chat_json 抛错时, generate() 不静默吞掉, 由调用方降级."""
        client = MagicMock()

        async def _raise(*args, **kwargs):
            raise RuntimeError("LLM down")

        client.chat_json.side_effect = _raise

        with patch(
            "app.services.sentiment.quarterly.generator.LlmClientFactory.preferred",
            return_value=client,
        ):
            gen = QuarterlyReportGenerator()
            with pytest.raises(RuntimeError, match="LLM down"):
                await gen.generate(
                    company_name="X",
                    project_id=1,
                    fiscal_year=2024,
                    period_type="Q1",
                    period_end="2024-03-31",
                    financial_input=_mock_financial_input(),
                    briefings=[],
                    events=[],
                )
