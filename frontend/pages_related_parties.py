"""关联方专项页面 (Pack B). 8 个 tab."""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import requests
import streamlit as st

from frontend._http import API_BASE_URL, api_request, auth_headers


def _api(method: str, endpoint: str, *, expect_bytes: bool = False, **kwargs):
    return api_request(method, endpoint, expect_bytes=expect_bytes, timeout=60, **kwargs)


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
    options = {f"{p['id']} - {p.get('name','')} / {p.get('company_name','')}": p["id"] for p in projects}
    label = st.selectbox("选择项目", list(options.keys()), key="rp_pick_project")
    return options[label]


_PARTY_TYPE_LABELS = {
    "controlling_shareholder": "控股股东",
    "actual_controller": "实际控制人",
    "director_or_senior": "董监高",
    "key_management": "关键管理人员",
    "shareholder_5pct": "持股 5%+ 股东",
    "family_member": "家庭成员",
    "controlled_entity": "受控制的企业",
    "joint_controlled_entity": "共同控制的企业",
    "significant_influence": "重大影响关系企业",
    "other": "其他",
}


def _tab_main_data(project_id: int) -> None:
    st.markdown("### 📋 关联方主数据")
    cols = st.columns(4)
    party_type = cols[0].selectbox(
        "类型", ["全部"] + list(_PARTY_TYPE_LABELS.keys()),
        format_func=lambda k: "全部" if k == "全部" else _PARTY_TYPE_LABELS.get(k, k),
    )
    confirmed = cols[1].selectbox("状态", ["全部", "已确认", "待确认"])
    keyword = cols[2].text_input("名称关键词")
    cols[3].markdown("&nbsp;")

    params: Dict[str, Any] = {"limit": 500}
    if party_type != "全部":
        params["party_type"] = party_type
    if confirmed != "全部":
        params["is_confirmed"] = confirmed == "已确认"
    if keyword:
        params["keyword"] = keyword

    res = _api("GET", f"/api/related-parties/projects/{project_id}/parties", params=params) or {
        "total": 0, "items": []
    }
    items = res.get("items", [])
    st.metric("命中数", res.get("total", 0))
    if items:
        df = pd.DataFrame([
            {
                "ID": i["id"],
                "名称": i["name"],
                "类型": _PARTY_TYPE_LABELS.get(i["party_type"], i["party_type"]),
                "性质": "公司" if i["party_kind"] == "entity" else "自然人",
                "信用代码": i.get("unified_credit_code") or "-",
                "持股%": i.get("holding_pct") or 0,
                "来源": i.get("source"),
                "已确认": "✅" if i.get("is_confirmed") else "⏳",
                "已披露": "✅" if i.get("is_disclosed_in_prospectus") else "❌",
                "可信度": f"{i.get('confidence', 0):.2f}",
            }
            for i in items
        ])
        st.dataframe(df, width="stretch", height=400)

    with st.expander("➕ 新建关联方", expanded=False):
        with st.form("new_rp"):
            c1, c2 = st.columns(2)
            name = c1.text_input("名称*")
            party_type_new = c2.selectbox(
                "类型*", list(_PARTY_TYPE_LABELS.keys()),
                format_func=lambda k: _PARTY_TYPE_LABELS.get(k, k),
            )
            party_kind = c1.selectbox("性质", ["entity", "person"])
            credit_code = c2.text_input("统一社会信用代码")
            holding_pct = c1.number_input("持股 %", min_value=0.0, max_value=100.0, step=0.01)
            disclosed = c2.checkbox("已在招股书披露")
            relation_chain = st.text_area("关系链描述")
            ok = st.form_submit_button("提交", type="primary")
        if ok and name:
            payload = {
                "name": name,
                "party_type": party_type_new,
                "party_kind": party_kind,
                "unified_credit_code": credit_code or None,
                "holding_pct": holding_pct if holding_pct > 0 else None,
                "is_disclosed_in_prospectus": disclosed,
                "relation_chain": relation_chain or None,
                "source": "manual",
                "confidence": 1.0,
            }
            r = _api("POST", f"/api/related-parties/projects/{project_id}/parties", json=payload)
            if r:
                st.success(f"已新建关联方 {r['name']}")
                st.rerun()


def _tab_relations(project_id: int) -> None:
    st.markdown("### 🔗 关系图")
    rows = _api("GET", f"/api/related-parties/projects/{project_id}/relations") or []
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch")
    else:
        st.info("暂无关系数据, 在下方添加")

    with st.expander("➕ 新增关系", expanded=False):
        with st.form("new_rel"):
            c1, c2, c3 = st.columns(3)
            a = c1.number_input("party_a_id", min_value=1, step=1)
            b = c2.number_input("party_b_id", min_value=1, step=1)
            rt = c3.text_input("关系类型 (持股/配偶/父母/子女/任职 等)")
            holding = st.number_input("持股 %", min_value=0.0, max_value=100.0, step=0.01)
            ok = st.form_submit_button("提交", type="primary")
        if ok and a and b and rt:
            payload = {
                "party_a_id": int(a), "party_b_id": int(b), "relation_type": rt,
                "holding_pct": holding if holding > 0 else None,
            }
            r = _api("POST", f"/api/related-parties/projects/{project_id}/relations", json=payload)
            if r:
                st.success("已新增")
                st.rerun()


def _tab_detector(project_id: int) -> None:
    st.markdown("### 🔍 识别引擎")
    st.caption(
        "多通道识别 — 序时账摘要扫描 (关联方关键词) + 客户/供应商交叉重叠. "
        "命中后产生候选, 需人工 confirm 才落库."
    )
    c1, c2, c3 = st.columns(3)
    enable_chrono = c1.checkbox("启用序时账扫描", value=True)
    enable_overlap = c2.checkbox("启用客户/供应商重叠", value=True)
    c3.markdown("&nbsp;")
    extra_keywords = st.text_input("额外关键词 (逗号分隔, 可选)")

    if st.button("🚀 立即识别", type="primary"):
        payload = {
            "project_id": project_id,
            "enable_chrono_scan": enable_chrono,
            "enable_customer_overlap": enable_overlap,
            "enable_prospectus_compare": False,
        }
        if extra_keywords:
            payload["keywords_extra"] = [k.strip() for k in extra_keywords.split(",") if k.strip()]
        with st.spinner("识别中..."):
            res = _api("POST", f"/api/related-parties/projects/{project_id}/detector/run", json=payload)
        if res:
            st.success(f"扫描 {res.get('scanned_vouchers', 0)} 凭证 / {res.get('scanned_customers', 0)} 客户, 新候选 {res.get('new_candidates', 0)}")
            cands = res.get("candidates", [])
            if cands:
                st.markdown(f"#### 候选关联方 ({len(cands)} 个)")
                df = pd.DataFrame([
                    {
                        "名称": c["name"],
                        "类型": _PARTY_TYPE_LABELS.get(c["party_type"], c["party_type"]),
                        "来源": c["source"],
                        "可信度": f"{c.get('confidence', 0):.2f}",
                        "证据": "\n".join(c.get("evidence", [])),
                    }
                    for c in cands
                ])
                st.dataframe(df, width="stretch", height=400)
                st.info("候选已生成. 请到 '主数据' tab 手工新建对应关联方 (类型选 controlled_entity 等), 完成 confirm 流程.")


def _tab_transactions(project_id: int) -> None:
    st.markdown("### 💰 关联交易")
    rows = _api("GET", f"/api/related-parties/projects/{project_id}/transactions",
                params={"limit": 500}) or []
    if rows:
        df = pd.DataFrame(rows)
        cols_show = ["id", "party_id", "transaction_type", "period_end", "amount",
                     "currency", "pricing_basis", "is_fair", "fairness_score"]
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], width="stretch", height=380)
    else:
        st.info("暂无关联交易")

    with st.expander("➕ 新增关联交易", expanded=False):
        with st.form("new_tx"):
            c1, c2, c3 = st.columns(3)
            party_id = c1.number_input("party_id*", min_value=1, step=1)
            tx_type = c2.selectbox(
                "类型*",
                ["sales", "purchase", "loan_receivable", "loan_payable",
                 "guarantee", "lease", "service", "shared_resource",
                 "asset_transfer", "other"],
            )
            amount = c3.number_input("金额*", min_value=0.0, step=100.0)
            period_end = c1.text_input("期末日期 YYYY-MM-DD")
            pricing = c2.text_input("定价依据 (市场公允/成本加成/协议)")
            note = c3.text_input("备注")
            ok = st.form_submit_button("提交", type="primary")
        if ok and party_id and amount > 0:
            payload = {
                "party_id": int(party_id), "transaction_type": tx_type,
                "amount": float(amount), "period_end": period_end or None,
                "pricing_basis": pricing or None, "notes": note or None,
            }
            r = _api("POST", f"/api/related-parties/projects/{project_id}/transactions", json=payload)
            if r:
                st.success("已新增")
                st.rerun()

    st.markdown("---")
    st.markdown("#### 公允性测试")
    period_end_for_check = st.text_input("期末日期 (留空 = 全部)", key="rp_fair_pe")
    if st.button("🔍 跑公允性测试"):
        payload = {"period_end": period_end_for_check or None}
        with st.spinner("分析中..."):
            r = _api("POST",
                     f"/api/related-parties/projects/{project_id}/transactions/check-fairness",
                     json=payload)
        if r:
            st.success(
                f"评估 {r.get('assessed', 0)} 笔, 公允 {r.get('fair', 0)}, "
                f"不公允 {r.get('not_fair', 0)}, 平均分 {r.get('avg_score', 0)}"
            )


def _tab_capital_occupation(project_id: int) -> None:
    st.markdown("### 💸 资金占用穿行")
    rows = _api("GET", f"/api/related-parties/projects/{project_id}/capital-occupations") or []
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", height=380)
    else:
        st.info("暂无资金占用记录")

    with st.expander("🔍 自动计算占用余额", expanded=False):
        c1, c2, c3 = st.columns(3)
        party_id = c1.number_input("party_id", min_value=1, step=1, key="rp_co_party")
        period_start = c2.text_input("起始日期", key="rp_co_start")
        period_end = c3.text_input("结束日期", key="rp_co_end")
        if st.button("📊 计算"):
            if party_id and period_start and period_end:
                r = _api(
                    "GET",
                    f"/api/related-parties/projects/{project_id}/capital-occupations/auto-compute",
                    params={
                        "party_id": int(party_id),
                        "period_start": period_start,
                        "period_end": period_end,
                    },
                )
                if r:
                    st.success(
                        f"最大占用 {r.get('max_amount', 0):.2f} 元 "
                        f"(发生 {r.get('voucher_count', 0)} 笔, "
                        f"最大日期 {r.get('max_date', '-')}, 期末 {r.get('ending_balance', 0):.2f})"
                    )


def _tab_peer_competition(project_id: int) -> None:
    st.markdown("### 🏭 同业竞争评估")
    rows = _api("GET", f"/api/related-parties/projects/{project_id}/peer-competition") or []
    if rows:
        df = pd.DataFrame(rows)
        cols_show = ["party_id", "overlap_score", "overlap_keywords",
                     "risk_level", "solution_type", "assessed_at"]
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], width="stretch")
    else:
        st.info("暂无评估记录")

    with st.expander("🔍 评估同业竞争", expanded=False):
        with st.form("peer_assess"):
            party_id = st.number_input("关联方 ID*", min_value=1, step=1)
            keywords_input = st.text_area(
                "发行人主业关键词 (逗号分隔, 例如: 芯片设计, 集成电路, EDA)*",
            )
            ok = st.form_submit_button("评估", type="primary")
        if ok and party_id and keywords_input:
            kw = [k.strip() for k in keywords_input.split(",") if k.strip()]
            payload = {
                "party_id": int(party_id),
                "issuer_business_keywords": kw,
                "use_ai": False,
            }
            r = _api(
                "POST",
                f"/api/related-parties/projects/{project_id}/peer-competition/assess",
                json=payload,
            )
            if r:
                st.success(
                    f"重合度 {r.get('overlap_score', 0)} / 风险等级 {r.get('risk_level', '-')}"
                )
                st.rerun()


def _tab_disclosure(project_id: int) -> None:
    st.markdown("### 📑 招股书披露 diff")
    st.caption("把招股书 '关联方及关联交易' 章节里所披露的关联方名单粘到下方 (一行一个), 与系统识别的 diff.")

    prospectus_names = st.text_area(
        "招股书披露的关联方名单 (一行一个)",
        height=120,
        placeholder="X 集团有限公司\nXX 投资管理 (上海) 有限公司\n...",
    )
    if st.button("🔍 对比 diff", type="primary"):
        names = [n.strip() for n in prospectus_names.splitlines() if n.strip()]
        payload = {"project_id": project_id, "prospectus_party_names": names}
        with st.spinner("对比中..."):
            r = _api(
                "POST",
                f"/api/related-parties/projects/{project_id}/disclosure/check",
                json=payload,
            )
        if r:
            c1, c2, c3 = st.columns(3)
            c1.metric("🔴 critical (系统识别未披露)", r.get("total_critical", 0))
            c2.metric("🟡 review (披露但系统未识别)", r.get("total_review", 0))
            c3.metric("✅ matched", r.get("matched", 0))

    st.markdown("---")
    st.markdown("#### 当前 gap 列表")
    gap_status = st.selectbox("状态筛选", ["全部", "critical", "review"])
    params: Dict[str, Any] = {}
    if gap_status != "全部":
        params["gap_status"] = gap_status
    gaps = _api(
        "GET",
        f"/api/related-parties/projects/{project_id}/disclosure/gaps",
        params=params,
    ) or []
    if gaps:
        df = pd.DataFrame(gaps)
        cols_show = ["party_name", "gap_status", "in_system", "in_prospectus",
                     "transaction_count", "total_amount", "suggested_action", "resolved"]
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], width="stretch", height=350)
    else:
        st.info("暂无 gap, 请先点 '对比 diff'")


def _tab_report(project_id: int) -> None:
    st.markdown("### 📄 关联方专项报告")
    st.caption(
        "汇总关联方主数据 / 交易 / 资金占用 / 同业竞争 / 披露 diff / 整改建议, 一键导出 Word.\n"
        "MVP: 导出 Excel 多 sheet 版本 (Word 报告留 Pack B.2)."
    )
    period_end = st.text_input("期末日期", value="2024-12-31", key="rp_rpt_pe")
    if st.button("📊 生成报告 (TODO)", disabled=True):
        st.info("报告生成功能 Pack B.2 实现 — 当前可分别在各 tab 导出明细")


def show_related_parties() -> None:
    st.markdown(
        '<p style="font-size:1.8rem;font-weight:bold;color:#4472C4;">🤝 关联方专项</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "IPO 最大雷区 — 主数据 + 识别引擎 + 交易公允性 + 资金占用 + 同业竞争 + 招股书披露核查"
    )
    project_id = _pick_project()
    if not project_id:
        return

    tabs = st.tabs([
        "📋 主数据", "🔗 关系图", "🔍 识别引擎", "💰 关联交易",
        "💸 资金占用", "🏭 同业竞争", "📑 披露 diff", "📄 专项报告",
    ])
    with tabs[0]:
        _tab_main_data(project_id)
    with tabs[1]:
        _tab_relations(project_id)
    with tabs[2]:
        _tab_detector(project_id)
    with tabs[3]:
        _tab_transactions(project_id)
    with tabs[4]:
        _tab_capital_occupation(project_id)
    with tabs[5]:
        _tab_peer_competition(project_id)
    with tabs[6]:
        _tab_disclosure(project_id)
    with tabs[7]:
        _tab_report(project_id)
