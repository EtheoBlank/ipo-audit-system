"""舆情季报测试 — P0 测试空白 (2026-06-19).

app/services/sentiment/quarterly/ 6 个文件 (aggregator / financial_input /
generator / trigger / verifier / word_exporter) 此前 0 测试覆盖.

这里测纯函数 / 数据类:
- QuarterlyPeriodSpec.for_type: 4 个 period_type 边界
- QuarterlyPeriodSpec.title: 中文标签拼接
- aggregate_window / lock_references 走 DB 测试留给集成测试
"""
from __future__ import annotations

import json

import pytest

from app.services.sentiment.quarterly.trigger import QuarterlyPeriodSpec


# ============================================================
# QuarterlyPeriodSpec.for_type — 4 个 period_type
# ============================================================


class TestQuarterlyPeriodSpec:
    """P0 — 季报期次规格计算, 错一个日期窗口会影响所有引用的事件/简报."""

    @pytest.mark.parametrize(
        "period_type,fiscal_year,expected_pe,expected_ws,expected_we",
        [
            ("Q1", 2024, "2024-03-31", "2024-01-01", "2024-03-31"),
            ("H1", 2024, "2024-06-30", "2024-01-01", "2024-06-30"),
            ("Q3", 2024, "2024-09-30", "2024-01-01", "2024-09-30"),
            ("ANNUAL", 2024, "2024-12-31", "2024-01-01", "2024-12-31"),
            # 不同年份也正确
            ("Q1", 2023, "2023-03-31", "2023-01-01", "2023-03-31"),
            ("ANNUAL", 2025, "2025-12-31", "2025-01-01", "2025-12-31"),
        ],
    )
    def test_period_end_and_window(self, period_type, fiscal_year, expected_pe, expected_ws, expected_we):
        spec = QuarterlyPeriodSpec.for_type(period_type, fiscal_year)
        assert spec.period_end == expected_pe
        assert spec.window_start == expected_ws
        assert spec.window_end == expected_we
        assert spec.fiscal_year == fiscal_year

    def test_unknown_period_type_raises(self):
        # 边界: 不认识的 period_type 必须抛错而非静默
        with pytest.raises(ValueError) as ei:
            QuarterlyPeriodSpec.for_type("Q5", 2024)
        assert "Q5" in str(ei.value)

    def test_lowercase_period_type_rejected(self):
        # 大小写敏感 — "q1" 应被拒, 防止大小写不一致导致查询不到
        with pytest.raises(ValueError):
            QuarterlyPeriodSpec.for_type("q1", 2024)

    def test_title_uses_chinese_labels(self):
        # P0: title 给前端展示用, 错了用户看不懂
        spec = QuarterlyPeriodSpec.for_type("Q1", 2024)
        assert spec.title == "2024 第一季度 跟踪报告"
        spec2 = QuarterlyPeriodSpec.for_type("H1", 2024)
        assert spec2.title == "2024 半年度 跟踪报告"
        spec3 = QuarterlyPeriodSpec.for_type("Q3", 2024)
        assert spec3.title == "2024 第三季度 跟踪报告"
        spec4 = QuarterlyPeriodSpec.for_type("ANNUAL", 2024)
        assert spec4.title == "2024 年度 跟踪报告"

    def test_window_inclusive(self):
        # Q1 简报窗口应包含 1-1 到 3-31 全部 90 天
        spec = QuarterlyPeriodSpec.for_type("Q1", 2024)
        from datetime import date

        d_start = date.fromisoformat(spec.window_start)
        d_end = date.fromisoformat(spec.window_end)
        # 1月1 + 90 天 ≈ 4月1, 3月31 在范围内
        assert (d_end - d_start).days == 90


# ============================================================
# lock_references — JSON 序列化 (mock 简化)
# ============================================================


class TestLockReferencesJsonContract:
    """P0 — lock_references 把 briefing/event id 写回 report 字段.

    用 mock ORM 对象测 JSON 序列化的契约, 不依赖 DB.
    """

    def test_lock_writes_ids_to_json_field(self):
        # 模拟 lock_references 行为: briefings/events → JSON
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.services.sentiment.quarterly.aggregator import lock_references

        # 模拟 report + briefings + events
        report = SimpleNamespace(
            referenced_briefing_ids_json=None,
            referenced_event_ids_json=None,
        )
        briefings = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        events = [SimpleNamespace(id=10), SimpleNamespace(id=20)]
        db = AsyncMock()

        import asyncio

        asyncio.run(lock_references(db, report, briefings, events))

        # 验证 JSON 字段被正确写入
        br_ids = json.loads(report.referenced_briefing_ids_json)
        ev_ids = json.loads(report.referenced_event_ids_json)
        assert br_ids == [1, 2]
        assert ev_ids == [10, 20]
        db.commit.assert_awaited_once()

    def test_lock_empty_lists(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.services.sentiment.quarterly.aggregator import lock_references
        import asyncio

        report = SimpleNamespace(
            referenced_briefing_ids_json=None,
            referenced_event_ids_json=None,
        )
        db = AsyncMock()
        asyncio.run(lock_references(db, report, [], []))

        # 空列表也应序列化, 不能为 None (下游反序列化失败)
        assert report.referenced_briefing_ids_json == "[]"
        assert report.referenced_event_ids_json == "[]"