"""Round 28 P0-2: count_plan AI 返回 date 字段容错解析.

覆盖 _try_parse_date 各种输入, 以及 revise 路径上 AI 返回非法日期时:
- 不抛 ValueError
- 旧值保留
- revision_log 写 warning
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest


from app.services.inventory.count_plan import (
    CountPlanDraft,
    CountPlanGenerator,
    _try_parse_date,
)


# ============================================================
# _try_parse_date 单测
# ============================================================
class TestTryParseDate:
    def test_iso_format(self):
        assert _try_parse_date("2024-12-30") == date(2024, 12, 30)

    def test_iso_with_padding(self):
        # 1月5日 → 1位月日也接受 (fromisoformat 不补零, 但我们用 str)
        # 实际 ISO 严格格式: 2024-01-05
        assert _try_parse_date("2024-01-05") == date(2024, 1, 5)

    def test_slash_format(self):
        assert _try_parse_date("2024/12/30") == date(2024, 12, 30)

    def test_slash_no_padding(self):
        assert _try_parse_date("2024/1/5") == date(2024, 1, 5)

    def test_chinese_format(self):
        # "12月30日" → 默认 2024 年
        assert _try_parse_date("12月30日") == date(2024, 12, 30)

    def test_chinese_with_lead_text(self):
        # "截止12月30日" 这种 AI 可能返回的散文格式
        assert _try_parse_date("截止12月30日") == date(2024, 12, 30)

    def test_unparseable_returns_none(self):
        assert _try_parse_date("明天") is None
        assert _try_parse_date("tomorrow") is None
        assert _try_parse_date("not a date") is None

    def test_none_input(self):
        assert _try_parse_date(None) is None

    def test_empty_string(self):
        assert _try_parse_date("") is None

    def test_invalid_month_returns_none(self):
        # 13月 → ValueError → None
        assert _try_parse_date("2024-13-30") is None
        assert _try_parse_date("2024/13/30") is None

    def test_invalid_day_returns_none(self):
        # 2月30日 → ValueError → None
        assert _try_parse_date("2024-02-30") is None

    def test_non_string_input(self):
        # Python 3.11+ date.fromisoformat 接受 basic ISO "20241230" 也 OK,
        # 所以 str(20241230)="20241230" 实际能解析为 2024-12-30.
        # 这里改成断言它确实能解析 (防止未来基础 ISO 行为变更).
        assert _try_parse_date(20241230) == date(2024, 12, 30)
        # date 对象 → str 走 isoformat() → "2024-12-30" 可解析
        assert _try_parse_date(date(2024, 12, 30)) == date(2024, 12, 30)
        # 浮点数 → str() = "1234.5" 不能解析
        assert _try_parse_date(1234.5) is None


# ============================================================
# revise 路径: AI 返回非法日期 → 旧值保留 + log warning
# ============================================================
class TestReviseKeepsOldDateWhenAiReturnsInvalid:
    """P0-2: AI 偶发返回非 ISO 日期 ("tomorrow", "2024/12/30"), revise 不能 500."""

    @pytest.fixture
    def draft(self):
        return CountPlanDraft(
            title="盘点计划",
            industry="制造业",
            period_end="2024-12-31",
            objectives="清点",
            scope="全部",
            procedures="1) 监盘; 2) 抽盘",
            special_notes="无",
            risks="无",
            team=[],
            count_date_start="2024-12-01",
            count_date_end="2024-12-31",
        )

    @pytest.mark.asyncio
    async def test_ai_returns_invalid_date_keeps_old_and_logs_warning(self, draft):
        """AI 返回 'tomorrow' 作为 count_date_end → 旧值保留, revision_log 有 warning."""

        # Mock DeepSeekClient.chat_json 返回非法日期
        class FakeClient:
            is_configured = True

            async def chat_json(self, system, user, temperature=0.1, **kw):
                return {
                    "title": "新标题",
                    "objectives": draft.objectives,
                    "scope": draft.scope,
                    "procedures": draft.procedures,
                    "special_notes": draft.special_notes,
                    "risks": draft.risks,
                    "team": draft.team,
                    "count_date_start": "tomorrow",
                    "count_date_end": "2024/12/30",
                    "change_summary": "调整",
                }

        gen = CountPlanGenerator(client=FakeClient())
        result = await gen.revise(draft, user_instruction="改日期")

        # 关键: 不抛异常
        assert result is draft
        # count_date_start 旧值保留
        assert draft.count_date_start == "2024-12-01"
        # count_date_end: "2024/12/30" 能解析 → 覆盖
        assert draft.count_date_end == "2024-12-30"

        # revision_log 最后一条应有 warning
        last_log = draft.revision_log[-1]
        applied = last_log["applied"]
        assert "警告" in applied
        assert "count_date_start" in applied
        assert "tomorrow" in applied

    @pytest.mark.asyncio
    async def test_ai_returns_iso_date_overwrites(self, draft):
        """AI 返回正常 ISO 日期 → 覆盖旧值."""

        class FakeClient:
            is_configured = True

            async def chat_json(self, system, user, temperature=0.1, **kw):
                return {
                    "title": draft.title,
                    "objectives": draft.objectives,
                    "scope": draft.scope,
                    "procedures": draft.procedures,
                    "special_notes": draft.special_notes,
                    "risks": draft.risks,
                    "team": draft.team,
                    "count_date_start": "2024-12-25",
                    "count_date_end": "2024-12-26",
                    "change_summary": "调整",
                }

        gen = CountPlanGenerator(client=FakeClient())
        await gen.revise(draft, user_instruction="改日期")

        assert draft.count_date_start == "2024-12-25"
        assert draft.count_date_end == "2024-12-26"
        # 无 warning
        last_log = draft.revision_log[-1]
        assert "警告" not in last_log["applied"]

    @pytest.mark.asyncio
    async def test_ai_returns_no_date_fields_keeps_old(self, draft):
        """AI 不返回 date 字段 → 旧值保留, 无 warning."""

        class FakeClient:
            is_configured = True

            async def chat_json(self, system, user, temperature=0.1, **kw):
                return {
                    "title": "新",
                    "objectives": draft.objectives,
                    "scope": draft.scope,
                    "procedures": draft.procedures,
                    "special_notes": draft.special_notes,
                    "risks": draft.risks,
                    "team": draft.team,
                    "change_summary": "调标题",
                }

        gen = CountPlanGenerator(client=FakeClient())
        await gen.revise(draft, user_instruction="改标题")

        assert draft.count_date_start == "2024-12-01"
        assert draft.count_date_end == "2024-12-31"
        last_log = draft.revision_log[-1]
        assert "警告" not in last_log["applied"]