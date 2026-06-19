"""Inventory count-sheet builder — 「金额优先 + 阈值覆盖」抽样.

策略目标：用最少的盘点行覆盖尽可能多的期末金额，让基层人员易于理解，
让现场监盘的"金额覆盖率"指标达到约定阈值（如 80%）。

算法：
  1. 按 ``ending_amount`` 降序排序所有物料。
  2. 取累计金额覆盖率 ≥ ``coverage_threshold`` 的最少行（A 类）。
  3. 若有"必盘仓库" (high_value_warehouses)，将其全部物料也并入 A 类。
  4. 剩余物料在 B 类（按比例抽样，默认 20%）。
  5. 剩下进入 C 类（覆盖性抽样，默认 5%）。

输出 ``list[dict]`` 即可直接写入 ``InventoryCountSheet``。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CountSheetStrategy:
    """与用户对话调整的抽样参数。"""

    coverage_threshold: float = 0.80  # A 类金额累计覆盖率
    b_sample_ratio: float = 0.20  # B 类抽样比例
    c_sample_ratio: float = 0.05  # C 类覆盖性抽样比例
    high_value_warehouses: list[str] = field(default_factory=list)  # 必盘仓库
    must_include_categories: list[str] = field(default_factory=list)  # 必盘类别
    must_include_codes: list[str] = field(default_factory=list)  # 必盘物料编码（如审计师指定）
    min_unit_amount: float = 0.0  # 单行金额 < 此值的直接跳过 (省纸)
    random_seed: int = 42  # 抽样可复现
    # 重要性水平（如税前利润 5% 估算的金额）— 单条 ending_amount ≥ materiality 的物料强制入 A
    materiality: float = 0.0
    # B 类抽样方式：random=简单随机；mus=按金额加权（金额大的更易被抽中），更符合审计实务
    b_sample_method: str = "mus"
    # 反向抽盘比例（物→账）— 在 A/B/C 之外，随机额外挑这么多比例的物料标 R 类，
    # 让审计师拿着实物去查账，验证"账外存货"。
    reverse_sample_ratio: float = 0.05

    def describe(self) -> str:
        """对话式描述，回显给用户确认。"""
        parts = [
            f"A 类（金额累计覆盖 {self.coverage_threshold:.0%}）→ 全盘",
            f"B 类（{'金额加权抽' if self.b_sample_method == 'mus' else '随机抽'} {self.b_sample_ratio:.0%}）",
            f"C 类（剩余）→ 覆盖性抽 {self.c_sample_ratio:.0%}",
        ]
        if self.materiality > 0:
            parts.append(f"单条金额 ≥ 重要性水平 ¥{self.materiality:,.0f} 强制入 A")
        if self.high_value_warehouses:
            parts.append(f"必盘仓库：{', '.join(self.high_value_warehouses)}")
        if self.must_include_categories:
            parts.append(f"必盘类别：{', '.join(self.must_include_categories)}")
        if self.must_include_codes:
            parts.append(f"必盘物料编码：{len(self.must_include_codes)} 个")
        if self.min_unit_amount > 0:
            parts.append(f"忽略单行金额 < ¥{self.min_unit_amount:,.0f} 的物料")
        return "；".join(parts)


@dataclass
class CountSheetResult:
    rows: list[dict[str, Any]]  # 可直接写库的行
    total_amount: float  # 总期末金额
    covered_amount: float  # 选中的金额
    coverage_ratio: float  # = covered / total
    total_items: int  # 总物料数
    selected_items: int  # 选中的物料数
    tier_summary: dict[str, dict[str, Any]]  # {"A": {...}, "B": {...}, "C": {...}}
    strategy: CountSheetStrategy
    # P0-2 (2026-06-19): 抽样审计日志 — 把 B/C/R 类抽到的 DataFrame index 写下来,
    # 现场监盘核对 "同 seed 必出同结果" / 出具审计底稿时回溯.
    audit_log: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "total_amount": round(self.total_amount, 2),
            "covered_amount": round(self.covered_amount, 2),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "total_items": self.total_items,
            "selected_items": self.selected_items,
            "tier_summary": self.tier_summary,
            "strategy_desc": self.strategy.describe(),
            "audit_log": self.audit_log,
        }


class CountSheetBuilder:
    """根据期末时点数生成盘点用表。"""

    @staticmethod
    def _row_from_movement(m: Any, tier: str, reason: str, rank: int) -> dict[str, Any]:
        """Convert an InventoryMovement-like object/dict to a sheet row.

        P0-1 (2026-06-19): ERP 通常先存数量+金额, 算加权平均前 unit_cost 暂为 0,
        此处兜底: 若 unit_cost <= 0 但 amount 和 qty 都非 0, 用 amount/qty 推回单价,
        否则下游 completion_stats 算 delta_amount 时会全 0 (盘盈盘亏金额失真).
        """
        get = (lambda k: m.get(k)) if isinstance(m, dict) else (lambda k: getattr(m, k, None))
        qty = float(get("ending_qty") or 0)
        unit_cost = float(get("unit_cost") or 0)
        amount = float(get("ending_amount") or qty * unit_cost)
        # 兜底反推 unit_cost (防止 ERP 落地前 unit_cost=0 的全 0 跌价)
        if unit_cost <= 0 and amount > 0 and qty > 0:
            unit_cost = amount / qty
        return {
            "material_code": str(get("material_code") or ""),
            "material_name": str(get("material_name") or ""),
            "category": str(get("category") or ""),
            "warehouse": str(get("warehouse") or ""),
            "batch_no": str(get("batch_no") or ""),
            "unit": str(get("unit") or ""),
            "book_qty": qty,
            "book_unit_cost": unit_cost,
            "book_amount": amount,
            "sample_tier": tier,
            "sample_reason": reason,
            "coverage_rank": rank,
        }

    @classmethod
    def build(
        cls,
        movements: Iterable[Any],
        strategy: Optional[CountSheetStrategy] = None,
    ) -> CountSheetResult:
        """Build the count sheet rows. ``movements`` is iterable of
        InventoryMovement ORM rows OR plain dicts with the same fields."""
        strategy = strategy or CountSheetStrategy()

        # Collect → DataFrame (use only ending_amount > 0 OR ending_qty > 0 rows;
        # zero-balance rows shouldn't be on the count sheet)
        raw: list[dict[str, Any]] = []
        for m in movements:
            get = (lambda k: m.get(k)) if isinstance(m, dict) else (lambda k: getattr(m, k, None))
            qty = float(get("ending_qty") or 0)
            unit_cost = float(get("unit_cost") or 0)
            amount = float(get("ending_amount") or qty * unit_cost)
            if qty <= 0 and amount <= 0:
                continue
            if strategy.min_unit_amount > 0 and amount < strategy.min_unit_amount:
                continue
            raw.append(
                {
                    "material_code": str(get("material_code") or ""),
                    "material_name": str(get("material_name") or ""),
                    "category": str(get("category") or ""),
                    "warehouse": str(get("warehouse") or ""),
                    "batch_no": str(get("batch_no") or ""),
                    "unit": str(get("unit") or ""),
                    "ending_qty": qty,
                    "unit_cost": unit_cost,
                    "ending_amount": amount,
                }
            )

        if not raw:
            return CountSheetResult(
                rows=[],
                total_amount=0.0,
                covered_amount=0.0,
                coverage_ratio=0.0,
                total_items=0,
                selected_items=0,
                tier_summary={},
                strategy=strategy,
                audit_log={"sampled_indexes": {"B": [], "C": [], "R": []}, "seed": strategy.random_seed},
            )

        df = pd.DataFrame(raw).sort_values("ending_amount", ascending=False).reset_index(drop=True)
        total_amount = float(df["ending_amount"].sum())

        # ---- A 类：累计金额覆盖到阈值 -----------------------------------
        df["_cum"] = df["ending_amount"].cumsum()
        a_mask = df["_cum"] <= total_amount * strategy.coverage_threshold
        # 至少包含第 1 行；金额第 1 行就超阈值时 a_mask 全 False，补一行
        if not a_mask.any():
            a_mask.iloc[0] = True
        # 把刚好跨过阈值的那一行也并进 A，确保真的 >= threshold
        if a_mask.sum() < len(df):
            first_false = a_mask[~a_mask].index[0]
            a_mask.loc[first_false] = True

        # 必盘扩展：仓库 / 类别 / 物料编码 / 单条金额 ≥ 重要性水平
        must_in = (
            df["warehouse"].isin(strategy.high_value_warehouses)
            | df["category"].isin(strategy.must_include_categories)
            | df["material_code"].isin(strategy.must_include_codes)
        )
        if strategy.materiality > 0:
            must_in = must_in | (df["ending_amount"] >= strategy.materiality)
        a_mask = a_mask | must_in

        a_df = df[a_mask].copy()
        rest = df[~a_mask].copy()

        # ---- B/C/R 类：numpy Generator 抽样, 保证跨 pandas 版本复现 ----
        # P0-2 (2026-06-19): pd.DataFrame.sample(random_state=N) 在不同 pandas
        # 版本下行为不一致 (np.random MT19937 vs Generator), 审计现场复现要求
        # "同 seed 必出同结果". 改用 np.random.default_rng(seed) 显式构造,
        # 一次抽完所有候选 index 再按比例切片, 同时把抽到的 index 写入 log/audit.
        rng = np.random.default_rng(strategy.random_seed)
        # 抽样日志 (审计追溯: 同 seed 必出同结果 + 现场监盘时核对)
        sampled_indexes_log: dict[str, list[int]] = {"B": [], "C": [], "R": []}
        all_rest_idx = list(rest.index)
        b_idx: list[int] = []  # 提前定义, 防下方 if 块未赋值时 NameError
        if all_rest_idx:
            # ---- B 类：MUS（按金额加权）或随机抽 --------------------
            b_n = math.ceil(len(rest) * strategy.b_sample_ratio)
            b_n = min(b_n, len(rest))
            if b_n > 0:
                if strategy.b_sample_method == "mus" and rest["ending_amount"].sum() > 0:
                    # weights = ending_amount / sum，金额越大越易抽中
                    weights = rest["ending_amount"].clip(lower=0.0001).to_numpy()
                    weights = weights / weights.sum()
                    chosen = rng.choice(len(all_rest_idx), size=b_n, replace=False, p=weights)
                else:
                    chosen = rng.choice(len(all_rest_idx), size=b_n, replace=False)
                b_idx = [all_rest_idx[i] for i in chosen]
                sampled_indexes_log["B"] = b_idx
                b_df = rest.loc[b_idx]
            else:
                b_df = rest.head(0)
            b_idx_set = set(b_idx)
            rest_after_b_idx = [i for i in all_rest_idx if i not in b_idx_set] if b_n > 0 else all_rest_idx
            rest_after_b = rest.loc[rest_after_b_idx]

            # ---- C 类：在剩余里覆盖性抽 ----------------------------
            c_n = math.ceil(len(rest_after_b) * strategy.c_sample_ratio)
            c_n = min(c_n, len(rest_after_b))
            if c_n > 0:
                rab_idx = list(rest_after_b.index)
                chosen_c = rng.choice(len(rab_idx), size=c_n, replace=False)
                c_idx = [rab_idx[i] for i in chosen_c]
                sampled_indexes_log["C"] = c_idx
                c_df = rest_after_b.loc[c_idx]
            else:
                c_df = rest_after_b.head(0)
        else:
            b_df = rest.head(0)
            c_df = rest.head(0)

        # ---- R 类：反向抽盘（物→账）— 从所有物料里再随机抽，验证账外存货 ----
        # 注意：R 类与 A/B/C 不互斥，可重复采（审计师同一物料既正向核对账面，也反向从实物查账）
        r_n = math.ceil(len(df) * strategy.reverse_sample_ratio)
        r_n = min(r_n, len(df))
        if r_n > 0:
            all_df_idx = list(df.index)
            chosen_r = rng.choice(len(all_df_idx), size=r_n, replace=False)
            r_idx = [all_df_idx[i] for i in chosen_r]
            sampled_indexes_log["R"] = r_idx
            r_df = df.loc[r_idx]
        else:
            r_df = df.head(0)

        # 写审计日志 (与 results 一起回填到 CountSheetResult)
        logger.info(
            "抽样完成 seed=%s B=%d C=%d R=%d 抽样index=%s",
            strategy.random_seed,
            len(sampled_indexes_log["B"]),
            len(sampled_indexes_log["C"]),
            len(sampled_indexes_log["R"]),
            sampled_indexes_log,
        )

        # 组装最终行
        rows: list[dict[str, Any]] = []
        for rank, (_, r) in enumerate(a_df.iterrows(), start=1):
            reason_bits = []
            if r["warehouse"] in strategy.high_value_warehouses:
                reason_bits.append("必盘仓库")
            if r["category"] in strategy.must_include_categories:
                reason_bits.append("必盘类别")
            if r["material_code"] in strategy.must_include_codes:
                reason_bits.append("指定物料")
            if strategy.materiality > 0 and r["ending_amount"] >= strategy.materiality:
                reason_bits.append("超重要性水平")
            if not reason_bits:
                reason_bits.append("金额累计覆盖")
            rows.append(cls._row_from_movement(r.to_dict(), "A", "/".join(reason_bits), rank))
        for rank, (_, r) in enumerate(b_df.iterrows(), start=1):
            reason = "金额加权抽" if strategy.b_sample_method == "mus" else "随机抽样"
            rows.append(cls._row_from_movement(r.to_dict(), "B", reason, rank))
        for rank, (_, r) in enumerate(c_df.iterrows(), start=1):
            rows.append(cls._row_from_movement(r.to_dict(), "C", "覆盖性抽样", rank))
        for rank, (_, r) in enumerate(r_df.iterrows(), start=1):
            rows.append(cls._row_from_movement(r.to_dict(), "R", "反向抽盘(物→账)", rank))

        covered = float(
            a_df["ending_amount"].sum() + b_df["ending_amount"].sum() + c_df["ending_amount"].sum()
        )
        coverage_ratio = covered / total_amount if total_amount else 0.0

        tier_summary = {
            tier: {
                "items": int(sub.shape[0]),
                "amount": round(float(sub["ending_amount"].sum()), 2),
                "amount_pct": round(float(sub["ending_amount"].sum()) / total_amount, 4)
                if total_amount
                else 0.0,
            }
            for tier, sub in (("A", a_df), ("B", b_df), ("C", c_df), ("R", r_df))
        }

        return CountSheetResult(
            rows=rows,
            total_amount=total_amount,
            covered_amount=covered,
            coverage_ratio=coverage_ratio,
            total_items=int(df.shape[0]),
            selected_items=len(rows),
            tier_summary=tier_summary,
            strategy=strategy,
            audit_log={
                "seed": strategy.random_seed,
                "method": "numpy.default_rng",
                "sampled_indexes": sampled_indexes_log,
                "b_method": strategy.b_sample_method,
            },
        )

    @classmethod
    def simulate(
        cls,
        movements: Iterable[Any],
        strategies: list[CountSheetStrategy],
    ) -> list[dict[str, Any]]:
        """Compare multiple strategies side-by-side. Used by the interactive
        Streamlit page so the user can pick the trade-off they want."""
        out: list[dict[str, Any]] = []
        mvs = list(movements)
        for s in strategies:
            r = cls.build(mvs, s)
            out.append(
                {
                    "strategy": s.describe(),
                    "coverage_ratio": round(r.coverage_ratio, 4),
                    "selected_items": r.selected_items,
                    "total_items": r.total_items,
                    "covered_amount": round(r.covered_amount, 2),
                    "total_amount": round(r.total_amount, 2),
                    "tier_summary": r.tier_summary,
                }
            )
        return out
