"""项目组管理 Streamlit 页面 — 7 个 Tab。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd
import streamlit as st

from frontend._http import api_request
from frontend._components.project_picker import pick_project_dict


def _projects_selectbox(label: str = "选择项目") -> Optional[dict]:
    """包装 pick_project_dict, 保留历史 API 兼容 (各 tab 仍调 _projects_selectbox)."""
    return pick_project_dict(label=label, fmt="team_mgmt")


# ============================================================
#  Tab 1 — 人员管理
# ============================================================


def _tab_members() -> None:
    st.subheader("👤 人员管理")

    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown("##### 人员清单")
        level_filter = st.selectbox(
            "按级别筛选",
            ["全部", "项目负责人", "高级经理", "经理", "高级审计员", "审计员"],
            key="members_level_filter",
        )
        level_map = {
            "全部": None,
            "项目负责人": "lead",
            "高级经理": "senior_manager",
            "经理": "manager",
            "高级审计员": "senior_auditor",
            "审计员": "auditor",
        }
        params: dict[str, Any] = {}
        if level_map[level_filter]:
            params["level"] = level_map[level_filter]

        members = api_request("GET", "/api/team-management/members", params=params) or []
        if members:
            df = pd.DataFrame(
                [
                    {
                        "ID": m["id"],
                        "姓名": m["full_name"],
                        "级别": m["level"],
                        "邮箱": m.get("email"),
                        "电话": m.get("phone"),
                        "专长": m.get("specialties"),
                        "状态": m.get("status"),
                        "入职日期": m.get("joined_at"),
                    }
                    for m in members
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("尚无人员。右侧添加第一位审计师。")

    with col_r:
        st.markdown("##### ➕ 新增人员")
        with st.form("add_member", clear_on_submit=True):
            full_name = st.text_input("姓名 *")
            col1, col2 = st.columns(2)
            with col1:
                level = st.selectbox(
                    "级别 *",
                    ["auditor", "senior_auditor", "manager", "senior_manager", "lead"],
                    format_func=lambda x: {
                        "auditor": "审计员",
                        "senior_auditor": "高级审计员",
                        "manager": "经理",
                        "senior_manager": "高级经理",
                        "lead": "项目负责人",
                    }.get(x, x),
                )
            with col2:
                status = st.selectbox("状态", ["active", "inactive"], index=0)
            email = st.text_input("邮箱")
            phone = st.text_input("电话")
            specialties = st.text_input('专长 (JSON 数组, 例 ["收入循环","存货盘点"])')
            joined_at = st.date_input("入职日期", value=None)
            notes = st.text_area("备注")
            submitted = st.form_submit_button("添加")
            if submitted:
                if not full_name.strip():
                    st.error("请填写姓名")
                else:
                    payload = {
                        "full_name": full_name.strip(),
                        "level": level,
                        "status": status,
                        "email": email.strip() or None,
                        "phone": phone.strip() or None,
                        "specialties": specialties.strip() or None,
                        "joined_at": joined_at.isoformat() if joined_at else None,
                        "notes": notes.strip() or None,
                    }
                    res = api_request("POST", "/api/team-management/members", json=payload)
                    if res:
                        st.success(f"已添加人员 #{res.get('id')} {res.get('full_name')}")


# ============================================================
#  Tab 2 — 项目分配
# ============================================================


def _tab_assignments() -> None:
    st.subheader("📋 项目人员分配")
    proj = _projects_selectbox("项目")
    if not proj:
        return
    pid = proj["id"]

    col_l, col_r = st.columns([3, 2])
    with col_l:
        st.markdown("##### 当前分配")
        assigns = api_request("GET", f"/api/team-management/projects/{pid}/assignments") or []
        if assigns:
            df = pd.DataFrame(
                [
                    {
                        "ID": a["id"],
                        "姓名": a.get("member", {}).get("full_name", "?"),
                        "级别": a.get("member", {}).get("level", "?"),
                        "项目角色": a["role_in_project"],
                        "投入%": a["workload_pct"],
                        "开始": a.get("start_date"),
                        "结束": a.get("end_date"),
                    }
                    for a in assigns
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("项目尚未分配人员。")

    with col_r:
        st.markdown("##### ➕ 添加成员")
        members = (
            api_request("GET", "/api/team-management/members", params={"status": "active"}) or []
        )
        if not members:
            st.warning("请先在『人员管理』录入人员。")
            return
        member_options = {f"#{m['id']} {m['full_name']} ({m['level']})": m for m in members}
        sel = st.selectbox("选择人员", list(member_options.keys()))
        with st.form("add_assign", clear_on_submit=True):
            role = st.selectbox(
                "项目内角色",
                ["lead", "deputy", "reviewer", "member"],
                format_func=lambda x: {
                    "lead": "项目负责人",
                    "deputy": "副负责人",
                    "reviewer": "复核人",
                    "member": "组员",
                }.get(x, x),
            )
            workload = st.slider("投入百分比", 0, 100, 100)
            col1, col2 = st.columns(2)
            with col1:
                start_d = st.date_input("入场日期", value=None)
            with col2:
                end_d = st.date_input("退场日期", value=None)
            submitted = st.form_submit_button("添加")
            if submitted and sel:
                m = member_options[sel]
                payload = {
                    "member_id": m["id"],
                    "role_in_project": role,
                    "workload_pct": float(workload),
                    "start_date": start_d.isoformat() if start_d else None,
                    "end_date": end_d.isoformat() if end_d else None,
                }
                res = api_request(
                    "POST", f"/api/team-management/projects/{pid}/assignments", json=payload
                )
                if res:
                    st.success("已添加")


# ============================================================
#  Tab 3 — 工作计划
# ============================================================


_TASK_STATUS_LABELS = {
    "pending": "待办",
    "in_progress": "进行中",
    "blocked": "阻塞",
    "done": "已完成",
    "cancelled": "已取消",
}


def _tab_work_plan() -> None:
    st.subheader("🎯 工作计划")
    proj = _projects_selectbox("项目")
    if not proj:
        return
    pid = proj["id"]

    col_top_l, col_top_r = st.columns([3, 1])
    with col_top_r:
        if st.button("🤖 AI 重新生成计划", type="primary", use_container_width=True):
            with st.spinner("AI 生成中…"):
                res = api_request("POST", f"/api/team-management/projects/{pid}/work-plan/generate")
            if res:
                st.success(f"已生成计划 #{res.get('id')} — {len(res.get('items', []))} 项任务")

    plans = api_request("GET", f"/api/team-management/projects/{pid}/work-plan") or []
    if not plans:
        st.info("项目还没有工作计划。先添加项目人员 + 导入账套，或点上方按钮手动生成。")
        return

    plan_idx = st.selectbox(
        "选择计划",
        range(len(plans)),
        format_func=lambda i: (
            f"#{plans[i]['id']} {plans[i]['name']} ({plans[i]['status']}, {len(plans[i].get('items', []))} 项)"
        ),
    )
    plan = plans[plan_idx]
    items = plan.get("items", [])

    # 任务看板 — 按状态分列
    st.markdown("##### 任务看板")
    statuses = ["pending", "in_progress", "blocked", "done"]
    cols = st.columns(4)
    for col, status in zip(cols, statuses):
        with col:
            st.markdown(
                f"**{_TASK_STATUS_LABELS[status]} ({sum(1 for x in items if x['status'] == status)})**"
            )
            for it in [x for x in items if x["status"] == status]:
                with st.expander(f"{it['title'][:30]}{'…' if len(it['title']) > 30 else ''}"):
                    st.caption(
                        f"模块: {it.get('related_module', '?')} | 优先级: {it.get('priority', '?')} | 估时: {it.get('estimated_hours', 0)}h"
                    )
                    if it.get("description"):
                        st.write(it["description"])
                    st.write(f"建议级别: {it.get('recommended_level', '?')}")
                    new_status = st.selectbox(
                        "改状态",
                        statuses + ["cancelled"],
                        index=statuses.index(it["status"]) if it["status"] in statuses else 0,
                        key=f"st_{it['id']}",
                    )
                    new_member = st.number_input(
                        "分配给 (member_id, 0=未分配)",
                        min_value=0,
                        value=it.get("member_id") or 0,
                        key=f"mb_{it['id']}",
                    )
                    new_actual = st.number_input(
                        "实际工时",
                        min_value=0.0,
                        value=float(it.get("actual_hours") or 0),
                        key=f"ah_{it['id']}",
                    )
                    if st.button("保存", key=f"sv_{it['id']}"):
                        payload = {
                            "status": new_status,
                            "member_id": int(new_member) if new_member > 0 else None,
                            "actual_hours": float(new_actual),
                        }
                        res = api_request(
                            "PUT", f"/api/team-management/work-plan-items/{it['id']}", json=payload
                        )
                        if res is not None:
                            st.success("已更新")
                            st.rerun()


# ============================================================
#  Tab 4 — 每日汇报
# ============================================================


def _tab_daily_reports() -> None:
    st.subheader("📅 每日汇报")
    proj = _projects_selectbox("项目")
    if not proj:
        return
    pid = proj["id"]

    members = api_request("GET", "/api/team-management/members", params={"status": "active"}) or []
    member_options = {f"#{m['id']} {m['full_name']}": m for m in members}

    col_l, col_r = st.columns([2, 3])
    with col_l:
        st.markdown("##### ➕ 提交今日汇报")
        if not members:
            st.warning("请先在『人员管理』Tab 添加人员。")
        else:
            sel = st.selectbox("汇报人", list(member_options.keys()))
            with st.form("daily_report", clear_on_submit=True):
                # 默认昨天 — 凌晨提交昨日工作的常见场景
                yesterday = date.today() - timedelta(days=1)
                report_date = st.date_input(
                    "日期 (默认昨日)",
                    value=yesterday,
                    max_value=date.today(),
                )
                completed = st.text_area("✅ 已完成 *", height=80)
                in_progress = st.text_area("🔄 进行中", height=60)
                blockers = st.text_area("⛔ 卡点摘要", height=60)
                next_plan = st.text_area("➡️ 次日计划", height=60)
                hours = st.number_input("工时", min_value=0.0, max_value=24.0, value=8.0, step=0.5)
                submitted = st.form_submit_button("提交")
                if submitted and sel and completed.strip():
                    m = member_options[sel]
                    payload = {
                        "report_date": report_date.isoformat(),
                        "completed_work": completed,
                        "in_progress_work": in_progress or None,
                        "blockers_summary": blockers or None,
                        "next_day_plan": next_plan or None,
                        "hours_logged": float(hours),
                    }
                    res = api_request(
                        "POST",
                        f"/api/team-management/projects/{pid}/daily-reports?member_id={m['id']}",
                        json=payload,
                    )
                    if res:
                        st.success("已提交 — 刷新历史列表请滚动右侧")
                        st.rerun()

    with col_r:
        st.markdown("##### 📜 历史汇报")
        reports = (
            api_request(
                "GET", f"/api/team-management/projects/{pid}/daily-reports", params={"limit": 30}
            )
            or []
        )
        if reports:
            df = pd.DataFrame(
                [
                    {
                        "日期": r["report_date"],
                        "提交人": r.get("member", {}).get("full_name", "?"),
                        "工时": r.get("hours_logged", 0),
                        "已完成": r.get("completed_work", "")[:80],
                        "卡点": (r.get("blockers_summary") or "")[:60],
                    }
                    for r in reports
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("暂无汇报")


# ============================================================
#  Tab 5 — 会议管理
# ============================================================


_MEETING_TYPE_LABELS = {
    "daily": "站会",
    "weekly": "周会",
    "kickoff": "启动会",
    "review": "复核会",
    "adhoc": "临时",
}


def _tab_meetings() -> None:
    st.subheader("🗣 会议管理")
    proj = _projects_selectbox("项目")
    if not proj:
        return
    pid = proj["id"]

    col_l, col_r = st.columns([2, 3])
    with col_l:
        st.markdown("##### ➕ 排期会议")
        with st.form("create_meeting", clear_on_submit=True):
            title = st.text_input("会议标题 *")
            mtype = st.selectbox(
                "类型",
                list(_MEETING_TYPE_LABELS.keys()),
                format_func=lambda x: _MEETING_TYPE_LABELS[x],
            )
            d = st.date_input("日期", value=date.today())
            t = st.time_input("时间", value=None)
            duration = st.number_input("时长(分钟)", 15, 480, 60, step=15)
            location = st.text_input("地点")
            agenda = st.text_area("议程")
            if st.form_submit_button("创建"):
                if title.strip():
                    sched = f"{d.isoformat()} {t.strftime('%H:%M') if t else '09:00'}"
                    payload = {
                        "title": title,
                        "meeting_type": mtype,
                        "scheduled_at": sched,
                        "duration_minutes": int(duration),
                        "location": location or None,
                        "agenda": agenda or None,
                    }
                    res = api_request(
                        "POST", f"/api/team-management/projects/{pid}/meetings", json=payload
                    )
                    if res:
                        st.success(f"已创建会议 #{res.get('id')}")

    with col_r:
        st.markdown("##### 📋 会议清单")
        meetings = api_request("GET", f"/api/team-management/projects/{pid}/meetings") or []
        if not meetings:
            st.info("暂无会议")
            return
        for m in meetings[:20]:
            with st.expander(
                f"#{m['id']} {m['title']} ({_MEETING_TYPE_LABELS.get(m['meeting_type'], '?')}) {m['scheduled_at']} [{m['status']}]"
            ):
                # 提交纪要
                st.markdown("**📝 提交纪要 (AI 自动评估质量)**")
                with st.form(f"record_{m['id']}", clear_on_submit=True):
                    content = st.text_area("纪要正文 *", height=120, key=f"ct_{m['id']}")
                    attendees_raw = st.text_input("与会人 (逗号分隔)", key=f"at_{m['id']}")
                    decisions_raw = st.text_area(
                        "决策 (每行一条: decision | owner)", key=f"dc_{m['id']}", height=60
                    )
                    actions_raw = st.text_area(
                        "行动项 (每行一条: action | owner | due)", key=f"ac_{m['id']}", height=60
                    )
                    recorded_by = st.text_input("记录人", key=f"rb_{m['id']}")
                    submit = st.form_submit_button("提交并评估")
                    if submit and content.strip():
                        payload = {
                            "content": content,
                            "attendees": [a.strip() for a in attendees_raw.split(",") if a.strip()]
                            or None,
                            "decisions": _parse_lines(decisions_raw, ["decision", "owner"]),
                            "action_items": _parse_lines(actions_raw, ["action", "owner", "due"]),
                            "recorded_by": recorded_by or None,
                        }
                        res = api_request(
                            "PUT", f"/api/team-management/meetings/{m['id']}/record", json=payload
                        )
                        if res:
                            st.success(f"已记录，AI 评分 {res.get('quality_score', '-')}")
                            ai = res.get("ai_assessment") or {}
                            if isinstance(ai, dict):
                                if ai.get("strengths"):
                                    st.markdown("**亮点**")
                                    for s in ai["strengths"]:
                                        st.markdown(f"- ✅ {s}")
                                if ai.get("weaknesses"):
                                    st.markdown("**不足**")
                                    for s in ai["weaknesses"]:
                                        st.markdown(f"- ⚠️ {s}")
                                if ai.get("suggestions"):
                                    st.markdown("**建议**")
                                    for s in ai["suggestions"]:
                                        st.markdown(f"- 💡 {s}")
                if m.get("record"):
                    st.caption(
                        f"已记录 — 质量 {m['record'].get('quality_score', '-')}/100, "
                        f"AI {'已评估' if m['record'].get('ai_enabled') else '未启用'}"
                    )


def _parse_lines(raw: str, keys: list[str]) -> Optional[list[dict[str, Any]]]:
    if not raw or not raw.strip():
        return None
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        item: dict[str, Any] = {}
        for i, k in enumerate(keys):
            item[k] = parts[i] if i < len(parts) and parts[i] else None
        out.append(item)
    return out


# ============================================================
#  Tab 6 — 进度看板
# ============================================================


def _tab_dashboard() -> None:
    st.subheader("📊 项目进度看板")
    proj = _projects_selectbox("项目")
    if not proj:
        return
    pid = proj["id"]
    dash = api_request("GET", f"/api/team-management/projects/{pid}/dashboard")
    if not dash:
        st.error("无法获取 dashboard")
        return

    p = dash["project"]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("任务总数", p["total_items"])
    col2.metric("已完成", p["completed_items"], f"{p['completion_rate'] * 100:.0f}%")
    col3.metric("进行中", p["in_progress_items"])
    col4.metric("阻塞", p["blocked_items"])

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("估时(总)", f"{p['total_estimated_hours']:.0f} h")
    col6.metric("实工(总)", f"{p['total_actual_hours']:.0f} h")
    col7.metric(
        "开放卡点",
        p["open_blockers"],
        delta=f"紧急 {p['critical_blockers']}",
        delta_color="inverse",
    )
    col8.metric("平均存续", f"{dash['blockers']['avg_age_hours']:.0f} h")

    st.markdown("---")

    # 任务状态分布
    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("##### 任务状态分布")
        if dash.get("by_status"):
            df_status = pd.DataFrame(
                [
                    {"状态": _TASK_STATUS_LABELS.get(k, k), "数量": v}
                    for k, v in dash["by_status"].items()
                ]
            )
            st.bar_chart(df_status.set_index("状态"))
        else:
            st.info("暂无数据")

    with col_r:
        st.markdown("##### 模块分布")
        if dash.get("by_module"):
            df_mod = pd.DataFrame([{"模块": k, "数量": v} for k, v in dash["by_module"].items()])
            st.bar_chart(df_mod.set_index("模块"))
        else:
            st.info("暂无数据")

    # 人员进度
    st.markdown("##### 人员完成率")
    members = p.get("members", [])
    if members:
        df_mem = pd.DataFrame(
            [
                {
                    "姓名": m["full_name"],
                    "级别": m["level"],
                    "完成率": m["completion_rate"],
                    "已完成": m["completed_items"],
                    "进行中": m["in_progress_items"],
                    "阻塞": m["blocked_items"],
                    "7天工时": m["hours_logged_7d"],
                    "开放卡点": m["open_blockers"],
                }
                for m in members
            ]
        )
        st.dataframe(df_mem, use_container_width=True, hide_index=True)
        st.bar_chart(df_mem.set_index("姓名")["完成率"])
    else:
        st.info("项目尚未分配人员")


# ============================================================
#  Tab 7 — 管理建议
# ============================================================


def _tab_recommendations() -> None:
    st.subheader("💡 AI 管理建议")
    proj = _projects_selectbox("项目")
    if not proj:
        return
    pid = proj["id"]

    if st.button("🤖 生成新一轮管理建议", type="primary"):
        with st.spinner("AI 分析中…"):
            res = api_request(
                "POST", f"/api/team-management/projects/{pid}/recommendations/generate"
            )
        if res:
            st.success(f"已生成建议 #{res.get('id')}")
            st.rerun()

    recs = api_request("GET", f"/api/team-management/projects/{pid}/recommendations") or []
    if not recs:
        st.info("暂无建议。点击上方按钮生成。")
        return

    for r in recs:
        confirmed = "✅ 已确认" if r.get("is_confirmed") else "⏳ 待确认"
        with st.expander(
            f"#{r['id']} {r.get('period_start', '-')}~{r.get('period_end', '-')} {confirmed} | AI {'已启用' if r.get('ai_enabled') else '未启用'}"
        ):
            # 关键发现
            findings = r.get("findings") or []
            if findings:
                st.markdown("##### 关键发现")
                for f in findings:
                    sev = f.get("severity", "info")
                    icon = {
                        "critical": "🔴",
                        "high": "🟠",
                        "medium": "🟡",
                        "low": "🟢",
                        "info": "ℹ️",
                    }.get(sev, "•")
                    st.markdown(f"{icon} **{f.get('category', '')}** — {f.get('finding', '')}")
                    if f.get("evidence"):
                        st.caption(f"   证据: {f['evidence']}")

            # 优先行动
            actions = r.get("priority_actions") or []
            if actions:
                st.markdown("##### 优先行动")
                for a in actions:
                    st.markdown(
                        f"- **{a.get('action', '')}** — 负责人 {a.get('owner', '?')}, "
                        f"截止 {a.get('deadline', '?')} "
                        f"({a.get('rationale', '')})"
                    )

            # Markdown 长文
            if r.get("recommendations"):
                st.markdown("##### AI 总结")
                st.markdown(r["recommendations"])

            # 确认按钮
            if not r.get("is_confirmed"):
                with st.form(f"confirm_{r['id']}", clear_on_submit=True):
                    notes = st.text_area("负责人备注")
                    cb = st.text_input("确认人 *")
                    if st.form_submit_button("确认采纳") and cb.strip():
                        res = api_request(
                            "POST",
                            f"/api/team-management/recommendations/{r['id']}/confirm",
                            json={"confirmed_by": cb.strip(), "manager_notes": notes or None},
                        )
                        if res:
                            st.success("已确认")
            else:
                st.caption(f"由 {r.get('confirmed_by')} 于 {r.get('confirmed_at')} 确认")
                if r.get("manager_notes"):
                    st.markdown(f"**负责人备注**: {r['manager_notes']}")


# ============================================================
#  入口
# ============================================================


def show_team_management() -> None:
    st.markdown("## 👥 项目组管理")
    st.caption("人员 → 计划 → 日报/会议 → 进度看板 → AI 管理建议")
    tabs = st.tabs(
        [
            "👤 人员管理",
            "📋 项目分配",
            "🎯 工作计划",
            "📅 每日汇报",
            "🗣 会议管理",
            "📊 进度看板",
            "💡 管理建议",
        ]
    )
    with tabs[0]:
        _tab_members()
    with tabs[1]:
        _tab_assignments()
    with tabs[2]:
        _tab_work_plan()
    with tabs[3]:
        _tab_daily_reports()
    with tabs[4]:
        _tab_meetings()
    with tabs[5]:
        _tab_dashboard()
    with tabs[6]:
        _tab_recommendations()
