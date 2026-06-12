"""Streamlit frontend for IPO Audit System - 全功能版."""
import os

import streamlit as st
import requests
import pandas as pd
from datetime import datetime

# 后端 API 基础地址。
#
# 默认值 "http://localhost:8000" 同时适用于:
#   1. 本地开发 (uvicorn :8000 + streamlit :8501/7860)
#   2. Hugging Face Space 单容器双进程部署 (uvicorn :8000 + streamlit :7860)
#      — Streamlit 通过服务端 HTTP 调 FastAPI, 浏览器只连 Streamlit 的 7860。
#
# 如果未来要把前端/后端拆成两个独立 Space 部署, 在 HF Space 的
# "Variables and secrets" 里覆盖 API_BASE_URL 为后端 Space 的公网 URL 即可。
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

st.set_page_config(
    page_title="IPO审计系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header {font-size: 2.5rem;font-weight:bold;color:#4472C4;text-align:center;padding:1rem;}
    .sub-header {font-size:1.5rem;font-weight:bold;color:#2E4057;}
    .success-box {padding:1rem;border-radius:0.5rem;background-color:#D4EDDA;border:1px solid #C3E6CB;color:#155724;}
    .warning-box {padding:1rem;border-radius:0.5rem;background-color:#FFF3CD;border:1px solid #FFEAA7;color:#856404;}
    .error-box {padding:1rem;border-radius:0.5rem;background-color:#F8D7DA;border:1px solid #F5C6CB;color:#721C24;}
    .metric-card {padding:1rem;border-radius:0.5rem;background-color:#E9ECEF;border:1px solid #DEE2E6;}
    .sentiment-badge {position: fixed; top: 0.5rem; right: 1rem; z-index: 999;
        background: #dc3545; color: white; border-radius: 999px;
        padding: 0.3rem 0.8rem; font-weight: bold; font-size: 0.85rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);}
    .sentiment-badge-zero {position: fixed; top: 0.5rem; right: 1rem; z-index: 999;
        background: #6c757d; color: white; border-radius: 999px;
        padding: 0.3rem 0.8rem; font-weight: bold; font-size: 0.85rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=30)
def get_sentiment_unread_count() -> int:
    """全局红点: 30 秒缓存, 调一次 /notifications/unread."""
    try:
        r = requests.get(f"{API_BASE_URL}/api/sentiment/notifications/unread?limit=1", timeout=2)
        if r.status_code == 200:
            return int((r.json() or {}).get("count", 0))
    except requests.exceptions.RequestException:
        pass
    return 0


def render_sentiment_global_badge() -> None:
    """在主页 main() 顶部渲染右上角红点 (全局)."""
    count = get_sentiment_unread_count()
    if count > 0:
        st.markdown(
            f'<div class="sentiment-badge">🔴 舆情 {count}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="sentiment-badge-zero">⚪ 舆情 0</div>',
            unsafe_allow_html=True,
        )


def check_api_health() -> bool:
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=2)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def api_request(method: str, endpoint: str, **kwargs):
    try:
        url = f"{API_BASE_URL}{endpoint}"
        response = requests.request(method, url, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        st.error("无法连接到后端服务，请确保FastAPI服务已启动")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"API错误: {e}")
        return None


@st.cache_data(ttl=60)
def get_projects():
    return api_request("GET", "/api/projects/") or []


def main():
    st.markdown('<p class="main-header">📊 IPO 审计系统 (专业版)</p>', unsafe_allow_html=True)

    # 全局红点 — 舆情未读 (右上角 fixed)
    render_sentiment_global_badge()

    # Sidebar
    st.sidebar.title("功能菜单")
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
        ]
    )

    if page == "🏠 首页概览":
        show_homepage()
    elif page == "📁 项目管理":
        show_projects()
    elif page == "📤 数据导入":
        show_data_import()
    elif page == "📊 底稿生成":
        show_workbook_generation()
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
                f"销售清单模块加载失败：{exc}。"
                "请确认已安装 pdfplumber (`uv add pdfplumber`)。"
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


def show_homepage():
    st.markdown("## 📊 系统概览")

    col1, col2, col3, col4 = st.columns(4)
    projects = get_projects() or []

    with col1:
        st.metric("项目总数", len(projects))
    with col2:
        active = len([p for p in projects if p.get("status") == "active"])
        st.metric("进行中项目", active)
    with col3:
        st.metric("API状态", "在线" if check_api_health() else "离线")
    with col4:
        st.metric("版本", "0.2.0")

    st.markdown("---")

    st.markdown("### ⚡ 快速操作")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("➕ 新建项目", use_container_width=True, type="primary"):
            st.session_state["page"] = "📁 项目管理"
            st.rerun()
    with col2:
        if st.button("📤 导入数据", use_container_width=True):
            st.session_state["page"] = "📤 数据导入"
            st.rerun()
    with col3:
        if st.button("📊 生成底稿", use_container_width=True):
            st.session_state["page"] = "📊 底稿生成"
            st.rerun()
    with col4:
        if st.button("📄 生成报告", use_container_width=True):
            st.session_state["page"] = "📄 综合报告"
            st.rerun()

    st.markdown("---")
    st.markdown("### 📋 最近项目")
    if projects:
        df = pd.DataFrame(projects[:5])
        st.dataframe(df[["name", "company_name", "fiscal_year", "status"]], use_container_width=True)
    else:
        st.info("暂无项目，请先创建项目")


def show_projects():
    st.markdown('<p class="sub-header">📁 项目管理</p>', unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["📋 项目列表", "➕ 新建项目"])

    with tab1:
        st.markdown("#### 项目列表")
        projects = get_projects()
        if projects:
            df = pd.DataFrame(projects)
            st.dataframe(df[["id", "name", "company_name", "industry", "fiscal_year", "status"]], use_container_width=True)
        else:
            st.info("暂无项目")

    with tab2:
        st.markdown("#### 创建新项目")
        with st.form("create_project_form"):
            name = st.text_input("项目名称", placeholder="例如：XX公司IPO审计")
            company_name = st.text_input("公司名称", placeholder="例如：华大基因")
            industry = st.selectbox("所属行业", ["制造业", "信息技术", "医药生物", "金融服务", "房地产", "零售", "其他"])
            fiscal_year = st.number_input("审计年度", min_value=2000, max_value=2030, value=2024)
            submitted = st.form_submit_button("创建项目", type="primary", use_container_width=True)

            if submitted:
                if not name or not company_name:
                    st.error("请填写项目名称和公司名称")
                else:
                    result = api_request("POST", "/api/projects/", json={"name": name, "company_name": company_name, "industry": industry, "fiscal_year": fiscal_year})
                    if result:
                        st.success(f"✅ 项目创建成功: {result.get('name')}")
                        st.rerun()


def show_data_import():
    st.markdown('<p class="sub-header">📤 数据导入</p>', unsafe_allow_html=True)

    projects = get_projects()
    if not projects:
        st.warning("请先创建项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected]

    tab1, tab2, tab3 = st.tabs(["📋 科目余额表", "📒 序时账", "🏦 银行对账单"])

    with tab1:
        st.markdown("#### 导入科目余额表")
        st.markdown("**支持格式**: .xlsx, .xls, .csv | **ERP**: 金蝶、用友、SAP自动识别")
        uploaded_file = st.file_uploader("选择科目余额表文件", type=["xlsx", "xls", "csv"])
        if uploaded_file and st.button("导入", type="primary"):
            files = {"file": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
            result = api_request("POST", f"/api/projects/{project_id}/account-balances", files=files)
            if result:
                st.success(f"✅ {result.get('message', '导入成功')}")
                st.rerun()

    with tab2:
        st.markdown("#### 导入序时账")
        uploaded_file = st.file_uploader("选择序时账文件", type=["xlsx", "xls", "csv"])
        if uploaded_file and st.button("导入序时账", type="primary"):
            files = {"file": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
            result = api_request("POST", f"/api/projects/{project_id}/chronological-accounts", files=files)
            if result:
                st.success(f"✅ {result.get('message', '导入成功')}")
                st.rerun()

    with tab3:
        st.markdown("#### 导入银行对账单")
        uploaded_file = st.file_uploader("选择银行对账单文件", type=["xlsx", "xls", "csv"])
        if uploaded_file and st.button("导入", type="primary"):
            files = {"file": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
            result = api_request("POST", f"/api/projects/{project_id}/bank-statements", files=files)
            if result:
                st.success(f"✅ {result.get('message', '导入成功')}")


def show_workbook_generation():
    st.markdown('<p class="sub-header">📊 底稿生成</p>', unsafe_allow_html=True)

    projects = get_projects()
    if not projects:
        st.warning("请先创建项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected]

    st.markdown("#### 选择模板类型")
    template_options = {
        "📋 科目明细表": "account_detail",
        "📈 利润表": "income_statement",
        "📊 资产负债表": "balance_sheet",
        "💰 现金流量表": "cash_flow",
        "⚖️ 试算平衡表": "trial_balance",
    }
    selected_template = st.selectbox("模板类型", list(template_options.keys()))
    template_type = template_options[selected_template]

    if st.button("生成底稿", type="primary", use_container_width=True):
        with st.spinner("生成中..."):
            result = api_request("POST", "/api/workbooks/generate", json={"project_id": project_id, "template_type": template_type})
            if result:
                st.success(f"✅ 底稿生成成功")
                st.markdown(f"📁 文件路径: `{result.get('file_path')}`")
                st.markdown(f"📥 下载链接: [{result.get('file_name')}]({result.get('download_url')})")


def show_trial_balance():
    st.markdown('<p class="sub-header">⚖️ 试算平衡</p>', unsafe_allow_html=True)

    projects = get_projects()
    if not projects:
        st.warning("请先创建项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected]

    if st.button("检查试算平衡", type="primary", use_container_width=True):
        with st.spinner("检查中..."):
            result = api_request("POST", "/api/workbooks/trial-balance", json={"project_id": project_id})
            if result:
                if result.get("is_balanced"):
                    st.markdown('<div class="success-box">✅ <strong>试算平衡</strong> - 资产负债表平衡！</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="warning-box">⚠️ <strong>试算不平衡</strong> - 存在差异，请检查数据</div>', unsafe_allow_html=True)

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("借方合计", f"¥ {result.get('total_debit', 0):,.2f}")
                with col2:
                    st.metric("贷方合计", f"¥ {result.get('total_credit', 0):,.2f}")
                with col3:
                    st.metric("差异", f"¥ {result.get('difference', 0):,.2f}")

                if result.get("account_details"):
                    st.markdown("#### 科目明细")
                    df = pd.DataFrame(result["account_details"])
                    st.dataframe(df, use_container_width=True)


def show_regulatory_cases():
    st.markdown('<p class="sub-header">🔍 监管案例库</p>', unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["📥 抓取案例", "📋 案例列表", "🔎 关键词搜索"])

    with tab1:
        st.markdown("#### 抓取监管案例")
        st.info("从证监会、上交所、深交所自动抓取问询函和处罚案例")
        if st.button("开始抓取", type="primary"):
            with st.spinner("抓取中，请稍候..."):
                result = api_request("POST", "/api/regulatory-cases/scrape")
                if result:
                    st.success(f"✅ 成功抓取 {result.get('scraped_count', 0)} 条案例")

    with tab2:
        st.markdown("#### 案例列表")
        cases = api_request("GET", "/api/regulatory-cases/")
        if cases:
            df = pd.DataFrame(cases)
            st.dataframe(df[["case_no", "case_type", "source", "publish_date", "title"]], use_container_width=True)
        else:
            st.info("暂无案例，请先抓取")

    with tab3:
        st.markdown("#### 关键词搜索")
        keywords = st.text_input("输入关键词（逗号分隔）")
        if keywords and st.button("搜索"):
            result = api_request("GET", f"/api/regulatory-cases/search/by-keywords?keywords={keywords}")
            if result:
                st.success(f"找到 {result.get('matched_count', 0)} 条相关案例")


def show_ai_analysis():
    st.markdown('<p class="sub-header">🤖 AI 风险分析</p>', unsafe_allow_html=True)

    projects = get_projects()
    if not projects:
        st.warning("请先创建项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected]

    st.info("🤖 AI分析功能需要配置MINIMAX_API_KEY后使用")

    st.markdown("### 📊 仪表盘数据")
    if st.button("获取仪表盘数据"):
        result = api_request("GET", f"/api/reports/dashboard?project_id={project_id}")
        if result:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("总资产", f"¥ {result.get('financial_summary', {}).get('total_assets', 0):,.0f}")
            with col2:
                st.metric("营业收入", f"¥ {result.get('financial_summary', {}).get('revenue', 0):,.0f}")
            with col3:
                st.metric("风险等级", result.get('risk_assessment', {}).get('level', '未知'))


def show_anomaly_detection():
    st.markdown('<p class="sub-header">📋 异常检测</p>', unsafe_allow_html=True)

    projects = get_projects()
    if not projects:
        st.warning("请先创建项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected]

    if st.button("开始异常检测", type="primary"):
        with st.spinner("检测中..."):
            result = api_request("GET", f"/api/reports/anomalies?project_id={project_id}")
            if result:
                anomalies = result.get("anomalies", [])
                st.success(f"检测到 {len(anomalies)} 项异常")

                if anomalies:
                    df = pd.DataFrame(anomalies)
                    st.dataframe(df, use_container_width=True)

                    # 风险汇总
                    risk_counts = df.groupby("risk_level").size() if "risk_level" in df.columns else pd.Series()
                    if not risk_counts.empty:
                        st.markdown("### 风险分布")
                        col1, col2, col3 = st.columns(3)
                        for level, count in risk_counts.items():
                            with col1 if level == "高" else col2 if level == "中" else col3:
                                st.metric(f"{level}风险", count)


def show_comprehensive_report():
    st.markdown('<p class="sub-header">📄 综合报告</p>', unsafe_allow_html=True)

    projects = get_projects()
    if not projects:
        st.warning("请先创建项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected]

    col1, col2 = st.columns(2)

    with col1:
        if st.button("📄 生成Word报告", use_container_width=True):
            with st.spinner("生成中..."):
                result = api_request("POST", f"/api/reports/generate/word?project_id={project_id}")
                if result:
                    st.success("✅ Word报告生成成功")

    with col2:
        if st.button("📕 生成PDF报告", use_container_width=True):
            with st.spinner("生成中..."):
                result = api_request("POST", f"/api/reports/generate/pdf?project_id={project_id}")
                if result:
                    st.success("✅ PDF报告生成成功")

    st.markdown("---")
    st.markdown("### 📊 快速预览")
    if st.button("预览仪表盘数据"):
        result = api_request("GET", f"/api/reports/dashboard?project_id={project_id}")
        if result:
            st.json(result)


if __name__ == "__main__":
    main()