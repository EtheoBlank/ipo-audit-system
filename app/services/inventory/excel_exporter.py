"""Multi-sheet Excel exporter for inventory module.

Sheets:
  - 收发存明细       (InventoryMovement)
  - 盘点用表        (InventoryCountSheet) - 含留空的实盘列
  - 盘点计划        (InventoryCountPlan)  - 一行一字段
  - 已盘点情况      (back-filled counts)  - 含差异
  - 盘点率统计      (completion stats)
  - 库龄分析       (InventoryImpairment - 库龄部分)
  - 跌价测试       (InventoryImpairment - NRV / 跌价 / 转回)
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _g(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class InventoryExporter:
    @staticmethod
    def _movements_df(rows: Iterable[Any]) -> pd.DataFrame:
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "物料编码": _g(r, "material_code"),
                    "物料名称": _g(r, "material_name"),
                    "类别": _g(r, "category"),
                    "规格": _g(r, "spec"),
                    "单位": _g(r, "unit"),
                    "仓库": _g(r, "warehouse"),
                    "批次号": _g(r, "batch_no"),
                    "入库日期": _g(r, "inbound_date"),
                    "期初数量": _g(r, "opening_qty", 0),
                    "期初金额": _g(r, "opening_amount", 0),
                    "本期入库数量": _g(r, "inbound_qty", 0),
                    "本期入库金额": _g(r, "inbound_amount", 0),
                    "本期出库数量": _g(r, "outbound_qty", 0),
                    "本期出库金额": _g(r, "outbound_amount", 0),
                    "期末数量": _g(r, "ending_qty", 0),
                    "期末金额": _g(r, "ending_amount", 0),
                    "期末单价": _g(r, "unit_cost", 0),
                    "期末日期": _g(r, "period_end"),
                    "是否上年": "是" if _g(r, "is_prior_year") else "否",
                }
            )
        return pd.DataFrame(out)

    @staticmethod
    def _count_sheet_df(rows: Iterable[Any]) -> pd.DataFrame:
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "抽样层": _g(r, "sample_tier"),
                    "抽样原因": _g(r, "sample_reason"),
                    "排名": _g(r, "coverage_rank"),
                    "物料编码": _g(r, "material_code"),
                    "物料名称": _g(r, "material_name"),
                    "类别": _g(r, "category"),
                    "仓库": _g(r, "warehouse"),
                    "批次号": _g(r, "batch_no"),
                    "单位": _g(r, "unit"),
                    "账面数量": _g(r, "book_qty", 0),
                    "账面单价": _g(r, "book_unit_cost", 0),
                    "账面金额": _g(r, "book_amount", 0),
                    "实盘数量": _g(r, "counted_qty"),
                    "盘点人": _g(r, "counted_by"),
                    "盘点时间": _g(r, "counted_at"),
                    "备注": _g(r, "remark"),
                }
            )
        return pd.DataFrame(out)

    @staticmethod
    def _plan_df(plan: Any) -> pd.DataFrame:
        if plan is None:
            return pd.DataFrame()
        team_raw = _g(plan, "team", "[]")
        try:
            team = json.loads(team_raw) if isinstance(team_raw, str) else team_raw or []
        except json.JSONDecodeError:
            team = []
        team_text = "\n".join(
            f"{m.get('name', '')}({m.get('role', '')}){m.get('contact', '')}".strip("()")
            for m in team
        )

        rows = [
            ("计划标题", _g(plan, "title")),
            ("行业", _g(plan, "industry")),
            ("基准日", _g(plan, "period_end")),
            ("监盘开始日", _g(plan, "count_date_start")),
            ("监盘结束日", _g(plan, "count_date_end")),
            ("监盘目标", _g(plan, "objectives")),
            ("监盘范围", _g(plan, "scope")),
            ("监盘小组", team_text),
            ("监盘程序", _g(plan, "procedures")),
            ("特殊事项", _g(plan, "special_notes")),
            ("重大风险", _g(plan, "risks")),
        ]
        return pd.DataFrame(rows, columns=["项目", "内容"])

    @staticmethod
    def _completion_df(stats: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        overall = stats.get("overall") or {}
        overall_df = pd.DataFrame(
            [
                {
                    "总物料数": overall.get("total_items", 0),
                    "已盘物料数": overall.get("counted_items", 0),
                    "盘点率(数量)": f"{overall.get('items_rate', 0):.2%}",
                    "总账面金额": overall.get("total_amount", 0),
                    "已盘账面金额": overall.get("counted_amount", 0),
                    "盘点率(金额)": f"{overall.get('amount_rate', 0):.2%}",
                }
            ]
        )
        by_wh = stats.get("by_warehouse") or []
        by_wh_df = pd.DataFrame(by_wh) if by_wh else pd.DataFrame()
        diffs = stats.get("differences") or []
        diff_df = pd.DataFrame(diffs) if diffs else pd.DataFrame()
        return overall_df, by_wh_df, diff_df

    @staticmethod
    def _impairment_df(rows: Iterable[Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        aging_rows: list[dict[str, Any]] = []
        nrv_rows: list[dict[str, Any]] = []
        for r in rows:
            uc = float(_g(r, "book_unit_cost", 0) or 0)
            aging_rows.append(
                {
                    "物料编码": _g(r, "material_code"),
                    "物料名称": _g(r, "material_name"),
                    "类别": _g(r, "category"),
                    "期末数量": _g(r, "ending_qty", 0),
                    "账面单价": uc,
                    "账面金额": _g(r, "book_amount", 0),
                    "≤90天数量": _g(r, "age_le_90", 0),
                    "91-180天数量": _g(r, "age_91_180", 0),
                    "181-365天数量": _g(r, "age_181_365", 0),
                    "366-730天数量": _g(r, "age_366_730", 0),
                    ">730天数量": _g(r, "age_gt_730", 0),
                    "加权平均库龄(天)": _g(r, "weighted_avg_age", 0),
                }
            )
            nrv_rows.append(
                {
                    "物料编码": _g(r, "material_code"),
                    "物料名称": _g(r, "material_name"),
                    "期末数量": _g(r, "ending_qty", 0),
                    "账面单价": uc,
                    "账面金额": _g(r, "book_amount", 0),
                    "NRV单价": _g(r, "nrv_unit_price"),
                    "NRV来源": _g(r, "nrv_source"),
                    "NRV金额(扣销售费用后)": _g(r, "nrv_amount", 0),
                    "估计销售费用": _g(r, "estimated_sell_cost", 0),
                    "期末应保留跌价": _g(r, "impairment_current", 0),
                    "期初已计提跌价": _g(r, "impairment_opening", 0),
                    "本期转回": _g(r, "impairment_reversal", 0),
                    "  └ 已售出转销营业成本": _g(r, "reversal_to_cogs", 0),
                    "  └ 仍在库转回资产减值损失": _g(r, "reversal_to_loss", 0),
                    "本期新增计提": _g(r, "impairment_provision", 0),
                    "本期净变动": _g(r, "net_impairment_change", 0),
                    "方法": _g(r, "method"),
                    "备注": _g(r, "note"),
                }
            )
        return pd.DataFrame(aging_rows), pd.DataFrame(nrv_rows)

    # ---- public ---------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        movements: Optional[Iterable[Any]] = None,
        count_sheets: Optional[Iterable[Any]] = None,
        plan: Any = None,
        completion: Optional[dict[str, Any]] = None,
        impairments: Optional[Iterable[Any]] = None,
        summary: Optional[dict[str, Any]] = None,
    ) -> bytes:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            if movements is not None:
                df = cls._movements_df(movements)
                if not df.empty:
                    df.to_excel(w, sheet_name="收发存明细", index=False)

            if plan is not None:
                pdf = cls._plan_df(plan)
                if not pdf.empty:
                    pdf.to_excel(w, sheet_name="盘点计划", index=False)

            if count_sheets is not None:
                csdf = cls._count_sheet_df(count_sheets)
                if not csdf.empty:
                    csdf.to_excel(w, sheet_name="盘点用表", index=False)

                # 已盘点情况 = 仅显示 counted_qty 非空
                done_df = csdf[csdf["实盘数量"].notna()] if not csdf.empty else csdf
                if not done_df.empty:
                    done_df.to_excel(w, sheet_name="已盘点情况", index=False)

            if completion:
                overall_df, by_wh_df, diff_df = cls._completion_df(completion)
                # 把三张子表纵向拼到同一个 sheet，便于一屏看
                start = 0
                overall_df.to_excel(w, sheet_name="盘点率统计", index=False, startrow=start)
                start += len(overall_df) + 2
                if not by_wh_df.empty:
                    pd.DataFrame([{"--": "按仓库"}]).to_excel(
                        w, sheet_name="盘点率统计", index=False, startrow=start, header=False
                    )
                    start += 1
                    by_wh_df.to_excel(w, sheet_name="盘点率统计", index=False, startrow=start)
                    start += len(by_wh_df) + 2
                if not diff_df.empty:
                    pd.DataFrame([{"--": "盘盈/盘亏明细"}]).to_excel(
                        w, sheet_name="盘点率统计", index=False, startrow=start, header=False
                    )
                    start += 1
                    diff_df.to_excel(w, sheet_name="盘点率统计", index=False, startrow=start)

            if impairments is not None:
                aging_df, nrv_df = cls._impairment_df(impairments)
                if not aging_df.empty:
                    aging_df.to_excel(w, sheet_name="库龄分析", index=False)
                if not nrv_df.empty:
                    nrv_df.to_excel(w, sheet_name="跌价测试", index=False)

            if summary:
                pd.DataFrame([summary]).to_excel(w, sheet_name="跌价汇总", index=False)

        buf.seek(0)
        return buf.getvalue()
