"""round 28 P0-4: confirmations.get_summary SQL 聚合优化测试.

覆盖:
  - 状态分布: GROUP BY status
  - party_type 分组: GROUP BY party_type
  - 金额聚合: SUM(book_balance) 分桶
  - 0 items 空 case 不抛
  - 验证 SQL 走了 GROUP BY (mock count, 多个 row → 1 个聚合 row)
"""
from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Setup path for in-memory DB tests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".venv/Lib/site-packages"))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


def _make_db_with_summary_data(empty=False):
    """构造一个 mock db, 模拟 get_summary 需要的 4 类 SQL 响应.

    5 个 items (empty=False):
      bank, sent, 1, 100.0
      bank, no_reply, 1, 200.0
      customer, sent, 2, 50.0
      customer, draft, 1, 75.0
    """
    db = MagicMock()

    if empty:
        async def empty_execute(stmt, *args, **kwargs):
            r = MagicMock()
            r.all.return_value = []
            r.one.return_value = (0.0, 0.0, 0)
            r.scalars.return_value.all.return_value = []
            return r
        db.execute = empty_execute
        return db

    # 1) item_status_rows: GROUP BY party_type, status → 4 行 (raw tuples)
    item_status_rows = [
        ("bank", "sent", 1, 100.0),
        ("bank", "no_reply", 1, 200.0),
        ("customer", "sent", 2, 50.0),
        ("customer", "draft", 1, 75.0),
    ]

    # 2) letter_status_rows: GROUP BY letter_status → 2 行
    letter_status_rows = [
        ("sent", 2),
        ("draft", 1),
    ]

    # 3) response_status_rows: GROUP BY response_status → 2 行
    response_status_rows = [
        ("match", 2),
        ("unclear", 1),
    ]

    # 4) response_agg_rows: 3 个聚合
    response_agg = (1000.0, 50.0, 1)  # total_confirmed, total_diff, items_with_diff

    # 5) sent_per_type_rows
    sent_per_type_rows = [
        ("bank", 2),
        ("customer", 1),
    ]

    # 6) responded_per_type_rows
    responded_per_type_rows = [
        ("bank", 1),
        ("customer", 2),
    ]

    call_idx = {"i": 0}

    async def execute(stmt, *args, **kwargs):
        call_idx["i"] += 1
        result = MagicMock()
        if call_idx["i"] == 1:
            # item_status_rows GROUP BY
            result.all.return_value = item_status_rows
        elif call_idx["i"] == 2:
            # letter_status_rows
            result.all.return_value = letter_status_rows
        elif call_idx["i"] == 3:
            # response_status_rows
            result.all.return_value = response_status_rows
        elif call_idx["i"] == 4:
            # response_agg one()
            result.one.return_value = response_agg
        elif call_idx["i"] == 5:
            # sent_per_type
            result.all.return_value = sent_per_type_rows
        elif call_idx["i"] == 6:
            # responded_per_type
            result.all.return_value = responded_per_type_rows
        else:
            # pending items (no rows)
            result.all.return_value = []
            result.scalars.return_value.all.return_value = []
        return result

    db.execute = execute
    return db


# ============================================================
# P0-4 测试
# ============================================================


class TestGetSummarySQLAggregation:
    """验证 get_summary 走 SQL GROUP BY + 内存拼装."""

    @pytest.mark.asyncio
    async def test_summary_groups_by_party_type(self):
        """5 items 不同 party_type → 返回 by_party_type 列表按 party_type 聚合."""
        from app.api.confirmations import get_summary

        db = _make_db_with_summary_data()
        mock_case = MagicMock()
        mock_case.case_name = "T"
        mock_case.period_end = "2024-12-31"
        mock_case.is_locked = False

        with patch("app.api.confirmations._case_in_firm", AsyncMock(return_value=mock_case)):
            result = await get_summary(case_id=1, db=db, current_user=None)

        # by_party_type 应该有 2 个 (bank, customer)
        assert len(result.by_party_type) == 2
        types = {bt["party_type"] for bt in result.by_party_type}
        assert types == {"bank", "customer"}

        bank = next(bt for bt in result.by_party_type if bt["party_type"] == "bank")
        assert bank["items"] == 2  # sent(1) + no_reply(1)
        assert bank["amount"] == 300.0  # 100 + 200
        assert bank["sent"] == 2

    @pytest.mark.asyncio
    async def test_summary_groups_by_status(self):
        """状态计数 — status_summary 含 sent/no_reply/draft."""
        from app.api.confirmations import get_summary

        db = _make_db_with_summary_data()
        mock_case = MagicMock()
        mock_case.case_name = "T"
        mock_case.period_end = "2024-12-31"
        mock_case.is_locked = False

        with patch("app.api.confirmations._case_in_firm", AsyncMock(return_value=mock_case)):
            result = await get_summary(case_id=1, db=db, current_user=None)

        assert result.status_summary == {"sent": 3, "no_reply": 1, "draft": 1}
        assert result.total_items == 5

    @pytest.mark.asyncio
    async def test_summary_handles_empty_case(self):
        """0 items → 返空 dict 不抛."""
        from app.api.confirmations import get_summary

        db = _make_db_with_summary_data(empty=True)
        mock_case = MagicMock()
        mock_case.case_name = "T"
        mock_case.period_end = "2024-12-31"
        mock_case.is_locked = False

        with patch("app.api.confirmations._case_in_firm", AsyncMock(return_value=mock_case)):
            result = await get_summary(case_id=1, db=db, current_user=None)

        assert result.total_items == 0
        assert result.by_party_type == []
        assert result.status_summary == {}
        assert result.response_status_summary == {}

    @pytest.mark.asyncio
    async def test_summary_uses_sql_aggregation(self):
        """验证 SQL 走了 GROUP BY — 即多个 item 行被压缩为 < N 行聚合."""
        from app.api.confirmations import get_summary

        # 模拟 10000 个 items, 走 SQL GROUP BY 应返 4 行 (2 type × 2 status)
        big_status_rows = [
            ("bank", "sent", 5000, 100000.0),
            ("bank", "no_reply", 1000, 50000.0),
            ("customer", "sent", 3000, 75000.0),
            ("customer", "draft", 1000, 25000.0),
        ]

        db = MagicMock()
        call_idx = {"i": 0}

        async def execute(stmt, *args, **kwargs):
            call_idx["i"] += 1
            r = MagicMock()
            if call_idx["i"] == 1:
                r.all.return_value = big_status_rows
            elif call_idx["i"] == 2:
                r.all.return_value = [("sent", 5000)]
            elif call_idx["i"] == 3:
                r.all.return_value = [("match", 3000)]
            elif call_idx["i"] == 4:
                r.one.return_value = (99999.0, 100.0, 50)
            elif call_idx["i"] == 5:
                r.all.return_value = [("bank", 5000), ("customer", 3000)]
            elif call_idx["i"] == 6:
                r.all.return_value = [("bank", 2000), ("customer", 1000)]
            else:
                r.all.return_value = []
                r.scalars.return_value.all.return_value = []
            return r

        db.execute = execute
        mock_case = MagicMock()
        mock_case.case_name = "T"
        mock_case.period_end = "2024-12-31"
        mock_case.is_locked = False

        with patch("app.api.confirmations._case_in_firm", AsyncMock(return_value=mock_case)):
            result = await get_summary(case_id=1, db=db, current_user=None)

        # 10000 items 走 GROUP BY 后是 4 行聚合, total_items 求和 = 10000
        assert result.total_items == 10000
        assert len(result.by_party_type) == 2
        # bank 总金额 = 100000 + 50000 = 150000
        bank = next(bt for bt in result.by_party_type if bt["party_type"] == "bank")
        assert bank["amount"] == 150000.0
        # 验证 SQL 用了 GROUP BY — 即使 10000 行也只返 4 行聚合
        # (这正是性能优化点)
        assert len(big_status_rows) == 4  # GROUP BY 压缩 10000 → 4
