"""Excel exporter for the sales-ledger module.

Produces a single workbook with multiple sheets:
  - 销售清单          (raw records)
  - 客户毛利率         (summary by customer)
  - 产品毛利率         (summary by product)
  - 月度毛利率         (monthly trend)
  - 客户×产品×月度     (3-D cross pivot)
  - 截止性测试         (cut-off alerts)
  - 单价波动           (price volatility)
  - 收发存对账         (inventory recon)
  - 行业参考           (industry benchmark, when available)
"""

from __future__ import annotations

import io
import logging
from typing import Any, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class SalesLedgerExporter:
    @staticmethod
    def _records_df(records: Iterable[Any]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for r in records:
            if isinstance(r, dict):
                rows.append(r)
                continue
            rows.append(
                {
                    "合同号": getattr(r, "contract_no", ""),
                    "客户": getattr(r, "customer_name", ""),
                    "产品编号": getattr(r, "product_code", ""),
                    "产品名称": getattr(r, "product_name", ""),
                    "发票号": getattr(r, "invoice_no", ""),
                    "币种": getattr(r, "currency", "CNY"),
                    "税率": getattr(r, "tax_rate", 0),
                    "税额": getattr(r, "tax_amount", 0),
                    "价税合计": getattr(r, "gross_amount", 0),
                    "数量": getattr(r, "quantity", 0),
                    "不含税单价": getattr(r, "unit_price", 0),
                    "不含税收入": getattr(r, "revenue_amount", 0),
                    "成本": getattr(r, "cost_amount", 0),
                    "运费": getattr(r, "shipping_fee", 0),
                    "报关费": getattr(r, "customs_fee", 0),
                    "其他直接费用": getattr(r, "other_direct_fee", 0),
                    "退货金额": getattr(r, "return_amount", 0),
                    "折扣折让": getattr(r, "discount_amount", 0),
                    "销售返利": getattr(r, "rebate_amount", 0),
                    "发货日期": getattr(r, "ship_date", ""),
                    "签收日期": getattr(r, "receipt_date", ""),
                    "收入确认日期": getattr(r, "revenue_confirm_date", ""),
                    "函证状态": getattr(r, "confirmation_status", "未发函"),
                    "函证编号": getattr(r, "confirmation_ref", ""),
                    "回函差异": getattr(r, "confirmation_diff", 0),
                    "来源": getattr(r, "source", ""),
                    "已核对": bool(getattr(r, "is_verified", False)),
                }
            )
        return pd.DataFrame(rows)

    @classmethod
    def build(
        cls,
        records: Iterable[Any],
        analysis: Optional[dict[str, Any]] = None,
    ) -> bytes:
        """Return an XLSX file as bytes ready to be streamed to the client."""
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            # 1. raw records
            df = cls._records_df(records)
            df.to_excel(writer, sheet_name="销售清单", index=False)

            if analysis:
                # 2. summary
                summary = analysis.get("summary") or {}
                if summary:
                    pd.DataFrame([summary]).to_excel(writer, sheet_name="总览", index=False)

                # 3-5. pivots
                for key, sheet in (
                    ("by_customer", "客户毛利率"),
                    ("by_product", "产品毛利率"),
                    ("by_month", "月度毛利率"),
                    ("by_customer_product_month", "客户×产品×月度"),
                ):
                    rows = analysis.get(key) or []
                    if rows:
                        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet, index=False)

                # 6-7. alerts
                for key, sheet in (
                    ("cut_off_alerts", "截止性测试"),
                    ("price_volatility_alerts", "单价波动"),
                ):
                    rows = analysis.get(key) or []
                    if rows:
                        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet, index=False)

                # 8-11. new procedures
                for key, sheet in (
                    ("confirmation_coverage", "函证覆盖率"),
                    ("dso_by_customer", "DSO分客户"),
                    ("return_discount_impact", "退折返影响"),
                    ("recognition_timing_diff", "收入确认时点差异"),
                ):
                    rows = analysis.get(key) or []
                    if rows:
                        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet, index=False)

                # 8. inventory recon
                recon = analysis.get("inventory_recon") or []
                if recon:
                    pd.DataFrame(recon).to_excel(writer, sheet_name="收发存对账", index=False)

                # 9. industry benchmark
                bench = analysis.get("industry_benchmark")
                if bench:
                    pd.DataFrame(_flatten_benchmark(bench)).to_excel(
                        writer, sheet_name="行业参考", index=False
                    )

        buf.seek(0)
        return buf.getvalue()


def _flatten_benchmark(bench: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the nested industry-benchmark JSON into a flat two-column sheet."""
    rows: list[dict[str, Any]] = []
    if not bench:
        return rows
    if bench.get("error"):
        rows.append({"项目": "错误", "内容": bench["error"]})
        return rows
    rows.append({"项目": "行业", "内容": bench.get("industry", "")})
    for metric, vals in (bench.get("metrics") or {}).items():
        if not isinstance(vals, dict):
            continue
        for k, v in vals.items():
            rows.append({"项目": f"{metric}.{k}", "内容": v})
    for note in bench.get("notes") or []:
        rows.append({"项目": "说明", "内容": note})
    if bench.get("disclaimer"):
        rows.append({"项目": "免责声明", "内容": bench["disclaimer"]})
    return rows
