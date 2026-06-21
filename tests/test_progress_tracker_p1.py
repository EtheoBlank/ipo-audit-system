"""Round 28 P1-12: collect_project_summary 沿用 round 12 模式, 项目级也 SQL 聚合.

bug: collect_project_summary 仍是 Python 全扫 (select WorkPlanItem + len() 累加),
大项目 (1万 items) 慢. 修复: GROUP BY status 一次拿 count, dict lookup O(1).

测试策略: 用 AsyncMock mock db session, 验证走了 SQL 聚合 (execute call count 较少,
而不是拉全表). 模拟 row = (status, count, est_sum, act_sum).
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.team_management.progress_tracker import ProgressTracker


def _make_mock_db(rows_status: list, rows_module: list | None = None):
    """构造一个 AsyncMock db, 两次 execute 分别返 status / module 行."""
    db = AsyncMock()

    # 用 side_effect 区分多次调用
    call_count = {"n": 0}

    def make_result(rows):
        result = MagicMock()
        result.all.return_value = rows
        return result

    async def fake_execute(stmt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return make_result(rows_status)
        return make_result(rows_module or [])

    db.execute.side_effect = fake_execute
    return db


class TestCollectProjectSummary:
    @pytest.mark.asyncio
    async def test_collect_project_summary_groups_by_project(self):
        """2 个 status 值 → by_status dict 2 个 key, 不是拉全表."""
        # 模拟 SQL GROUP BY status 的结果
        rows_status = [
            ("done", 5, 20.0, 18.0),
            ("in_progress", 3, 12.0, 6.0),
        ]
        rows_module = [
            ("底稿", 6),
            ("函证", 2),
        ]
        db = _make_mock_db(rows_status, rows_module)

        result = await ProgressTracker.collect_project_summary(db, project_id=1)

        assert result["total_items"] == 8
        assert result["completed_items"] == 5
        assert result["in_progress_items"] == 3
        assert result["blocked_items"] == 0  # 无 blocked
        assert result["completion_rate"] == 0.625
        assert result["total_estimated_hours"] == 32.0
        assert result["total_actual_hours"] == 24.0
        assert result["by_status"] == {"done": 5, "in_progress": 3}
        assert result["by_module"] == {"底稿": 6, "函证": 2}

    @pytest.mark.asyncio
    async def test_collect_project_summary_uses_sql(self):
        """验证走 SQL 聚合, 不拉全表 — execute 调用次数极少 (2 次)."""
        rows_status = [
            ("done", 10, 50.0, 45.0),
        ]
        rows_module = []
        db = _make_mock_db(rows_status, rows_module)

        await ProgressTracker.collect_project_summary(db, project_id=1)
        # 内部应该只调用 2 次 execute (status 聚合 + module 聚合), 不是 N 次
        assert db.execute.call_count == 2  # noqa: PGH005 — MagicMock 计数

    @pytest.mark.asyncio
    async def test_collect_project_summary_empty_project(self):
        """空项目: 没 WorkPlanItem → 全 0 + 空 dict."""
        db = _make_mock_db([], [])
        result = await ProgressTracker.collect_project_summary(db, project_id=999)
        assert result["total_items"] == 0
        assert result["completed_items"] == 0
        assert result["completion_rate"] == 0.0
        assert result["by_status"] == {}
        assert result["by_module"] == {}

    @pytest.mark.asyncio
    async def test_collect_project_summary_all_status(self):
        """覆盖所有 status 枚举: done / in_progress / blocked / pending / cancelled."""
        rows_status = [
            ("done", 5, 20.0, 18.0),
            ("in_progress", 3, 12.0, 6.0),
            ("blocked", 1, 4.0, 2.0),
            ("pending", 2, 8.0, 0.0),
            ("cancelled", 1, 0.0, 0.0),
        ]
        # 模拟 module 聚合 (已经 WHERE status != cancelled 过滤完) — 11 条非 cancelled 任务
        rows_module = [("其他", 11)]
        db = _make_mock_db(rows_status, rows_module)

        result = await ProgressTracker.collect_project_summary(db, project_id=1)
        assert result["total_items"] == 12
        assert result["completed_items"] == 5
        assert result["in_progress_items"] == 3
        assert result["blocked_items"] == 1
        assert result["by_status"]["cancelled"] == 1
        assert result["by_status"]["pending"] == 2
        assert result["by_module"] == {"其他": 11}

    @pytest.mark.asyncio
    async def test_collect_project_summary_null_module(self):
        """related_module 为 NULL 时归到 '其他'."""
        rows_status = [("done", 1, 1.0, 1.0)]
        rows_module = [(None, 1)]  # 模拟 NULL
        db = _make_mock_db(rows_status, rows_module)

        result = await ProgressTracker.collect_project_summary(db, project_id=1)
        # None → "其他"
        assert result["by_module"] == {"其他": 1}
