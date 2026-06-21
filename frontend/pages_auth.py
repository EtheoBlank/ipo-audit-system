"""认证与权限管理页面 (Pack A)."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import requests
import streamlit as st

from frontend._components import apply_feishu_theme, page_header
from frontend._http import (
    API_BASE_URL,
    api_request,
    validate_password_strength,
)


def _api(method: str, endpoint: str, *, expect_json: bool = True, **kwargs):
    """薄封装 — 复用共享 _http.api_request."""
    return api_request(method, endpoint, **kwargs)


def _login_form() -> None:
    st.markdown("### 🔐 登录")
    with st.form("login_form"):
        username = st.text_input("用户名", value=st.session_state.get("last_login_user", ""))
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary")
    if not submitted:
        st.info("默认管理员: admin / Admin@1234 (首次启动自动创建, 请尽快修改密码)")
        return
    if not username or not password:
        st.error("请输入用户名和密码")
        return
    r = requests.post(
        f"{API_BASE_URL}/api/auth/login",
        json={"username": username, "password": password},
        timeout=15,
    )
    if r.status_code == 200:
        data = r.json()
        st.session_state.auth_token = data["access_token"]
        st.session_state.auth_refresh_token = data.get("refresh_token")
        st.session_state.auth_user = data["user"]
        st.session_state.last_login_user = username
        st.success(f"欢迎 {data['user']['full_name']} ({data['user']['role']})")
        st.rerun()
    else:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text[:300]
        st.error(f"登录失败: {detail}")


def _tab_me() -> None:
    user = st.session_state.get("auth_user") or {}
    st.markdown("### 👤 当前用户")
    cols = st.columns(3)
    cols[0].metric("用户名", user.get("username", "-"))
    cols[1].metric("角色", user.get("role", "-"))
    cols[2].metric("状态", "✅" if user.get("is_active") else "🚫")

    with st.expander("修改密码", expanded=False):
        with st.form("change_pwd"):
            old = st.text_input("旧密码", type="password")
            new = st.text_input("新密码 (至少 8 位 + 字母 + 数字)", type="password")
            new2 = st.text_input("确认新密码", type="password")
            ok = st.form_submit_button("提交", type="primary")
        if ok:
            if not old or not new:
                st.error("不能为空")
            elif new != new2:
                st.error("两次输入的新密码不一致")
            else:
                err = validate_password_strength(new)
                if err:
                    st.error(err)
                else:
                    res = _api(
                        "POST",
                        "/api/auth/me/change-password",
                        json={"old_password": old, "new_password": new},
                    )
                    if res:
                        st.success("密码已更新, 下次登录请使用新密码")

    if st.button("🚪 登出", key="auth_tab_profile_logout"):  # round 31 widget key
        _api("POST", "/api/auth/logout")
        st.session_state.pop("auth_token", None)
        st.session_state.pop("auth_user", None)
        st.session_state.pop("auth_refresh_token", None)
        st.rerun()


def _tab_users() -> None:
    st.markdown("### 👥 用户管理")
    cols = st.columns(4)
    keyword = cols[0].text_input("关键词 (用户名/姓名/邮箱)", key="auth_users_kw")
    role = cols[1].selectbox(
        "角色",
        ["", "assistant", "manager", "partner", "qc_partner", "signing_partner", "admin"],
        key="auth_users_role",
    )
    active = cols[2].selectbox("状态", ["", "active", "inactive"], key="auth_users_active")
    cols[3].markdown("&nbsp;")
    is_active = None if active == "" else (active == "active")

    params: Dict[str, Any] = {"limit": 200}
    if keyword:
        params["keyword"] = keyword
    if role:
        params["role"] = role
    if is_active is not None:
        params["is_active"] = is_active

    rows = _api("GET", "/api/auth/users", params=params) or []
    if rows:
        df = pd.DataFrame(rows)
        cols_show = [
            "id",
            "username",
            "full_name",
            "role",
            "firm_id",
            "is_active",
            "is_locked",
            "failed_login_count",
            "last_login_at",
            "created_at",
        ]
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], width="stretch", height=400)
    else:
        st.info("无用户")

    with st.expander("➕ 新建用户", expanded=False):
        with st.form("new_user"):
            c1, c2 = st.columns(2)
            username = c1.text_input("用户名*")
            full_name = c2.text_input("姓名*")
            email = c1.text_input("邮箱")
            phone = c2.text_input("电话")
            role_new = c1.selectbox(
                "角色*",
                ["assistant", "manager", "partner", "qc_partner", "signing_partner", "admin"],
            )
            firm_id = c2.number_input("事务所 ID", min_value=0, step=1, value=1)
            password = c1.text_input("初始密码 (至少 8 位 + 字母 + 数字)", type="password")
            notes = c2.text_input("备注")
            ok = st.form_submit_button("创建", type="primary")
        if ok:
            if not (username and full_name and password):
                st.error("用户名/姓名/密码必填")
            else:
                err = validate_password_strength(password)
                if err:
                    st.error(f"密码强度不足: {err}")
                else:
                    payload = {
                        "username": username,
                        "full_name": full_name,
                        "email": email or None,
                        "phone": phone or None,
                        "role": role_new,
                        "firm_id": int(firm_id) if firm_id else None,
                        "password": password,
                        "notes": notes or None,
                    }
                    res = _api("POST", "/api/auth/users", json=payload)
                    if res:
                        st.success(f"已创建用户 {res['username']}")
                        st.rerun()


def _tab_firms() -> None:
    st.markdown("### 🏢 事务所管理")
    rows = _api("GET", "/api/auth/firms") or []
    if rows:
        st.dataframe(pd.DataFrame(rows, height=400), width="stretch")
    with st.expander("➕ 新建事务所", expanded=False):
        with st.form("new_firm"):
            name = st.text_input("事务所名称*")
            short_name = st.text_input("简称")
            license_no = st.text_input("执业证号")
            address = st.text_input("地址")
            email = st.text_input("联系邮箱")
            phone = st.text_input("联系电话")
            ok = st.form_submit_button("创建", type="primary")
        if ok and name:
            payload = {
                "name": name,
                "short_name": short_name or None,
                "license_no": license_no or None,
                "address": address or None,
                "contact_email": email or None,
                "contact_phone": phone or None,
            }
            res = _api("POST", "/api/auth/firms", json=payload)
            if res:
                st.success(f"已创建 {res['name']}")
                st.rerun()


def _tab_roles_permissions() -> None:
    st.markdown("### 🛡️ 角色与权限")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**角色清单 (内置 + 自定义)**")
        rows = _api("GET", "/api/auth/roles") or []
        if rows:
            st.dataframe(pd.DataFrame(rows, height=400), width="stretch")
    with c2:
        st.markdown("**权限清单**")
        rows = _api("GET", "/api/auth/permissions") or []
        if rows:
            df = pd.DataFrame(rows)
            module = st.selectbox(
                "按模块筛选",
                [""] + sorted(df["module"].dropna().unique().tolist()),
                key="auth_perms_module",
            )  # round 31 widget key
            if module:
                df = df[df["module"] == module]
            st.dataframe(df, width="stretch", height=400)


def _tab_audit_logs() -> None:
    st.markdown("### 📜 审计轨迹")
    cols = st.columns(5)
    action = cols[0].selectbox(
        "动作",
        [
            "",
            "create",
            "update",
            "delete",
            "login",
            "logout",
            "approve",
            "reject",
            "export",
            "import",
            "http",
        ],
        key="auth_logs_action",  # round 31 widget key
    )
    resource_type = cols[1].text_input("资源类型", key="auth_logs_res_type")  # round 31 widget key
    keyword = cols[2].text_input("关键词", key="auth_logs_kw")  # round 31 widget key
    start_date = cols[3].text_input("开始日期 YYYY-MM-DD", key="auth_logs_start")  # round 31 widget key
    end_date = cols[4].text_input("结束日期", key="auth_logs_end")  # round 31 widget key

    params: Dict[str, Any] = {"limit": 200}
    if action:
        params["action"] = action
    if resource_type:
        params["resource_type"] = resource_type
    if keyword:
        params["keyword"] = keyword
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    res = _api("GET", "/api/auth/audit-logs", params=params) or {"total": 0, "items": []}
    st.metric("命中条数", res.get("total", 0))
    items = res.get("items", [])
    if items:
        df = pd.DataFrame(items)
        cols_show = [
            "id",
            "created_at",
            "user_display",
            "user_role",
            "action",
            "resource_type",
            "resource_id",
            "method",
            "path",
            "status_code",
            "summary",
        ]
        cols_show = [c for c in cols_show if c in df.columns]
        st.dataframe(df[cols_show], width="stretch", height=480)
    else:
        st.info("无记录")


def _tab_approvals() -> None:
    st.markdown("### ✍️ 审批流")
    status_filter = st.selectbox(
        "状态",
        ["", "pending", "in_progress", "approved", "rejected", "withdrawn"],
        key="auth_appr_status",  # round 31 widget key
    )
    params: Dict[str, Any] = {"limit": 100}
    if status_filter:
        params["status"] = status_filter
    rows = _api("GET", "/api/auth/approvals", params=params) or []
    if rows:
        df = pd.DataFrame(
            [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "resource": f"{r['resource_type']}/{r['resource_id']}",
                    "current_step": f"{r['current_step']}/{r['total_steps']}",
                    "status": r["status"],
                    "initiator": r.get("initiator_display") or "-",
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        )
        st.dataframe(df, width="stretch", height=400)

        sel_id = st.number_input("审批流 ID", min_value=0, step=1, value=0, key="auth_appr_id")  # round 31 widget key
        if sel_id:
            detail = _api("GET", f"/api/auth/approvals/{sel_id}")
            if detail:
                st.json(detail)
                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("✅ 通过", key=f"appr_{sel_id}"):
                        res = _api(
                            "POST",
                            f"/api/auth/approvals/{sel_id}/decide",
                            json={"action": "approve"},
                        )
                        if res:
                            st.success("已通过")
                            st.rerun()
                with c2:
                    if st.button("❌ 拒绝", key=f"rej_{sel_id}"):
                        res = _api(
                            "POST",
                            f"/api/auth/approvals/{sel_id}/decide",
                            json={"action": "reject"},
                        )
                        if res:
                            st.warning("已拒绝")
                            st.rerun()
                with c3:
                    if st.button("↩️ 撤回", key=f"wd_{sel_id}"):
                        res = _api("POST", f"/api/auth/approvals/{sel_id}/withdraw")
                        if res:
                            st.info("已撤回")
                            st.rerun()
    else:
        st.info("无审批流记录")


def show_auth() -> None:
    apply_feishu_theme()
    page_header('🔐', '系统管理', '用户 / 事务所 / 角色权限 / 审计轨迹 / 审批流')

    """对外入口."""
    st.markdown(
        '<p style="font-size:1.8rem;font-weight:bold;color:#4472C4;">🔐 系统管理 (认证 / 用户 / 审计轨迹)</p>',
        unsafe_allow_html=True,
    )

    if not st.session_state.get("auth_token"):
        _login_form()
        return

    tabs = st.tabs(
        [
            "👤 我的信息",
            "👥 用户",
            "🏢 事务所",
            "🛡️ 角色与权限",
            "📜 审计轨迹",
            "✍️ 审批流",
        ]
    )
    with tabs[0]:
        _tab_me()
    with tabs[1]:
        _tab_users()
    with tabs[2]:
        _tab_firms()
    with tabs[3]:
        _tab_roles_permissions()
    with tabs[4]:
        _tab_audit_logs()
    with tabs[5]:
        _tab_approvals()
