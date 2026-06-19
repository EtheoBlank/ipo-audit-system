"""P0-B fix regression tests — completion_cost_unit 简化模型.

Round 27 (2026-06-19). 验证修复:
  1. manual_completion_cost 覆盖 rate 简化模型, 与 NRV 解耦
  2. 缺失物料仍走 rate 兜底
  3. API 透传 manual_completion_cost 不报错
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.inventory.aging_engine import InventoryAgingEngine


def _mov(code: str, name: str, category: str, *, nrv: float = 0.0,
         book_unit: float = 100.0, qty: float = 10.0) -> dict:
    """构造一个 NRV-完工口径物料, 期初+入库 = 期末"""
    return {
        "material_code": code,
        "material_name": name,
        "category": category,
        "opening_qty": qty,
        "opening_amount": qty * book_unit,
        "inbound_qty": 0,
        "inbound_amount": 0,
        "outbound_qty": 0,
        "outbound_amount": 0,
        "ending_qty": qty,
        "ending_amount": qty * book_unit,
        "inbound_date": datetime(2024, 6, 1),
    }


class TestCompletionCostManualOverride:
    """P0-B: manual_completion_cost 覆盖 rate 简化模型"""

    def test_manual_completion_cost_overrides_rate(self):
        """传 manual={X: 250} → completion_cost_unit=250 (与 NRV 无关)"""
        pe = datetime(2024, 12, 31)
        movs = [_mov("X", "钢材-X", "原材料")]
        # NRV=500, rate=0.6 → 旧模型得 300; 传 manual=250 → 应得 250
        engine = InventoryAgingEngine(
            industry="默认",
            sell_cost_rate=0.0,
            completion_cost_rate=0.6,
            manual_completion_cost={"X": 250.0},
        )
        sales = [{"product_code": "X", "unit_price": 500.0, "qty": 100}]
        result = engine.compute(movs, pe, sales_records=sales, manual_nrv={"X": 500.0})
        # 找 X 行
        rows = [r for r in result.rows if r.material_code == "X"]
        assert len(rows) == 1
        # nrv_net_unit = 500 - 0 - 250 = 250
        # book=100, nrv_net=250 → 0 跌价, 但 nrv_amount = 250 * 10 = 2500
        assert rows[0].nrv_amount == pytest.approx(2500.0, rel=1e-6)
        assert rows[0].estimated_sell_cost == pytest.approx(2500.0, rel=1e-6)
        assert rows[0].impairment_current == pytest.approx(0.0)
        assert rows[0].method == "nrv-完工口径(手工)"

    def test_falls_back_to_rate_when_no_manual(self):
        """没传 manual → 仍走 nrv_unit * rate (rate 兜底)"""
        pe = datetime(2024, 12, 31)
        movs = [_mov("X", "钢材-X", "原材料")]
        engine = InventoryAgingEngine(
            industry="默认",
            sell_cost_rate=0.0,
            completion_cost_rate=0.6,
        )
        sales = [{"product_code": "X", "unit_price": 500.0, "qty": 100}]
        result = engine.compute(movs, pe, sales_records=sales, manual_nrv={"X": 500.0})
        rows = [r for r in result.rows if r.material_code == "X"]
        assert len(rows) == 1
        # nrv_net = 500 - 0 - (500*0.6=300) = 200
        # nrv_amount = 200 * 10 = 2000
        assert rows[0].nrv_amount == pytest.approx(2000.0, rel=1e-6)
        assert rows[0].estimated_sell_cost == pytest.approx(3000.0, rel=1e-6)
        assert rows[0].method == "nrv-完工口径"

    def test_manual_partial_fallback(self):
        """传 manual={A: 250}, B 不在 → A 走 manual, B 走 rate"""
        pe = datetime(2024, 12, 31)
        movs = [
            _mov("A", "钢材-A", "原材料"),
            _mov("B", "钢材-B", "原材料"),
        ]
        engine = InventoryAgingEngine(
            industry="默认",
            sell_cost_rate=0.0,
            completion_cost_rate=0.5,
            manual_completion_cost={"A": 250.0},
        )
        sales = [
            {"product_code": "A", "unit_price": 500.0, "qty": 100},
            {"product_code": "B", "unit_price": 500.0, "qty": 100},
        ]
        result = engine.compute(
            movs, pe, sales_records=sales,
            manual_nrv={"A": 500.0, "B": 500.0},
        )
        rows_by_code = {r.material_code: r for r in result.rows}
        # A: manual=250, nrv_net = 500 - 0 - 250 = 250, nrv_amount = 2500
        a = rows_by_code["A"]
        assert a.nrv_amount == pytest.approx(2500.0, rel=1e-6)
        assert a.method == "nrv-完工口径(手工)"
        # B: rate=0.5, completion = 500*0.5 = 250, nrv_net = 500 - 0 - 250 = 250
        b = rows_by_code["B"]
        assert b.nrv_amount == pytest.approx(2500.0, rel=1e-6)
        assert b.method == "nrv-完工口径"

    def test_manual_via_compute_param(self):
        """manual_completion_cost 通过 compute() 传入 (而非 __init__) 也生效"""
        pe = datetime(2024, 12, 31)
        movs = [_mov("X", "钢材-X", "原材料")]
        engine = InventoryAgingEngine(
            industry="默认",
            sell_cost_rate=0.0,
            completion_cost_rate=0.0,  # __init__ 不传 manual
        )
        sales = [{"product_code": "X", "unit_price": 500.0, "qty": 100}]
        result = engine.compute(
            movs, pe, sales_records=sales,
            manual_nrv={"X": 500.0},
            manual_completion_cost={"X": 100.0},
        )
        rows = [r for r in result.rows if r.material_code == "X"]
        assert len(rows) == 1
        # completion=100, nrv_net = 500-0-100 = 400, nrv_amount = 400*10 = 4000
        assert rows[0].nrv_amount == pytest.approx(4000.0, rel=1e-6)
        assert rows[0].method == "nrv-完工口径(手工)"

    def test_non_completion_category_skips_manual(self):
        """非完工口径 (category='产成品') 不应受 manual_completion_cost 影响"""
        pe = datetime(2024, 12, 31)
        movs = [_mov("X", "成品-X", "产成品")]
        engine = InventoryAgingEngine(
            industry="默认",
            sell_cost_rate=0.0,
            completion_cost_rate=0.5,
            manual_completion_cost={"X": 250.0},  # 完工口径才生效
        )
        sales = [{"product_code": "X", "unit_price": 500.0, "qty": 100}]
        result = engine.compute(movs, pe, sales_records=sales, manual_nrv={"X": 500.0})
        rows = [r for r in result.rows if r.material_code == "X"]
        # 产成品 → 出售口径, completion_cost_unit=0
        assert rows[0].method == "nrv-出售口径"
        # nrv_net = 500, nrv_amount = 5000
        assert rows[0].nrv_amount == pytest.approx(5000.0, rel=1e-6)
        assert rows[0].estimated_sell_cost == pytest.approx(0.0, rel=1e-6)
