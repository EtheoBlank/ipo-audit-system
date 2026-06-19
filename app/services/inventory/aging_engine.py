"""Inventory aging / NRV impairment / reversal engine.

库龄计算：FIFO 推算
  - 把所有"入库批次"按入库日期升序排列；
  - 依次扣减本期"出库数量"，剩下的就是期末仍在库的批次（含批次入库日期）；
  - 用 报告期截止日 - 该批入库日期 = 该批库龄；
  - 加权平均库龄 = Σ(批量 × 库龄) / 总数量。

  实际数据中很多 ERP 没有逐批明细，只有汇总收发存。
  本引擎做"折中近似"：把按 ``InventoryMovement`` 行聚合到 (material_code) 后，
  用每行的 ``inbound_date`` 与 ``inbound_qty`` 构造合成批次，把上一期期末
  当做"零日批次"。如果完全缺批次/日期，退化为周转率反推法。

跌价（NRV）：
  - NRV 单价 = max(0, 期末后销售清单加权平均单价 - 估计销售费用 - 估计销售税费)
  - 若 NRV 单价 < 账面单价 → 计提跌价 = (账面 - NRV) × 数量；否则 0
  - 行业默认跌价比例（按库龄分层）作为兜底（无销售清单时使用）

跌价转回：
  - 上年末已计提跌价 = ``opening_impairment``
  - 本期末应保留跌价 = ``current_impairment``
  - 若 current < opening → 转回 = opening - current（在原已计提范围内）
  - 若 current > opening → 新增计提 = current - opening
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# 行业默认跌价比例（按库龄分层；销售清单无价时兜底）
DEFAULT_AGING_RATES: dict[str, dict[str, float]] = {
    "默认": {"le_90": 0.00, "91_180": 0.05, "181_365": 0.15, "366_730": 0.50, "gt_730": 1.00},
    "制造业": {"le_90": 0.00, "91_180": 0.05, "181_365": 0.20, "366_730": 0.60, "gt_730": 1.00},
    "医药生物": {"le_90": 0.00, "91_180": 0.10, "181_365": 0.40, "366_730": 0.80, "gt_730": 1.00},
    "零售": {"le_90": 0.00, "91_180": 0.10, "181_365": 0.30, "366_730": 0.80, "gt_730": 1.00},
    "信息技术": {"le_90": 0.00, "91_180": 0.10, "181_365": 0.30, "366_730": 0.70, "gt_730": 1.00},
    "化工": {"le_90": 0.00, "91_180": 0.05, "181_365": 0.15, "366_730": 0.50, "gt_730": 1.00},
}


# 行业默认估计销售费用率（销售费用 + 税金及附加 占含税收入的比例）。
# 来源：参考主流上市公司年报近三年均值；用户应根据被审主体实际口径调整。
DEFAULT_SELL_COST_RATES: dict[str, float] = {
    "默认": 0.05,
    "制造业": 0.06,
    "医药生物": 0.07,
    "零售": 0.05,
    "信息技术": 0.06,
    "化工": 0.08,
    "建筑施工": 0.08,
    "重型机械": 0.10,
}


def sell_cost_rate_for(industry: Optional[str]) -> float:
    """按行业返回默认销售费用率；找不到时返回 0.05。"""
    if not industry:
        return DEFAULT_SELL_COST_RATES["默认"]
    if industry in DEFAULT_SELL_COST_RATES:
        return DEFAULT_SELL_COST_RATES[industry]
    for k, v in DEFAULT_SELL_COST_RATES.items():
        if k != "默认" and (k in industry or industry in k):
            return v
    return DEFAULT_SELL_COST_RATES["默认"]


@dataclass
class AgingBucket:
    le_90: float = 0.0
    age_91_180: float = 0.0
    age_181_365: float = 0.0
    age_366_730: float = 0.0
    gt_730: float = 0.0
    weighted_avg_age: float = 0.0


@dataclass
class ImpairmentRow:
    material_code: str
    material_name: str
    category: str
    period_end: str
    ending_qty: float
    book_unit_cost: float
    book_amount: float
    aging: AgingBucket
    nrv_unit_price: Optional[float]
    nrv_source: str
    nrv_amount: float
    estimated_sell_cost: float
    impairment_current: float
    impairment_opening: float
    impairment_reversal: float
    impairment_provision: float
    net_impairment_change: float
    method: str
    note: str = ""
    # 转回拆分（CAS 1 第 21 条）：已售出部分应"转销营业成本"，仍在库部分才"转回资产减值损失"
    reversal_to_cogs: float = 0.0  # 已售出部分对应的跌价（随销售转出营业成本）
    reversal_to_loss: float = 0.0  # 仍在库部分对应的跌价（转回资产减值损失）

    def to_db_kwargs(self) -> dict[str, Any]:
        return {
            "material_code": self.material_code,
            "material_name": self.material_name,
            "category": self.category,
            "period_end": self.period_end,
            "ending_qty": self.ending_qty,
            "book_unit_cost": self.book_unit_cost,
            "book_amount": self.book_amount,
            "age_le_90": self.aging.le_90,
            "age_91_180": self.aging.age_91_180,
            "age_181_365": self.aging.age_181_365,
            "age_366_730": self.aging.age_366_730,
            "age_gt_730": self.aging.gt_730,
            "weighted_avg_age": self.aging.weighted_avg_age,
            "nrv_unit_price": self.nrv_unit_price,
            "nrv_source": self.nrv_source,
            "nrv_amount": self.nrv_amount,
            "estimated_sell_cost": self.estimated_sell_cost,
            "impairment_current": self.impairment_current,
            "impairment_opening": self.impairment_opening,
            "impairment_reversal": self.impairment_reversal,
            "impairment_provision": self.impairment_provision,
            "net_impairment_change": self.net_impairment_change,
            "method": self.method,
            "note": self.note,
            "reversal_to_cogs": self.reversal_to_cogs,
            "reversal_to_loss": self.reversal_to_loss,
        }


@dataclass
class ImpairmentResult:
    rows: list[ImpairmentRow]
    summary: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_db_kwargs() for r in self.rows],
            "summary": self.summary,
        }


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def _to_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """P1-8 (2026-06-19 round 28): 统一 datetime tzinfo — 业务侧统一存 naive UTC,
    比较时若发现 tz-aware, 仅去 tzinfo 不改值."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _aging_bucket(days: float, qty: float) -> dict[str, float]:
    if days <= 90:
        return {"le_90": qty}
    if days <= 180:
        return {"age_91_180": qty}
    if days <= 365:
        return {"age_181_365": qty}
    if days <= 730:
        return {"age_366_730": qty}
    return {"gt_730": qty}


class InventoryAgingEngine:
    """Compute aging / impairment / reversal per material."""

    # 哪些 category 走"完工口径"：原材料/在产品/半成品（NRV 还需扣除至完工成本）
    COMPLETION_CATEGORIES = ("原材料", "在产品", "半成品", "原料", "辅料", "包装物", "委外材料")

    def __init__(
        self,
        industry: str = "默认",
        sell_cost_rate: float = 0.05,
        completion_cost_rate: float = 0.0,
        manual_completion_cost: Optional[dict[str, float]] = None,
    ):
        # ``sell_cost_rate`` = 估计销售费用 + 税费 占含税收入的比例（默认 5%）
        # ``completion_cost_rate`` = 至完工成本占产成品售价的比例（原材料/在产品 NRV 用）
        # P0-B (2026-06-19): 完工成本是绝对值, 与 NRV 单价线性相关不合理.
        # 增加 ``manual_completion_cost`` {material_code: 至完工单价} 优先于 rate 模型.
        self.industry = industry
        self.rates = DEFAULT_AGING_RATES.get(industry) or DEFAULT_AGING_RATES["默认"]
        # 包含匹配
        if industry and industry not in DEFAULT_AGING_RATES:
            for k, v in DEFAULT_AGING_RATES.items():
                if k != "默认" and (k in industry or industry in k):
                    self.rates = v
                    break
        self.sell_cost_rate = sell_cost_rate
        self.completion_cost_rate = completion_cost_rate
        self.manual_completion_cost = dict(manual_completion_cost or {})

    @classmethod
    def is_completion_category(cls, category: Optional[str]) -> bool:
        """判断该物料是否走"完工口径"（原材料/在产品类）"""
        if not category:
            return False
        return any(k in category for k in cls.COMPLETION_CATEGORIES)

    # ---- FIFO aging -----------------------------------------------------

    @staticmethod
    def fifo_aging(
        movements: list[dict[str, Any]],
        period_end: datetime,
        prior_period_end: Optional[datetime] = None,
    ) -> AgingBucket:
        """Recompute aging from per-batch movements of ONE material.

        Each movement dict needs: opening_qty, opening_amount, inbound_qty,
        inbound_date, outbound_qty, ending_qty.

        :param prior_period_end: 上年期末日（如 2023-12-31），用于精确计算
            期初库龄。为 None 时退回 period_end - 365 天保守兜底。

        P0-3 (2026-06-19): 重复计入防御. 若用户在 movements 同时提供 prior_period_end
        又在每行填了 inbound_date (典型场景: 整体迁移 ERP 历史但明细不全),
        opening_qty 会被当作"prior 期末的单一聚合批" + 每行 inbound_qty 独立批,
        双重计入. ERP 通常无逐批明细, opening 是 prior 期末聚合, 不能再拆.
        同一物料: prior_period_end 给定 → opening 仅作 1 批, 不再叠加 inbound 批;
        compute() 层负责校验并拒绝错误组合.
        """
        # 构造合成批次列表 [(入库日期, 剩余数量)]
        batches: list[tuple[datetime, float]] = []
        opening_qty_total = sum(float(m.get("opening_qty") or 0) for m in movements)
        if opening_qty_total > 0:
            if prior_period_end is not None:
                # 精确使用上年期末日, 期初库龄 = period_end - prior_period_end
                # P0-3 (2026-06-19): 当 prior_period_end 提供时, opening_qty 视为
                # "prior 期末的单一聚合批" — 不再叠加每行 inbound_qty (否则双重计入).
                batches.append((prior_period_end, opening_qty_total))
                # 提前返回聚合路径: 跳过 inbound 批次解析
                batches.sort(key=lambda x: x[0])
                # 直接走 FIFO 扣减 (后续逻辑复用)
                return InventoryAgingEngine._fifo_aging_from_batches(
                    batches, period_end, movements, opening_qty_total
                )
            else:
                # 兜底: 期初统一当 "已经 365 天" 处理（保守）
                batches.append((period_end - pd.Timedelta(days=365), opening_qty_total))

        for m in movements:
            qty = float(m.get("inbound_qty") or 0)
            if qty <= 0:
                continue
            dt = _parse_dt(m.get("inbound_date")) or period_end
            batches.append((dt, qty))

        batches.sort(key=lambda x: x[0])  # FIFO: 最早入库的先出

        return InventoryAgingEngine._fifo_aging_from_batches(
            batches, period_end, movements, opening_qty_total
        )

    @staticmethod
    def _fifo_aging_from_batches(
        batches: list[tuple[datetime, float]],
        period_end: datetime,
        movements: list[dict[str, Any]],
        opening_qty_total: float,
    ) -> AgingBucket:
        """从已构造的批次列表 [(dt, qty)] 走完 FIFO 扣减 + 库龄分桶.

        P0-3 (2026-06-19): 抽出 fifo_aging 的剩余逻辑, 让"仅含 prior 期末的单一聚合批"
        路径也能复用扣减 + 分桶逻辑, 不再走 inbound 重复.
        """
        total_outbound = sum(float(m.get("outbound_qty") or 0) for m in movements)
        remaining_out = total_outbound
        # 从头扣减
        # P0-5 修复 (2026-06-19): 浮点精度防负 — 多次扣减后 qty 可能变为极小负值,
        # 仍走 _aging_bucket 把负数归类到某天, 库龄错误. 用 max(0, qty-take) 兜底
        # 保留 (dt, max(0, qty - take)) 而非 (dt, qty - take), 避免下游污染
        zero = 0.0
        for i, (dt, qty) in enumerate(batches):
            if remaining_out <= 0:
                break
            take = min(qty, remaining_out)
            new_qty = qty - take
            if new_qty < zero:
                new_qty = zero
            batches[i] = (dt, new_qty)
            remaining_out -= take

        # 剩下的就是期末
        ending_qty_total = sum(float(m.get("ending_qty") or 0) for m in movements)
        leftover_qty = sum(q for _, q in batches if q > 0)
        # 校准：如果 leftover ≈ ending（小幅四舍五入差），按比例缩放；
        # 若偏离 > 5%，**不静默校准**（审计场景下账实差异是核心信号，应留给审计师查），
        # 标记给上层，库龄按 leftover 原值计算。
        scale = 1.0
        if leftover_qty > 0 and ending_qty_total > 0:
            ratio = ending_qty_total / leftover_qty
            if 0.95 <= ratio <= 1.05:
                scale = ratio
            else:
                # 留下 scale=1，调用方在 compute() 里会在 note 标注差异
                scale = 1.0

        bucket = AgingBucket()
        weighted_age_sum = 0.0
        total_weight = 0.0
        for dt, qty in batches:
            if qty <= 0:
                continue
            adj_qty = qty * scale
            # round 28 P1-8: 统一 naive, 避免 aware/naive 混算 TypeError
            dt_naive = _to_naive(dt) or dt
            pe_naive = _to_naive(period_end) or period_end
            days = max(0.0, (pe_naive - dt_naive).days)
            b = _aging_bucket(days, adj_qty)
            bucket.le_90 += b.get("le_90", 0.0)
            bucket.age_91_180 += b.get("age_91_180", 0.0)
            bucket.age_181_365 += b.get("age_181_365", 0.0)
            bucket.age_366_730 += b.get("age_366_730", 0.0)
            bucket.gt_730 += b.get("gt_730", 0.0)
            weighted_age_sum += days * adj_qty
            total_weight += adj_qty

        if total_weight > 0:
            bucket.weighted_avg_age = round(weighted_age_sum / total_weight, 1)
        else:
            bucket.weighted_avg_age = 0.0

        return bucket

    # ---- NRV (期末后销售清单加权平均价) ---------------------------------

    @staticmethod
    def nrv_unit_price_from_sales(
        sales_records: list[Any],
        material_code: str,
        period_end: datetime,
    ) -> Optional[tuple[float, int]]:
        """Return (weighted avg unit price, sample count) of post-period sales
        for the given material. None if no qualifying sales found.

        性能 (2026-06-19): P1 — 旧版 O(N×M), N 物料 × M 销售行;
        上层 compute() 已按 product_code 预分桶, 此函数只扫自己桶里少量记录,
        单次 compute_impairments 从 2.5M iter 降到 ~M iter.
        """
        """Return (weighted avg unit price, sample count) of post-period sales
        for the given material. None if no qualifying sales found."""
        total_amount = 0.0
        total_qty = 0.0
        count = 0
        for r in sales_records:
            code = getattr(r, "product_code", "") or (
                r.get("product_code", "") if isinstance(r, dict) else ""
            )
            if str(code) != str(material_code):
                continue
            confirm = getattr(r, "revenue_confirm_date", None) or (
                r.get("revenue_confirm_date") if isinstance(r, dict) else None
            )
            ship = getattr(r, "ship_date", None) or (
                r.get("ship_date") if isinstance(r, dict) else None
            )
            ref_dt = _parse_dt(confirm) or _parse_dt(ship)
            # round 28 P1-8: 统一 naive, 避免 aware/naive 混算 TypeError
            ref_dt = _to_naive(ref_dt) if ref_dt is not None else None
            pe_naive = _to_naive(period_end) or period_end
            if ref_dt is None or ref_dt <= pe_naive:
                continue
            qty = float(
                getattr(r, "quantity", 0) or (r.get("quantity", 0) if isinstance(r, dict) else 0)
            )
            rev = float(
                getattr(r, "revenue_amount", 0)
                or (r.get("revenue_amount", 0) if isinstance(r, dict) else 0)
            )
            if qty <= 0 or rev <= 0:
                continue
            total_amount += rev
            total_qty += qty
            count += 1
        if total_qty <= 0:
            return None
        return total_amount / total_qty, count

    # ---- Aging-rate impairment fallback ---------------------------------

    def aging_impairment(self, book_amount: float, bucket: AgingBucket, qty: float) -> float:
        """按行业默认比例，对每个库龄段单独计提。"""
        if qty <= 0 or book_amount <= 0:
            return 0.0
        unit_cost = book_amount / qty
        amt = (
            bucket.le_90 * unit_cost * self.rates["le_90"]
            + bucket.age_91_180 * unit_cost * self.rates["91_180"]
            + bucket.age_181_365 * unit_cost * self.rates["181_365"]
            + bucket.age_366_730 * unit_cost * self.rates["366_730"]
            + bucket.gt_730 * unit_cost * self.rates["gt_730"]
        )
        return round(amt, 2)

    # ---- main -----------------------------------------------------------

    def compute(
        self,
        movements: list[Any],
        period_end: datetime,
        *,
        sales_records: Optional[list[Any]] = None,
        prior_impairments: Optional[dict[str, float]] = None,
        prior_qty: Optional[dict[str, float]] = None,
        manual_nrv: Optional[dict[str, float]] = None,
        prior_period_end: Optional[dict[str, datetime]] = None,
        manual_completion_cost: Optional[dict[str, float]] = None,
    ) -> ImpairmentResult:
        """
        :param movements: 收发存行（ORM 对象或 dict）。
        :param period_end: 报告期截止日（如 2024-12-31）。
        :param sales_records: 销售清单（用于 NRV），可为空。
        :param prior_impairments: {material_code: 期初已计提跌价}（可选）。
        :param prior_qty: {material_code: 上年期末数量}（可选）— 用于把转回拆成
            "已售出转销营业成本"和"仍在库转回资产减值损失"两部分。
        :param manual_nrv: {material_code: 手工 NRV 单价}（覆盖销售清单结果）。
        :param prior_period_end: {material_code: 上年期末日 datetime}（可选）—
            用于精确计算期初库龄。为 None 时退回 period_end - 365 天保守兜底。
        :param manual_completion_cost: {material_code: 至完工成本单价}（可选）—
            P0-B (2026-06-19): 完工成本是绝对值, 优先于 completion_cost_rate
            (nrv_unit * rate) 的简化模型. 缺失物料沿用 rate 模型.
        """
        prior_impairments = prior_impairments or {}
        prior_qty = prior_qty or {}
        manual_nrv = manual_nrv or {}
        sales_records = sales_records or []
        # P0-B: 合并 __init__ 传参与 compute 传参, 后者覆盖前者
        if manual_completion_cost is not None:
            self.manual_completion_cost = {**self.manual_completion_cost, **manual_completion_cost}

        # P1 性能 (2026-06-19): O(N×M) → O(N+M) — 一次性按 product_code 分桶
        # 旧版每物料调用 nrv_unit_price_from_sales 全表扫 sales_records,
        # 500 物料 × 5K 销售 = 2.5M iter. 分桶后每桶 O(本桶大小)
        _sales_bucket: dict[str, list[Any]] = {}
        for _sr in sales_records:
            _code = (
                getattr(_sr, "product_code", "")
                if not isinstance(_sr, dict)
                else _sr.get("product_code", "")
            )
            _sales_bucket.setdefault(str(_code) if _code else "", []).append(_sr)

        # 仅取本期（is_prior_year=False）数据
        def _is_current(m: Any) -> bool:
            if isinstance(m, dict):
                return not m.get("is_prior_year", False)
            return not bool(getattr(m, "is_prior_year", False))

        cur = [m for m in movements if _is_current(m)]
        # 按 material_code 聚合
        groups: dict[str, list[dict[str, Any]]] = {}
        for m in cur:
            d = (
                m
                if isinstance(m, dict)
                else {
                    "material_code": getattr(m, "material_code", ""),
                    "material_name": getattr(m, "material_name", ""),
                    "category": getattr(m, "category", ""),
                    "opening_qty": getattr(m, "opening_qty", 0),
                    "opening_amount": getattr(m, "opening_amount", 0),
                    "inbound_qty": getattr(m, "inbound_qty", 0),
                    "inbound_amount": getattr(m, "inbound_amount", 0),
                    "outbound_qty": getattr(m, "outbound_qty", 0),
                    "outbound_amount": getattr(m, "outbound_amount", 0),
                    "ending_qty": getattr(m, "ending_qty", 0),
                    "ending_amount": getattr(m, "ending_amount", 0),
                    "unit_cost": getattr(m, "unit_cost", 0),
                    "inbound_date": getattr(m, "inbound_date", None),
                }
            )
            code = str(d.get("material_code") or "").strip()
            if not code:
                continue
            groups.setdefault(code, []).append(d)

        rows: list[ImpairmentRow] = []
        for code, ms in groups.items():
            name = next(
                (str(m.get("material_name") or "") for m in ms if m.get("material_name")), ""
            )
            category = next((str(m.get("category") or "") for m in ms if m.get("category")), "")
            ending_qty = sum(float(m.get("ending_qty") or 0) for m in ms)
            ending_amount = sum(float(m.get("ending_amount") or 0) for m in ms)
            book_unit_cost = (ending_amount / ending_qty) if ending_qty > 0 else 0.0

            if ending_qty <= 0:
                # 已无库存的物料：若上年有跌价 → 全额转回（全部走"已售/已耗用"路径，进营业成本）
                opening = float(prior_impairments.get(code, 0.0))
                if opening > 0:
                    rows.append(
                        ImpairmentRow(
                            material_code=code,
                            material_name=name,
                            category=category,
                            period_end=period_end.strftime("%Y-%m-%d"),
                            ending_qty=0.0,
                            book_unit_cost=0.0,
                            book_amount=0.0,
                            aging=AgingBucket(),
                            nrv_unit_price=None,
                            nrv_source="无",
                            nrv_amount=0.0,
                            estimated_sell_cost=0.0,
                            impairment_current=0.0,
                            impairment_opening=opening,
                            impairment_reversal=opening,
                            impairment_provision=0.0,
                            net_impairment_change=-opening,
                            method="reversal",
                            note="期末已无库存，上年跌价全额转回",
                            reversal_to_cogs=opening,
                            reversal_to_loss=0.0,
                        )
                    )
                continue

            code_prior_end = prior_period_end.get(code) if prior_period_end else None
            # P0-3 (2026-06-19): 错误组合校验. 若同时提供 prior_period_end
            # 又在 movements 填了 inbound_date (典型场景: ERP 历史迁移明细不全),
            # opening_qty 会被双重计入 (单批 + 每行 inbound). 此时应拒绝,
            # 强制二选一: 要么用 prior_period_end (整体聚合), 要么走逐批 inbound.
            if code_prior_end is not None:
                has_inbound_with_date = any(
                    (m.get("inbound_qty") or 0) > 0 and m.get("inbound_date") is not None
                    for m in ms
                )
                if has_inbound_with_date:
                    raise ValueError(
                        f"物料 {code}: prior_period_end 与 inbound_date/inbound_qty "
                        f"不可同时提供 (opening_qty 会被双重计入). "
                        f"请二选一: 要么删除 inbound_date 走聚合路径, 要么不传 prior_period_end."
                    )
            aging = self.fifo_aging(ms, period_end, prior_period_end=code_prior_end)

            # 账实差异检测：FIFO 推算后剩余数量与账面期末数偏离 > 5% → 在 note 标注
            opening_qty_total = sum(float(m.get("opening_qty") or 0) for m in ms)
            inbound_qty_total = sum(float(m.get("inbound_qty") or 0) for m in ms)
            outbound_qty_total = sum(float(m.get("outbound_qty") or 0) for m in ms)
            implied_ending = opening_qty_total + inbound_qty_total - outbound_qty_total
            recon_note = ""
            if ending_qty > 0 and abs(implied_ending - ending_qty) / ending_qty > 0.05:
                recon_note = (
                    f"⚠️ 账实差异：期初+入库-出库={implied_ending:.2f}，"
                    f"账面期末={ending_qty:.2f}，差异 {(implied_ending - ending_qty):.2f}。"
                    "请审计师查明差异原因后再依赖本行库龄/跌价结果。"
                )

            # NRV
            nrv_unit: Optional[float] = None
            nrv_src = "无"
            sample_n = 0
            if code in manual_nrv:
                nrv_unit = float(manual_nrv[code])
                nrv_src = "手工/外部询价"
            else:
                # 传入本物料预分桶, 避免每次全表扫
                result = self.nrv_unit_price_from_sales(
                    _sales_bucket.get(code, []), code, period_end
                )
                if result:
                    nrv_unit, sample_n = result
                    nrv_src = f"销售清单({sample_n}笔)"

            if nrv_unit is not None:
                est_sell_cost_unit = nrv_unit * self.sell_cost_rate
                # 完工口径：原材料/在产品 NRV 还要扣"至完工的加工成本"
                # P0-B (2026-06-19): 完工成本是绝对值, 优先 manual_completion_cost[code],
                # 否则回退到行业默认 (nrv * completion_cost_rate, 简化模型).
                if self.is_completion_category(category):
                    manual_cost = self.manual_completion_cost.get(code)
                    if manual_cost is not None:
                        completion_cost_unit = float(manual_cost)
                        method_label = "nrv-完工口径(手工)"
                    elif self.completion_cost_rate > 0:
                        # 简化模型: 完工成本 = NRV * rate, 已知与 NRV 线性相关的局限
                        completion_cost_unit = nrv_unit * self.completion_cost_rate
                        method_label = "nrv-完工口径"
                    else:
                        completion_cost_unit = 0.0
                        method_label = "nrv-出售口径"
                else:
                    completion_cost_unit = 0.0
                    method_label = "nrv-出售口径"
                nrv_net_unit = max(0.0, nrv_unit - est_sell_cost_unit - completion_cost_unit)
                nrv_amount = nrv_net_unit * ending_qty
                est_sell_cost = (est_sell_cost_unit + completion_cost_unit) * ending_qty
                if nrv_net_unit < book_unit_cost:
                    impairment_current = (book_unit_cost - nrv_net_unit) * ending_qty
                else:
                    impairment_current = 0.0
                method = method_label
            else:
                # fallback: 用库龄表
                est_sell_cost = 0.0
                nrv_amount = ending_amount
                impairment_current = self.aging_impairment(ending_amount, aging, ending_qty)
                method = "aging"

            impairment_current = round(impairment_current, 2)

            opening = float(prior_impairments.get(code, 0.0))
            if impairment_current >= opening:
                provision = impairment_current - opening
                reversal = 0.0
            else:
                provision = 0.0
                reversal = opening - impairment_current

            # 转回拆分：按"已售出数量占上年期末数量的比例"分摊
            # 已售出部分应随销售转销营业成本；仍在库部分才转回资产减值损失
            reversal_to_cogs = 0.0
            reversal_to_loss = reversal
            py_qty = float(prior_qty.get(code, 0.0))
            if reversal > 0 and py_qty > 0:
                sold_qty = max(0.0, py_qty - ending_qty)
                sold_ratio = min(1.0, sold_qty / py_qty)
                reversal_to_cogs = reversal * sold_ratio
                reversal_to_loss = reversal - reversal_to_cogs

            rows.append(
                ImpairmentRow(
                    material_code=code,
                    material_name=name,
                    category=category,
                    period_end=period_end.strftime("%Y-%m-%d"),
                    ending_qty=round(ending_qty, 4),
                    book_unit_cost=round(book_unit_cost, 4),
                    book_amount=round(ending_amount, 2),
                    aging=aging,
                    nrv_unit_price=round(nrv_unit, 4) if nrv_unit is not None else None,
                    nrv_source=nrv_src,
                    nrv_amount=round(nrv_amount, 2),
                    estimated_sell_cost=round(est_sell_cost, 2),
                    impairment_current=impairment_current,
                    impairment_opening=round(opening, 2),
                    impairment_reversal=round(reversal, 2),
                    impairment_provision=round(provision, 2),
                    net_impairment_change=round(provision - reversal, 2),
                    method=method,
                    note=(
                        recon_note
                        + ("；" if recon_note and nrv_unit is None else "")
                        + ("无期末后销售样本，按库龄比例兜底" if nrv_unit is None else "")
                    )
                    .strip("；")
                    .strip(),
                    reversal_to_cogs=round(reversal_to_cogs, 2),
                    reversal_to_loss=round(reversal_to_loss, 2),
                )
            )

        # 汇总
        # P0-5 修复 (2026-06-19): summary 金额一致性 — 旧版按
        #   bucket.le_90 * r.book_unit_cost
        # 算金额, 但 bucket.le_90 是已 scale 校准后的数量; 当 scale != 1 时,
        #   total_weight > ending_qty_total, 金额就会和 row.ending_amount 对不上.
        # 现改为按各 row 的 bucket 占比分摊 ending_amount:
        #   aging_xxx_amount = ending_amount * (bucket.xxx / total_in_row_buckets)
        # 这样各分段金额之和 = ending_amount (= book_amount), 与 row 一致.
        # 注: scale 仅用于库龄加权平均的小幅容差 (5% 内), summary 金额以账面 ending_qty 为准.
        def _row_bucket_amount(row, attr):
            total = (
                row.aging.le_90 + row.aging.age_91_180 + row.aging.age_181_365
                + row.aging.age_366_730 + row.aging.gt_730
            )
            if total <= 0 or row.book_amount <= 0:
                return 0.0
            return row.book_amount * (getattr(row.aging, attr) / total)

        summary = {
            "items": len(rows),
            "book_amount": round(sum(r.book_amount for r in rows), 2),
            "opening_impairment": round(sum(r.impairment_opening for r in rows), 2),
            "ending_impairment": round(sum(r.impairment_current for r in rows), 2),
            "current_provision": round(sum(r.impairment_provision for r in rows), 2),
            "current_reversal": round(sum(r.impairment_reversal for r in rows), 2),
            "net_change": round(sum(r.net_impairment_change for r in rows), 2),
            "aging_le_90": round(sum(_row_bucket_amount(r, "le_90") for r in rows), 2),
            "aging_91_180": round(sum(_row_bucket_amount(r, "age_91_180") for r in rows), 2),
            "aging_181_365": round(sum(_row_bucket_amount(r, "age_181_365") for r in rows), 2),
            "aging_366_730": round(sum(_row_bucket_amount(r, "age_366_730") for r in rows), 2),
            "aging_gt_730": round(sum(_row_bucket_amount(r, "gt_730") for r in rows), 2),
        }

        return ImpairmentResult(rows=rows, summary=summary)
