"""Streamlit frontend for IPO Audit System."""
import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import time

# Configuration
API_BASE_URL = "http://localhost:8000"

st.set_page_config(
    page_title="IPO审计系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #4472C4;
        text-align: center;
        padding: 1rem;
    }
    .sub-header {
        font-size: 1.5rem;
        font-weight: bold;
        color: #2E4057;
    }
    .success-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #D4EDDA;
        border: 1px solid #C3E6CB;
        color: #155724;
    }
    .warning-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #FFF3CD;
        border: 1px solid #FFEAA7;
        color: #856404;
    }
    .error-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #F8D7DA;
        border: 1px solid #F5C6CB;
        color: #721C24;
    }
</style>
""", unsafe_allow_html=True)


def check_api_health() -> bool:
    """Check if API is available."""
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=2)
        return response.status_code == 200
    except:
        return False


def api_request(method: str, endpoint: str, **kwargs):
    """Make API request with error handling."""
    try:
        url = f"{API_BASE_URL}{endpoint}"
        response = requests.request(method, url, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        st.error("❌ 无法连接到后端服务，请确保 FastAPI 服务已启动")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"❌ API错误: {e}")
        return None


@st.cache_data(ttl=60)
def get_projects():
    """Get all projects."""
    return api_request("GET", "/api/projects/")


@st.cache_data(ttl=60)
def get_project(project_id: int):
    """Get single project."""
    return api_request("GET", f"/api/projects/{project_id}")


@st.cache_data(ttl=60)
def get_account_balances(project_id: int):
    """Get account balances for a project."""
    return api_request("GET", f"/api/projects/{project_id}/account-balances")


def create_project(name: str, company_name: str, industry: str, fiscal_year: int):
    """Create a new project."""
    data = {
        "name": name,
        "company_name": company_name,
        "industry": industry,
        "fiscal_year": fiscal_year,
    }
    result = api_request("POST", "/api/projects/", json=data)
    if result:
        st.cache_data.clear()
        return result
    return None


def upload_account_balances(project_id: int, file) -> bool:
    """Upload account balances Excel file."""
    files = {"file": (file.name, file.read(), file.type)}
    result = api_request("POST", f"/api/projects/{project_id}/account-balances", files=files)
    return result is not None


def upload_chronological_accounts(project_id: int, file) -> bool:
    """Upload chronological accounts Excel file."""
    files = {"file": (file.name, file.read(), file.type)}
    result = api_request("POST", f"/api/projects/{project_id}/chronological-accounts", files=files)
    return result is not None


def generate_workbook(project_id: int, template_type: str, include_charts: bool = True):
    """Generate workbook."""
    data = {
        "project_id": project_id,
        "template_type": template_type,
        "include_charts": include_charts,
    }
    return api_request("POST", "/api/workbooks/generate", json=data)


def check_trial_balance(project_id: int):
    """Check trial balance."""
    data = {"project_id": project_id}
    return api_request("POST", "/api/workbooks/trial-balance", json=data)


def main():
    """Main Streamlit application."""
    st.markdown('<p class="main-header">📊 IPO 审计系统</p>', unsafe_allow_html=True)

    # Sidebar
    st.sidebar.title("功能菜单")
    st.sidebar.markdown("---")

    # API health check
    if not check_api_health():
        st.sidebar.error("⚠️ 后端服务未连接")
        st.sidebar.markdown("请启动 FastAPI 服务:")
        st.sidebar.code("uv run uvicorn app.main:app --reload --port 8000")
    else:
        st.sidebar.success("✅ 后端服务已连接")

    st.sidebar.markdown("---")

    # Navigation
    page = st.sidebar.radio(
        "选择功能",
        ["🏠 首页概览", "📁 项目管理", "📤 数据导入", "📊 底稿生成", "⚖️ 试算平衡", "🔍 监管案例", "🤖 AI分析"]
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
    elif page == "🔍 监管案例":
        show_regulatory_cases()
    elif page == "🤖 AI分析":
        show_ai_analysis()


def show_homepage():
    """Show homepage with system overview."""
    st.markdown("## 📊 系统概览")

    # Stats cards
    col1, col2, col3, col4 = st.columns(4)

    projects = get_projects() or []
    with col1:
        st.metric("项目总数", len(projects))

    with col2:
        active_projects = len([p for p in projects if p.get("status") == "active"])
        st.metric("进行中项目", active_projects)

    with col3:
        st.metric("API状态", "在线" if check_api_health() else "离线")

    with col4:
        st.metric("版本", "0.1.0")

    st.markdown("---")

    # Quick actions
    st.markdown("###⚡ 快速操作")

    col1, col2, col3 = st.columns(3)

    with col1:
        ifst.button("➕ 新建项目", use_container_width=True, type="primary"):
            st.session_state["page"] = "📁 项目管理"
            st.rerun()

    with col2:
        if st.button("📤导入数据", use_container_width=True):
            st.session_state["page"] = "📤 数据导入"
            st.rerun()

    with col3:
        if st.button("📊 生成底稿", use_container_width=True):
            st.session_state["page"] = "📊 底稿生成"
            st.rerun()

    st.markdown("---")

    # Recent projects
    st.markdown("### 📋 最近项目")

    if projects:
        recent = projects[:5]
        df = pd.DataFrame(recent)
        st.dataframe(
            df[["name", "company_name", "fiscal_year", "status", "created_at"]],
            use_container_width=True,
        )
    else:
        st.info("暂无项目，请先创建项目")


def show_projects():
    """Show project management page."""
    st.markdown('<p class="sub-header">📁 项目管理</p>', unsafe_allow_html=True)

    # Tabs
    tab1, tab2 = st.tabs(["📋 项目列表","➕ 新建项目"])

    with tab1:
        st.markdown("#### 项目列表")

        projects = get_projects()
        if projects:
            df = pd.DataFrame(projects)
            st.dataframe(
                df[["id", "name", "company_name", "industry", "fiscal_year", "status"]],
                use_container_width=True,
            )
        else:
            st.info("暂无项目")

    with tab2:
        st.markdown("#### 创建新项目")

        with st.form("create_project_form"):
            name = st.text_input("项目名称", placeholder="例如：XX公司IPO审计")
            company_name = st.text_input("公司名称", placeholder="例如：华大基因")
            industry = st.selectbox(
                "所属行业",
                ["制造业", "信息技术", "医药生物", "金融服务", "房地产", "零售", "其他"]
            )
            fiscal_year = st.number_input("审计年度", min_value=2000, max_value=2030, value=2024)

            submitted = st.form_submit_button("创建项目", type="primary", use_container_width=True)

            if submitted:
                if not name or not company_name:
                    st.error("请填写项目名称和公司名称")
                else:
                    result = create_project(name, company_name, industry, fiscal_year)
                    if result:
                        st.success(f"✅ 项目创建成功: {result.get('name')}")
                        st.rerun()


def show_data_import():
    """Show data import page."""
    st.markdown('<p class="sub-header">📤 数据导入</p>', unsafe_allow_html=True)

    projects = get_projects()
    if not projects:
        st.warning("请先创建项目")
        return

    project_options = {f"{p['id']} - {p['name']}": p["id"] for p in projects}
    selected = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected]

    # Import tabs
    tab1, tab2, tab3 = st.tabs(["📋 科目余额表", "📒 序时账", "🏦 银行对账单"])

    with tab1:
        st.markdown("#### 导入科目余额表")
        st.markdown("**支持格式**: .xlsx, .xls, .csv")
        st.markdown("**必需列**: 科目编码, 科目名称, 余额方向")
        st.markdown("**可选列**: 期初余额, 借方发生额, 贷方发生额, 期末余额")

        uploaded_file = st.file_uploader("选择科目余额表文件", type=["xlsx", "xls", "csv"])

        if uploaded_file:
            if st.button("导入科目余额表", type="primary"):
                with st.spinner("导入中..."):
                    success = upload_account_balances(project_id, uploaded_file)
                    if success:
                        st.success("✅ 科目余额表导入成功")
                        st.rerun()

    with tab2:
        st.markdown("#### 导入序时账")
        st.markdown("**支持格式**: .xlsx, .xls, .csv")
        st.markdown("**必需列**: 凭证日期, 凭证号, 科目编码, 科目名称, 借方金额, 贷方金额")

        uploaded_file = st.file_uploader("选择序时账文件", type=["xlsx", "xls", "csv"])

        if uploaded_file:
            if st.button("导入序时账", type="primary"):
                with st.spinner("导入中..."):
                    success = upload_chronological_accounts(project_id, uploaded_file)
                    if success:
                        st.success("✅ 序时账导入成功")
                        st.rerun()

    with tab3:
        st.markdown("#### 导入银行对账单")
        st.markdown("**支持格式**: .xlsx, .xls, .csv")
        st.markdown("**必需列**: 对账单日期, 凭证号, 描述, 借方金额, 贷方金额, 余额")

        uploaded_file = st.file_uploader("选择银行对账单文件", type=["xlsx", "xls", "csv"])

        if uploaded_file:
            if st.button("导入银行对账单", type="primary"):
                with st.spinner("导入中..."):
                    st.info("银行对账单导入功能开发中")


def show_workbook_generation():
    """Show workbook generation page."""
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

    include_charts = st.checkbox("包含图表", value=True)

    if st.button("生成底稿", type="primary", use_container_width=True):
        with st.spinner("生成中..."):
            result = generate_workbook(project_id, template_type, include_charts)
            if result:
                st.success(f"✅ 底稿生成成功")
                st.markdown(f"📁 文件路径: `{result.get('file_path')}`")
                st.markdown(f"📥 下载链接: [{result.get('file_name')}]({result.get('download_url')})")


def show_trial_balance():
    """Show trial balance check page."""
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
            result = check_trial_balance(project_id)
            if result:
                is_balanced = result.get("is_balanced")

                if is_balanced:
                    st.markdown("""
                    <div class="success-box">
                        ✅<strong>试算平衡</strong> - 资产负债表平衡！
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown("""
                    <div class="warning-box">
                        ⚠️ <strong>试算不平衡</strong> - 存在差异，请检查数据
                    </div>
                    """, unsafe_allow_html=True)

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("借方合计", f"¥ {result.get('total_debit',0):,.2f}")
                with col2:
                    st.metric("贷方合计", f"¥ {result.get('total_credit', 0):,.2f}")
                with col3:
                    st.metric("差异", f"¥ {result.get('difference', 0):,.2f}")

                st.markdown("#### 科目明细")

                account_details = result.get("account_details", [])
                if account_details:
                    df = pd.DataFrame(account_details)
                    st.dataframe(df, use_container_width=True)


def show_regulatory_cases():
    """Show regulatory cases page."""
    st.markdown('<p class="sub-header">🔍 监管案例</p>', unsafe_allow_html=True)

    st.info("监管案例库功能正在开发中，敬请期待！")


def show_ai_analysis():
    """Show AI analysis page."""
    st.markdown('<p class="sub-header">🤖 AI 分析</p>', unsafe_allow_html=True)

    st.info("AI 分析功能正在开发中，敬请期待！")

    st.markdown("""
    ### 功能预告

    -🔍 **风险智能识别**: 结合监管案例，自动识别潜在风险点
    - 🔢 **数字自动校验**: 比对明细表与试算表，发现数字差异
    - ⚠️ **异常预警**: 发现异常波动、关联交易等
    - 📝 **审计建议生成**: 根据风险点生成下一步审计程序建议
    """)


if __name__ == "__main__":
    main()