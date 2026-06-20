"""Streamlit 函证管理页面 (Confirmation Module).

Tabs:
  1. 函证科目与模板  — 查看函证涉及的全部科目 + 银行官方模板字段
  2. 案卷管理        — 创建/查看/锁定案卷
  3. 统计表生成      — 从账套自动生成函证对象 (可调整阈值/抽样)
  4. 确定发函        — 锁定案卷 + 逐个发函 (生成询证函,锁定发函日期)
  5. 回函登记        — 上传回函照片 (OCR + AI) / 手工录入
  6. 回函情况        — 函证汇总 / 回函率 / 差异 / 催办
  7. 导出工作簿      — 一键导出多 Sheet Excel
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

# P0 安全修复: 使用共享 api_request (带 Authorization header + 401 处理)
from frontend._http import api_request
from frontend._components import apply_feishu_theme, page_header
from frontend._components.safe_render import safe_inline_text


def _current_user_name() -> str:
    """返回当前登录用户姓名; 未登录时返回空字符串 (避免 '审计师' 歧义)."""
    user = st.session_state.get("auth_user") or {}
    return user.get("full_name") or user.get("username") or ""

def fetch_subjects():
    return api_request("GET", "/api/confirmations/subjects") or {}


@st.cache_data(ttl=30)
def fetch_projects():
    return api_request("GET", "/api/projects/") or []


@st.cache_data
def fetch_cases(project_id: int):
    return api_request("GET", f"/api/confirmations/cases?project_id={project_id}") or []


@st.cache_data
def fetch_case_items(case_id: int):
    return api_request("GET", f"/api/confirmations/cases/{case_id}/items") or []


@st.cache_data
def fetch_case_summary(case_id: int):
    return api_request("GET", f"/api/confirmations/cases/{case_id}/summary") or {}


def show_confirmations():
    apply_feishu_theme()
    page_header('📬', '函证管理', '财政部模板 + 锁定金额快照 + 回函 OCR + AI 解析 + 差异统计')

    # [飞书化]     st.markdown("## 📬 函证管理 (Confirmation)")  # 已被 page_header() 替代

    st.caption(
        "从账套自动生成银行/客户/供应商/其他往来询证函统计表 → "
        "确定发函后锁定 → 回函照片 OCR + AI 解析 → 回函情况自动统计"
    )

    catalogue = fetch_subjects()
    projects = fetch_projects()

    if not projects:
        st.warning("请先在『项目管理』中创建项目。")
        return

    # 项目选择
    proj = st.selectbox(
        "选择审计项目",
        options=projects,
        format_func=lambda p: f"#{p['id']} {p['name']} - {p['company_name']} ({p['fiscal_year']})",
        key="conf_proj",  # round 31 widget key
    )
    if not proj:
        return
    project_id = proj["id"]
    fiscal_year = proj["fiscal_year"]

    tabs = st.tabs(
        [
            "📋 函证科目",
            "📁 案卷管理",
            "🧮 统计表生成",
            "✉️ 确定发函",
            "📥 回函登记",
            "📊 回函情况",
            "📤 导出工作簿",
        ]
    )

    # ============================================================
    # Tab 1: 函证科目
    # ============================================================
    with tabs[0]:
        st.markdown("### 函证涉及的全部科目清单")
        st.caption(
            "银行询证函按财政部《银行询证函参考格式》(财会[2024]6号 等)要求至少函证 26 个项目；"
            "客户/供应商函证按 CSA 1311 / 1502 / 1504 等准则要求函证余额+本期发生额+票据+合同条款。"
        )

        subjects = catalogue.get("subjects", [])
        if subjects:
            df = pd.DataFrame(
                [
                    {
                        "科目编号": s["code"],
                        "科目名称": s["name"],
                        "类别": s["category"],
                        "函证方类型": s["party_type_label"],
                        "必发阈值": s.get("threshold", 0),
                        "默认函证项数": len(s.get("default_subjects", [])),
                        "默认函证项": " / ".join(s.get("default_subjects", [])[:3])
                        + ("..." if len(s.get("default_subjects", [])) > 3 else ""),
                    }
                    for s in subjects
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True, height=400)

            with st.expander("📄 银行询证函官方模板字段 (26 项)", expanded=False):
                bank_fields = catalogue.get("bank_template_fields", [])
                st.dataframe(pd.DataFrame(bank_fields, height=400), use_container_width=True, hide_index=True)

            with st.expander("📄 客户/供应商函证默认函证项", expanded=False):
                for s in subjects:
                    if s["party_type"] in ("customer", "supplier", "other_recv", "other_pay"):
                        st.markdown(f"**{s['party_type_label']} - {s['name']}**")
                        for subj in s.get("default_subjects", []):
                            st.markdown(f"- {subj}")
                        st.markdown("---")
        else:
            st.info("无法加载科目清单")

    # ============================================================
    # Tab 2: 案卷管理
    # ============================================================
    with tabs[1]:
        st.markdown("### 案卷管理")
        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown("#### 已有案卷")
            cases = fetch_cases(project_id)
            if cases:
                rows = []
                for c in cases:
                    rows.append(
                        {
                            "ID": c["id"],
                            "案卷名称": c["case_name"],
                            "报告期": c["period_end"],
                            "年度": c["fiscal_year"],
                            "锁定": "🔒 是" if c["is_locked"] else "🔓 否",
                            "生成时间": c["generated_at"][:19] if c.get("generated_at") else "",
                        }
                    )
                st.dataframe(pd.DataFrame(rows, height=400), use_container_width=True, hide_index=True)
            else:
                st.info("该项目下尚无案卷")

        with col2:
            st.markdown("#### 新建案卷")
            with st.form("create_case"):
                case_name = st.text_input("案卷名称", value=f"{fiscal_year}年度审计函证")
                period_end = st.date_input("报告期截止日", value=date(fiscal_year, 12, 31))
                notes = st.text_area("备注", value="")
                generated_by = st.text_input(
                    "生成人", value=_current_user_name()
                )
                if st.form_submit_button("✅ 创建案卷", type="primary"):
                    payload = {
                        "project_id": project_id,
                        "case_name": case_name,
                        "period_end": period_end.isoformat(),
                        "fiscal_year": period_end.year,
                        "generated_by": generated_by,
                        "notes": notes,
                    }
                    r = api_request("POST", "/api/confirmations/cases", json=payload)
                    if r:
                        st.success(f"案卷 #{r['id']} 已创建")
                        st.rerun()

    # ============================================================
    # Tab 3: 统计表生成
    # ============================================================
    with tabs[2]:
        st.markdown("### 统计表生成 — 从账套自动生成函证对象")
        cases = fetch_cases(project_id)
        if not cases:
            st.warning("请先创建案卷")
        else:
            case = st.selectbox(
                "选择案卷",
                options=cases,
                format_func=lambda c: (
                    f"#{c['id']} {c['case_name']} ({c['period_end']})"
                    + (" 🔒" if c["is_locked"] else "")
                ),
                key="conf_case",  # round 31 widget key
            )
            if case and case["is_locked"]:
                st.warning("⚠️ 案卷已锁定, 无法重新生成。请新建案卷。")
            elif case:
                st.markdown("#### 选样规则")
                col1, col2, col3 = st.columns(3)
                with col1:
                    bank_threshold = st.number_input("银行阈值 (0=全发)", value=0.0, min_value=0.0, key="conf_bank_thr")  # round 31 widget key
                    customer_threshold = st.number_input("客户阈值", value=100000.0, min_value=0.0, key="conf_cust_thr")  # round 31 widget key
                with col2:
                    supplier_threshold = st.number_input(
                        "供应商阈值", value=100000.0, min_value=0.0, key="conf_supp_thr",  # round 31 widget key
                    )
                    other_threshold = st.number_input("其他往来阈值", value=50000.0, min_value=0.0, key="conf_other_thr")  # round 31 widget key
                with col3:
                    sample_ratio = st.slider("阈值以下随机抽样比例", 0.0, 0.5, 0.10, 0.05)
                    include_zero = st.checkbox("包含零余额", value=False, key="conf_inc_zero")  # round 31 widget key

                if st.button("🧮 生成统计表", type="primary", key="conf_gen_stats"):  # round 31 widget key
                    payload = {
                        "case_id": case["id"],
                        "bank_threshold": bank_threshold,
                        "customer_threshold": customer_threshold,
                        "supplier_threshold": supplier_threshold,
                        "other_threshold": other_threshold,
                        "additional_sample_ratio": sample_ratio,
                        "random_seed": 42,
                        "include_zero_balance": include_zero,
                        "persist": True,
                        "generated_by": _current_user_name(),
                    }
                    with st.spinner("正在从账套聚合并选样..."):
                        r = api_request(
                            "POST", f"/api/confirmations/cases/{case['id']}/generate", json=payload
                        )
                    if r:
                        st.success(
                            f"✅ 已生成 {r['selected_count']} 个函证对象, "
                            f"账面金额合计 {r['total_amount']:,.2f} 元"
                        )
                        # 按类型分布
                        if r.get("by_party_type"):
                            st.markdown("#### 按函证方类型分布")
                            tdf = pd.DataFrame(
                                [
                                    {"类型": k, "数量": v["count"], "金额": v["amount"]}
                                    for k, v in r["by_party_type"].items()
                                ]
                            )
                            st.dataframe(tdf, use_container_width=True, hide_index=True, height=400)

                # 现有 items
                items = fetch_case_items(case["id"])
                if items:
                    st.markdown("#### 当前函证对象清单")
                    rows = [
                        {
                            "ID": it["id"],
                            "类型": it["party_type_label"],
                            "对方": it["party_name"],
                            "对方编号": it.get("party_id") or "",
                            "我方科目": it.get("account_name") or "",
                            "账面余额": it["book_balance"],
                            "函证金额": it["total_confirm_amount"],
                            "重要性": it["importance"],
                            "选样原因": it.get("selection_reason") or "",
                            "状态": it["status_label"],
                        }
                        for it in items
                    ]
                    st.dataframe(pd.DataFrame(rows, height=400), use_container_width=True, hide_index=True)

    # ============================================================
    # Tab 4: 确定发函
    # ============================================================
    with tabs[3]:
        st.markdown("### 确定发函 (锁定案卷 + 逐个发函)")
        st.caption(
            "⚠️ 一旦『确定发函』并锁定, 发函日期与金额快照不可再修改, "
            "避免后续账套数据更新导致多版本混乱。"
        )

        cases = fetch_cases(project_id)
        if not cases:
            st.warning("请先创建案卷")
        else:
            case = st.selectbox(
                "选择案卷",
                options=cases,
                format_func=lambda c: (
                    f"#{c['id']} {c['case_name']} ({c['period_end']})"
                    + (" 🔒" if c["is_locked"] else "")
                ),
                key="send_case",
            )
            if not case:
                return

            col1, col2, col3 = st.columns([1, 1, 2])
            with col1:
                if not case["is_locked"]:
                    if st.button("🔒 确定发函 (锁定案卷)", type="primary", key="conf_lock_case"):  # round 31 widget key
                        payload = {
                            "locked_by": _current_user_name(),
                            "lock_reason": "已确认函证对象清单",
                        }
                        r = api_request(
                            "POST", f"/api/confirmations/cases/{case['id']}/lock", json=payload
                        )
                        if r:
                            st.success("案卷已锁定")
                            st.rerun()
                else:
                    st.success("🔒 案卷已锁定")
            with col2:
                if case["is_locked"]:
                    if st.button("🔓 解锁", help="仅当无发函时允许", key="conf_unlock_case"):  # round 31 widget key
                        r = api_request("POST", f"/api/confirmations/cases/{case['id']}/unlock")
                        if r:
                            st.success("已解锁")
                            st.rerun()

            st.markdown("---")
            st.markdown("#### 函证对象与发函")
            items = fetch_case_items(case["id"])
            for it in items:
                with st.expander(
                    f"{it['party_type_label']} - {it['party_name']} - 余额 {it['book_balance']:,.2f} - {it['status_label']}",
                    expanded=False,
                ):
                    st.markdown(f"**科目**: {it.get('account_name')} ({it.get('account_code')})")
                    st.markdown(f"**对方编号**: {it.get('party_id') or '-'}")
                    st.markdown(f"**选样原因**: {it.get('selection_reason') or '-'}")
                    st.markdown("**函证项**:")
                    for s in it.get("subject_matters", []):
                        st.markdown(f"- {s}")

                    if it["status"] in ("draft", "confirmed", "no_reply", "rejected", "voided"):
                        if not case["is_locked"]:
                            st.info("案卷未锁定, 无法发函")
                        else:
                            with st.form(f"send_{it['id']}"):
                                col1, col2 = st.columns(2)
                                with col1:
                                    sent_date = st.date_input("发函日期", value=date.today())
                                    sent_method = st.selectbox(
                                        "发函方式", ["邮寄", "电子邮件", "跟函", "电邮+邮寄"]
                                    )
                                    sent_by = st.text_input(
                                        "发函人", value=_current_user_name()
                                    )
                                with col2:
                                    recipient = st.text_input("收件人", value=it["party_name"])
                                    recipient_address = st.text_area("收件地址", value="")
                                    courier_no = st.text_input("快递单号", value="")
                                expected_reply = st.date_input(
                                    "预计回函日", value=date.today() + timedelta(days=14)
                                )
                                file_format = st.selectbox("文件格式", ["docx", "pdf"])
                                if st.form_submit_button("✉️ 发函 (生成询证函并锁定)"):
                                    payload = {
                                        "item_id": it["id"],
                                        "sent_date": sent_date.isoformat(),
                                        "sent_method": sent_method,
                                        "sent_by": sent_by,
                                        "recipient": recipient,
                                        "recipient_address": recipient_address,
                                        "courier_no": courier_no,
                                        "expected_reply_date": expected_reply.isoformat(),
                                        "template_id": "standard",
                                        "file_format": file_format,
                                    }
                                    r = api_request(
                                        "POST",
                                        f"/api/confirmations/items/{it['id']}/send",
                                        json=payload,
                                    )
                                    if r:
                                        st.success(f"✅ 询证函已生成: {r.get('letter_no')}")
                                        st.rerun()
                    elif it["status"] == "sent" and it.get("sent_letter_id"):
                        st.success(f"已发函 #{it['sent_letter_id']}")
                        # 提供下载
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("📥 下载询证函", key=f"dl_{it['id']}"):
                                content = api_request(
                                    "GET",
                                    f"/api/confirmations/letters/{it['sent_letter_id']}/download",
                                    expect_bytes=True,
                                )
                                if content:
                                    st.download_button(
                                        "下载",
                                        data=content,
                                        file_name=f"询证函_{it['party_name']}.docx",
                                        mime="application/octet-stream",
                                    )
                        with col2:
                            if st.button("📝 录入回函", key=f"rec_{it['id']}"):
                                st.session_state["active_letter"] = it["sent_letter_id"]
                                st.session_state["active_tab"] = "回函登记"

    # ============================================================
    # Tab 5: 回函登记
    # ============================================================
    with tabs[4]:
        st.markdown("### 回函登记 — 照片 OCR + AI 解析 / 手工录入")
        cases = fetch_cases(project_id)
        if not cases:
            st.warning("请先创建案卷")
        else:
            case = st.selectbox(
                "选择案卷",
                options=cases,
                format_func=lambda c: f"#{c['id']} {c['case_name']} ({c['period_end']})",
                key="resp_case",
            )
            if case:
                items = fetch_case_items(case["id"])
                # 列出已发函的 items
                sent_items = [it for it in items if it.get("sent_letter_id")]
                if not sent_items:
                    st.info("该案卷下尚无已发函项目")
                else:
                    sel = st.selectbox(
                        "选择已发函项目",
                        options=sent_items,
                        format_func=lambda it: (
                            f"#{it['id']} {it['party_name']} - {it['book_balance']:,.2f} - {it['status_label']}"
                        ),
                        key="conf_sent_item",  # round 31 widget key
                    )
                    if sel:
                        letter_id = sel["sent_letter_id"]
                        st.markdown(f"#### 函证对象: {sel['party_name']}")
                        st.markdown(f"账面余额: **{sel['book_balance']:,.2f}**")
                        st.markdown("---")

                        mode = st.radio(
                            "录入方式",
                            ["📷 上传回函照片 (OCR+AI)", "✍️ 手工录入"],
                            horizontal=True,
                            key="conf_input_mode",  # round 31 widget key
                        )

                        if mode.startswith("📷"):
                            uploaded = st.file_uploader(
                                "上传回函照片/PDF",
                                type=["jpg", "jpeg", "png", "pdf"],
                                key=f"ph_{letter_id}",
                            )
                            auto_confirm = st.checkbox(
                                "AI 解析后自动回填状态",
                                value=True,
                                help="取消则只解析不修改状态, 需人工确认",
                                key="conf_auto_confirm",  # round 31 widget key
                            )
                            if uploaded and st.button("🚀 OCR + AI 解析", type="primary", key="conf_ocr_run"):  # round 31 widget key
                                files = {
                                    "file": (
                                        uploaded.name,
                                        uploaded.getvalue(),
                                        uploaded.type or "image/jpeg",
                                    )
                                }
                                data = {"auto_confirm": str(auto_confirm).lower()}
                                r = api_request(
                                    "POST",
                                    f"/api/confirmations/letters/{letter_id}/photos",
                                    files=files,
                                    data=data,
                                )
                                if r:
                                    st.success(r.get("message", "OK"))
                                    parsed = r.get("parsed_data") or {}
                                    if parsed:
                                        with st.expander("AI 解析结果", expanded=False):
                                            st.json(parsed)
                                    st.rerun()
                        else:
                            with st.form(f"manual_resp_{letter_id}"):
                                col1, col2 = st.columns(2)
                                with col1:
                                    received_date = st.date_input("收函日期", value=date.today())
                                    response_method = st.selectbox(
                                        "回函方式", ["纸质原件", "扫描件", "电邮", "传真"]
                                    )
                                    response_status = st.selectbox(
                                        "回函状态",
                                        ["match", "partial", "mismatch", "reject", "unclear"],
                                        format_func=lambda x: {
                                            "match": "相符",
                                            "partial": "部分相符",
                                            "mismatch": "不符",
                                            "reject": "拒函",
                                            "unclear": "待人工核对",
                                        }.get(x, x),
                                    )
                                with col2:
                                    amount_confirmed = st.number_input("对方确认金额", value=0.0)
                                    difference_reason = st.text_area("差异原因", value="")
                                response_summary = st.text_area("回函摘要", value="")
                                auditor_note = st.text_area("审计师备注", value="")
                                if st.form_submit_button("✅ 提交回函"):
                                    payload = {
                                        "letter_id": letter_id,
                                        "received_date": received_date.isoformat(),
                                        "response_method": response_method,
                                        "response_status": response_status,
                                        "amount_confirmed": amount_confirmed,
                                        "difference_reason": difference_reason,
                                        "response_summary": response_summary,
                                        "auditor_note": auditor_note,
                                        "confirmed_by": _current_user_name(),
                                    }
                                    r = api_request(
                                        "POST",
                                        f"/api/confirmations/letters/{letter_id}/response",
                                        json=payload,
                                    )
                                    if r:
                                        st.success("回函已录入")
                                        st.rerun()

                        # 显示已有回函
                        rd = api_request("GET", f"/api/confirmations/letters/{letter_id}/response")
                        if rd and rd.get("response"):
                            st.markdown("---")
                            st.markdown("#### 当前回函")
                            resp = rd["response"]
                            col1, col2, col3 = st.columns(3)
                            with col1:
                                st.metric("回函状态", resp["response_status_label"])
                            with col2:
                                st.metric("对方确认金额", f"{resp['amount_confirmed']:,.2f}")
                            with col3:
                                st.metric(
                                    "差异金额",
                                    f"{resp['amount_difference']:,.2f}",
                                    delta=f"{resp['amount_difference']:,.2f}",
                                )
                            st.markdown(f"**收函日期**: {resp.get('received_date') or '-'}")
                            st.markdown(f"**回函方式**: {resp.get('response_method')}")
                            st.markdown(f"**差异原因**: {resp.get('difference_reason') or '-'}")
                            st.markdown(f"**回函摘要**: {resp.get('response_summary') or '-'}")
                            st.markdown(f"**审计师备注**: {safe_inline_text(resp.get('auditor_note', ''), max_len=500) or '-'}")
                            if resp.get("photos"):
                                st.markdown(f"**已上传 {len(resp['photos'])} 张回函照片**")

    # ============================================================
    # Tab 6: 回函情况
    # ============================================================
    with tabs[5]:
        st.markdown("### 回函情况汇总")
        cases = fetch_cases(project_id)
        if not cases:
            st.warning("请先创建案卷")
        else:
            case = st.selectbox(
                "选择案卷",
                options=cases,
                format_func=lambda c: f"#{c['id']} {c['case_name']} ({c['period_end']})",
                key="sum_case",
            )
            if case:
                summary = fetch_case_summary(case["id"])
                if summary:
                    st.markdown(f"#### {summary['case_name']} (报告期: {summary['period_end']})")
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("函证对象数", summary["total_items"])
                    with col2:
                        st.metric("账面金额合计", f"{summary['total_amount']:,.2f}")
                    with col3:
                        st.metric("已发函数", summary["sent_count"])
                    with col4:
                        st.metric("回函率", f"{summary['response_rate'] * 100:.1f}%")

                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("已回函数", summary["responded_count"])
                    with col2:
                        st.metric("差异笔数", summary["items_with_difference"])
                    with col3:
                        st.metric("差异金额合计", f"{summary['total_difference_amount']:,.2f}")
                    with col4:
                        st.metric("已确认金额合计", f"{summary['total_confirmed']:,.2f}")

                    # 按 party_type
                    if summary.get("by_party_type"):
                        st.markdown("#### 按函证方类型")
                        df = pd.DataFrame(summary["by_party_type"])
                        st.dataframe(df, use_container_width=True, hide_index=True, height=400)

                    # 状态分布
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("#### 函证对象状态分布")
                        if summary.get("status_summary"):
                            df = pd.DataFrame(
                                [
                                    {"状态": k, "数量": v}
                                    for k, v in summary["status_summary"].items()
                                ]
                            )
                            st.dataframe(df, use_container_width=True, hide_index=True, height=400)
                    with col2:
                        st.markdown("#### 回函状态分布")
                        if summary.get("response_status_summary"):
                            df = pd.DataFrame(
                                [
                                    {"状态": k, "数量": v}
                                    for k, v in summary["response_status_summary"].items()
                                ]
                            )
                            st.dataframe(df, use_container_width=True, hide_index=True, height=400)

                    # 催办
                    if summary.get("no_reply_items"):
                        st.markdown("#### ⏰ 未回函催办")
                        df = pd.DataFrame(summary["no_reply_items"])
                        st.dataframe(df, use_container_width=True, hide_index=True, height=400)

                    if summary.get("pending_items"):
                        st.markdown("#### ⏳ 待回函")
                        df = pd.DataFrame(summary["pending_items"])
                        st.dataframe(df, use_container_width=True, hide_index=True, height=400)

    # ============================================================
    # Tab 7: 导出
    # ============================================================
    with tabs[6]:
        st.markdown("### 导出函证工作簿")
        st.caption(
            "多 Sheet Excel: 函证统计表 / 发函清单 / 回函情况 / 差异分析 / 汇总 / 未回函催办"
        )
        cases = fetch_cases(project_id)
        if not cases:
            st.warning("请先创建案卷")
        else:
            case = st.selectbox(
                "选择案卷",
                options=cases,
                format_func=lambda c: f"#{c['id']} {c['case_name']} ({c['period_end']})",
                key="exp_case",
            )
            if case and st.button("📤 导出工作簿", type="primary", key="conf_export_wb"):  # round 31 widget key
                content = api_request(
                    "GET", f"/api/confirmations/cases/{case['id']}/export", expect_bytes=True
                )
                if content and isinstance(content, bytes):
                    st.download_button(
                        "下载 Excel",
                        data=content,
                        file_name=f"函证工作簿_{case['case_name']}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                    st.success("导出完成")
