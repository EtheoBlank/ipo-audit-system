"""舆情跟踪前端页面 — v0.2 新增.

5 个 Tab:
    1. 舆情总览 (overview)
    2. 事件库 (events)
    3. 每日简报 (briefings)
    4. 季度跟踪报告 (quarterly)
    5. 别名与信源 (settings)

设计要点:
    - 简报页: 状态机按钮 (DRAFT: 生成/提交 / REVIEW: 批准/驳回 / APPROVED+FROZEN: 下载/修订)
    - 季度报告页: 双数据源录入 + 触发
    - 顶部红点: 调 /notifications/unread
"""

from __future__ import annotations

import json
import streamlit as st
from datetime import date, timedelta
from frontend._components import apply_feishu_theme, page_header
import pandas as pd

from frontend._http import api_request
from frontend._components.project_picker import pick_project
from frontend._components.download import download_word
from frontend._components.safe_render import safe_inline_text, safe_url


# ============================================================
#  工具
# ============================================================


def _badge(text: str, color: str) -> str:
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:6px;font-size:0.8rem;font-weight:bold;">{text}</span>'


def _status_badge(status: str) -> str:
    return {
        "draft": _badge("草稿", "#6c757d"),
        "review": _badge("审阅中", "#fd7e14"),
        "approved": _badge("已批准", "#28a745"),
        "frozen": _badge("已锁定", "#007bff"),
        "rejected": _badge("已驳回", "#dc3545"),
    }.get(status, _badge(status, "#6c757d"))


def _severity_badge(sev: str) -> str:
    return {
        "critical": _badge("重大", "#dc3545"),
        "warn": _badge("警示", "#fd7e14"),
        "notice": _badge("关注", "#ffc107"),
        "info": _badge("一般", "#6c757d"),
    }.get(sev, _badge(sev, "#6c757d"))


def _pick_project() -> int:
    """舆情页选项目 — 无项目时调 st.stop() 终止页面 (历史行为)."""
    pid = pick_project(fmt="sentiment")
    if pid is None:
        st.stop()
    return pid


def _render_unread_badge() -> int:
    """顶部红点 (在页面内). 主页统一红点在 app.py."""
    data = api_request("GET", "/api/sentiment/notifications/unread?limit=10")
    if not data:
        return 0
    count = data.get("count", 0)
    if count > 0:
        items = data.get("items", [])
        st.markdown(
            f'<div style="background:#dc3545;color:white;padding:0.4rem 1rem;border-radius:8px;display:inline-block;margin-bottom:0.5rem;font-weight:bold;">🔴 您有 {count} 条未读舆情通知</div>',
            unsafe_allow_html=True,
        )
        with st.expander(f"查看 {count} 条未读", expanded=False):
            for n in items:
                col1, col2 = st.columns([5, 1])
                col1.write(f"**{n['title']}**")
                if n.get("body"):
                    col1.caption(safe_inline_text(n.get("body", ""), max_len=500))
                col1.caption(f"类型: {n['notification_type']} · {n['created_at']}")
                if col2.button("标已读", key=f"read_{n['id']}"):
                    api_request("POST", f"/api/sentiment/notifications/{n['id']}/read")
                    st.rerun()
    return count


# ============================================================
#  Tab 1: 舆情总览
# ============================================================


def _tab_overview(project_id: int) -> None:
    st.subheader("📊 舆情总览")

    # 触发扫描
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("🔄 立即扫描"):
            with st.spinner("扫描中..."):
                r = api_request(
                    "POST", "/api/sentiment/scheduler/scan/now", json={"project_id": project_id}
                )
            if r:
                st.success(f"扫描完成: 新增 {r.get('events_added', 0)} 条事件")
                st.rerun()

    # 调度器状态
    sched = api_request("GET", "/api/sentiment/scheduler/status")
    if sched:
        with col2:
            if sched.get("running"):
                st.success(
                    f"🟢 调度器运行中 · 下次扫描: {sched['jobs'][0]['next_run_time'] if sched.get('jobs') else '—'}"
                )
            else:
                st.warning("🔴 调度器未运行")
                if st.button("启动调度器"):
                    api_request("POST", "/api/sentiment/scheduler/start")
                    st.rerun()

    st.divider()

    # 当日事件数 / 最近简报 — P1 (2026-06-19) 用本地 date.today() 替 pd.Timestamp.now()
    # pd.Timestamp.now() 用本地 tz, 后端用 UTC, 跨日期 (凌晨 0-8 点) 不一致
    today = date.today().isoformat()
    events_today = api_request(
        "GET", f"/api/sentiment/events?project_id={project_id}&date_from={today}&date_to={today}"
    )
    briefings = api_request("GET", f"/api/sentiment/briefings?project_id={project_id}")
    reports = api_request("GET", f"/api/sentiment/reports?project_id={project_id}")

    c1, c2, c3 = st.columns(3)
    c1.metric("今日事件", len(events_today or []))
    c2.metric("累计简报", len(briefings or []))
    c3.metric("季度报告", len(reports or []))
    # P1 修复 (2026-06-19): 旧 c4.metric("_render_unread_badge()") 在 metric 内调函数
    # 函数本身 st.markdown + expander + 按钮 → 双重渲染 (metric 一次, 函数一次)
    # 现在 c4 单独放红点徽章, 不再嵌套 metric
    with c4:
        _render_unread_badge()

    # 严重度分布
    if events_today:
        sev_counts = {}
        for e in events_today:
            sev_counts[e["severity"]] = sev_counts.get(e["severity"], 0) + 1
        st.markdown("#### 今日事件严重度分布")
        cols = st.columns(4)
        for i, (sev, label) in enumerate(
            [("critical", "重大"), ("warn", "警示"), ("notice", "关注"), ("info", "一般")]
        ):
            cols[i].metric(label, sev_counts.get(sev, 0))


# ============================================================
#  Tab 2: 事件库
# ============================================================


def _tab_events(project_id: int) -> None:
    st.subheader("📰 事件库")

    # 筛选
    col1, col2, col3 = st.columns(3)
    severity = col1.selectbox("严重度", ["全部", "critical", "warn", "notice", "info"])
    review_status = col2.selectbox("审核状态", ["全部", "unread", "read", "ignored"])
    date_from = col3.date_input("起始日期", value=date.today() - timedelta(days=30))

    params = f"project_id={project_id}&date_from={date_from}"
    if severity != "全部":
        params += f"&severity={severity}"
    if review_status != "全部":
        params += f"&review_status={review_status}"

    events = api_request("GET", f"/api/sentiment/events?{params}&size=200")
    if not events:
        st.info("暂无事件")
        return

    # DataFrame
    df = pd.DataFrame(events)[
        ["id", "publish_date", "severity", "title", "publisher", "review_status", "url"]
    ]
    # P0: 校验 url 协议
    safe_urls = [(safe_url(u), t) for u, t in zip(df.get('url', []), df.get('title', []))]
    df['url'] = [u for u, _ in safe_urls]
    df["严重度"] = df["severity"].map(
        {"critical": "🔴 重大", "warn": "🟠 警示", "notice": "🟡 关注", "info": "⚪ 一般"}
    )
    df["状态"] = df["review_status"].map({"unread": "未读", "read": "已读", "ignored": "已忽略"})
    st.dataframe(
        df[["id", "publish_date", "严重度", "title", "publisher", "状态", "url"]],
        use_container_width=True,
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("链接")},
    )

    # 详情 + 忽略
    st.divider()
    st.markdown("##### 查看 / 操作")
    event_id = st.number_input("事件 ID", min_value=1, value=int(events[0]["id"]), step=1)
    ev = api_request("GET", f"/api/sentiment/events/{event_id}")
    if ev:
        st.markdown(f"**{ev['title']}**")
        st.caption(
            f"{ev.get('publisher', '—')} · {ev.get('publish_date', '—')} · {ev.get('url', '—')}"
        )
        st.code(ev.get("content_text", ""), language=None)
        if ev.get("review_status") != "ignored":
            if st.button("标记忽略"):
                api_request("POST", f"/api/sentiment/events/{event_id}/ignore")
                st.rerun()

    # 手工录入
    with st.expander("➕ 手工录入事件"):
        with st.form("import_event"):
            title = st.text_input("标题*")
            content = st.text_area("内容", height=100)
            publisher = st.text_input("来源")
            url = st.text_input("URL")
            event_date = st.date_input("日期", value=date.today())
            sev = st.selectbox("严重度", ["info", "notice", "warn", "critical"])
            if st.form_submit_button("录入"):
                r = api_request(
                    "POST",
                    "/api/sentiment/events/import",
                    json={
                        "project_id": project_id,
                        "title": title,
                        "content_text": content,
                        "publisher": publisher,
                        "url": url or None,
                        "publish_date": str(event_date),
                        "severity": sev,
                    },
                )
                if r:
                    st.success(f"已录入事件 id={r['id']}")
                    st.rerun()


# ============================================================
#  Tab 3: 每日简报
# ============================================================


def _tab_briefings(project_id: int) -> None:
    st.subheader("🗞️ 每日简报")

    # 强制重新生成 + 立即生成
    col1, col2 = st.columns(2)
    with col1:
        target_date = st.date_input("指定日期", value=date.today())
    with col2:
        force = st.checkbox("强制重新生成 (覆盖今日简报)", value=False)

    if st.button("📝 生成简报"):
        with st.spinner("LLM 4 轮协议中 (提取/自检/挑刺/拼装)..."):
            r = api_request(
                "POST",
                "/api/sentiment/briefings/generate",
                json={
                    "project_id": project_id,
                    "briefing_date": str(target_date),
                    "force": force,
                },
            )
        if r:
            if "detail" in r:
                st.warning(f"未生成: {r['detail']}")
            else:
                st.success(f"已生成简报 id={r['id']}, 校验失败={r['verification_failed']}")
                st.rerun()

    st.divider()

    # 简报列表
    briefings = api_request("GET", f"/api/sentiment/briefings?project_id={project_id}")
    if not briefings:
        st.info("暂无简报")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        st.markdown("##### 简报列表")
        for b in briefings[:30]:
            label = f"{b['briefing_date']} · {b['event_count']} 条"
            if st.button(label, key=f"b_{b['id']}"):
                # P0: 加 project_id 前缀防跨项目污染
                st.session_state[f"selected_briefing_id_{project_id}"] = b["id"]

    with c2:
        bid = st.session_state.get(f"selected_briefing_id_{project_id}")
        if not bid:
            bid = briefings[0]["id"]
        br = api_request("GET", f"/api/sentiment/briefings/{bid}")
        if br:
            _render_briefing_detail(br, project_id)


def _render_briefing_detail(br: dict, project_id: int) -> None:
    """渲染简报详情 + 审阅操作."""
    st.markdown(f"### {br['title']}")
    st.markdown(
        f"状态: {_status_badge(br['status'])} · 锁定: {'是' if br['is_locked'] else '否'} · "
        f"事件数: {br['event_count']} · 校验: {'❌ 失败' if br['verification_failed'] else '✅ 通过'}"
    )
    if br.get("word_report_sha256"):
        st.caption(f"Word SHA-256: {br['word_report_sha256'][:24]}…")

    tabs = st.tabs(["📄 简报正文", "🔍 事实溯源", "🧮 数字核验"])

    with tabs[0]:
        st.markdown(br.get("ai_summary") or "_无内容_")

    with tabs[1]:
        # 关联事件列表
        events = api_request(
            "GET",
            f"/api/sentiment/events?project_id={project_id}&date_from={br['briefing_date']}&date_to={br['briefing_date']}",
        )
        if not events:
            st.info("无关联事件")
        else:
            for e in events:
                with st.expander(f"[事件#{e['id']}] {_severity_badge(e['severity'])} {e['title']}"):
                    st.caption(f"来源: {e.get('publisher', '—')} · {e.get('publish_date', '—')}")
                    if e.get("url"):
                        # P0 安全: 校验 URL 协议, 防 javascript: 注入
                        from frontend._components.safe_render import safe_link
                        st.markdown(safe_link("原文链接", e["url"]))
                    st.text_area(
                        "内容",
                        e.get("content_text", ""),
                        height=150,
                        disabled=True,
                        key=f"e_{e['id']}",
                    )
                    if e.get("review_status") == "unread":
                        if st.button("标已读", key=f"r_{e['id']}"):
                            # 调 ignore 端点实现"已处理"语义, 后续可换专门 /read 端点
                            r = api_request(
                                "POST",
                                f"/api/sentiment/events/{e['id']}/ignore",
                            )
                            if r is not None:
                                st.success("已标记为已处理")
                                st.rerun()

    with tabs[2]:
        if br.get("audit_verification_json"):
            st.json(json.loads(br["audit_verification_json"]))
        else:
            st.info("无核验报告")

    st.divider()

    # 审阅操作 — 按状态显示不同按钮
    st.markdown("##### 审阅操作")
    if br["status"] == "draft" and not br["is_locked"]:
        c1, c2, c3 = st.columns(3)
        reviewer = c1.text_input("审计师姓名", key=f"rv_{br['id']}")
        with c2:
            if st.button("✅ 提交审阅", key=f"submit_{br['id']}"):
                if not reviewer:
                    st.error("请填写审计师姓名")
                else:
                    api_request(
                        "POST",
                        f"/api/sentiment/briefings/{br['id']}/submit",
                        json={"reviewer": reviewer},
                    )
                    st.rerun()
        with c3:
            if st.button("🔁 重新核验", key=f"reverify_{br['id']}"):
                api_request("GET", f"/api/sentiment/briefings/{br['id']}/verify")
                st.rerun()

    elif br["status"] == "review" and not br["is_locked"]:
        reviewer = st.text_input("审批人姓名", key=f"app_{br['id']}")
        comment = st.text_area("审批意见", key=f"cm_{br['id']}")
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("✅ 批准", key=f"approve_{br['id']}"):
                api_request(
                    "POST",
                    f"/api/sentiment/briefings/{br['id']}/approve",
                    json={"reviewer": reviewer, "comment": comment},
                )
                st.rerun()
        with c2:
            if st.button("❌ 驳回", key=f"reject_{br['id']}"):
                if not comment:
                    st.error("驳回必须填写意见")
                else:
                    api_request(
                        "POST",
                        f"/api/sentiment/briefings/{br['id']}/reject",
                        json={"reviewer": reviewer, "comment": comment},
                    )
                    st.rerun()
        with c3:
            if st.button("⬅️ 撤回", key=f"recall_{br['id']}"):
                api_request(
                    "POST",
                    f"/api/sentiment/briefings/{br['id']}/recall",
                    json={"reviewer": reviewer, "comment": "撤回审阅"},
                )
                st.rerun()

    elif br["status"] in ("approved", "frozen") or br["is_locked"]:
        st.success("✅ 已批准 / 锁定, 不可修改")
        c1, c2 = st.columns(2)
        with c1:
            # 走统一 api_request 走 auth 头
            content = api_request(
                "GET",
                f"/api/sentiment/briefings/{br['id']}/download",
                expect_bytes=True,
            )
            if isinstance(content, bytes) and content:
                download_word(content, file_name=f"briefing_{br['id']}.docx")
        with c2:
            reviser = st.text_input("修订人", key=f"rvs_{br['id']}")
            if st.button("🔄 基于本版修订", key=f"revise_{br['id']}"):
                if not reviser:
                    st.error("请填写修订人")
                else:
                    api_request(
                        "POST",
                        f"/api/sentiment/briefings/{br['id']}/revise",
                        json={"reviser": reviser, "change_note": "由领导批准后修订"},
                    )
                    st.rerun()

    elif br["status"] == "rejected":
        st.error(f"❌ 已驳回: {br.get('review_comment', '—')}")
        if st.button("🔄 重新生成", key=f"regen_{br['id']}"):
            api_request(
                "POST",
                "/api/sentiment/briefings/generate",
                json={
                    "project_id": project_id,
                    "briefing_date": br["briefing_date"],
                    "force": True,
                },
            )
            st.rerun()


# ============================================================
#  Tab 4: 季度跟踪报告
# ============================================================


def _tab_quarterly(project_id: int) -> None:
    st.subheader("📈 季度跟踪报告")

    # 新建报告
    with st.expander("➕ 新建报告"):
        with st.form("create_report"):
            c1, c2, c3 = st.columns(3)
            period_type = c1.selectbox("报告期", ["Q1", "H1", "Q3", "ANNUAL"])
            fiscal_year = c2.number_input("年度", min_value=2000, max_value=2099, value=2025)
            trigger = c3.selectbox("触发方式", ["manual", "financials_uploaded", "scheduled"])
            if st.form_submit_button("创建"):
                r = api_request(
                    "POST",
                    "/api/sentiment/reports",
                    json={
                        "project_id": project_id,
                        "period_type": period_type,
                        "fiscal_year": int(fiscal_year),
                        "trigger_type": trigger,
                    },
                )
                if r and "id" in r:
                    st.success(f"已创建报告 id={r['id']}")
                    st.rerun()

    # 报告列表
    reports = api_request("GET", f"/api/sentiment/reports?project_id={project_id}")
    if not reports:
        st.info("暂无报告")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        st.markdown("##### 报告列表")
        for r in reports[:30]:
            label = f"{r['fiscal_year']} {r['period_type']} · {r['status']}"
            if st.button(label, key=f"r_{r['id']}"):
                # P0: 加 project_id 前缀防跨项目污染
                st.session_state[f"selected_report_id_{project_id}"] = r["id"]

    with c2:
        rid = st.session_state.get(f"selected_report_id_{project_id}")
        if not rid:
            rid = reports[0]["id"]
        rep = api_request("GET", f"/api/sentiment/reports/{rid}")
        if rep:
            _render_report_detail(rep, project_id)


def _render_report_detail(rep: dict, project_id: int) -> None:
    st.markdown(f"### {rep['title']}")
    st.markdown(
        f"状态: {_status_badge(rep['status'])} · 锁定: {'是' if rep['is_locked'] else '否'} · "
        f"窗口: {rep.get('daily_briefing_window_start', '—')} ~ {rep.get('daily_briefing_window_end', '—')}"
    )
    st.caption(f"已引用简报: {rep.get('referenced_briefing_ids_json', '[]')}")
    st.caption(f"已引用事件: {rep.get('referenced_event_ids_json', '[]')}")

    # 财务数据录入
    with st.expander("📊 录入季报数据 (8 项必填 + 审计师签名)"):
        with st.form(f"fin_{rep['id']}"):
            c1, c2 = st.columns(2)
            revenue = c1.number_input("营业收入 (元)", min_value=0.0, value=1_000_000_000.0)
            net_profit = c1.number_input("净利润 (元)", value=50_000_000.0)
            non_recurring = c1.number_input("扣非净利润 (元)", value=45_000_000.0)
            gross_margin = c2.number_input(
                "毛利率 (%, 0-100)", min_value=-100.0, max_value=100.0, value=25.0
            )
            yoy_rev = c2.number_input("营收同比 (%, 正负)", value=12.0)
            yoy_np = c2.number_input("净利同比 (%, 正负)", value=-5.0)
            total_assets = c1.number_input("期末总资产 (元)", min_value=0.0, value=5_000_000_000.0)
            op_cf = c2.number_input("经营现金流 (元)", value=80_000_000.0)
            verified_by = st.text_input("审计师签名*")
            note = st.text_area("备注")
            if st.form_submit_button("💾 保存"):
                r = api_request(
                    "POST",
                    f"/api/sentiment/reports/{rep['id']}/financials",
                    json={
                        "revenue": revenue,
                        "net_profit": net_profit,
                        "non_recurring_pnl": non_recurring,
                        "gross_margin": gross_margin,
                        "yoy_revenue": yoy_rev,
                        "yoy_net_profit": yoy_np,
                        "total_assets": total_assets,
                        "operating_cash_flow": op_cf,
                        "verified_by": verified_by,
                        "note": note,
                    },
                )
                if r and "id" in r:
                    st.success("已保存")
                    st.rerun()

    # 触发生成
    if st.button("⚡ 触发报告生成 (4 轮 LLM + 双数据源对账)", key=f"gen_{rep['id']}"):
        with st.spinner("生成中..."):
            r = api_request("POST", f"/api/sentiment/reports/{rep['id']}/generate")
        if r and "id" in r:
            st.success("已生成")
            st.rerun()
        elif r and "detail" in r:
            st.error(r["detail"])

    # 正文 / 校验
    if rep.get("ai_report_md"):
        tabs = st.tabs(["📄 报告正文", "🧮 双数据源对账"])
        with tabs[0]:
            st.markdown(rep["ai_report_md"])
        with tabs[1]:
            if rep.get("ai_report_verification_json"):
                st.json(json.loads(rep["ai_report_verification_json"]))
            else:
                st.info("无对账报告")

    # 审阅操作
    st.divider()
    st.markdown("##### 审阅操作")
    if rep["status"] == "draft":
        reviewer = st.text_input("提交人", key=f"rs_{rep['id']}")
        if st.button("✅ 提交审阅", key=f"rsub_{rep['id']}"):
            api_request(
                "POST", f"/api/sentiment/reports/{rep['id']}/submit", json={"reviewer": reviewer}
            )
            st.rerun()

    elif rep["status"] == "review":
        reviewer = st.text_input("审批人", key=f"ra_{rep['id']}")
        comment = st.text_area("意见", key=f"rcm_{rep['id']}")
        c1, c2 = st.columns(2)
        if c1.button("✅ 批准", key=f"rapp_{rep['id']}"):
            api_request(
                "POST",
                f"/api/sentiment/reports/{rep['id']}/approve",
                json={"reviewer": reviewer, "comment": comment},
            )
            st.rerun()
        if c2.button("❌ 驳回", key=f"rrej_{rep['id']}"):
            if not comment:
                st.error("驳回必须填写意见")
            else:
                api_request(
                    "POST",
                    f"/api/sentiment/reports/{rep['id']}/reject",
                    json={"reviewer": reviewer, "comment": comment},
                )
                st.rerun()

    elif rep["is_locked"]:
        st.success("✅ 已批准并锁定")
        # 走统一 api_request 走 auth 头
        content = api_request(
            "GET",
            f"/api/sentiment/reports/{rep['id']}/download",
            expect_bytes=True,
        )
        if isinstance(content, bytes) and content:
            download_word(content, file_name=f"report_{rep['id']}.docx")


# ============================================================
#  Tab 5: 别名与信源
# ============================================================


def _tab_settings(project_id: int) -> None:
    st.subheader("⚙️ 别名与信源")

    st.markdown("##### 搜索别名 (SentimentSubject)")
    subjects = api_request("GET", f"/api/sentiment/subjects?project_id={project_id}")
    if subjects:
        df = pd.DataFrame(subjects)[
            ["id", "alias_type", "alias_value", "match_mode", "weight", "is_active"]
        ]
        st.dataframe(df, use_container_width=True, hide_index=True)

    with st.form("add_subject"):
        c1, c2, c3, c4 = st.columns(4)
        alias_type = c1.selectbox(
            "类型", ["company", "brand", "product", "person", "domain", "code", "extra"]
        )
        alias_value = c2.text_input("值*")
        match_mode = c3.selectbox("匹配", ["contains", "exact", "regex"])
        weight = c4.number_input("权重", min_value=0, max_value=100, value=10)
        if st.form_submit_button("➕ 新增"):
            r = api_request(
                "POST",
                "/api/sentiment/subjects",
                json={
                    "project_id": project_id,
                    "alias_type": alias_type,
                    "alias_value": alias_value,
                    "match_mode": match_mode,
                    "weight": int(weight),
                    "is_active": True,
                },
            )
            if r and "id" in r:
                st.success(f"已添加 id={r['id']}")
                st.rerun()

    st.divider()
    st.markdown("##### 信源 (SentimentSource)")
    sources = api_request("GET", "/api/sentiment/sources")
    if sources:
        df = pd.DataFrame(sources)[
            [
                "id",
                "code",
                "display_name",
                "is_paid",
                "is_enabled",
                "last_run_status",
                "last_run_at",
            ]
        ]
        st.dataframe(df, use_container_width=True, hide_index=True)

        src_id = st.number_input("切换启用/停用 (信源 ID)", min_value=1, value=1, step=1)
        cur = next((s for s in sources if s["id"] == src_id), None)
        if cur:
            new_state = not cur["is_enabled"]
            label = "✅ 启用" if new_state else "🚫 停用"
            if st.button(f"{label} #{src_id}"):
                api_request(
                    "PUT", f"/api/sentiment/sources/{src_id}", json={"is_enabled": new_state}
                )
                st.rerun()


# ============================================================
#  入口
# ============================================================


def show_sentiment() -> None:
    apply_feishu_theme()
    page_header('📡', '舆情跟踪', '多源抓取 + AI 去重校验 + 简报/季报 + 全局红点')

    """舆情跟踪 — Streamlit 页面入口."""
    # [飞书化] st.markdown('<p class="sub-header">📡 舆情跟踪 (IPO 客户)</p>', unsafe_allow_html=True)  # 已被 page_header() 替代

    project_id = _pick_project()
    _render_unread_badge()
    tabs = st.tabs(["📊 舆情总览", "📰 事件库", "🗞️ 每日简报", "📈 季度跟踪报告", "⚙️ 别名与信源"])
    with tabs[0]:
        _tab_overview(project_id)
    with tabs[1]:
        _tab_events(project_id)
    with tabs[2]:
        _tab_briefings(project_id)
    with tabs[3]:
        _tab_quarterly(project_id)
    with tabs[4]:
        _tab_settings(project_id)
