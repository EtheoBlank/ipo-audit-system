"""Revenue cycle analyzer.

Builds on top of a list of SalesRecord-shaped objects (dicts or ORM rows) and
produces:
  - gross-margin pivots by customer / product / month
  - cut-off test around a period end date
  - unit-price volatility alerts
  - inventory-vs-sales reconciliation (if the caller provides 收发存 outflows)
  - industry benchmark via DeepSeek (returns ranges only — clearly labelled)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

import pandas as pd

from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)


INDUSTRY_SYSTEM = """你是行业研究助理。根据用户给定的公司行业，给出该行业上市公司常见的关键财务指标
参考区间（毛利率区间、前五大客户集中度、月度收入波动幅度等）。

严格要求：仅以 JSON 输出，结构如下：
{
  "industry": "<行业名>",
  "metrics": {
    "gross_margin": {"low": 0.10, "high": 0.35, "median": 0.22, "unit": "ratio"},
    "top5_customer_concentration": {"low": 0.20, "high": 0.60, "median": 0.40, "unit": "ratio"},
    "monthly_revenue_volatility": {"low": 0.05, "high": 0.30, "median": 0.15, "unit": "ratio"}
  },
  "notes": ["<简短说明1>", "<简短说明2>"],
  "disclaimer": "以上为 AI 根据行业一般情况的参考值，非权威数据，不可作为审计证据。"
}
如果没有该行业信息，metrics 中相应字段填 null。
"""


@dataclass
class AnalysisResult:
    """In-memory result for a single revenue analysis run."""

    summary: dict[str, Any]
    by_customer: list[dict[str, Any]]
    by_product: list[dict[str, Any]]
    by_month: list[dict[str, Any]]
    by_customer_product_month: list[dict[str, Any]]
    cut_off_alerts: list[dict[str, Any]]
    price_volatility_alerts: list[dict[str, Any]]
    inventory_recon: list[dict[str, Any]]
    # New (incremental patch):
    confirmation_coverage: list[dict[str, Any]] = field(default_factory=list)
    dso_by_customer: list[dict[str, Any]] = field(default_factory=list)
    return_discount_impact: list[dict[str, Any]] = field(default_factory=list)
    recognition_timing_diff: list[dict[str, Any]] = field(default_factory=list)
    industry_benchmark: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "by_customer": self.by_customer,
            "by_product": self.by_product,
            "by_month": self.by_month,
            "by_customer_product_month": self.by_customer_product_month,
            "cut_off_alerts": self.cut_off_alerts,
            "price_volatility_alerts": self.price_volatility_alerts,
            "inventory_recon": self.inventory_recon,
            "confirmation_coverage": self.confirmation_coverage,
            "dso_by_customer": self.dso_by_customer,
            "return_discount_impact": self.return_discount_impact,
            "recognition_timing_diff": self.recognition_timing_diff,
            "industry_benchmark": self.industry_benchmark,
        }


class RevenueAnalyzer:
    def __init__(
        self,
        records: Iterable[Any],
        client: Optional[DeepSeekClient] = None,
        industry: str = "",
    ):
        self.df = self._to_dataframe(records)
        self.client = client
        self.industry = industry

    # --- public API -----------------------------------------------------

    def run(
        self,
        *,
        period_end: Optional[date] = None,
        cut_off_window_days: int = 10,
        price_volatility_pct: float = 0.20,
        inventory_outs: Optional[pd.DataFrame] = None,
        run_industry_benchmark: bool = False,
    ) -> AnalysisResult:
        period_end = period_end or date.today()
        summary = self._summary()
        by_customer = self._pivot("customer_name")
        by_product = self._pivot("product_code", name_col="product_code")
        by_month = self._pivot_month()
        by_cpm = self._pivot_customer_product_month()
        cut_off = self._cut_off_test(period_end, cut_off_window_days)
        price_alerts = self._price_volatility(price_volatility_pct)
        recon = (
            self._inventory_recon(inventory_outs)
            if inventory_outs is not None and not inventory_outs.empty
            else []
        )

        result = AnalysisResult(
            summary=summary,
            by_customer=by_customer,
            by_product=by_product,
            by_month=by_month,
            by_customer_product_month=by_cpm,
            cut_off_alerts=cut_off,
            price_volatility_alerts=price_alerts,
            inventory_recon=recon,
            confirmation_coverage=self._confirmation_coverage(),
            dso_by_customer=self._dso_by_customer(),
            return_discount_impact=self._return_discount_impact(),
            recognition_timing_diff=self._recognition_timing_diff(),
        )

        if run_industry_benchmark and self.client and self.industry:
            try:
                # Note: this is sync from the caller's POV; DeepSeekClient is async,
                # so we expose an `arun` variant too. The synchronous run() leaves
                # industry_benchmark as None and the caller can populate it.
                pass
            except DeepSeekError as exc:  # pragma: no cover
                logger.warning("Industry benchmark failed: %s", exc)

        return result

    async def arun(
        self,
        *,
        period_end: Optional[date] = None,
        cut_off_window_days: int = 10,
        price_volatility_pct: float = 0.20,
        inventory_outs: Optional[pd.DataFrame] = None,
        run_industry_benchmark: bool = False,
    ) -> AnalysisResult:
        result = self.run(
            period_end=period_end,
            cut_off_window_days=cut_off_window_days,
            price_volatility_pct=price_volatility_pct,
            inventory_outs=inventory_outs,
        )
        if (
            run_industry_benchmark
            and self.client is not None
            and self.client.is_configured
            and self.industry
        ):
            result.industry_benchmark = await self._industry_benchmark()
        return result

    # --- DataFrame helpers ---------------------------------------------

    @staticmethod
    def _to_dataframe(records: Iterable[Any]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for r in records:
            if isinstance(r, dict):
                rows.append(r)
            else:
                # ORM row — read attributes
                rows.append(
                    {
                        "id": getattr(r, "id", None),
                        "document_id": getattr(r, "document_id", None),
                        "contract_no": getattr(r, "contract_no", ""),
                        "customer_name": getattr(r, "customer_name", ""),
                        "product_code": getattr(r, "product_code", ""),
                        "product_name": getattr(r, "product_name", ""),
                        "invoice_no": getattr(r, "invoice_no", ""),
                        "currency": getattr(r, "currency", "CNY"),
                        "tax_rate": getattr(r, "tax_rate", 0),
                        "tax_amount": getattr(r, "tax_amount", 0),
                        "gross_amount": getattr(r, "gross_amount", 0),
                        "quantity": getattr(r, "quantity", 0),
                        "unit_price": getattr(r, "unit_price", 0),
                        "revenue_amount": getattr(r, "revenue_amount", 0),
                        "cost_amount": getattr(r, "cost_amount", 0),
                        "shipping_fee": getattr(r, "shipping_fee", 0),
                        "customs_fee": getattr(r, "customs_fee", 0),
                        "other_direct_fee": getattr(r, "other_direct_fee", 0),
                        "return_amount": getattr(r, "return_amount", 0),
                        "discount_amount": getattr(r, "discount_amount", 0),
                        "rebate_amount": getattr(r, "rebate_amount", 0),
                        "ship_date": getattr(r, "ship_date", None),
                        "receipt_date": getattr(r, "receipt_date", None),
                        "revenue_confirm_date": getattr(
                            r, "revenue_confirm_date", None
                        ),
                        "confirmation_status": getattr(
                            r, "confirmation_status", "未发函"
                        ),
                        "confirmation_ref": getattr(r, "confirmation_ref", ""),
                        "confirmation_diff": getattr(r, "confirmation_diff", 0),
                        "source": getattr(r, "source", ""),
                        "confidence": getattr(r, "confidence", 1.0),
                    }
                )
        if not rows:
            return pd.DataFrame(
                columns=[
                    "contract_no",
                    "customer_name",
                    "product_code",
                    "product_name",
                    "invoice_no",
                    "currency",
                    "tax_rate",
                    "tax_amount",
                    "gross_amount",
                    "quantity",
                    "unit_price",
                    "revenue_amount",
                    "cost_amount",
                    "shipping_fee",
                    "customs_fee",
                    "other_direct_fee",
                    "return_amount",
                    "discount_amount",
                    "rebate_amount",
                    "ship_date",
                    "receipt_date",
                    "revenue_confirm_date",
                    "confirmation_status",
                    "confirmation_ref",
                    "confirmation_diff",
                ]
            )
        df = pd.DataFrame(rows)
        for col in (
            "quantity",
            "unit_price",
            "revenue_amount",
            "cost_amount",
            "shipping_fee",
            "customs_fee",
            "other_direct_fee",
            "return_amount",
            "discount_amount",
            "rebate_amount",
            "tax_rate",
            "tax_amount",
            "gross_amount",
            "confirmation_diff",
        ):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        for col in ("ship_date", "receipt_date", "revenue_confirm_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        if "confirmation_status" in df.columns:
            df["confirmation_status"] = df["confirmation_status"].fillna("未发函")
        return df

    # --- per-dimension analysis ----------------------------------------

    def _summary(self) -> dict[str, Any]:
        if self.df.empty:
            return {
                "record_count": 0,
                "total_revenue": 0.0,
                "total_cost": 0.0,
                "total_direct_fee": 0.0,
                "gross_profit": 0.0,
                "gross_margin": 0.0,
            }
        revenue = float(self.df["revenue_amount"].sum())
        cost = float(self.df["cost_amount"].sum())
        direct = float(
            self.df[["shipping_fee", "customs_fee", "other_direct_fee"]].sum().sum()
        )
        profit = revenue - cost - direct
        return {
            "record_count": int(len(self.df)),
            "total_revenue": round(revenue, 2),
            "total_cost": round(cost, 2),
            "total_direct_fee": round(direct, 2),
            "gross_profit": round(profit, 2),
            "gross_margin": round(profit / revenue, 4) if revenue else 0.0,
        }

    def _pivot(self, dim: str, name_col: Optional[str] = None) -> list[dict[str, Any]]:
        if self.df.empty:
            return []
        name_col = name_col or dim
        grouped = (
            self.df.groupby(dim, dropna=False)
            .agg(
                revenue=("revenue_amount", "sum"),
                cost=("cost_amount", "sum"),
                direct_fee=(
                    "shipping_fee",
                    "sum",
                ),  # placeholder, will be summed properly below
                quantity=("quantity", "sum"),
            )
            .reset_index()
        )
        # sum the direct-fee columns explicitly
        direct = (
            self.df.groupby(dim)[["shipping_fee", "customs_fee", "other_direct_fee"]]
            .sum()
            .sum(axis=1)
            .reset_index(name="direct_fee")
        )
        grouped = grouped.drop(columns=["direct_fee"]).merge(direct, on=dim, how="left")
        grouped["profit"] = grouped["revenue"] - grouped["cost"] - grouped["direct_fee"]
        grouped["gross_margin"] = grouped.apply(
            lambda r: (r["profit"] / r["revenue"]) if r["revenue"] else 0.0,
            axis=1,
        )
        grouped["revenue_pct"] = grouped["revenue"] / grouped["revenue"].sum()
        grouped = grouped.sort_values("revenue", ascending=False)
        grouped.columns = [name_col if c == dim else c for c in grouped.columns]
        return grouped.round(4).to_dict(orient="records")

    def _pivot_month(self) -> list[dict[str, Any]]:
        if self.df.empty or self.df["revenue_confirm_date"].isna().all():
            return []
        df = self.df.dropna(subset=["revenue_confirm_date"]).copy()
        df["month"] = df["revenue_confirm_date"].dt.to_period("M").astype(str)
        grouped = (
            df.groupby("month")
            .agg(
                revenue=("revenue_amount", "sum"),
                cost=("cost_amount", "sum"),
                quantity=("quantity", "sum"),
            )
            .reset_index()
        )
        direct = (
            df.groupby("month")[["shipping_fee", "customs_fee", "other_direct_fee"]]
            .sum()
            .sum(axis=1)
            .reset_index(name="direct_fee")
        )
        grouped = grouped.merge(direct, on="month", how="left")
        grouped["profit"] = grouped["revenue"] - grouped["cost"] - grouped["direct_fee"]
        grouped["gross_margin"] = grouped.apply(
            lambda r: (r["profit"] / r["revenue"]) if r["revenue"] else 0.0,
            axis=1,
        )
        return grouped.round(4).to_dict(orient="records")

    def _pivot_customer_product_month(self) -> list[dict[str, Any]]:
        if self.df.empty or self.df["revenue_confirm_date"].isna().all():
            return []
        df = self.df.dropna(subset=["revenue_confirm_date"]).copy()
        df["month"] = df["revenue_confirm_date"].dt.to_period("M").astype(str)
        grouped = (
            df.groupby(["customer_name", "product_code", "product_name", "month"])
            .agg(
                revenue=("revenue_amount", "sum"),
                cost=("cost_amount", "sum"),
                quantity=("quantity", "sum"),
            )
            .reset_index()
        )
        grouped["gross_margin"] = grouped.apply(
            lambda r: ((r["revenue"] - r["cost"]) / r["revenue"])
            if r["revenue"]
            else 0.0,
            axis=1,
        )
        return grouped.round(4).to_dict(orient="records")

    # --- alerts ---------------------------------------------------------

    def _cut_off_test(
        self, period_end: date, window_days: int
    ) -> list[dict[str, Any]]:
        if self.df.empty:
            return []
        alerts: list[dict[str, Any]] = []
        end = pd.Timestamp(period_end)
        lower = end - pd.Timedelta(days=window_days)
        upper = end + pd.Timedelta(days=window_days)
        df = self.df.dropna(subset=["ship_date", "revenue_confirm_date"]).copy()
        if df.empty:
            return alerts
        df["delta_days"] = (df["revenue_confirm_date"] - df["ship_date"]).dt.days
        suspicious = df[
            (df["ship_date"].between(lower, upper))
            | (df["revenue_confirm_date"].between(lower, upper))
        ]
        for _, row in suspicious.iterrows():
            alerts.append(
                {
                    "contract_no": row.get("contract_no", ""),
                    "customer_name": row.get("customer_name", ""),
                    "product_code": row.get("product_code", ""),
                    "ship_date": _fmt(row.get("ship_date")),
                    "revenue_confirm_date": _fmt(row.get("revenue_confirm_date")),
                    "delta_days": int(row.get("delta_days", 0))
                    if not pd.isna(row.get("delta_days"))
                    else None,
                    "revenue_amount": float(row.get("revenue_amount", 0) or 0),
                    "reason": (
                        "跨期风险：发货/确认日期落在年末 ± window 天内，"
                        "需核实是否属于正确期间"
                    ),
                }
            )
        return alerts

    def _price_volatility(self, threshold_pct: float) -> list[dict[str, Any]]:
        if self.df.empty:
            return []
        grouped = (
            self.df.groupby(["product_code", "customer_name"])
            .agg(
                min_price=("unit_price", "min"),
                max_price=("unit_price", "max"),
                avg_price=("unit_price", "mean"),
                records=("unit_price", "count"),
            )
            .reset_index()
        )
        grouped = grouped[grouped["records"] >= 2]
        if grouped.empty:
            return []
        grouped["spread_pct"] = grouped.apply(
            lambda r: (
                (r["max_price"] - r["min_price"]) / r["avg_price"]
                if r["avg_price"]
                else 0
            ),
            axis=1,
        )
        alerts = grouped[grouped["spread_pct"] > threshold_pct]
        return alerts.round(4).to_dict(orient="records")

    def _inventory_recon(
        self, inventory_outs: pd.DataFrame
    ) -> list[dict[str, Any]]:
        """Compare sales quantity (grouped by product_code + month) with the
        user's 收发存 outflows. Returns a row per (product, month) where the
        delta is non-zero."""
        if self.df.empty or inventory_outs is None or inventory_outs.empty:
            return []
        sales = (
            self.df.dropna(subset=["revenue_confirm_date"])
            .assign(
                month=lambda d: d["revenue_confirm_date"].dt.to_period("M").astype(str)
            )
            .groupby(["product_code", "month"], as_index=False)["quantity"].sum()
            .rename(columns={"quantity": "sales_qty"})
        )
        outs = inventory_outs.copy()
        # Try to be liberal about column names
        rename_map = {}
        for col in outs.columns:
            lc = str(col).strip().lower()
            if "产品" in str(col) or "product" in lc or "编码" in str(col):
                rename_map[col] = "product_code"
            elif "期间" in str(col) or "月份" in str(col) or "month" in lc:
                rename_map[col] = "month"
            elif "发出" in str(col) or "出库" in str(col) or "qty" in lc or "数量" in str(col):
                rename_map[col] = "out_qty"
        outs = outs.rename(columns=rename_map)
        if "out_qty" not in outs.columns or "product_code" not in outs.columns:
            return [{
                "warning": "收发存文件缺少必要列（产品编号/发出数量/期间）",
                "found_columns": list(inventory_outs.columns),
            }]
        outs["month"] = outs["month"].astype(str)
        merged = sales.merge(outs, on=["product_code", "month"], how="outer")
        merged[["sales_qty", "out_qty"]] = merged[["sales_qty", "out_qty"]].fillna(0)
        merged["delta_qty"] = merged["sales_qty"] - merged["out_qty"]
        merged = merged[merged["delta_qty"].abs() > 0.0001]
        return merged.round(2).to_dict(orient="records")

    # --- new (incremental patch) procedures ----------------------------

    def _confirmation_coverage(self) -> list[dict[str, Any]]:
        """Per-customer confirmation coverage ratio.

        Returns one row per customer with: revenue_amount, sent_amount, replied_amount,
        coverage_pct (sent/revenue), reply_pct (replied/sent), open_diff_amount
        (sum of confirmation_diff where status is 已回函).
        Useful for designing the AR-confirmation sample and arguing coverage > 80%.
        """
        if self.df.empty:
            return []
        df = self.df.copy()
        df["status"] = df.get("confirmation_status", "未发函").fillna("未发函")
        df["is_sent"] = df["status"].isin(["已发函", "已回函", "未回函", "作废"])
        df["is_replied"] = df["status"] == "已回函"
        grouped = (
            df.groupby("customer_name", dropna=False)
            .agg(
                revenue=("revenue_amount", "sum"),
                sent_amount=("revenue_amount", lambda s: s[df.loc[s.index, "is_sent"]].sum()),
                replied_amount=("revenue_amount", lambda s: s[df.loc[s.index, "is_replied"]].sum()),
                open_diff=("confirmation_diff", lambda s: s[df.loc[s.index, "is_replied"]].sum()),
            )
            .reset_index()
        )
        grouped["coverage_pct"] = grouped.apply(
            lambda r: (r["sent_amount"] / r["revenue"]) if r["revenue"] else 0.0,
            axis=1,
        )
        grouped["reply_pct"] = grouped.apply(
            lambda r: (r["replied_amount"] / r["sent_amount"]) if r["sent_amount"] else 0.0,
            axis=1,
        )
        grouped = grouped.sort_values("revenue", ascending=False)
        return grouped.round(4).to_dict(orient="records")

    def _dso_by_customer(self) -> list[dict[str, Any]]:
        """Days Sales Outstanding by customer.

        Heuristic: DSO ≈ (sign_date − ship_date).days, median per customer.
        Only meaningful when both ship_date and revenue_confirm_date are present.
        """
        if self.df.empty:
            return []
        df = self.df.dropna(subset=["ship_date", "revenue_confirm_date"]).copy()
        if df.empty:
            return []
        df["days_to_confirm"] = (df["revenue_confirm_date"] - df["ship_date"]).dt.days
        grouped = (
            df.groupby("customer_name")
            .agg(
                records=("days_to_confirm", "count"),
                median_days=("days_to_confirm", "median"),
                mean_days=("days_to_confirm", "mean"),
                max_days=("days_to_confirm", "max"),
                revenue=("revenue_amount", "sum"),
            )
            .reset_index()
        )
        grouped = grouped.sort_values("median_days", ascending=False)
        return grouped.round(2).to_dict(orient="records")

    def _return_discount_impact(self) -> list[dict[str, Any]]:
        """Monthly impact of returns / discounts / rebates on gross margin.

        Surfaces: return_amount, discount_amount, rebate_amount vs revenue, and
        the implied gross-margin erosion.
        """
        if self.df.empty or self.df["revenue_confirm_date"].isna().all():
            return []
        df = self.df.dropna(subset=["revenue_confirm_date"]).copy()
        df["month"] = df["revenue_confirm_date"].dt.to_period("M").astype(str)
        grouped = (
            df.groupby("month")
            .agg(
                revenue=("revenue_amount", "sum"),
                cost=("cost_amount", "sum"),
                return_amount=("return_amount", "sum"),
                discount_amount=("discount_amount", "sum"),
                rebate_amount=("rebate_amount", "sum"),
            )
            .reset_index()
        )
        grouped["net_revenue"] = (
            grouped["revenue"]
            - grouped["return_amount"]
            - grouped["discount_amount"]
            - grouped["rebate_amount"]
        )
        grouped["gross_profit"] = grouped["net_revenue"] - grouped["cost"]
        grouped["gross_margin"] = grouped.apply(
            lambda r: (r["gross_profit"] / r["net_revenue"]) if r["net_revenue"] else 0.0,
            axis=1,
        )
        grouped["adjustment_ratio"] = grouped.apply(
            lambda r: (
                (r["return_amount"] + r["discount_amount"] + r["rebate_amount"])
                / r["revenue"]
                if r["revenue"]
                else 0.0
            ),
            axis=1,
        )
        return grouped.round(4).to_dict(orient="records")

    def _recognition_timing_diff(self) -> list[dict[str, Any]]:
        """Flag rows where ship_date and receipt_date diverge by more than
        ``window_days`` — a soft signal that the revenue recognition point
        may not match the control-transfer evidence (IFRS 15 / ASC 606).
        """
        if self.df.empty:
            return []
        df = self.df.dropna(subset=["ship_date", "receipt_date"]).copy()
        if df.empty:
            return []
        df["ship_to_receipt_days"] = (df["receipt_date"] - df["ship_date"]).dt.days
        # Suspicious when the gap is unusually long or negative (impossible)
        df = df[(df["ship_to_receipt_days"] < 0) | (df["ship_to_receipt_days"] > 30)]
        if df.empty:
            return []
        df["confirmation_lag_days"] = df.apply(
            lambda r: (
                (r["revenue_confirm_date"] - r["receipt_date"]).days
                if pd.notna(r.get("revenue_confirm_date"))
                else None
            ),
            axis=1,
        )
        cols = [
            "contract_no",
            "customer_name",
            "product_code",
            "ship_date",
            "receipt_date",
            "revenue_confirm_date",
            "ship_to_receipt_days",
            "confirmation_lag_days",
            "revenue_amount",
        ]
        existing = [c for c in cols if c in df.columns]
        df = df[existing].copy()
        for c in ("ship_date", "receipt_date", "revenue_confirm_date"):
            if c in df.columns:
                df[c] = df[c].apply(_fmt)
        return df.to_dict(orient="records")

    # --- industry benchmark --------------------------------------------

    async def _industry_benchmark(self) -> dict[str, Any]:
        if self.client is None or not self.client.is_configured:
            return {"error": "DEEPSEEK_API_KEY 未配置，跳过行业对比"}
        user_msg = f"行业：{self.industry}\n请给出该行业的常见财务指标参考区间。"
        try:
            data = await self.client.chat_json(
                system=INDUSTRY_SYSTEM, user=user_msg
            )
        except DeepSeekError as exc:
            return {"error": f"行业对比失败: {exc}"}
        data.setdefault("disclaimer", "AI 参考值，非权威，不可作为审计证据")
        return data


def _fmt(ts: Any) -> str:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return ""
    if isinstance(ts, pd.Timestamp):
        return ts.strftime("%Y-%m-%d")
    return str(ts)
