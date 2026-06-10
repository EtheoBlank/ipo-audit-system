"""Streamlit page for the Sales Ledger (销售清单整理) module.

Designed to be called from `frontend/app.py` via `show_sales_ledger()`.
The page walks the auditor through:
  1) 文档上传   — upload Word/PDF/Excel
  2) AI 合成    — DeepSeek builds the structured sales list
  3) 核对修改   — review the synthesized rows, fix anything that's off
  4) 收入分析   — gross margin / cut-off / volatility / industry benchmark
  5) 导出       — download a multi-sheet Excel workbook
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

from frontend.app import API_BASE_URL, api_request, get_projects


# ---------- helpers ------------------------------------------------------


def _projects_selectbox(label: str = "选择项目") -> Optional[dict[str, Any]]:
    projects = get_projects() or []
    if not projects:
        st.warning("⚠️ 请先在『项目管理』中创建一个项目。")
        return None
    options = {f"#{p['id']} {p['name']} ({p.get('company_name', '')})": p for p in projects}
    label_chosen = st.selectbox(label, list(options.keys()))
    return options.get(label_chosen)


def _format_df_for_editor(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    # Streamlit's data_editor doesn't handle datetime/date columns well unless
    # they're parsed — coerce here.
    for col in ("ship_date", "revenue_confirm_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df


# ---------- main entry point --------------------------------------------


def show_sales_ledger() -> None:
    st.markdown("## 📦 销售清单整理")
    st.caption(
        "上传散乱文档（合同 / 发票 / 发货单 / 报关单）→ AI 自动合成结构化销售清单"
        "→ 收入循环分析（毛利率、截止性、单价波动、收发存对账、同行业参考）"
    )

    project = _projects_selectbox()
    if not project:
        return

    project_id = project["id"]
    industry = project.get("industry") or ""
    st.info(
        f"当前项目：**{project['name']}** | 公司：{project.get('company_name', '')}"
        + (f" | 行业：{industry}" if industry else "")
    )

    tab_upload, tab_synth, tab_review, tab_analyse, tab_export = st.tabs(
        ["📤 文档上传", "🤖 AI 合成", "✏️ 核对修改", "💰 收入分析", "📥 导出"]
    )

    # --- Tab 1: 文档上传 -------------------------------------------------
    with tab_upload:
        st.subheader("上传销售相关文档")
        st.write(
            "支持 `.docx / .pdf / .xlsx`。"
            "如果没有结构化文档，可以提供一段文本或半结构化表格让 AI 抽取。"
        )

        files = st.file_uploader(
            "选择文档（可多选）",
            type=["docx", "pdf", "xlsx", "xls"],
            accept_multiple_files=True,
        )
        note = st.text_input("可选备注（例如：'2024年第一季度销售合同汇总'）", "")

        if st.button("📤 上传并解析", type="primary"):
            if not files:
                st.warning("请先选择文件。")
            else:
                ok = 0
                for f in files:
                    files_param = {
                        "file": (f.name, f.read(), f.type or "application/octet-stream")
                    }
                    params = {"note": note} if note else None
                    r = api_request(
                        "POST",
                        f"/api/sales-ledger/projects/{project_id}/sales-documents",
                        files=files_param,
                        params=params,
                    )
                    if r is not None:
                        ok += 1
                if ok:
                    st.success(f"✅ 成功上传 {ok} 份文档")
                    st.rerun()

        st.divider()
        st.subheader("已上传的文档")
        docs = api_request("GET", f"/api/sales-ledger/projects/{project_id}/sales-documents") or []
        if docs:
            df = pd.DataFrame(
                [
                    {
                        "ID": d["id"],
                        "文件名": d["filename"],
                        "类型": d["doc_type"],
                        "备注": d.get("note") or "",
                        "上传时间": d.get("uploaded_at", ""),
                    }
                    for d in docs
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption("(尚无文档)")

    # --- Tab 2: AI 合成 --------------------------------------------------
    with tab_synth:
        st.subheader("AI 合成销售清单")
        st.write(
            "调用 DeepSeek 把上一步上传的文档抽取为结构化销售记录，"
            "并存入数据库。已存在的（合同号+产品编号）会被更新。"
        )
        extra_hint = st.text_area(
            "可选提示词（追加到 system prompt）",
            "",
            placeholder="例如：'只关心 2024 年的出口订单'",
        )

        if st.button("🤖 开始合成", type="primary"):
            with st.spinner("DeepSeek 正在解析文档……"):
                payload = {"project_id": project_id, "document_ids": [], "extra_hint": extra_hint}
                result = api_request(
                    "POST",
                    f"/api/sales-ledger/projects/{project_id}/sales-records/synthesize",
                    json=payload,
                )
            if result is None:
                st.error("合成失败，请查看上方错误。")
            elif result.get("synthesized_count", 0) == 0:
                st.warning(
                    "⚠️ AI 没有抽取到任何销售记录。"
                    "请确认文档中包含销售明细（金额、发货时间、收入确认时间、"
                    "数量、单价、产品编号），或尝试上传更结构化的 Excel。"
                )
            else:
                st.success(f"✅ 已合成 {result['synthesized_count']} 条记录")
                st.session_state["sl_records"] = result["records"]
                st.rerun()

        st.divider()
        st.subheader("当前销售记录（数据库中）")
        records = api_request("GET", f"/api/sales-ledger/projects/{project_id}/sales-records") or []
        st.session_state["sl_records"] = records
        if records:
            df = _format_df_for_editor(records)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"共 {len(records)} 条")
        else:
            st.caption("(尚无销售记录，请先『开始合成』)")

    # --- Tab 3: 核对修改 -------------------------------------------------
    with tab_review:
        st.subheader("核对 / 修改销售记录")
        records = st.session_state.get("sl_records") or api_request(
            "GET", f"/api/sales-ledger/projects/{project_id}/sales-records"
        ) or []
        if not records:
            st.info("没有可核对的记录。请先在『AI 合成』生成记录。")
        else:
            df = _format_df_for_editor(records)
            keep_cols = [
                "id",
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
                "source",
                "is_verified",
            ]
            for c in keep_cols:
                if c not in df.columns:
                    df[c] = None
            edited = st.data_editor(
                df[keep_cols],
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "is_verified": st.column_config.CheckboxColumn("已核对"),
                    "ship_date": st.column_config.DateColumn("发货日期"),
                    "receipt_date": st.column_config.DateColumn("签收日期"),
                    "revenue_confirm_date": st.column_config.DateColumn("收入确认日期"),
                    "confirmation_status": st.column_config.SelectboxColumn(
                        "函证状态",
                        options=["未发函", "已发函", "已回函", "未回函", "作废"],
                    ),
                },
                key="sl_editor",
            )

            if st.button("💾 保存修改", type="primary"):
                changed = _diff_and_save(project_id, records, edited)
                if changed:
                    st.success(f"✅ 已保存 {changed} 条修改")
                    st.rerun()
                else:
                    st.info("没有检测到变化")

    # --- Tab 4: 收入分析 ------------------------------------------------
    with tab_analyse:
        st.subheader("收入循环分析")
        col1, col2 = st.columns(2)
        with col1:
            period_end = st.date_input("期末日期（截止性测试用）", value=date.today())
        with col2:
            window = st.number_input("截止性测试窗口（天）", min_value=1, max_value=60, value=10)
        price_vol = st.slider("单价波动报警阈值", 0.05, 0.80, 0.20, 0.05)
        run_bench = st.checkbox("生成同行业 AI 参考值（需要 DeepSeek）", value=False)
        use_industry = st.text_input("行业（用于行业参考）", value=industry)

        if st.button("📊 开始分析", type="primary"):
            payload = {
                "project_id": project_id,
                "period_end": period_end.isoformat(),
                "cut_off_window_days": int(window),
                "price_volatility_pct": float(price_vol),
                "run_industry_benchmark": bool(run_bench),
                "industry": use_industry,
            }
            with st.spinner("分析中……"):
                res = api_request(
                    "POST",
                    f"/api/sales-ledger/projects/{project_id}/revenue-analysis",
                    json=payload,
                )
            if res is None:
                st.error("分析失败，请检查后端日志。")
            else:
                st.session_state["sl_analysis"] = res
                st.success("✅ 分析完成")
                st.rerun()

        result = st.session_state.get("sl_analysis")
        if result:
            s = result.get("summary", {})
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("记录数", s.get("record_count", 0))
            m2.metric("总收入", f"{s.get('total_revenue', 0):,.2f}")
            m3.metric("总成本", f"{s.get('total_cost', 0):,.2f}")
            m4.metric("毛利率", f"{s.get('gross_margin', 0) * 100:.2f}%")

            with st.expander("👥 客户毛利率", expanded=True):
                st.dataframe(pd.DataFrame(result.get("by_customer", [])), use_container_width=True)
            with st.expander("📦 产品毛利率"):
                st.dataframe(pd.DataFrame(result.get("by_product", [])), use_container_width=True)
            with st.expander("📅 月度毛利率"):
                st.dataframe(pd.DataFrame(result.get("by_month", [])), use_container_width=True)
            with st.expander("🔀 客户×产品×月度"):
                st.dataframe(
                    pd.DataFrame(result.get("by_customer_product_month", [])),
                    use_container_width=True,
                )
            with st.expander("✉️ 函证覆盖率（按客户）"):
                cov = result.get("confirmation_coverage") or []
                if cov:
                    st.dataframe(pd.DataFrame(cov), use_container_width=True)
                    total_cov = sum(c.get("sent_amount", 0) for c in cov)
                    total_rev = sum(c.get("revenue", 0) for c in cov) or 1
                    st.metric("整体函证覆盖率", f"{total_cov / total_rev * 100:.2f}%")
                else:
                    st.caption("(尚无函证状态数据，请在核对环节把『函证状态』标为已发函/已回函)")
            with st.expander("⏱️ DSO 分客户（账期）"):
                st.dataframe(
                    pd.DataFrame(result.get("dso_by_customer", [])),
                    use_container_width=True,
                )
            with st.expander("↩️ 退换货 / 折扣 / 返利 对毛利影响"):
                st.dataframe(
                    pd.DataFrame(result.get("return_discount_impact", [])),
                    use_container_width=True,
                )
            with st.expander("🕒 收入确认时点差异（发货→签收）"):
                st.dataframe(
                    pd.DataFrame(result.get("recognition_timing_diff", [])),
                    use_container_width=True,
                )
            with st.expander("⚠️ 截止性测试（年末跨期）"):
                st.dataframe(
                    pd.DataFrame(result.get("cut_off_alerts", [])), use_container_width=True
                )
            with st.expander("📈 单价波动"):
                st.dataframe(
                    pd.DataFrame(result.get("price_volatility_alerts", [])),
                    use_container_width=True,
                )
            bench = result.get("industry_benchmark")
            if bench:
                with st.expander("🏭 同行业 AI 参考"):
                    st.warning(
                        "⚠️ 以下为 DeepSeek 给出的行业一般参考值，非权威数据，"
                        "**不可作为审计证据**。"
                    )
                    st.json(bench)

    # --- Tab 5: 导出 ----------------------------------------------------
    with tab_export:
        st.subheader("导出销售清单 + 收入分析")
        st.write("生成多 Sheet Excel 工作簿（销售清单、毛利率、异常、行业参考等）。")
        if st.button("📥 生成 Excel", type="primary"):
            with st.spinner("生成中……"):
                url = f"{API_BASE_URL}/api/sales-ledger/projects/{project_id}/export?run_analysis=true"
                r = requests.get(url, timeout=60)
                if r.status_code != 200:
                    st.error(f"导出失败：{r.status_code} {r.text[:300]}")
                else:
                    st.session_state["sl_xlsx"] = r.content
                    st.success("✅ 已生成，可点击下方下载")
        if st.session_state.get("sl_xlsx"):
            st.download_button(
                "⬇️ 下载 Excel",
                data=st.session_state["sl_xlsx"],
                file_name=f"sales_ledger_project_{project_id}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


# ---------- helpers (continuation) --------------------------------------


def _diff_and_save(
    project_id: int,
    original: list[dict[str, Any]],
    edited: pd.DataFrame,
) -> int:
    """Compare original API response with the edited DataFrame and PUT changes
    one-by-one. Returns the number of records updated."""
    if not original:
        return 0
    by_id = {r["id"]: r for r in original}
    changes = 0
    for _, row in edited.iterrows():
        rid = row.get("id")
        if not rid or rid not in by_id:
            continue
        before = by_id[rid]
        payload: dict[str, Any] = {}
        for col in (
            "contract_no",
            "customer_name",
            "product_code",
            "product_name",
            "invoice_no",
            "currency",
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
            "confirmation_status",
            "confirmation_ref",
            "confirmation_diff",
            "source",
            "is_verified",
        ):
            new = row.get(col)
            if _is_different(before.get(col), new, col):
                payload[col] = _coerce_for_api(new, col)
        for col in ("tax_rate", "tax_amount", "gross_amount"):
            new = row.get(col)
            if _is_different(before.get(col), new, col):
                payload[col] = _coerce_for_api(new, col)
        for col in ("ship_date", "receipt_date", "revenue_confirm_date"):
            new = row.get(col)
            if _is_different(before.get(col), new, col):
                payload[col] = new.isoformat() if hasattr(new, "isoformat") else new
        if payload:
            api_request("PUT", f"/api/sales-ledger/sales-records/{rid}", json=payload)
            changes += 1
    return changes


def _is_different(before: Any, after: Any, col: str) -> bool:
    if before is None and after in (None, ""):
        return False
    if isinstance(before, float) and isinstance(after, float):
        return abs(before - after) > 1e-6
    return str(before or "") != str(after or "")


def _coerce_for_api(value: Any, col: str):
    if value is None:
        return None
    if col in (
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
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    return value
