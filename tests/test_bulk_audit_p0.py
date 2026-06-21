"""round 28 P0-6: bulk_audit partial commit 测试.

覆盖:
  - 5 行全成功 → success_count=5
  - 3 成功 + 2 失败 → success=3, errors=2
  - 失败后, 成功的 3 行仍落库
  - 失败信息含行号 + error message
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Setup path for in-memory DB tests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".venv/Lib/site-packages"))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.models.account_audit import MovementAuditBulkItem


def _make_item(idx: int, account_code: str = "1601", voucher_no: str = "JZ-1") -> MovementAuditBulkItem:
    return MovementAuditBulkItem(
        account_code=account_code,
        voucher_no=voucher_no,
        voucher_line_no=1,
        direction="debit",
        audited_amount=100.0 + idx,
    )


def _make_db_with_rows(num_rows: int):
    """构造 mock db + 拉本期所有审定行."""
    db = MagicMock()
    rows = []
    for i in range(num_rows):
        r = MagicMock()
        r.account_code = "1601"
        r.voucher_no = f"JZ-{i + 1}"
        r.voucher_line_no = 1
        r.direction = "debit"
        r.book_amount = 100.0
        r.audited_amount = 100.0
        r.audited_by_user_id = None
        r.audited_by_display = None
        r.audited_at = None
        rows.append(r)

    # execute → 返所有 rows
    async def execute(stmt, *args, **kwargs):
        r = MagicMock()
        r.scalars.return_value.all.return_value = rows
        return r

    db.execute = execute

    # 默认 commit / rollback 都成功
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db, rows


# ============================================================
# P0-6 测试
# ============================================================


class TestBulkAuditPartialCommit:
    """验证 partial commit: 失败行不影响成功行."""

    @pytest.mark.asyncio
    async def test_bulk_audit_all_success(self):
        """5 行全成功 → matched=5, updated=5, errors=[]."""
        from app.services.account_audit import AccountAuditService

        db, rows = _make_db_with_rows(5)
        items = [_make_item(i, voucher_no=f"JZ-{i + 1}") for i in range(5)]

        result = await AccountAuditService.bulk_audit(
            db,
            project_id=1,
            period_end="2024-12-31",
            items=items,
            user_id=1,
            user_display="测试员",
        )

        assert result["matched"] == 5
        assert result["updated"] == 5
        assert result["not_found"] == 0
        assert result["errors"] == []
        # commit 调用 5 次 (一行一次)
        assert db.commit.await_count == 5

    @pytest.mark.asyncio
    async def test_bulk_audit_partial_failure(self):
        """3 成功 + 2 失败 → updated=3, errors=2."""
        from app.services.account_audit import AccountAuditService

        db, rows = _make_db_with_rows(3)  # 只有 3 行存在
        # 5 个 item, 第 4/5 个 not_found
        items = [_make_item(i, voucher_no=f"JZ-{i + 1}") for i in range(5)]

        result = await AccountAuditService.bulk_audit(
            db,
            project_id=1,
            period_end="2024-12-31",
            items=items,
            user_id=1,
            user_display="测试员",
        )

        # 3 匹配, 2 not_found (不是 errors)
        assert result["matched"] == 3
        assert result["updated"] == 3
        assert result["not_found"] == 2
        assert result["errors"] == []
        # commit 3 次 (每个成功行一次)
        assert db.commit.await_count == 3

    @pytest.mark.asyncio
    async def test_bulk_audit_successful_rows_persist(self):
        """失败后, 成功的 3 行仍落库可查 — 即使失败行 rollback 不影响成功行."""
        from app.services.account_audit import AccountAuditService

        db, rows = _make_db_with_rows(5)
        # 第 2 行 commit 时故意抛 (模拟 partial 失败)
        call_count = {"i": 0}

        async def flaky_commit(*args, **kwargs):
            call_count["i"] += 1
            if call_count["i"] == 2:
                raise RuntimeError("simulated DB error on row 2")

        db.commit = flaky_commit

        items = [_make_item(i, voucher_no=f"JZ-{i + 1}") for i in range(5)]

        result = await AccountAuditService.bulk_audit(
            db,
            project_id=1,
            period_end="2024-12-31",
            items=items,
            user_id=1,
            user_display="测试员",
        )

        # 4 行 matched, 第 2 行 commit 失败 → updated=4, errors 含第 2 行
        assert result["matched"] == 5
        assert result["updated"] == 4
        assert len(result["errors"]) == 1
        assert "行 1" in result["errors"][0]  # idx=1 = 第 2 行 (0-indexed)
        # rollback 调用 1 次 (失败行)
        assert db.rollback.await_count == 1

    @pytest.mark.asyncio
    async def test_bulk_audit_error_contains_row_index(self):
        """失败信息含行号 + error message."""
        from app.services.account_audit import AccountAuditService

        db, rows = _make_db_with_rows(5)
        # 第 3 行 commit 时抛
        call_count = {"i": 0}

        async def flaky_commit(*args, **kwargs):
            call_count["i"] += 1
            if call_count["i"] == 3:
                raise ValueError("invalid amount")

        db.commit = flaky_commit

        items = [_make_item(i, voucher_no=f"JZ-{i + 1}") for i in range(5)]

        result = await AccountAuditService.bulk_audit(
            db,
            project_id=1,
            period_end="2024-12-31",
            items=items,
            user_id=1,
            user_display="测试员",
        )

        assert len(result["errors"]) == 1
        # 错误信息: "行 {idx}/{account_code}/{voucher_no}: {exc}"
        err = result["errors"][0]
        assert "行 2" in err  # idx=2
        assert "1601" in err
        assert "JZ-3" in err
        assert "invalid amount" in err
