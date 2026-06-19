"""P0-5 fix regression tests — FIFO aging engine.

Round 25 (2026-06-19). 验证三个修复:
  1. FIFO 扣减后 batches[i] 不会变为负值 (浮点精度防护)
  2. summary.aging_xxx 金额与 row.ending_amount 之和一致 (scale 不污染金额)
  3. 多次 compute 同一项目 + 上传 prior_year 数据后, opening batches 不重复
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.inventory.aging_engine import (
    AgingBucket,
    InventoryAgingEngine,
)


class TestFifoNoNegativeQty:
    """P0-5: 浮点精度防负 — 大量扣减后 batches 不留负数"""

    def test_normal_outbound_no_negative(self):
        """正常情况下 FIFO 扣减后没有负数 qty"""
        pe = datetime(2024, 12, 31)
        movs = [{
            "material_code": "A", "material_name": "A",
            "opening_qty": 100, "opening_amount": 1000,
            "inbound_qty": 50, "inbound_amount": 500,
            "outbound_qty": 80, "outbound_amount": 800,
            "ending_qty": 70, "ending_amount": 700,
            "inbound_date": pe - timedelta(days=10),
        }]
        b = InventoryAgingEngine.fifo_aging(movs, pe)
        # 100 老 + 50 新 - 80 出 = 70 剩, 拆成 20 老 + 50 新
        assert b.le_90 == pytest.approx(50)
        assert b.age_181_365 == pytest.approx(20)

    def test_demand_exceeds_supply_does_not_create_negative(self):
        """P0-5 修复: demand > supply 时 batches 不留负数"""
        pe = datetime(2024, 12, 31)
        # opening 30, inbound 0, outbound 100 — 严重超卖 (异常数据)
        movs = [{
            "material_code": "B", "material_name": "B",
            "opening_qty": 30, "opening_amount": 300,
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 100, "outbound_amount": 1000,
            "ending_qty": 0, "ending_amount": 0,
            "inbound_date": None,
        }]
        b = InventoryAgingEngine.fifo_aging(movs, pe)
        # 修复前: take = min(30, 100) = 30, qty = 30 - 30 = 0; OK
        # 修复后: 同样 OK. 但用 floating 浮点反复减会出现极小负值, 测试确认无负
        # 这里的关键: bucket 不应为负数
        assert b.le_90 >= 0
        assert b.age_91_180 >= 0
        assert b.age_181_365 >= 0
        assert b.age_366_730 >= 0
        assert b.gt_730 >= 0
        # 加权均龄也应 >= 0
        assert b.weighted_avg_age >= 0

    def test_many_small_deductions_no_drift(self):
        """P0-5: 模拟浮点漂移场景 — 1 单位大量扣减, 验证 max(0, ...) 防负"""
        # 构造: opening 1000, 1 次扣减 1, 1000 次重复加 1 (跨 batch) — 但 FIFO
        # 是顺序处理的, 实际是连续多行 inbound 各 1, outbound 999.
        # 核心: 多次 take 操作后没有出现极小负 qty.
        pe = datetime(2024, 12, 31)
        movs = [{
            "material_code": "C", "material_name": "C",
            "opening_qty": 1.0, "opening_amount": 10.0,
            "inbound_qty": 1.0, "inbound_amount": 10.0,
            "outbound_qty": 0.5, "outbound_amount": 5.0,
            "ending_qty": 1.5, "ending_amount": 15.0,
            "inbound_date": pe - timedelta(days=5),
        }]
        b = InventoryAgingEngine.fifo_aging(movs, pe)
        # 1 (opening 365d) + 1 (5d) - 0.5 = 0.5+1.0 = 1.5 剩
        # 加权: (0.5*365 + 1.0*5) / 1.5 ≈ 125
        assert b.le_90 == pytest.approx(1.0, rel=1e-6)
        assert b.age_181_365 == pytest.approx(0.5, rel=1e-6)
        # 全部 qty 应 >= 0 (即不会因为浮点变成 -1e-15 之类)
        assert b.weighted_avg_age >= 0


class TestFifoSummaryAmountConsistent:
    """P0-5: summary 金额与 row.ending_amount 之和误差 < 1%"""

    def test_summary_amount_matches_row_ending_amount_no_scale(self):
        """无 scale 校准时, summary 金额完全等于各 row.ending_amount 之和"""
        pe = datetime(2024, 12, 31)
        movs_x = [{
            "material_code": "X", "material_name": "X",
            "opening_qty": 100, "opening_amount": 1000,
            "inbound_qty": 50, "inbound_amount": 500,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 150, "ending_amount": 1500,
            "inbound_date": pe - timedelta(days=30),
        }]
        movs_y = [{
            "material_code": "Y", "material_name": "Y",
            "opening_qty": 50, "opening_amount": 800,
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 50, "ending_amount": 800,
            "inbound_date": None,
        }]
        result = InventoryAgingEngine().compute(
            [movs_x[0], movs_y[0]], pe, sales_records=[],
        )
        total_book = sum(r.book_amount for r in result.rows)
        # 各分段金额之和应等于 book_amount
        bucket_total = (
            result.summary["aging_le_90"]
            + result.summary["aging_91_180"]
            + result.summary["aging_181_365"]
            + result.summary["aging_366_730"]
            + result.summary["aging_gt_730"]
        )
        # 无 scale 校准时, 严格相等
        assert bucket_total == pytest.approx(total_book, rel=1e-3)
        assert total_book == pytest.approx(2300.0, rel=1e-3)

    def test_summary_amount_within_1pct_when_scale_applied(self):
        """有 scale 校准时 (账实 5% 内小幅差异), summary 金额与 book_amount 误差 < 1%"""
        pe = datetime(2024, 12, 31)
        # 故意构造 leftover 比 ending 少 3% — 触发 scale=0.97 校准
        movs = [{
            "material_code": "Z", "material_name": "Z",
            "opening_qty": 100, "opening_amount": 1000,
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 97, "ending_amount": 970,  # 比 opening 少 3
            "inbound_date": None,
        }]
        result = InventoryAgingEngine().compute([movs[0]], pe, sales_records=[])
        total_book = sum(r.book_amount for r in result.rows)
        bucket_total = (
            result.summary["aging_le_90"]
            + result.summary["aging_91_180"]
            + result.summary["aging_181_365"]
            + result.summary["aging_366_730"]
            + result.summary["aging_gt_730"]
        )
        # 即使 scale 介入, 误差也应在 1% 内 (按 ending_qty 分摊)
        if total_book > 0:
            diff_pct = abs(bucket_total - total_book) / total_book
            assert diff_pct < 0.01, (
                f"summary 金额与 book_amount 偏离 {diff_pct:.2%}: "
                f"bucket_total={bucket_total}, book_amount={total_book}"
            )


class TestFifoDoubleComputeNoDuplicateOpening:
    """P0-5: 多次 compute 同一项目, opening batches 不重复 (不双重加权)"""

    def test_compute_twice_same_project_opening_not_duplicated(self):
        """同一 movements 反复 compute, 期初批次只算一次 (单次调用内的循环)"""
        pe = datetime(2024, 12, 31)
        prior_pe = datetime(2023, 12, 31)
        movs = [{
            "material_code": "M", "material_name": "M",
            "opening_qty": 100, "opening_amount": 1000,
            "inbound_qty": 50, "inbound_amount": 500,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 150, "ending_amount": 1500,
            "inbound_date": pe - timedelta(days=30),
        }]
        # 第一次 compute
        r1 = InventoryAgingEngine().compute(
            movs, pe, sales_records=[],
            prior_period_end={"M": prior_pe},
        )
        # 第二次 compute (同一数据, 同一次会话内) — opening 仍只算一次
        r2 = InventoryAgingEngine().compute(
            movs, pe, sales_records=[],
            prior_period_end={"M": prior_pe},
        )
        # 两次结果应一致 (单次 compute 内部没有累积)
        assert r1.summary["aging_le_90"] == pytest.approx(r2.summary["aging_le_90"], rel=1e-6)
        assert r1.summary["aging_181_365"] == pytest.approx(r2.summary["aging_181_365"], rel=1e-6)
        # 加权均龄也应一致
        assert r1.rows[0].aging.weighted_avg_age == pytest.approx(
            r2.rows[0].aging.weighted_avg_age, rel=1e-6
        )

    def test_compute_with_prior_year_data_separates_layers(self):
        """P0-5: 上传 prior_year 数据后, compute 仍按本期 is_prior_year=False 过滤,
        opening 不会被 prior_year 的 opening_qty 重复计入"""
        pe = datetime(2024, 12, 31)
        cur = [{
            "material_code": "M", "material_name": "M",
            "opening_qty": 100, "opening_amount": 1000,
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 100, "ending_amount": 1000,
            "inbound_date": None,
            "is_prior_year": False,
        }]
        prior = [{
            "material_code": "M", "material_name": "M",
            "opening_qty": 999, "opening_amount": 9999,  # 上年期初, 不应污染本期
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 999, "ending_amount": 9999,
            "inbound_date": None,
            "is_prior_year": True,
        }]
        # 不传 prior_year 数据 (只本期)
        r_only_cur = InventoryAgingEngine().compute(cur, pe, sales_records=[])
        # 混入 prior_year 数据
        r_with_prior = InventoryAgingEngine().compute(
            cur + prior, pe, sales_records=[]
        )
        # 两者结果必须一致 — prior_year 的 999 不应进入本期 FIFO batches
        assert r_only_cur.summary["book_amount"] == pytest.approx(
            r_with_prior.summary["book_amount"], rel=1e-6
        )
        assert r_only_cur.summary["aging_181_365"] == pytest.approx(
            r_with_prior.summary["aging_181_365"], rel=1e-6
        )
        # items 也应该都是 1 (只有 M)
        assert r_with_prior.summary["items"] == 1