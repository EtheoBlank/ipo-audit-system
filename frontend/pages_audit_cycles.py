"""Pack C — 10 个审计循环统一页面 (精简版)."""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import requests
import streamlit as st

from frontend._http import API_BASE_URL, api_request, auth_headers


def _api(method: str, endpoint: str, **kwargs):
    return api_request(method, endpoint, timeout=60, **kwargs)


@st.cache_data(ttl=60)
def _get_projects():
    try:
        r = requests.get(f"{API_BASE_URL}/api/projects/", timeout=10, headers=auth_headers())
        if r.status_code == 200:
            return r.json() or []
    except Exception:
        pass
    return []


def _pick_project() -> Optional[int]:
    projects = _get_projects()
    if not projects:
        st.warning("尚未创建项目, 请先在 '📁 项目管理' 创建")
        return None
    options = {f"{p['id']} - {p.get('name','')}": p["id"] for p in projects}
    label = st.selectbox("选择项目", list(options.keys()), key="ac_pick_project")
    return options[label]


def _tab_payables(project_id: int) -> None:
    st.markdown("### 💼 应付循环 (P2P)")
    st.caption("供应商主数据 + 应付账龄 + 应付函证. 函证走 '📬 函证管理' 模块, 此处仅维护账龄.")
    rows = _api("GET", f"/api/audit-cycles/payables/suppliers/projects/{project_id}/list") or {"items": []}
    if rows.get("items"):
        st.dataframe(pd.DataFrame(rows["items"]).head(50), width="stretch", height=300)
    else:
        st.info("暂无供应商数据")


def _tab_expenses(project_id: int) -> None:
    st.markdown("### 💸 费用循环")
    if st.button("🔍 扫描异常费用"):
        with st.spinner("扫描中..."):
            r = _api("POST", f"/api/audit-cycles/expenses/scan-anomalies/{project_id}")
        if r:
            st.success(f"扫描 {r.get('scanned')} 条, 标记异常 {r.get('flags_added')} 条")

    st.markdown("#### 业务招待费 60% / 1‰ 限额测算")
    c1, c2 = st.columns(2)
    sales = c1.number_input("营业收入", min_value=0.0, value=10_000_000.0, step=100000.0)
    entertainment = c2.number_input("业务招待费", min_value=0.0, value=100_000.0, step=1000.0)
    if st.button("📊 计算限额"):
        r = _api("POST", "/api/audit-cycles/expenses/entertainment-deduction-limit",
                 json={"sales_revenue": sales, "entertainment_amount": entertainment})
        if r:
            cols = st.columns(4)
            cols[0].metric("60% 额度", f"{r['60_pct']:,.0f}")
            cols[1].metric("1‰ 额度", f"{r['5_per_mille_of_revenue']:,.0f}")
            cols[2].metric("✅ 可扣除", f"{r['deductible']:,.0f}")
            cols[3].metric("❌ 纳税调增", f"{r['non_deductible_adjustment']:,.0f}")


def _tab_payroll(project_id: int) -> None:
    st.markdown("### 👷 薪酬循环")
    period = st.text_input("期间 YYYY-MM", value="2024-12", key="pay_period")
    if st.button("🔍 跑四表勾稽"):
        r = _api("POST", f"/api/audit-cycles/payroll/reconcile/{project_id}",
                 params={"period_yyyymm": period})
        if r:
            cols = st.columns(3)
            cols[0].metric("工资合计", f"{r.get('payroll_total', 0):,.2f}")
            cols[1].metric("差异", f"{r.get('discrepancy_amount', 0):,.2f}")
            cols[2].metric("勾稽", "✅" if r.get("is_balanced") else "❌")
            if r.get("notes"):
                st.warning(r["notes"])


def _tab_fixed_assets(project_id: int) -> None:
    st.markdown("### 🏭 固定资产 + 在建工程")
    st.caption("📑 长期资产发生额审定 见 Pack A 模块")
    st.markdown("#### 折旧重算计算器")
    c1, c2, c3 = st.columns(3)
    cost = c1.number_input("原值", value=120000.0, step=1000.0)
    salvage = c2.number_input("残值率", value=0.05, min_value=0.0, max_value=0.5, step=0.01)
    life = c3.number_input("使用年限 (月)", value=120, min_value=1, step=12)
    method = st.selectbox("方法", ["straight_line", "double_declining"])
    if st.button("📊 计算"):
        r = _api("POST", "/api/audit-cycles/fixed-assets/depreciation-calc",
                 json={"original_cost": cost, "salvage_rate": salvage,
                       "useful_life_months": int(life), "method": method,
                       "net_book_value": cost})
        if r:
            st.success(f"月折旧 {r.get('monthly_depreciation', 0):,.2f}, 年折旧 {r.get('annual_depreciation', 0):,.2f}")


def _tab_intangible(project_id: int) -> None:
    st.markdown("### 🧠 无形资产 + 研发资本化")
    st.markdown("#### CAS 6 五项条件 + 成本可计量 评估")
    cols = st.columns(2)
    c1 = cols[0].checkbox("技术可行性")
    c2 = cols[1].checkbox("完成意图")
    c3 = cols[0].checkbox("出售或使用能力")
    c4 = cols[1].checkbox("未来经济利益")
    c5 = cols[0].checkbox("资源充足")
    c6 = cols[1].checkbox("成本可计量")
    if st.button("📊 评估"):
        r = _api("POST", "/api/audit-cycles/intangible/rd-capitalization-check",
                 json={
                     "technical_feasibility": c1, "intent_to_complete": c2,
                     "ability_to_use_or_sell": c3, "future_economic_benefit": c4,
                     "resources_sufficient": c5, "cost_measurable": c6,
                 })
        if r:
            if r.get("can_capitalize"):
                st.success("✅ 全部条件满足, 可资本化")
            else:
                st.error(f"❌ 缺失条件: {', '.join(r.get('missing_conditions', []))}")

    st.markdown("#### 研发费用加计扣除")
    rd_exp = st.number_input("研发支出", value=1_000_000.0, step=10000.0)
    is_mfg = st.checkbox("制造业 (100% 加计)", value=True)
    if st.button("📊 算加计"):
        r = _api("POST", "/api/audit-cycles/intangible/rd-super-deduction",
                 json={"rd_expense": rd_exp, "manufacturing": is_mfg})
        if r:
            cols = st.columns(3)
            cols[0].metric("研发支出", f"{r['rd_expense']:,.0f}")
            cols[1].metric("加计扣除", f"{r['super_deduction']:,.0f}")
            cols[2].metric("税前扣除合计", f"{r['total_deductible']:,.0f}")


def _tab_long_term_investment(project_id: int) -> None:
    st.markdown("### 💎 长期股权投资 + 商誉减值")
    st.markdown("#### NPV 现金流折现 (商誉减值用)")
    cashflows_text = st.text_input(
        "未来年度现金流 (逗号分隔, 通常 5-8 年)",
        value="500000,550000,600000,650000,700000",
    )
    discount = st.number_input("折现率", value=0.10, min_value=0.01, max_value=0.30, step=0.01)
    if st.button("📊 算 NPV"):
        try:
            flows = [float(x.strip()) for x in cashflows_text.split(",") if x.strip()]
            r = _api("POST", "/api/audit-cycles/long-term-investment/goodwill-npv",
                     json={"annual_cashflows": flows, "discount_rate": discount})
            if r:
                st.metric("NPV (可收回金额估算)", f"{r.get('npv', 0):,.2f}")
        except Exception as exc:
            st.error(f"参数错: {exc}")

    st.markdown("#### 商誉减值金额")
    c1, c2 = st.columns(2)
    bv = c1.number_input("含商誉账面价值", value=1_000_000.0, step=10000.0)
    rec = c2.number_input("可收回金额", value=800_000.0, step=10000.0)
    if st.button("📊 算减值"):
        r = _api("POST", "/api/audit-cycles/long-term-investment/goodwill-impairment-amount",
                 json={"book_value_with_goodwill": bv, "recoverable_amount": rec})
        if r:
            if r.get("is_impaired"):
                st.error(f"❌ 需计提减值 {r['impairment_required']:,.2f}")
            else:
                st.success("✅ 不需减值")


def _tab_leases(project_id: int) -> None:
    st.markdown("### 🏠 租赁 (CAS 21)")
    st.markdown("#### 使用权资产现值计算")
    c1, c2, c3 = st.columns(3)
    payment = c1.number_input("月付租金", value=10000.0, step=1000.0)
    periods = c2.number_input("租期 (月)", value=36, min_value=1, max_value=360, step=1)
    rate = c3.number_input("年折现率", value=0.05, min_value=0.0, max_value=0.30, step=0.005)
    if st.button("📊 算 PV"):
        r = _api("POST", "/api/audit-cycles/leases/present-value",
                 json={"payment": payment, "periods": int(periods), "annual_rate": rate})
        if r:
            st.success(f"使用权资产 / 租赁负债初始值 (PV) = {r.get('present_value', 0):,.2f}")


def _tab_income_tax(project_id: int) -> None:
    st.markdown("### 💵 所得税重算")
    c1, c2 = st.columns(2)
    pretax = c1.number_input("利润总额", value=10_000_000.0, step=100000.0)
    permanent = c1.number_input("永久性差异", value=500_000.0, step=10000.0)
    temporary = c2.number_input("暂时性差异", value=200_000.0, step=10000.0)
    losses = c2.number_input("弥补亏损用", value=0.0, step=10000.0)
    rate = st.number_input("名义税率", value=0.25, min_value=0.0, max_value=0.35, step=0.01)
    if st.button("📊 重算"):
        r = _api("POST", "/api/audit-cycles/income-tax/reconcile",
                 json={"pretax_profit": pretax, "permanent_diff": permanent,
                       "temporary_diff": temporary, "losses_used": losses,
                       "nominal_rate": rate})
        if r:
            cols = st.columns(3)
            cols[0].metric("应纳税所得", f"{r['taxable_income']:,.0f}")
            cols[1].metric("应交所得税", f"{r['current_tax']:,.0f}")
            cols[2].metric("实际税率", f"{r['effective_rate']*100:.2f}%")


def _tab_estimates(project_id: int) -> None:
    st.markdown("### 📐 重要会计估计 (ECL 三阶段)")
    c1, c2, c3 = st.columns(3)
    receivable = c1.number_input("应收账款", value=1_000_000.0, step=10000.0)
    aging = c2.number_input("账龄 (天)", value=60, min_value=0, step=10)
    lgd = c3.number_input("LGD", value=0.45, min_value=0.0, max_value=1.0, step=0.05)
    if st.button("📊 算 ECL"):
        r = _api("POST", "/api/audit-cycles/accounting-estimates/ecl-compute",
                 json={"receivable": receivable, "aging_days": int(aging), "lgd": lgd})
        if r:
            cols = st.columns(4)
            cols[0].metric("Stage", f"{r['stage']} 阶段")
            cols[1].metric("默认 PD", f"{r['default_pd']*100:.0f}%")
            cols[2].metric("ECL 金额", f"{r['ecl_amount']:,.2f}")
            cols[3].metric("占比", f"{r['ecl_pct_of_receivable']:.2f}%")


def _tab_subsequent(project_id: int) -> None:
    st.markdown("### 📅 后续期间 + 持续经营")
    st.markdown("#### 后续期间事项分类")
    desc = st.text_input("事项描述", value="应收账款无法收回, 金额 100 万")
    event_date = st.text_input("事项日期", value="2025-02-15")
    bs_date = st.text_input("资产负债表日", value="2024-12-31")
    if st.button("📊 分类"):
        r = _api("POST", "/api/audit-cycles/subsequent-events/classify",
                 json={"event_description": desc, "event_date": event_date,
                       "balance_sheet_date": bs_date})
        if r:
            t = r.get("event_type")
            if t == "adjusting":
                st.warning("⚠️ 调整事项 — 需调整本期报表")
            else:
                st.info("📌 非调整事项 — 仅披露")

    st.markdown("#### 持续经营评估")
    c1, c2 = st.columns(2)
    ocf = c1.number_input("未来 12 月经营现金流", value=5_000_000.0, step=100000.0)
    interest = c2.number_input("12 月利息支出", value=500_000.0, step=10000.0)
    debt = c1.number_input("12 月到期债务", value=2_000_000.0, step=100000.0)
    cash = c2.number_input("当前现金", value=1_000_000.0, step=100000.0)
    if st.button("📊 评估"):
        r = _api("POST", "/api/audit-cycles/subsequent-events/going-concern",
                 json={"operating_cashflow_12m": ocf, "interest_expense_12m": interest,
                       "debt_due_12m": debt, "cash_balance": cash, "available_credit": 0})
        if r:
            level = r.get("risk_level")
            emoji = {"low": "✅", "medium": "🟡", "high": "🟠", "substantial_doubt": "🔴"}.get(level, "?")
            st.info(f"{emoji} 风险等级: **{level}** — {r.get('conclusion')}")


def show_audit_cycles() -> None:
    st.markdown(
        '<p style="font-size:1.8rem;font-weight:bold;color:#4472C4;">🔄 审计循环 (Pack C)</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "10 个循环 — 应付 / 费用 / 薪酬 / 固定资产 / 无形资产 / 长投 / 租赁 / 所得税 / 会计估计 / 后续期间"
    )
    project_id = _pick_project()
    if not project_id:
        return

    tabs = st.tabs([
        "💼 应付", "💸 费用", "👷 薪酬", "🏭 固定资产",
        "🧠 无形/研发", "💎 长投/商誉", "🏠 租赁", "💵 所得税",
        "📐 估计/ECL", "📅 期后/经营",
    ])
    with tabs[0]: _tab_payables(project_id)
    with tabs[1]: _tab_expenses(project_id)
    with tabs[2]: _tab_payroll(project_id)
    with tabs[3]: _tab_fixed_assets(project_id)
    with tabs[4]: _tab_intangible(project_id)
    with tabs[5]: _tab_long_term_investment(project_id)
    with tabs[6]: _tab_leases(project_id)
    with tabs[7]: _tab_income_tax(project_id)
    with tabs[8]: _tab_estimates(project_id)
    with tabs[9]: _tab_subsequent(project_id)
