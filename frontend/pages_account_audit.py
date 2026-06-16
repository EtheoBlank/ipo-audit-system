"""长期资产发生额审定页面 (Pack A — 用户特别要求)."""

from __future__ import annotations

import io
import re as _re_aa
from datetime import date
from typing import Any, Dict

import pandas as pd
import streamlit as st

from frontend._http import api_request
from frontend._components.project_picker import pick_project
from frontend._components.data_grid import edit_df as _edit_df


def _api(method: str, endpoint: str, *, expect_bytes: bool = False, **kwargs):
    """薄封装 — 复用共享 _http.api_request."""
    return api_request(method, endpoint, expect_bytes=expect_bytes, timeout=60, **kwargs)


def _tab_scope(project_id: int) -> None:
    st.markdown("### 📋 长期资产科目范围")
    st.caption(
        "默认前缀已内置 (固定资产/在建工程/无形资产/长投/商誉/使用权资产等)。"
        "如需追加 (include) 或排除 (exclude) 特定科目, 在下面操作。"
    )

    res = _api("GET", f"/api/account-audit/projects/{project_id}/effective-prefixes")
    if not res:
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("默认前缀数", len(res.get("default_prefixes", [])))
    c2.metric("项目追加", len(res.get("project_includes", [])))
    c3.metric("项目排除", len(res.get("project_excludes", [])))

    with st.expander("查看实际生效的科目前缀清单", expanded=False):
        eff = res.get("effective_prefixes", [])
        st.write("、".join(eff) if eff else "(空)")

    with st.expander("➕ 追加 / 排除", expanded=False):
        with st.form("scope_form"):
            cc = st.columns(3)
            prefix = cc[0].text_input("科目前缀 (例如 6602)")
            action = cc[1].selectbox("动作", ["include", "exclude"])
            reason = cc[2].text_input("原因")
            ok = st.form_submit_button("提交", type="primary")
        if ok and prefix:
            r = _api(
                "POST",
                f"/api/account-audit/projects/{project_id}/scope-overrides",
                json={"account_prefix": prefix, "action": action, "reason": reason or None},
            )
            if r:
                st.success(f"已 {action} {prefix}")
                st.rerun()


def _tab_initialize(project_id: int) -> None:
    st.markdown("### 🔄 从序时账初始化审定记录")
    st.caption(
        "把序时账里命中长期资产前缀的凭证逐笔抽出来, audited_amount 默认 = book_amount, "
        "等待审计师录入审定数。已审定/争议/跳过的记录会被保留, 仅 pending 行可替换。"
    )

    cols = st.columns(3)
    period_end = cols[0].text_input("期末日期 YYYY-MM-DD", value=str(date.today()))
    replace_pending = cols[1].checkbox("替换 pending 行", value=True)
    cols[2].markdown("&nbsp;")

    if st.button("🚀 立即初始化", type="primary"):
        # P0: 严格日期校验 (YYYY-MM-DD 格式)
        if not period_end or not _re_aa.match(r'^\d{4}-\d{2}-\d{2}$', period_end):
            st.error("请输入 YYYY-MM-DD 格式的日期")
            return
        r = _api(
            "POST",
            f"/api/account-audit/projects/{project_id}/initialize",
            params={"period_end": period_end, "replace_pending": replace_pending},
        )
        if r:
            st.success(
                f"扫描 {r.get('scanned')} 条 / 新增 {r.get('inserted')} 条待审定 / 删除旧 pending {r.get('deleted_pending')} 条"
            )


def _tab_overview(project_id: int) -> None:
    st.markdown("### 📊 项目总览 (按科目)")
    period_end = st.text_input("期末日期", value=str(date.today()), key="aa_ov_pe")
    if not period_end:
        return
    if st.button("🔍 刷新"):
        st.session_state["aa_overview_pe"] = period_end
    pe = st.session_state.get("aa_overview_pe", period_end)
    res = _api(
        "GET", f"/api/account-audit/projects/{project_id}/overview", params={"period_end": pe}
    )
    if not res:
        st.info("无数据, 请先初始化")
        return

    cols = st.columns(5)
    cols[0].metric("长期资产科目数", res.get("total_accounts", 0))
    cols[1].metric("已全部审定", res.get("accounts_fully_audited", 0))
    cols[2].metric("含 pending", res.get("accounts_with_pending", 0))
    cols[3].metric("含争议", res.get("accounts_with_dispute", 0))
    cols[4].metric("⚠️ 恒等式不平", res.get("accounts_unbalanced", 0))

    accts = res.get("accounts") or []
    if not accts:
        st.info("无长期资产科目余额, 请先导入科目余额表")
        return

    df = pd.DataFrame(
        [
            {
                "科目编码": a["account_code"],
                "科目名称": a["account_name"],
                "期初(账面)": a["beginning_balance_book"],
                "期初(审定)": a["beginning_balance_audited"],
                "借方(账面)": a["debit_book_total"],
                "借方(审定)": a["debit_audited_total"],
                "贷方(账面)": a["credit_book_total"],
                "贷方(审定)": a["credit_audited_total"],
                "期末(账面)": a["ending_balance_book"],
                "期末(审定)": a["ending_balance_audited"],
                "待审": a["debit_pending_count"] + a["credit_pending_count"],
                "已审": a["debit_audited_count"] + a["credit_audited_count"],
                "争议": a["debit_disputed_count"] + a["credit_disputed_count"],
                "恒等式": "✅"
                if a["is_balanced"]
                else f"❌ {round(a['identity_check_audited'], 2)}",
            }
            for a in accts
        ]
    )
    st.dataframe(df, width="stretch", height=480)

    unbalanced = [a for a in accts if not a["is_balanced"]]
    if unbalanced:
        st.warning(f"⚠️ {len(unbalanced)} 个科目恒等式不平, 请逐笔复核:")
        for a in unbalanced[:5]:
            st.write(
                f"- {a['account_code']} {a['account_name']} — "
                f"审定差额 {round(a['identity_check_audited'], 2)}"
            )


def _tab_movements(project_id: int) -> None:
    st.markdown("### 📝 发生额逐笔审定")
    cols = st.columns(5)
    account_code = cols[0].text_input("科目编码 (留空查全部)", value="")
    period_end = cols[1].text_input("期末日期", value=str(date.today()), key="aa_mov_pe")
    direction = cols[2].selectbox("方向", ["全部", "debit", "credit"])
    status = cols[3].selectbox("状态", ["全部", "pending", "audited", "disputed", "skipped"])
    keyword = cols[4].text_input("摘要关键词")

    params: Dict[str, Any] = {"limit": 500}
    if account_code:
        params["account_code"] = account_code
    if period_end:
        params["period_end"] = period_end
    if direction != "全部":
        params["direction"] = direction
    if status != "全部":
        params["status"] = status
    if keyword:
        params["keyword"] = keyword

    res = _api("GET", f"/api/account-audit/projects/{project_id}/movements", params=params)
    if not res or not res.get("items"):
        st.info("无数据。若刚导入序时账, 请先在 '初始化' 页跑一遍")
        return

    items = res["items"]
    st.metric("命中条数", res.get("total", 0))

    # 用 data_editor 让用户行内修改
    edit_df = pd.DataFrame(
        [
            {
                "id": i["id"],
                "科目": f"{i['account_code']} {i['account_name']}",
                "凭证日期": i["voucher_date"],
                "凭证号": i["voucher_no"],
                "行": i["voucher_line_no"],
                "方向": i["direction"],
                "摘要": i.get("summary") or "",
                "账面": i["book_amount"],
                "审定": i["audited_amount"],
                "调整": i["adjustment_amount"],
                "调整原因": i.get("adjustment_reason") or "",
                "底稿索引": i.get("working_paper_ref") or "",
                "状态": i["status"],
            }
            for i in items
        ]
    )
    edited = _edit_df(
        edit_df,
        key="aa_editor",
        height=520,
        width="stretch",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "科目": st.column_config.TextColumn(disabled=True),
            "凭证日期": st.column_config.TextColumn(disabled=True),
            "凭证号": st.column_config.TextColumn(disabled=True),
            "行": st.column_config.NumberColumn(disabled=True, width="small"),
            "方向": st.column_config.TextColumn(disabled=True),
            "摘要": st.column_config.TextColumn(disabled=True),
            "账面": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "审定": st.column_config.NumberColumn(format="%.2f"),
            "调整": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "状态": st.column_config.SelectboxColumn(
                options=["pending", "audited", "disputed", "skipped"],
            ),
        },
    )

    # 检查改动 → 提交
    if st.button("💾 保存改动", type="primary"):
        old_by_id = {r["id"]: r for r in edit_df.to_dict(orient="records")}
        changes = []
        for r in edited.to_dict(orient="records"):
            o = old_by_id.get(r["id"])
            if not o:
                continue
            audited_changed = abs(float(r["审定"]) - float(o["审定"])) > 0.001
            reason_changed = (r["调整原因"] or "") != (o["调整原因"] or "")
            ref_changed = (r["底稿索引"] or "") != (o["底稿索引"] or "")
            status_changed = r["状态"] != o["状态"]
            if audited_changed or reason_changed or ref_changed or status_changed:
                changes.append(
                    {
                        "id": int(r["id"]),
                        "audited_amount": float(r["审定"]),
                        "adjustment_reason": r["调整原因"] or None,
                        "working_paper_ref": r["底稿索引"] or None,
                        "status": r["状态"],
                    }
                )
        if not changes:
            st.info("没有改动")
        else:
            ok = 0
            fail = 0
            # P0 性能: 批量保存接口缺失, 暂时保留逐条 PUT, 待后端 /api/account-audit/movements/batch
            # 添加后切换 (后端已有 bulk endpoint 见 app/api/account_audit.py)
            for c in changes:
                mid = c.pop("id")
                r = _api("PUT", f"/api/account-audit/movements/{mid}", json=c)
                if r:
                    ok += 1
                else:
                    fail += 1
            st.success(f"已提交 {ok} 条修改, 失败 {fail} 条")
            if ok:
                st.rerun()


def _tab_bulk_upload(project_id: int) -> None:
    st.markdown("### 📤 Excel 批量上传审定结果")
    st.caption(
        "上传 Excel/CSV, 必填列: account_code, voucher_no, direction (debit/credit), audited_amount; "
        "可选: voucher_line_no(默认1) / adjustment_reason / working_paper_ref / note"
    )
    period_end = st.text_input("期末日期", value=str(date.today()), key="aa_bulk_pe")
    f = st.file_uploader("选择文件", type=["xlsx", "xls", "csv"], key="aa_bulk_file")

    # 提供模板下载
    sample = pd.DataFrame(
        {
            "account_code": ["1601", "1601"],
            "voucher_no": ["JZ-2024-001", "JZ-2024-001"],
            "voucher_line_no": [1, 2],
            "direction": ["debit", "credit"],
            "audited_amount": [10000.0, 0.0],
            "adjustment_reason": ["核对发票确认", ""],
            "working_paper_ref": ["E-1.1", "E-1.1"],
            "note": ["", ""],
        }
    )
    buf = io.BytesIO()
    sample.to_excel(buf, index=False)
    st.download_button(
        "📥 下载模板",
        data=buf.getvalue(),
        file_name="bulk_audit_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    if f and st.button("🚀 上传并审定", type="primary"):
        files = {"file": (f.name, f.read(), f.type or "application/octet-stream")}
        res = _api(
            "POST",
            f"/api/account-audit/projects/{project_id}/bulk-audit-upload",
            params={"period_end": period_end},
            files=files,
        )
        if res:
            st.success(
                f"匹配 {res.get('matched', 0)} / 更新 {res.get('updated', 0)} / 未找到 {res.get('not_found', 0)}"
            )
            errs = res.get("errors") or []
            if errs:
                with st.expander(f"⚠️ {len(errs)} 条错误明细", expanded=False):
                    for e in errs[:50]:
                        st.write(f"- {e}")


def _tab_export(project_id: int) -> None:
    st.markdown("### 📥 导出审定明细 Excel")
    cols = st.columns(2)
    period_end = cols[0].text_input("期末日期", value=str(date.today()), key="aa_exp_pe")
    account_code = cols[1].text_input("科目编码 (留空导出全部)", value="")

    if st.button("📥 导出", type="primary"):
        params = {"period_end": period_end}
        if account_code:
            params["account_code"] = account_code
        content = _api(
            "GET",
            f"/api/account-audit/projects/{project_id}/export",
            params=params,
            expect_bytes=True,
        )
        if content:
            fname = (
                f"long_term_asset_audit_p{project_id}"
                f"{('_' + account_code) if account_code else ''}_{period_end}.xlsx"
            )
            st.download_button(
                "💾 下载",
                data=content,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def show_account_audit() -> None:
    st.markdown(
        '<p style="font-size:1.8rem;font-weight:bold;color:#4472C4;">📑 长期资产发生额审定</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "用户特别要求: 长期资产科目 (固定资产/在建工程/无形资产/长投/商誉/使用权资产等), "
        "不只期初期末出审定数, 本期借/贷方发生额逐笔出审定数 + 审计调整, 底稿自动恒等式校验。"
    )

    project_id = pick_project(key="aa_pick_project")
    if not project_id:
        return

    tabs = st.tabs(
        [
            "📋 科目范围",
            "🔄 初始化",
            "📊 项目总览",
            "📝 发生额审定",
            "📤 批量上传",
            "📥 导出",
        ]
    )
    with tabs[0]:
        _tab_scope(project_id)
    with tabs[1]:
        _tab_initialize(project_id)
    with tabs[2]:
        _tab_overview(project_id)
    with tabs[3]:
        _tab_movements(project_id)
    with tabs[4]:
        _tab_bulk_upload(project_id)
    with tabs[5]:
        _tab_export(project_id)
