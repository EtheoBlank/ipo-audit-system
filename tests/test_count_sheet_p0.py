"""Round 26 P0 (#1, #2, #3) — count_sheet / aging_engine 抽样 + FIFO 库龄测试.

覆盖:
  P0-1 unit_cost 兜底反推 (ERP 落地前 unit_cost=0 但 amount/qty 非 0)
  P0-2 numpy Generator 抽样跨版本复现 + audit_log 字段
  P0-3 opening_qty 不与 prior_period_end + inbound_date 双重计入
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.inventory.aging_engine import InventoryAgingEngine
from app.services.inventory.count_sheet import (
    CountSheetBuilder,
    CountSheetStrategy,
)
from app.services.inventory.photo_processor import CountPhotoProcessor


# ============================================================
#  P0-1 — unit_cost 兜底反推
# ============================================================


def _mk_movement(code, amount, qty=10, warehouse="主仓", category="原材料", unit_cost=None):
    """构造一个物料 movement. unit_cost=None → 模拟 ERP 落地前的 0 单价场景."""
    return {
        "material_code": code,
        "material_name": f"物料{code}",
        "category": category,
        "warehouse": warehouse,
        "batch_no": "",
        "unit": "个",
        "ending_qty": qty,
        "ending_amount": amount,
        "unit_cost": unit_cost if unit_cost is not None else (amount / qty if qty else 0),
    }


class TestUnitCostBackfill:
    """P0-1 (2026-06-19): unit_cost=0 但 amount/qty 非 0 → 用 amount/qty 反推.

    真实场景: ERP 先存数量+金额, 期末加权平均单价算完前 unit_cost=0,
    若不兜底, 下游 completion_stats.delta_amount 全 0, 盘盈盘亏金额失真.
    """

    def test_unit_cost_backfilled_from_amount_qty(self):
        """unit_cost=0 + amount=10000 + qty=100 → 反推 unit_cost=100."""
        s = CountSheetStrategy(
            coverage_threshold=0.5, b_sample_ratio=0, c_sample_ratio=0, reverse_sample_ratio=0
        )
        # unit_cost=0 模拟 ERP 落地前
        movs = [_mk_movement("M1", 10000, qty=100, unit_cost=0)]
        res = CountSheetBuilder.build(movs, s)
        assert len(res.rows) == 1
        row = res.rows[0]
        assert row["book_unit_cost"] == pytest.approx(100.0)
        assert row["book_amount"] == pytest.approx(10000.0)

    def test_unit_cost_stays_zero_when_no_amount(self):
        """unit_cost=0 + amount=0 → 仍 0 (不除零)."""
        s = CountSheetStrategy(
            coverage_threshold=0.5, b_sample_ratio=0, c_sample_ratio=0, reverse_sample_ratio=0
        )
        # amount=0 + qty=0 会被前置 qty<=0 and amount<=0 过滤掉, 用 qty>0 amount=0
        # 这种边界在 _row_from_movement 里 amount = qty * 0 = 0, 然后兜底 unit_cost=0
        movs = [_mk_movement("M2", 0, qty=10, unit_cost=0)]
        # 会被 qty>0 and amount<=0 过滤 (raw 阶段). 但 _row_from_movement 单独调用时
        # 不走这个过滤, 所以直接调静态方法
        from app.services.inventory.count_sheet import CountSheetBuilder as CSB

        row = CSB._row_from_movement(movs[0], "A", "测试", 1)
        # amount = max(qty*unit_cost, ending_amount) = 0
        # 兜底: unit_cost<=0 but amount<=0 → 不反推, 仍 0
        assert row["book_unit_cost"] == 0.0
        assert row["book_amount"] == 0.0

    def test_completion_stats_delta_amount_nonzero(self):
        """即使 unit_cost 初始 0, 盘亏金额应非 0 (兜底反推生效)."""
        # 模拟 ORM 行: book_qty=100, counted_qty=95 (盘亏 5), book_unit_cost=0,
        # book_amount=10000 → 反推 unit_cost=100, delta_amount=5*100=500
        from types import SimpleNamespace

        sheet = SimpleNamespace(
            material_code="M3",
            material_name="测试",
            warehouse="主仓",
            book_qty=100,
            book_unit_cost=0,  # ERP 落地前
            book_amount=10000,
            counted_qty=95,
        )
        stats = CountPhotoProcessor.completion_stats([sheet], materiality=0)
        # 应找到一条盘亏, delta_amount = -5 * 100 = -500 (反推后).
        # 兜底前 unit_cost=0 → delta_amount 全 0, 兜底后应为 -500.
        diffs = stats.get("differences_major", []) + stats.get("differences_minor", [])
        assert len(diffs) == 1
        assert diffs[0]["delta_amount"] == pytest.approx(-500.0)


# ============================================================
#  P0-2 — numpy Generator 抽样跨版本复现 + audit_log
# ============================================================


class TestSamplingReproducible:
    """P0-2 (2026-06-19): numpy.random.default_rng 显式构造,
    跨 pandas 版本稳定复现; sampled_indexes 写入 audit_log."""

    def _mk_movs(self, n=50):
        # 50 个物料, 金额均匀 1~n
        return [_mk_movement(f"M{i:03d}", float(i + 1), qty=10) for i in range(n)]

    def test_sampling_reproducible_across_pandas_versions(self):
        """同样 seed 两次, 结果 index 完全一致 (np.random.default_rng 行为稳定)."""
        movs = self._mk_movs(50)
        s1 = CountSheetStrategy(
            coverage_threshold=0.5,
            b_sample_ratio=0.3,
            c_sample_ratio=0.1,
            reverse_sample_ratio=0.1,
            random_seed=42,
            b_sample_method="mus",
        )
        s2 = CountSheetStrategy(
            coverage_threshold=0.5,
            b_sample_ratio=0.3,
            c_sample_ratio=0.1,
            reverse_sample_ratio=0.1,
            random_seed=42,
            b_sample_method="mus",
        )
        r1 = CountSheetBuilder.build(movs, s1)
        r2 = CountSheetBuilder.build(movs, s2)
        # audit_log 里 sampled_indexes 完全一致
        idx1 = r1.audit_log["sampled_indexes"]
        idx2 = r2.audit_log["sampled_indexes"]
        assert sorted(idx1["B"]) == sorted(idx2["B"])
        assert sorted(idx1["C"]) == sorted(idx2["C"])
        assert sorted(idx1["R"]) == sorted(idx2["R"])

    def test_sampling_different_seeds_different_results(self):
        """seed=1 vs seed=2, B 类抽样 index 不重叠 (大概率)."""
        movs = self._mk_movs(50)
        s1 = CountSheetStrategy(
            coverage_threshold=0.5,
            b_sample_ratio=0.3,
            c_sample_ratio=0,
            reverse_sample_ratio=0,
            random_seed=1,
        )
        s2 = CountSheetStrategy(
            coverage_threshold=0.5,
            b_sample_ratio=0.3,
            c_sample_ratio=0,
            reverse_sample_ratio=0,
            random_seed=2,
        )
        r1 = CountSheetBuilder.build(movs, s1)
        r2 = CountSheetBuilder.build(movs, s2)
        b1 = set(r1.audit_log["sampled_indexes"]["B"])
        b2 = set(r2.audit_log["sampled_indexes"]["B"])
        # 大概率不重叠 (50 个里抽 15 个, 期望重叠 ~4.5)
        # 用 seed=1 vs seed=2 实际验证: 不要求完全 disjoint, 只要求不全等
        assert b1 != b2, "不同 seed 应产生不同抽样"

    def test_audit_log_records_sampled_indexes(self):
        """验证 audit_log 字段含 seed + sampled_indexes + method."""
        movs = self._mk_movs(20)
        s = CountSheetStrategy(
            coverage_threshold=0.5,
            b_sample_ratio=0.2,
            c_sample_ratio=0.1,
            reverse_sample_ratio=0.1,
            random_seed=42,
            b_sample_method="mus",
        )
        res = CountSheetBuilder.build(movs, s)
        log = res.audit_log
        assert "seed" in log
        assert log["seed"] == 42
        assert "sampled_indexes" in log
        for tier in ("B", "C", "R"):
            assert tier in log["sampled_indexes"]
            assert isinstance(log["sampled_indexes"][tier], list)
        # method 标识 np 路径
        assert log["method"] == "numpy.default_rng"


# ============================================================
#  P0-3 — opening_qty 不与 prior_period_end + inbound_date 双重计入
# ============================================================


class TestOpeningQtyNotDuplicated:
    """P0-3 (2026-06-19): 同一物料 prior_period_end + inbound_date 不可同时提供.

    ERP 通常无逐批明细, opening 是 prior 期末聚合, 不能再拆.
    """

    def test_opening_qty_not_duplicated_with_prior_period(self):
        """同一项目 prior_period_end 提供时, opening 只算 1 次 (不重复加 inbound 批)."""
        period_end = datetime(2024, 12, 31)
        prior_period_end = datetime(2023, 12, 31)
        movements = [
            {
                "opening_qty": 100,
                "opening_amount": 10000,
                "inbound_qty": 0,  # 无逐批明细
                "inbound_date": None,
                "outbound_qty": 0,
                "ending_qty": 100,
            }
        ]
        bucket = InventoryAgingEngine.fifo_aging(
            movements, period_end, prior_period_end=prior_period_end
        )
        # opening 100 全在 prior 期末聚合批里, 库龄 = 366 天, 应归到 366_730 段
        # 注意 2023-12-31 到 2024-12-31 = 366 天 (闰年)
        assert bucket.age_366_730 == pytest.approx(100.0, abs=1e-6)
        # 不应有重复加和
        total = (
            bucket.le_90
            + bucket.age_91_180
            + bucket.age_181_365
            + bucket.age_366_730
            + bucket.gt_730
        )
        assert total == pytest.approx(100.0, abs=1e-6)

    def test_compute_rejects_invalid_combination(self):
        """opening_qty + prior_period_end + 包含 inbound_date 的 movements → raise ValueError."""
        from app.services.inventory.aging_engine import InventoryAgingEngine

        period_end = datetime(2024, 12, 31)
        prior_period_end_dict = {"M1": datetime(2023, 12, 31)}
        movements = [
            {
                "material_code": "M1",
                "material_name": "测试",
                "category": "原材料",
                "opening_qty": 100,
                "opening_amount": 10000,
                "inbound_qty": 50,
                "inbound_amount": 5000,
                "inbound_date": datetime(2024, 6, 1),  # 同时给了 inbound_date
                "outbound_qty": 0,
                "outbound_amount": 0,
                "ending_qty": 150,
                "ending_amount": 15000,
                "unit_cost": 100.0,
            }
        ]
        engine = InventoryAgingEngine()
        with pytest.raises(ValueError) as exc_info:
            engine.compute(
                movements, period_end, prior_period_end=prior_period_end_dict
            )
        assert "M1" in str(exc_info.value)
        assert "双重计入" in str(exc_info.value) or "prior_period_end" in str(exc_info.value)

    def test_opening_with_only_inbound_no_prior_ok(self):
        """仅有 inbound, 无 prior → 走合成策略 (不抛错)."""
        period_end = datetime(2024, 12, 31)
        movements = [
            {
                "opening_qty": 0,
                "opening_amount": 0,
                "inbound_qty": 100,
                "inbound_date": datetime(2024, 6, 1),
                "outbound_qty": 0,
                "ending_qty": 100,
            }
        ]
        bucket = InventoryAgingEngine.fifo_aging(movements, period_end, prior_period_end=None)
        total = (
            bucket.le_90
            + bucket.age_91_180
            + bucket.age_181_365
            + bucket.age_366_730
            + bucket.gt_730
        )
        # 100 个 @ 2024-06-01 → 库龄 ~213 天 → 181_365 段
        assert total == pytest.approx(100.0, abs=1e-6)