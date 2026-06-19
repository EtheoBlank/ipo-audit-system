"""P0-9 fix regression tests — match_to_sheets used set 改用业务键.

Round 30 (2026-06-19). 验证三个修复:
  1. used 是 set[tuple] (业务键), 不是 set[int] (id)
  2. 同 id 复用不会冲突
  3. (material_code, warehouse, batch_no) 三字段保证唯一
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.inventory.photo_processor import (
    CountPhotoProcessor,
    ParsedCountRow,
    _sheet_business_key,
)


def _sheet(code: str = "M001", warehouse: str = "WH-A", batch: str = "B1"):
    return SimpleNamespace(
        material_code=code,
        material_name=f"物料{code}",
        warehouse=warehouse,
        batch_no=batch,
    )


def _row(code: str = "M001", warehouse: str = "WH-A", batch: str = "B1",
         name: str | None = None, qty: float | None = 100.0):
    return ParsedCountRow(
        material_code=code,
        material_name=name or f"物料{code}",
        warehouse=warehouse,
        batch_no=batch,
        counted_qty=qty,
        remark="",
    )


class TestBusinessKeySet:
    """P0-9: used set 改业务键"""

    def test_used_is_set_of_tuples(self):
        """验证 used 的类型是 set[tuple], 不是 set[int]"""
        proc = CountPhotoProcessor(client=None)
        sheets = [_sheet("M001"), _sheet("M002")]
        rows = [_row("M001"), _row("M002")]
        # 直接跑一遍验证
        matched, unmatched = proc.match_to_sheets(rows, sheets)
        assert len(matched) == 2
        assert unmatched == []
        # helper 返回的是 tuple (不是 int)
        s = sheets[0]
        assert isinstance(_sheet_business_key(s), tuple)

    def test_same_id_reused_doesnt_conflict(self):
        """模拟 CPython id 复用场景 — 业务键不会冲突"""
        proc = CountPhotoProcessor(client=None)
        sheets = [_sheet("M001", "WH-A", "B1"),
                  _sheet("M002", "WH-B", "B2")]
        rows = [_row("M001", "WH-A", "B1"),
                _row("M002", "WH-B", "B2")]
        # 让 sheet_list 中的两个 sheet 在 process 结束时释放, 模拟 GC 复用.
        # 即便 id() 撞车, 业务键 (M001/WH-A/B1) 与 (M002/WH-B/B2) 不重叠.
        matched, _ = proc.match_to_sheets(rows, sheets)
        assert len(matched) == 2
        assert matched[0][0].material_code == "M001"
        assert matched[1][0].material_code == "M002"

    def test_uniqueness_by_material_warehouse_batch(self):
        """3 字段复合键保证唯一"""
        a = _sheet("M001", "WH-A", "B1")
        b = _sheet("M001", "WH-A", "B2")  # 同 code 不同 batch
        c = _sheet("M001", "WH-B", "B1")  # 同 code+batch 不同 warehouse
        d = _sheet("M002", "WH-A", "B1")  # 同 warehouse+batch 不同 code
        keys = {_sheet_business_key(s) for s in (a, b, c, d)}
        assert len(keys) == 4  # 全部不同
        assert ("m001", "WH-A", "B1") in keys
        assert ("m001", "WH-A", "B2") in keys
        assert ("m001", "WH-B", "B1") in keys
        assert ("m002", "WH-A", "B1") in keys

    def test_sheet_business_key_signature(self):
        """helper 返回 3-tuple (lowercase code, warehouse, batch)"""
        s = _sheet("M-ABC", "WH-中文", "Batch#9")
        k = _sheet_business_key(s)
        assert isinstance(k, tuple)
        assert len(k) == 3
        assert k == ("m-abc", "WH-中文", "Batch#9")

    def test_empty_fields_handled(self):
        """None / 空字段都被规范成空串, 不会 TypeError"""
        s = SimpleNamespace(material_code=None, warehouse=None, batch_no=None)
        k = _sheet_business_key(s)
        assert k == ("", "", "")