"""Round 28 P1-9: _coerce_date 解析失败不能再静默.

bug: 解析失败返 None, 下游 FIFO 当 period_end 兜底, 库龄 0 天, 跌价低估.
修复: 收集 date_parse_failed_rows + InventoryImportResponse 透传 + 前端提示.
"""
from __future__ import annotations

from io import BytesIO
from datetime import datetime

import pandas as pd
import pytest

from app.services.inventory.importer import (
    InventoryImporter,
    _coerce_date,
    get_date_parse_failures,
    reset_date_parse_failures,
)
from app.models.inventory import InventoryImportResponse


class TestCoerceDate:
    def test_coerce_date_returns_none_for_invalid(self):
        """传 "invalid" → None (而非抛错)."""
        result = _coerce_date("invalid")
        assert result is None

    def test_coerce_date_returns_none_for_nonsense(self):
        """更杂的 invalid 串也返 None."""
        for v in ["abc", "2024-13-99", "not-a-date", "??", "@@@"]:
            result = _coerce_date(v)
            assert result is None

    def test_coerce_date_valid_iso(self):
        """标准 ISO 日期正常解析."""
        result = _coerce_date("2024-06-15")
        assert result is not None
        assert pd.notna(result)

    def test_coerce_date_none_passthrough(self):
        """None / 空串 → None."""
        assert _coerce_date(None) is None
        assert _coerce_date("") is None
        assert _coerce_date("   ") is None


class TestDateParseFailures:
    """Import 流程中失败行被收集, API 端能透传."""

    def _build_excel(self, rows):
        df = pd.DataFrame(rows)
        buf = BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue()

    def test_failed_dates_collected(self):
        """Excel 含非法日期, parse_bytes 后 _DATE_FAIL_HOLDER 收集失败行."""
        rows = {
            "物料编码": ["M001", "M002", "M003", "M004"],
            "物料名称": ["A", "B", "C", "D"],
            "期末数量": [10, 20, 30, 40],
            "期末金额": [100.0, 200.0, 300.0, 400.0],
            "入库日期": ["2024-01-15", "garbage", "2024-06-01", "???"],
        }
        content = self._build_excel(rows)
        # parse_bytes 内部会 reset
        df = InventoryImporter.parse_bytes(content, "test.xlsx")
        failures = get_date_parse_failures()
        # 应该有 2 个失败行: "garbage" 和 "???"
        assert len(failures) == 2
        # 检查 row_idx 和 raw_value
        raw_values = [raw for _idx, raw in failures]
        assert "garbage" in raw_values
        assert "???" in raw_values

    def test_all_valid_no_failures(self):
        """全部有效日期 → _DATE_FAIL_HOLDER 空."""
        rows = {
            "物料编码": ["M001", "M002"],
            "物料名称": ["A", "B"],
            "期末数量": [10, 20],
            "期末金额": [100.0, 200.0],
            "入库日期": ["2024-01-15", "2024-06-01"],
        }
        content = self._build_excel(rows)
        InventoryImporter.parse_bytes(content, "test.xlsx")
        failures = get_date_parse_failures()
        assert failures == []


class TestImportResponseSchema:
    def test_response_has_date_parse_failed_count(self):
        """InventoryImportResponse 含 date_parse_failed_count 字段."""
        resp = InventoryImportResponse(
            project_id=1,
            period_end="2024-12-31",
            is_prior_year=False,
            imported_count=10,
            total_ending_amount=12345.67,
            date_parse_failed_count=2,
            date_parse_failed_rows=[[1, "garbage"], [3, "???"]],
        )
        assert resp.date_parse_failed_count == 2
        assert len(resp.date_parse_failed_rows) == 2

    def test_response_default_zero(self):
        """默认值: date_parse_failed_count=0, rows=[]."""
        resp = InventoryImportResponse(
            project_id=1,
            period_end="2024-12-31",
            is_prior_year=False,
            imported_count=5,
            total_ending_amount=100.0,
        )
        assert resp.date_parse_failed_count == 0
        assert resp.date_parse_failed_rows == []

    def test_reset_holders(self):
        """reset_date_parse_failures 显式清空."""
        reset_date_parse_failures()
        assert get_date_parse_failures() == []
