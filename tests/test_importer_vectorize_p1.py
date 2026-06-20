"""Round 31 P1-1: importer 行级 _coerce_num 改向量化.

覆盖:
  - test_coerce_num_vectorized_preserves_valid: 有效值原样, 无效值变 0
  - test_coerce_num_vectorized_handles_empty_series: 空 series 不抛
"""
from __future__ import annotations

from io import BytesIO

import pandas as pd
import pytest

from app.services.inventory.importer import (
    InventoryImporter,
    InventoryImportError,
)


def _build_excel(rows: dict) -> bytes:
    df = pd.DataFrame(rows)
    buf = BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


class TestCoerceNumVectorized:
    """验证 pd.to_numeric(..., errors='coerce') + fillna(0) 与 _coerce_num 等价."""

    def test_coerce_num_vectorized_preserves_valid(self):
        """混合有效 / 非法值 → 有效保留, 非法变 0."""
        rows = {
            "物料编码": ["M001", "M002", "M003", "M004"],
            "物料名称": ["A", "B", "C", "D"],
            "期末数量": [1, 0, "1.5", "abc"],        # 1 / 0 / 1.5 / "abc"→0
            "期末金额": [100.0, 200.0, 300.0, 400.0],
        }
        content = _build_excel(rows)
        df = InventoryImporter.parse_bytes(content, "test.xlsx")

        # 期末数量列: 1, 0, 1.5, 0
        assert df["ending_qty"].tolist() == [1, 0, 1.5, 0], (
            f"实际: {df['ending_qty'].tolist()}"
        )

    def test_coerce_num_vectorized_handles_empty_series(self):
        """缺数字列 → 走默认 0.0 分支, 不抛."""
        rows = {
            "物料编码": ["M001"],
            "物料名称": ["A"],
            "期末数量": [10],
            "期末金额": [100.0],
            # 没有 unit_cost 列
        }
        content = _build_excel(rows)
        df = InventoryImporter.parse_bytes(content, "test.xlsx")
        # unit_cost 自动派生: 100/10 = 10
        assert df.loc[0, "unit_cost"] == pytest.approx(10.0)

    def test_coerce_num_handles_comma_and_yuan(self):
        """"1,000" / "¥500" 应被清洗为 1000 / 500 (round 31 兼容旧 _coerce_num 行为)."""
        rows = {
            "物料编码": ["M001", "M002"],
            "物料名称": ["A", "B"],
            "期末数量": ["1,000", "¥500"],
            "期末金额": [1000.0, 500.0],
        }
        content = _build_excel(rows)
        df = InventoryImporter.parse_bytes(content, "test.xlsx")
        assert df["ending_qty"].tolist() == [1000, 500], (
            f"逗号/¥ 清洗失败, 实际: {df['ending_qty'].tolist()}"
        )
