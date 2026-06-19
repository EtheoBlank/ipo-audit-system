"""Streamlit frontend for IPO Audit System - 全功能版."""

import streamlit as st
import requests
import pandas as pd

from frontend._http import API_BASE_URL, api_request, auth_headers
from frontend._components import (
    apply_feishu_theme,
    page_header,
    metric_card,
    render_top_badges,
    section_card_start,
    section_card_end,
    feishu_divider,
    empty_state,
    FEISHU_C,
    FEISHU_R,
)

st.set_page_config(
    page_title="IPO 审计系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 注入飞书浅色主题 (幂等, 后续 sub-page 再调一次也无副作用)
apply_feishu_theme()


# P0: 取消缓存, 每次都拉新 (跨用户 / 跨 firm 不能复用缓存)
def get_sentiment_unread_count() -> int:
    """全局红点: 30 秒缓存, 调一次 /notifications/unread."""
    try:
        r = requests.get(
            f"{API_BASE_URL}/api/sentiment/notifications/unread?limit=1",
            headers=auth_headers(),
            timeout=2,
        )
        if r.status_code == 200:
            return int((r.json() or {}).get("count", 0))
    except requests.exceptions.RequestException:
        pass
    return 0


# P0: 取消缓存, 每次都拉新 (跨用户 / 跨 firm 不能复用缓存)
def get_global_unread_count() -> dict:
    """Pack A: 通用通知中心未读数, 30s 缓存."""
    try:
        r = requests.get(
            f"{API_BASE_URL}/api/notifications/unread", headers=auth_headers(), timeout=2
        )
        if r.status_code == 200:
            return r.json() or {"total_unread": 0}
    except requests.exceptions.RequestException:
        pass
    return {"total_unread": 0}


def render_sentiment_global_badge() -> None:
    """在主页 main() 顶部渲染右上角红点 (舆情 + 全局通知) — 飞书化."""
    s_count = get_sentiment_unread_count()
    g = get_global_unread_count()
    total = int(g.get("total_unread", 0))
    render_top_badges(sentiment_count=s_count, notification_count=total)


def check_api_health() -> bool:
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=2)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def get_auth_status() -> dict:
    """Pack A: 取 /health 看 AUTH_ENABLED, 用于决定是否提示登录."""
    try:
        r = requests.get(f"{API_BASE_URL}/health", timeout=2)
        if r.status_code == 200:
            return r.json() or {}
    except requests.exceptions.RequestException:
        pass
    return {}


# P0: 取消缓存, 每次都拉新 (firm_id 已写入 token claims)
def get_projects():
    return api_request("GET", "/api/projects/") or []


def _render_user_card_in_sidebar() -> None:
    """Pack A: sidebar 顶部显示当前用户卡片 + 登出按钮."""
    user = st.session_state.get("auth_user")
    if user:
        st.sidebar.markdown(
            f"**👤 {user.get('full_name', '-')}**  \n"
            f"角色: `{user.get('role', '-')}`  \n"
            f"用户名: `{user.get('username', '-')}`"
        )
        if st.sidebar.button("🚪 登出", key="sidebar_logout", width="stretch"):
            try:
                requests.post(
                    f"{API_BASE_URL}/api/auth/logout",
                    headers=auth_headers(),
                    timeout=5,
                )
            except Exception:
                pass
            st.session_state.pop("auth_token", None)
            st.session_state.pop("auth_user", None)
            st.session_state.pop("auth_refresh_token", None)
            st.rerun()
    else:
        health = get_auth_status()
        if health.get("auth_enabled"):
            st.sidebar.warning("🔒 未登录, 仅可查看部分功能")
        else:
            st.sidebar.info("⚠️ 当前 AUTH_ENABLED=false (开发模式无认证)")


def main():
    # 飞书页头
    page_header(
        icon="📊",
        title="IPO 审计系统 (专业版)",
        subtitle="面向 IPO 审计的底稿生成 · 试算平衡 · AI 风险识别一站式平台",
    )

    # 全局红点 — 舆情 + 通用通知 (右上角 fixed)
    render_sentiment_global_badge()

    # Sidebar
    st.sidebar.title("功能菜单")

    # Pack A — 顶部用户卡片 + 登出
    _render_user_card_in_sidebar()
    st.sidebar.markdown("---")

    if not check_api_health():
        st.sidebar.error("⚠️ 后端服务未连接")
        st.sidebar.markdown("请启动FastAPI服务:")
        st.sidebar.code("uv run uvicorn app.main:app --reload --port 8000")
    else:
        st.sidebar.success("✅ 后端服务已连接")

    st.sidebar.markdown("---")

    # Navigation
    page = st.sidebar.radio(
        "选择功能",
        [
            "🏠 首页概览",
            "📁 项目管理",
            "📤 数据导入",
            "📊 底稿生成",
            "📑 长期资产发生额审定",  # Pack A — 用户特别要求
            "⚖️ 试算平衡",
            "🔍 监管案例库",
            "🤖 AI风险分析",
            "📋异常检测",
            "📄 综合报告",
            "📦 销售清单整理",
            "📄 收入合同分析",
            "🏷️ 收发存盘点&减值",
            "📬 函证管理",
            "⚖️ 法律法规库",
            "📚 自助知识库",
            "📑 综合底稿自动生成",
            "👥 项目组管理",
            "📡 舆情跟踪",
            "🎨 报告模板",  # Pack A
            "🔔 通知中心",  # Pack A
            "🤝 关联方专项",  # Pack B
            "🔄 审计循环 (Pack C)",  # Pack C
            "🎯 IPO 专属 (Pack D)",  # Pack D
            "🔐 系统管理",  # Pack A
        ],
        key="sidebar_page_radio",
    )

    # P0 修复: 改用 st.query_params 驱动跳转, 避免与 sidebar radio 抢状态
    # (原 session_state hack 会在 rerun 时被 pop 出去, 用户立即切 radio 会闪烁)
    _nav = st.query_params.get("nav")
    _NAV_MAP = {
        "projects": "📁 项目管理",
        "import": "📤 数据导入",
        "workbook": "📊 底稿生成",
        "report": "📄 综合报告",
    }
    if _nav and _nav in _NAV_MAP:
        page = _NAV_MAP[_nav]
        # 消费掉, 不持久化
        st.query_params.pop("nav", None)

    if page == "🏠 首页概览":
        show_homepage()
    elif page == "📁 项目管理":
        show_projects()
    elif page == "📤 数据导入":
        show_data_import()
    elif page == "📊 底稿生成":
        show_workbook_generation()
    elif page == "📑 长期资产发生额审定":
        try:
            from frontend.pages_account_audit import show_account_audit

            show_account_audit()
        except ImportError as exc:
            st.error(f"长期资产审定模块加载失败：{exc}")
    elif page == "⚖️ 试算平衡":
        show_trial_balance()
    elif page == "🔍 监管案例库":
        show_regulatory_cases()
    elif page == "🤖 AI风险分析":
        show_ai_analysis()
    elif page == "📋 异常检测":
        show_anomaly_detection()
    elif page == "📄 综合报告":
        show_comprehensive_report()
    elif page == "📦 销售清单整理":
        # Imported lazily so the rest of the app remains usable even if the
        # new module's optional deps (e.g. pdfplumber) aren't installed yet.
        try:
            from frontend.pages_sales_ledger import show_sales_ledger

            show_sales_ledger()
        except ImportError as exc:
            st.error(
                f"销售清单模块加载失败：{exc}。请确认已安装 pdfplumber (`uv add pdfplumber`)。"
            )
    elif page == "📄 收入合同分析":
        try:
            from frontend.pages_contracts import show_contracts

            show_contracts()
        except ImportError as exc:
            st.error(f"合同分析模块加载失败：{exc}。")
    elif page == "🏷️ 收发存盘点&减值":
        try:
            from frontend.pages_inventory import show_inventory

            show_inventory()
        except ImportError as exc:
            st.error(f"收发存盘点&减值模块加载失败：{exc}。")
    elif page == "📬 函证管理":
        try:
            from frontend.pages_confirmations import show_confirmations

            show_confirmations()
        except ImportError as exc:
            st.error(f"函证管理模块加载失败：{exc}。")
    elif page == "⚖️ 法律法规库":
        try:
            from frontend.pages_regulations import show_regulations

            show_regulations()
        except ImportError as exc:
            st.error(f"法律法规库模块加载失败：{exc}。")
    elif page == "📚 自助知识库":
        try:
            from frontend.pages_knowledge_base import show_knowledge_base

            show_knowledge_base()
        except ImportError as exc:
            st.error(f"自助知识库模块加载失败：{exc}。")
    elif page == "📑 综合底稿自动生成":
        try:
            from frontend.pages_comprehensive import show_comprehensive_workpaper

            show_comprehensive_workpaper()
        except ImportError as exc:
            st.error(f"综合底稿模块加载失败：{exc}。")
    elif page == "👥 项目组管理":
        try:
            from frontend.pages_team_management import show_team_management

            show_team_management()
        except ImportError as exc:
            st.error(f"项目组管理模块加载失败：{exc}。")
    elif page == "📡 舆情跟踪":
        try:
            from frontend.pages_sentiment import show_sentiment

            show_sentiment()
        except ImportError as exc:
            st.error(f"舆情跟踪模块加载失败：{exc}。")
    elif page == "🎨 报告模板":
        try:
            from frontend.pages_report_templates import show_report_templates

            show_report_templates()
        except ImportError as exc:
            st.error(f"报告模板模块加载失败：{exc}")
    elif page == "🔔 通知中心":
        try:
            from frontend.pages_notification import show_notifications

            show_notifications()
        except ImportError as exc:
            st.error(f"通知中心模块加载失败：{exc}")
    elif page == "🤝 关联方专项":
        try:
            from frontend.pages_related_parties import show_related_parties

            show_related_parties()
        except ImportError as exc:
            st.error(f"关联方专项模块加载失败：{exc}")
    elif page == "🔄 审计循环 (Pack C)":
        try:
            from frontend.pages_audit_cycles import show_audit_cycles

            show_audit_cycles()
        except ImportError as exc:
            st.error(f"审计循环模块加载失败：{exc}")
    elif page == "🎯 IPO 专属 (Pack D)":
        try:
            from frontend.pages_ipo_specials import show_ipo_specials

            show_ipo_specials()
        except ImportError as exc:
            st.error(f"IPO 专属模块加载失败：{exc}")
    elif page == "🔐 系统管理":
        try:
            from frontend.pages_auth import show_auth

            show_auth()
        except ImportError as exc:
            st.error(f"系统管理模块加载失败：{exc}")


def show_homepage():
    page_header(icon="🏠", title="系统概览", subtitle="一站式 IPO 审计工作台")

    projects = get_projects() or []
    active = len([p for p in projects if p.get("status") == "active"])
    api_status = "在线" if check_api_health() else "离线"

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        metric_card(
            "项目总数", str(len(projects)),
            delta=f"+{active} 进行中" if active else None,
            delta_direction="up" if active else "neutral",
        )
    with col2:
        metric_card("进行中项目", str(active), status="primary" if active else "default")
    with col3:
        metric_card(
            "API 状态", api_status,
            delta=("健康" if api_status == "在线" else "请检查后端"),
            delta_direction="up" if api_status == "在线" else "down",
        )
    with col4:
        metric_card("系统版本", "v0.2.0", delta="2026-06 更新", delta_direction="up")

    st.markdown("")  # 间距
    st.markdown("### ⚡ 快速操作")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("➕ 新建项目", use_container_width=True, type="primary", key="home_new_project"):
            st.query_params["nav"] = "projects"
            st.rerun()
    with col2:
        if st.button("📤 导入数据", use_container_width=True, key="home_import_data"):
            st.query_params["nav"] = "import"
            st.rerun()
    with col3:
        if st.button("📊 生成底稿", use_container_width=True, key="home_gen_workbook"):
            st.query_params["nav"] = "workbook"
            st.rerun()
    with col4:
        if st.button("📄 生成报告", use_container_width=True, key="home_gen_report"):
            st.query_params["nav"] = "report"
            st.rerun()

    feishu_divider()
    st.markdown("### 📋 最近项目")
    if projects:
        section_card_start("最新项目动态", "📁")
        df = pd.DataFrame(projects[:5])
        st.dataframe(
            df[["name", "company_name", "fiscal_year", "status"]],
            use_container_width=True, hide_index=True,
        )
        section_card_end()
    else:
        empty_state(
            icon="📁",
            message="暂无项目",
            hint="点击上方「➕ 新建项目」开始你的第一个 IPO 审计项目",
        )


def show_projects():
    page_header(icon="📁", title="项目管理", subtitle="管理所有 IPO 审计项目 (创建 / 查询)")

    tab1, tab2 = st.tabs(["📋 项目列表", "➕ 新建项目"])

    with tab1:
        section_card_start("项目列表", "📋")
        projects = get_projects()
        if projects:
            df = pd.DataFrame(projects)
            st.dataframe(
                df[["id", "name", "company_name", "industry", "fiscal_year", "status"]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            empty_state(icon="📁", message="暂无项目", hint="切到「➕ 新建项目」标签页创建")
        section_card_end()

    with tab2:
        section_card_start("创建新项目", "➕")
        with st.form("create_project_form"):
            name = st.text_input("项目名称", placeholder="例如：XX公司IPO审计", key="new_project_name")
            company_name = st.text_input("公司名称", placeholder="例如：华大基因", key="new_project_company")
            industry = st.selectbox(
                "所属行业", ["制造业", "信息技术", "医药生物", "金融服务", "房地产", "零售", "其他"],
                key="new_project_industry",
            )
            fiscal_year = st.number_input("审计年度", min_value=2000, max_value=2030, value=2024, key="new_project_fy")
            submitted = st.form_submit_button("创建项目", type="primary", use_container_width=True)

            if submitted:
                if not name or not company_name:
                    st.error("请填写项目名称和公司名称")
                else:
                    result = api_request(
                        "POST",
                        "/api/projects/",
                        json={
                            "name": name,
                            "company_name": company_name,
                            "industry": industry,
                            "fiscal_year": fiscal_year,
                        },
                    )
                    if result:
                        st.success(f"✅ 项目创建成功: {result.get('name')}")
                        st.rerun()
        section_card_end()


def show_data_import():
    page_header(icon="📤", title="数据导入", subtitle="科目余额表 / 序时账 / 银行对账单, 支持金蝶/用友/SAP 自动识别")

    projects = get_projects()
    if not projects:
        empty_state(icon="📁", message="请先创建项目", hint="到「📁 项目管理」创建第一个项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()), key="data_import_project")
    project_id = project_options[selected]

    tab1, tab2, tab3 = st.tabs(["📋 科目余额表", "📒 序时账", "🏦 银行对账单"])

    with tab1:
        section_card_start("导入科目余额表", "📋")
        st.markdown("**支持格式**: `.xlsx`, `.xls`, `.csv` | **ERP**: 金蝶、用友、SAP 自动识别")
        uploaded_file = st.file_uploader(
            "选择科目余额表文件", type=["xlsx", "xls", "csv"], key="data_import_balance_upload"
        )
        if uploaded_file and st.button("导入科目余额表", type="primary", use_container_width=True, key="imp_balance_run"):
            files = {"file": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
            result = api_request(
                "POST", f"/api/projects/{project_id}/account-balances", files=files
            )
            if result:
                st.success(f"✅ {result.get('message', '导入成功')}")
                st.rerun()
        section_card_end()

    with tab2:
        section_card_start("导入序时账", "📒")
        uploaded_file = st.file_uploader(
            "选择序时账文件", type=["xlsx", "xls", "csv"], key="data_import_ledger_upload"
        )
        if uploaded_file and st.button("导入序时账", type="primary", use_container_width=True, key="imp_ledger_run"):
            files = {"file": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
            result = api_request(
                "POST", f"/api/projects/{project_id}/chronological-accounts", files=files
            )
            if result:
                st.success(f"✅ {result.get('message', '导入成功')}")
                st.rerun()
        section_card_end()

    with tab3:
        section_card_start("导入银行对账单", "🏦")
        uploaded_file = st.file_uploader(
            "选择银行对账单文件", type=["xlsx", "xls", "csv"], key="data_import_bank_upload"
        )
        if uploaded_file and st.button("导入银行对账单", type="primary", use_container_width=True, key="imp_bank_run"):
            files = {"file": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
            result = api_request("POST", f"/api/projects/{project_id}/bank-statements", files=files)
            if result:
                st.success(f"✅ {result.get('message', '导入成功')}")
        section_card_end()


def show_workbook_generation():
    page_header(icon="📊", title="底稿生成", subtitle="5 种标准底稿模板, 一键导出 Excel")

    projects = get_projects()
    if not projects:
        empty_state(icon="📁", message="请先创建项目", hint="到「📁 项目管理」创建第一个项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()), key="workbook_project")
    project_id = project_options[selected]

    section_card_start("选择模板类型", "📋")
    template_options = {
        "📋 科目明细表": "account_detail",
        "📈 利润表": "income_statement",
        "📊 资产负债表": "balance_sheet",
        "💰 现金流量表": "cash_flow",
        "⚖️ 试算平衡表": "trial_balance",
    }
    selected_template = st.selectbox("模板类型", list(template_options.keys()), key="workbook_tpl")
    template_type = template_options[selected_template]

    if st.button("生成底稿", type="primary", use_container_width=True, key="workbook_run"):
        with st.spinner("生成中..."):
            result = api_request(
                "POST",
                "/api/workbooks/generate",
                json={"project_id": project_id, "template_type": template_type},
            )
            if result:
                st.success("✅ 底稿生成成功")
                st.markdown(f"📁 文件路径: `{result.get('file_path')}`")
                st.markdown(
                    f"📥 下载链接: [{result.get('file_name')}]({result.get('download_url')})"
                )
    section_card_end()


def show_trial_balance():
    page_header(icon="⚖️", title="试算平衡", subtitle="检查借/贷方合计与差异, 验证报表平衡")

    projects = get_projects()
    if not projects:
        empty_state(icon="📁", message="请先创建项目", hint="到「📁 项目管理」创建第一个项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()), key="trial_balance_project")
    project_id = project_options[selected]

    if st.button("检查试算平衡", type="primary", use_container_width=True, key="trial_balance_run"):
        with st.spinner("检查中..."):
            result = api_request(
                "POST", "/api/workbooks/trial-balance", json={"project_id": project_id}
            )
            if result:
                is_balanced = result.get("is_balanced")
                # 飞书化结果展示
                if is_balanced:
                    st.markdown(
                        f'<div style="background:{FEISHU_C.success_light};color:{FEISHU_C.success};'
                        f'padding:1rem 1.25rem;border-radius:{FEISHU_R.lg};'
                        f'border-left:4px solid {FEISHU_C.success};font-weight:500;">'
                        f'✅ 试算平衡 - 资产负债表平衡</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="background:{FEISHU_C.warning_light};color:{FEISHU_C.warning};'
                        f'padding:1rem 1.25rem;border-radius:{FEISHU_R.lg};'
                        f'border-left:4px solid {FEISHU_C.warning};font-weight:500;">'
                        f'⚠️ 试算不平衡 - 存在差异, 请检查数据</div>',
                        unsafe_allow_html=True,
                    )

                col1, col2, col3 = st.columns(3)
                with col1:
                    metric_card("借方合计", f"¥ {result.get('total_debit', 0):,.2f}")
                with col2:
                    metric_card("贷方合计", f"¥ {result.get('total_credit', 0):,.2f}")
                with col3:
                    diff = float(result.get("difference", 0))
                    metric_card(
                        "差异",
                        f"¥ {diff:,.2f}",
                        delta=("平衡" if abs(diff) < 0.01 else "需调整"),
                        delta_direction=("up" if abs(diff) < 0.01 else "down"),
                    )

                if result.get("account_details"):
                    section_card_start("科目明细", "📋")
                    df = pd.DataFrame(result["account_details"])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    section_card_end()


def show_regulatory_cases():
    page_header(icon="🔍", title="监管案例库", subtitle="证监会 / 上交所 / 深交所 问询函与处罚案例")

    tab1, tab2, tab3 = st.tabs(["📥 抓取案例", "📋 案例列表", "🔎 关键词搜索"])

    with tab1:
        section_card_start("抓取监管案例", "📥")
        st.info("从证监会、上交所、深交所自动抓取问询函和处罚案例")
        if st.button("开始抓取", type="primary", key="reg_scrape_run"):
            with st.spinner("抓取中，请稍候..."):
                result = api_request("POST", "/api/regulatory-cases/scrape")
                if result:
                    st.success(f"✅ 成功抓取 {result.get('scraped_count', 0)} 条案例")
        section_card_end()

    with tab2:
        section_card_start("案例列表", "📋")
        cases = api_request("GET", "/api/regulatory-cases/")
        if cases:
            df = pd.DataFrame(cases)
            st.dataframe(
                df[["case_no", "case_type", "source", "publish_date", "title"]],
                use_container_width=True, hide_index=True,
            )
        else:
            empty_state(icon="📋", message="暂无案例", hint="先到「📥 抓取案例」抓取")
        section_card_end()

    with tab3:
        section_card_start("关键词搜索", "🔎")
        keywords = st.text_input("输入关键词（逗号分隔）", key="reg_search_kw")
        if keywords and st.button("搜索", key="reg_search_run"):
            result = api_request(
                "GET", f"/api/regulatory-cases/search/by-keywords?keywords={keywords}"
            )
            if result:
                st.success(f"找到 {result.get('matched_count', 0)} 条相关案例")
        section_card_end()


def show_ai_analysis():
    page_header(icon="🤖", title="AI 风险分析", subtitle="MiniMax 大模型驱动的风险识别与仪表盘")

    projects = get_projects()
    if not projects:
        empty_state(icon="📁", message="请先创建项目", hint="到「📁 项目管理」创建第一个项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()), key="ai_project")
    project_id = project_options[selected]

    st.info("🤖 AI 分析功能需要配置 `MINIMAX_API_KEY` 后使用")

    section_card_start("仪表盘数据", "📊")
    if st.button("获取仪表盘数据", type="primary", key="ai_dashboard_run"):
        result = api_request("GET", f"/api/reports/dashboard?project_id={project_id}")
        if result:
            col1, col2, col3 = st.columns(3)
            with col1:
                metric_card(
                    "总资产",
                    f"¥ {result.get('financial_summary', {}).get('total_assets', 0):,.0f}",
                )
            with col2:
                metric_card(
                    "营业收入",
                    f"¥ {result.get('financial_summary', {}).get('revenue', 0):,.0f}",
                )
            with col3:
                level = result.get("risk_assessment", {}).get("level", "未知")
                level_status = {
                    "高": "error", "中": "warning", "低": "success",
                }.get(level, "default")
                metric_card(
                    "风险等级", level,
                    delta="需重点关注" if level == "高" else None,
                    delta_direction=("down" if level == "高" else "neutral"),
                    status=level_status,
                )
    section_card_end()


def show_anomaly_detection():
    page_header(icon="📋", title="异常检测", subtitle="自动识别交易/科目级异常并按风险分级")

    projects = get_projects()
    if not projects:
        empty_state(icon="📁", message="请先创建项目", hint="到「📁 项目管理」创建第一个项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()), key="anomaly_project")
    project_id = project_options[selected]

    if st.button("开始异常检测", type="primary", key="anomaly_run"):
        with st.spinner("检测中..."):
            result = api_request("GET", f"/api/reports/anomalies?project_id={project_id}")
            if result:
                anomalies = result.get("anomalies", [])
                st.success(f"检测到 {len(anomalies)} 项异常")

                if anomalies:
                    df = pd.DataFrame(anomalies)
                    st.dataframe(df, use_container_width=True, hide_index=True)

                    # 风险汇总
                    risk_counts = (
                        df.groupby("risk_level").size()
                        if "risk_level" in df.columns
                        else pd.Series()
                    )
                    if not risk_counts.empty:
                        st.markdown("### 风险分布")
                        cols = st.columns(max(len(risk_counts), 1))
                        for i, (level, count) in enumerate(risk_counts.items()):
                            with cols[i % len(cols)]:
                                status = {"高": "error", "中": "warning", "低": "success"}.get(
                                    level, "default"
                                )
                                metric_card(f"{level}风险", str(count), status=status)
                else:
                    empty_state(icon="✅", message="未发现异常", hint="当前项目数据健康")


def show_comprehensive_report():
    page_header(icon="📄", title="综合报告", subtitle="Word / PDF 报告生成 + 仪表盘预览")

    projects = get_projects()
    if not projects:
        empty_state(icon="📁", message="请先创建项目", hint="到「📁 项目管理」创建第一个项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()), key="comprehensive_project")
    project_id = project_options[selected]

    section_card_start("生成报告", "📄")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📄 生成 Word 报告", use_container_width=True, type="primary", key="comprehensive_word"):
            with st.spinner("生成中..."):
                result = api_request("POST", f"/api/reports/generate/word?project_id={project_id}")
                if result:
                    st.success("✅ Word 报告生成成功")
    with col2:
        if st.button("📕 生成 PDF 报告", use_container_width=True, key="comprehensive_pdf"):
            with st.spinner("生成中..."):
                result = api_request("POST", f"/api/reports/generate/pdf?project_id={project_id}")
                if result:
                    st.success("✅ PDF 报告生成成功")
    section_card_end()

    feishu_divider()
    section_card_start("快速预览", "📊")
    if st.button("预览仪表盘数据", key="comprehensive_preview"):
        result = api_request("GET", f"/api/reports/dashboard?project_id={project_id}")
        if result:
            st.json(result)
    section_card_end()


if __name__ == "__main__":
    main()
