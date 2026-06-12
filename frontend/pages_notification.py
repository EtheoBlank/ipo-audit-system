"""通知中心页面 (Pack A)."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import streamlit as st

from frontend._http import API_BASE_URL, api_request


def _api(method: str, endpoint: str, **kwargs):
    """薄封装 — 复用共享 _http.api_request, 保持页面内调用习惯."""
    return api_request(method, endpoint, **kwargs)


_MODULE_LABELS = {
    "auth": "认证 / 用户",
    "approval": "审批流",
    "confirmation": "函证",
    "inventory": "盘点",
    "blocker": "卡点",
    "account_audit": "长期资产审定",
    "related_party": "关联方",
    "prospectus": "招股书勾稽",
    "feedback": "反馈意见",
    "sentiment": "舆情跟踪",
    "system": "系统",
}

_SEVERITY_BADGE = {
    "info": "🔵",
    "notice": "🟢",
    "warn": "🟡",
    "critical": "🔴",
}


def show_notifications() -> None:
    st.markdown('<p style="font-size:1.8rem;font-weight:bold;color:#4472C4;">🔔 通知中心</p>',
                unsafe_allow_html=True)

    # 头部摘要
    counts = _api("GET", "/api/notifications/unread") or {
        "total_unread": 0, "by_module": {}, "by_severity": {}
    }
    cols = st.columns(5)
    cols[0].metric("未读总数", counts.get("total_unread", 0))
    sev = counts.get("by_severity", {}) or {}
    cols[1].metric("🔴 严重", sev.get("critical", 0))
    cols[2].metric("🟡 告警", sev.get("warn", 0))
    cols[3].metric("🟢 提示", sev.get("notice", 0))
    cols[4].metric("🔵 信息", sev.get("info", 0))

    with st.expander("按模块查看未读分布", expanded=False):
        by_mod = counts.get("by_module") or {}
        if by_mod:
            df = pd.DataFrame(
                [{"模块": _MODULE_LABELS.get(k, k), "code": k, "未读数": v} for k, v in by_mod.items()]
            ).sort_values("未读数", ascending=False)
            st.dataframe(df, width="stretch")
        else:
            st.info("当前无未读通知")

    st.markdown("---")
    # 筛选 + 列表
    c1, c2, c3, c4 = st.columns(4)
    module_filter = c1.selectbox(
        "模块",
        ["全部"] + list(_MODULE_LABELS.keys()),
        format_func=lambda k: "全部" if k == "全部" else f"{k} ({_MODULE_LABELS.get(k, k)})",
    )
    severity_filter = c2.selectbox("严重度", ["全部", "info", "notice", "warn", "critical"])
    only_unread = c3.checkbox("仅未读", value=True)
    limit = c4.number_input("每页", min_value=10, max_value=200, value=50, step=10)

    params: Dict[str, Any] = {"limit": int(limit), "only_unread": only_unread}
    if module_filter != "全部":
        params["module"] = module_filter
    if severity_filter != "全部":
        params["severity"] = severity_filter

    res = _api("GET", "/api/notifications/list", params=params) or {
        "total": 0, "unread": 0, "items": []
    }
    items = res.get("items", [])

    cright, cleft = st.columns([1, 1])
    with cright:
        if st.button("✅ 标记本页全部已读"):
            ids = [int(i["id"]) for i in items if not i["is_read"]]
            if ids:
                r = _api("POST", "/api/notifications/mark-read",
                         json={"ids": ids, "mark_all": False})
                if r:
                    st.success(f"已标记 {r.get('updated', 0)} 条")
                    st.rerun()
    with cleft:
        if st.button("📭 全部标记已读"):
            r = _api("POST", "/api/notifications/mark-read",
                     json={"mark_all": True})
            if r:
                st.success(f"已标记 {r.get('updated', 0)} 条")
                st.rerun()

    if not items:
        st.info("无符合条件的通知")
        return

    for it in items:
        sev = _SEVERITY_BADGE.get(it.get("severity"), "🔵")
        unread = "🆕 " if not it.get("is_read") else ""
        title = f"{unread}{sev} [{_MODULE_LABELS.get(it.get('module'), it.get('module'))}] {it.get('title')}"
        with st.expander(title, expanded=False):
            cc = st.columns([2, 1, 1])
            cc[0].markdown(f"**{it.get('title')}**")
            cc[1].markdown(f"时间: {it.get('created_at')}")
            cc[2].markdown(f"项目: {it.get('project_id') or '-'}")
            if it.get("body"):
                st.write(it["body"])
            if it.get("link"):
                st.markdown(f"🔗 [打开]({it['link']})")
            if it.get("payload"):
                with st.expander("详细载荷", expanded=False):
                    st.code(it["payload"])
            if not it.get("is_read"):
                if st.button(f"标记已读", key=f"mr_{it['id']}"):
                    r = _api("POST", "/api/notifications/mark-read",
                             json={"ids": [it["id"]]})
                    if r:
                        st.rerun()
