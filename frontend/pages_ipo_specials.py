"""Pack D — IPO 专属页面 (精简)."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from frontend._components import apply_feishu_theme, page_header
from frontend._http import api_request
from frontend._components.project_picker import pick_project
from frontend._components.safe_render import validate_date_input


def _api(method: str, endpoint: str, **kwargs):
    return api_request(method, endpoint, timeout=60, **kwargs)


def _tab_walkthrough(project_id: int) -> None:
    st.markdown("### 🔍 内控穿行测试 (Phase 16)")
    st.markdown("#### Mermaid 流程图生成")
    steps_text = st.text_area(
        "步骤描述 (一行一个步骤)",
        value="客户下单审核\n销售确认订单\n仓库发货\n开具发票\n确认收入",
        key="ipo_wt_steps",
    )
    if st.button("📊 生成流程图", key="ipo_wt_generate"):
        steps = [{"step_description": s.strip()} for s in steps_text.splitlines() if s.strip()]
        r = _api("POST", "/api/ipo-specials/walkthrough/mermaid-flowchart", json={"steps": steps})
        if r:
            st.code(r.get("mermaid", ""), language="mermaid")
            st.caption("提示: 复制到 https://mermaid.live 可视化")

    st.markdown("#### 抽样选笔")
    sample_text = st.text_area(
        "凭证列表 (一行一个 JSON, 含 amount/voucher_no)",
        value='{"voucher_no": "JZ-001", "amount": 100000}\n{"voucher_no": "JZ-002", "amount": 50000}\n{"voucher_no": "JZ-003", "amount": 300000}',
        key="ipo_wt_vouchers",
    )
    cycle = st.selectbox("循环", ["sales", "procurement", "inventory", "payroll"], key="ipo_wt_cycle")
    n = st.number_input("抽样数", min_value=1, max_value=20, value=3, key="ipo_wt_n")
    if st.button("🎲 抽样", key="ipo_wt_sample"):
        try:
            items = [json.loads(s) for s in sample_text.splitlines() if s.strip()]
            r = _api(
                "POST",
                "/api/ipo-specials/walkthrough/sample",
                json={"cycle_code": cycle, "items": items, "n": int(n)},
            )
            if r:
                st.dataframe(pd.DataFrame(r.get("samples", [])), width="stretch")
        except Exception as exc:
            st.error(f"解析错: {exc}")


def _tab_cutoff(project_id: int) -> None:
    st.markdown("### ⏰ 截止性测试 (Phase 17)")
    c1, c2, c3 = st.columns(3)
    # P1 (round 35): 三个日期统一包 validate_date_input, 不通过返回 ("", False)
    ship, ship_ok = validate_date_input("发货日期", key="ipo_co_ship", default="2024-12-28")
    confirm, confirm_ok = validate_date_input("收入确认日期", key="ipo_co_confirm", default="2025-01-03")
    period, period_ok = validate_date_input("期末日期", key="ipo_co_period", default="2024-12-31")
    cutoff_days = st.number_input("Cutoff window (天)", min_value=1, max_value=30, value=5, key="ipo_co_window")
    if st.button("📊 判定", key="ipo_co_judge"):
        if not (ship_ok and confirm_ok and period_ok):
            st.warning("日期格式错误, 请修正后再判定 (YYYY-MM-DD)")
            return
        r = _api(
            "POST",
            "/api/ipo-specials/revenue-cutoff/judge",
            json={
                "ship_date": ship,
                "revenue_confirm_date": confirm,
                "period_end": period,
                "cutoff_days": int(cutoff_days),
            },
        )
        if r:
            j = r.get("judgement")
            badge = {
                "early": "🔴 提前确认收入",
                "late": "🟡 延迟确认收入",
                "normal": "✅ 正常",
            }.get(j, j)
            st.info(f"{badge} (偏差 {r.get('diff_days')} 天)")
            if r.get("adjustment_required"):
                st.warning("建议生成跨期调整分录")
        else:
            # P1 (round 35): 错误处理补全 — 后端 422 / 400 详情暴露
            st.error("截止性判定失败, 请检查日期合法性或后端日志")


def _tab_prospectus(project_id: int) -> None:
    st.markdown("### 📖 招股书勾稽")
    rows = _api("GET", f"/api/ipo-specials/prospectus/projects/{project_id}/list") or []
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch")
    with st.expander("➕ 上传新版本招股书 (登记 metadata)", expanded=False):
        c1, c2 = st.columns(2)
        version = c1.text_input("版本", value="v1", key="ipo_pros_version")
        filename = c2.text_input("文件名 (可选)", key="ipo_pros_filename")
        if st.button("提交版本", key="ipo_pros_submit"):
            r = _api(
                "POST",
                f"/api/ipo-specials/prospectus/projects/{project_id}/upload",
                params={"version": version, "filename": filename or None},
            )
            if r:
                st.success(f"已登记 v{r.get('version')}, 招股书 ID = {r.get('id')}")
                st.rerun()

    st.markdown("#### 关键数据 metric 录入 + 系统勾稽")
    pid = st.number_input("招股书 ID", min_value=0, step=1, value=0, key="ipo_pros_pid")
    if pid and pid > 0:
        c1, c2 = st.columns(2)
        code = c1.text_input("指标 code (gross_margin / revenue / 等)", key="ipo_pros_code")
        name = c2.text_input("指标名称", key="ipo_pros_name")
        c3, c4, c5 = st.columns(3)
        period = c3.text_input("期间 (2024 / 2024H1)", key="ipo_pros_period")
        pv = c4.number_input("招股书数值", value=0.0, key="ipo_pros_pv")
        sv = c5.number_input("系统数值 (留 0 = 暂无)", value=0.0, key="ipo_pros_sv")
        if st.button("勾稽", key="ipo_pros_recon"):
            payload = {
                "metric_code": code,
                "metric_name": name,
                "period_label": period,
                "prospectus_value": pv,
                "system_value": sv if sv != 0 else None,
            }
            r = _api("POST", f"/api/ipo-specials/prospectus/{pid}/metrics", json=payload)
            if r:
                ok = (
                    "✅ 一致"
                    if r.get("is_matched")
                    else f"⚠️ 差异 {r.get('diff_amount')} ({r.get('diff_pct'):.2f}%)"
                )
                st.info(ok)
    else:
        st.warning("请先填招股书 ID (必须 > 0) 后再勾稽")


def _tab_period_compare(project_id: int) -> None:
    st.markdown("### 📊 三年一期对比")
    with st.expander("➕ 新增对比指标", expanded=False):
        with st.form("period_form"):
            c1, c2 = st.columns(2)
            rt = c1.selectbox(
                "报表类型", ["balance_sheet", "income_statement", "cash_flow", "ratios"],
                key="ipo_pc_rt",
            )
            code = c2.text_input("指标 code (gross_margin 等)", key="ipo_pc_code")
            name = st.text_input("指标名称", key="ipo_pc_name")
            c3, c4, c5, c6 = st.columns(4)
            v1 = c3.number_input("3 年前", value=0.0, key="ipo_pc_v1")
            v2 = c4.number_input("2 年前", value=0.0, key="ipo_pc_v2")
            v3 = c5.number_input("1 年前", value=0.0, key="ipo_pc_v3")
            vh = c6.number_input("一期(半年)", value=0.0, key="ipo_pc_vh")
            ok = st.form_submit_button("提交", type="primary")
        if ok and code:
            payload = {
                "report_type": rt,
                "metric_code": code,
                "metric_name": name,
                "value_period_1": v1,
                "value_period_2": v2,
                "value_period_3": v3,
                "value_period_h1": vh,
            }
            r = _api(
                "POST",
                f"/api/ipo-specials/period-comparison/projects/{project_id}/metrics",
                json=payload,
            )
            if r:
                msg = f"YoY {r.get('yoy_change_pct')}%"
                if r.get("anomaly_flag"):
                    msg += f" ⚠️ 异动: {r.get('anomaly_flag')}"
                st.success(msg)
                st.rerun()

    rows = _api("GET", f"/api/ipo-specials/period-comparison/projects/{project_id}/list") or []
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", height=350)


def _tab_overlap(project_id: int) -> None:
    st.markdown("### 🔀 客户/供应商重叠检测")
    c1, c2 = st.columns(2)
    customers = c1.text_area("客户名单 (一行一个)", value="北京 ABC 有限公司\n上海 XYZ 有限公司", key="ipo_ov_customers")
    suppliers = c2.text_area("供应商名单 (一行一个)", value="北京 ABC 公司\n广州 LMN 公司", key="ipo_ov_suppliers")
    threshold = st.slider("模糊匹配阈值", 0.5, 1.0, 0.75, 0.05, key="ipo_ov_threshold")
    if st.button("🔍 检测重叠", key="ipo_ov_detect"):
        payload = {
            "customer_names": [s.strip() for s in customers.splitlines() if s.strip()],
            "supplier_names": [s.strip() for s in suppliers.splitlines() if s.strip()],
            "fuzzy_threshold": threshold,
        }
        r = _api("POST", f"/api/ipo-specials/overlap/projects/{project_id}/detect", json=payload)
        if r:
            st.metric("重叠数", r.get("overlaps_found", 0))
            if r.get("details"):
                st.dataframe(pd.DataFrame(r["details"]), width="stretch")

    st.markdown("#### 历史检测记录")
    history = _api("GET", f"/api/ipo-specials/overlap/projects/{project_id}/list") or []
    if history:
        st.dataframe(pd.DataFrame(history), width="stretch", height=200)


def _tab_peer(project_id: int) -> None:
    st.markdown("### 🏢 可比公司基准库")
    rows = _api("GET", f"/api/ipo-specials/peer-companies/projects/{project_id}/list") or []
    if rows:
        st.dataframe(
            pd.DataFrame(rows)[["id", "stock_code", "short_name", "main_business"]], width="stretch"
        )

    with st.expander("➕ 新增可比公司", expanded=False):
        with st.form("peer_form"):
            c1, c2 = st.columns(2)
            stock = c1.text_input("股票代码", key="ipo_peer_stock")
            short = c2.text_input("简称*", key="ipo_peer_short")
            main_business = st.text_area("主业描述", key="ipo_peer_biz")
            ok = st.form_submit_button("提交")
        if ok and short:
            r = _api(
                "POST",
                f"/api/ipo-specials/peer-companies/projects/{project_id}",
                json={
                    "stock_code": stock or None,
                    "short_name": short,
                    "main_business": main_business or None,
                },
            )
            if r:
                st.success(f"已新增 {r.get('short_name')}")
                st.rerun()

    st.markdown("#### 发行人 vs 可比公司基准")
    c1, c2 = st.columns(2)
    issuer_value = c1.number_input("发行人指标值", value=20.0, key="ipo_peer_iv")
    peer_values_text = c2.text_input("可比公司值 (逗号分隔)", value="18,22,25,19,21", key="ipo_peer_pv")
    if st.button("📊 基准对比", key="ipo_peer_bench"):
        try:
            peers = [float(x) for x in peer_values_text.split(",") if x.strip()]
            r = _api(
                "POST",
                "/api/ipo-specials/peer-companies/benchmark",
                json={"issuer_value": issuer_value, "peer_values": peers},
            )
            if r:
                cols = st.columns(4)
                cols[0].metric("可比均值", f"{r['peer_avg']:.2f}")
                cols[1].metric("可比中位", f"{r['peer_median']:.2f}")
                cols[2].metric("偏离 %", f"{r['deviation_pct']:.2f}")
                cols[3].metric("异常?", "🔴 是" if r["is_outlier"] else "✅ 否")
        except Exception as exc:
            st.error(str(exc))


def _tab_feedback(project_id: int) -> None:
    st.markdown("### 📨 反馈意见 / 问询函")
    letters = _api("GET", f"/api/ipo-specials/feedback/projects/{project_id}/letters") or []
    if letters:
        df = pd.DataFrame(letters)
        cols_show = [
            "id",
            "letter_no",
            "issuer",
            "received_date",
            "reply_deadline",
            "days_to_deadline",
            "urgency",
            "status",
        ]
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], width="stretch")

    with st.expander("➕ 新增问询函", expanded=False):
        with st.form("letter_form"):
            c1, c2 = st.columns(2)
            ln = c1.text_input("函号*", key="ipo_letter_no")
            issuer = c2.selectbox("发函机构", ["CSRC", "SSE", "SZSE", "BSE", "other"], key="ipo_letter_issuer")
            c3, c4, c5 = st.columns(3)
            issue_d = c3.text_input("签发日期 YYYY-MM-DD", key="ipo_letter_issue")
            recv_d = c4.text_input("收到日期", key="ipo_letter_recv")
            deadline = c5.text_input("回复截止", key="ipo_letter_deadline")
            sla = st.number_input("SLA 天数", value=30, key="ipo_letter_sla")
            title = st.text_input("标题 (可选)", key="ipo_letter_title")
            ok = st.form_submit_button("提交")
        if ok and ln and issue_d:
            # P1 (round 35): form 内日期二次校验 — form 外用 is_valid_date_str 纯函数
            from frontend._components.safe_render import is_valid_date_str
            if not is_valid_date_str(issue_d):
                st.error(f"签发日期格式错误: '{issue_d}', 应为 YYYY-MM-DD")
                return
            if recv_d and not is_valid_date_str(recv_d):
                st.error(f"收到日期格式错误: '{recv_d}', 应为 YYYY-MM-DD")
                return
            if deadline and not is_valid_date_str(deadline):
                st.error(f"回复截止格式错误: '{deadline}', 应为 YYYY-MM-DD")
                return
            payload = {
                "letter_no": ln,
                "issuer": issuer,
                "issue_date": issue_d,
                "received_date": recv_d or issue_d,
                "reply_deadline": deadline or issue_d,
                "sla_days": int(sla),
                "title": title or None,
            }
            r = _api(
                "POST", f"/api/ipo-specials/feedback/projects/{project_id}/letters", json=payload
            )
            if r:
                st.success(f"已新增函 {r.get('id')}")
                st.rerun()
            else:
                # P1 (round 35): 错误处理补全
                st.error("新增问询函失败, 请检查后端日志或字段格式")


def _tab_checklist(project_id: int) -> None:
    st.markdown("### ✅ 申报材料清单")
    board = st.selectbox("板块", ["main_board", "chinext", "sse_star", "bse"], key="ipo_check_board")
    c1, c2 = st.columns(2)
    if c1.button("🔄 用内置模板初始化", key="ipo_check_init"):
        r = _api(
            "POST",
            f"/api/ipo-specials/submission/projects/{project_id}/init-checklist",
            params={"board_type": board},
        )
        if r:
            st.success(f"已添加 {r.get('added')} 条 (共内置 {r.get('total_default')} 条)")
            st.rerun()

    items = (
        _api(
            "GET",
            f"/api/ipo-specials/submission/projects/{project_id}/checklist",
            params={"board_type": board},
        )
        or []
    )
    if items:
        df = pd.DataFrame(items)
        # 高亮缺失项
        cols_show = [
            "id",
            "item_code",
            "item_name",
            "is_required",
            "is_uploaded",
            "upload_date",
            "uploaded_by_display",
        ]
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], width="stretch", height=400)
        # df.get("key", default) 返回的是 Series, 不会返回默认值, 必须用 in + 直接索引
        if "is_required" in df.columns and "is_uploaded" in df.columns:
            missing = df[(df["is_required"]) & (~df["is_uploaded"])]
            if not missing.empty:
                st.warning(f"⚠️ 必交但未上传: {len(missing)} 项")

    with st.expander("✏️ 标记某项已上传", expanded=False):
        item_id = st.number_input("清单项 ID", min_value=0, step=1, value=0, key="ipo_check_item_id")
        upload_date = st.date_input("上传日期", value=date(2025, 6, 13), key="ipo_check_upload_date")
        notes = st.text_input("备注", key="ipo_check_notes")
        if st.button("提交", key="ipo_check_submit"):
            if item_id and item_id > 0:
                r = _api(
                    "PUT",
                    f"/api/ipo-specials/submission/checklist/{item_id}",
                    json={
                        "is_uploaded": True,
                        "upload_date": upload_date.isoformat(),
                        "notes": notes or None,
                    },
                )
                if r:
                    st.success("已更新")
                    st.rerun()
            else:
                st.warning("请先填清单项 ID (必须 > 0)")


def show_ipo_specials() -> None:
    apply_feishu_theme()
    page_header('🎯', 'IPO 专属', '内控穿行测试 / 招股书勾稽 / 反馈意见回复')

    st.markdown(
        '<p style="font-size:1.8rem;font-weight:bold;color:#4472C4;">🎯 IPO 专属 (Pack D)</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "内控穿行 + 截止性 + 招股书勾稽 + 三年一期对比 + 客户供应商重叠 + 可比公司 + 反馈意见 + 申报清单"
    )
    project_id = pick_project(
        key="ipo_pick_project",
        fmt="name_only",
        no_projects_warning="尚未创建项目",
    )
    if not project_id:
        return

    tabs = st.tabs(
        [
            "🔍 内控穿行",
            "⏰ 截止性",
            "📖 招股书勾稽",
            "📊 三年一期",
            "🔀 客户供应商重叠",
            "🏢 可比公司",
            "📨 反馈意见",
            "✅ 申报清单",
        ]
    )
    with tabs[0]:
        _tab_walkthrough(project_id)
    with tabs[1]:
        _tab_cutoff(project_id)
    with tabs[2]:
        _tab_prospectus(project_id)
    with tabs[3]:
        _tab_period_compare(project_id)
    with tabs[4]:
        _tab_overlap(project_id)
    with tabs[5]:
        _tab_peer(project_id)
    with tabs[6]:
        _tab_feedback(project_id)
    with tabs[7]:
        _tab_checklist(project_id)
