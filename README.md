# IPO 审计系统 (专业版)

专业的 IPO 审计底稿生成与数据分析工具，支持从数据导入到AI风险分析的完整审计流程。

## 功能特性

###核心模块 (已全部实现)

| 阶段 | 模块 | 功能 |
|------|------|------|
| ✅ 第一阶段 | 数据接入系统 | Excel导入、金蝶/用友/SAP自动识别 |
| ✅ 第二阶段 | 底稿生成系统 | 科目明细表、利润表、资产负债表等 |
| ✅ 第三阶段 | 监管案例库 | 证监会/交易所问询函和处罚案例抓取 |
| ✅ 第四阶段 | AI分析引擎 | 风险识别、异常检测、审计程序生成 |
| ✅ 第五阶段 | 试算平衡系统 | 资产负债表平衡、银行对账、报表勾稽 |
| ✅ 第六阶段 | 综合报告生成 | Word/PDF报告、交互式仪表盘 |

### ERP系统支持

| ERP类型 | 支持版本 | 自动识别 |
|---------|----------|----------|
| 金蝶 | K3 Cloud / 云星空 | ✅ |
| 用友 | NC / U8 / YonBIP | ✅ |
| SAP | S/4HANA / ECC | ✅ |
| 标准模板 | CSV/Excel | ✅ |

### 支持的底稿模板

- `account_detail`: 科目明细表
- `income_statement`: 利润表
- `balance_sheet`: 资产负债表
- `cash_flow`: 现金流量表
- `trial_balance`: 试算平衡表

## 技术架构

```
┌─────────────────────────────────────────┐
│           用户界面 (Streamlit)           │
└─────────────────────────────────────────┘
                    │
       ┌───────────┴───────────┐
        ▼
┌─────────────────────────────────────────┐
│           FastAPI 后端服务              │
│  •项目管理 •数据导入  •底稿生成        │
│  •监管案例  •AI分析    •综合报告        │
└─────────────────────────────────────────┘
                    │
       ┌───────────┴───────────┐
        ▼
┌─────────────────────────────────────────┐
│              服务层 (Services)           │
│  •erp_adapters       •ai_analysis_engine │
│  •trial_balance_engine •report_generator │
│  •regulatory_case_service               │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│              SQLAlchemy (Async) │
│              SQLite / PostgreSQL         │
└─────────────────────────────────────────┘
```

## 快速开始

### 环境要求

- Python 3.10+
- uv 包管理器

### 安装

```bash
# 克隆仓库
git clone https://github.com/EtheoBlank/ipo-audit-system.git
cd ipo-audit-system

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑.env，填入必要的配置
```

### 启动服务

```bash
# 启动后端 (端口8000)
uv run uvicorn app.main:app --reload --port 8000

# 新终端，启动前端
uv run streamlit run frontend/app.py
```

访问:
- API文档: http://localhost:8000/docs
- Web界面: http://localhost:8500

## 项目结构

```
ipo-audit-system/
├── app/
│   ├── api/ # API路由
│   │   ├── projects.py # 项目管理
│   │   ├── workbooks.py        # 底稿生成
│   │   ├── regulatory_cases.py # 监管案例
│   │   └── reports.py         # 综合报告
│   ├── core/                   # 核心配置
│   ├── models/                # 数据模型
│   ├── services/              # 业务逻辑
│   │   ├── erp_adapters.py    # ERP适配器
│   │   ├── trial_balance_engine.py # 试算平衡
│   │   ├── ai_analysis_engine.py    # AI分析
│   │   ├── regulatory_case_service.py # 监管案例
│   │   └── report_generator.py        # 报告生成
│   └── main.py
├── frontend/
│   └── app.py                 # Streamlit前端
├── .claude/skills/
│   └── audit-procedures.md    # 审计程序技能库
├── docs/
│   └── DATA_FORMAT_SPEC.md    # ERP数据接口规范
└── tests/
```

## API接口

### 主要接口

```bash
# 项目管理
POST /api/projects/ # 创建项目
GET    /api/projects/              # 获取项目列表
GET    /api/projects/{id} # 获取单个项目

# 数据导入 (自动识别ERP类型)
POST   /api/projects/{id}/account-balances      # 导入科目余额表
POST   /api/projects/{id}/chronological-accounts # 导入序时账
POST   /api/projects/{id}/bank-statements        # 导入银行对账单

# 底稿生成
POST   /api/workbooks/generate     # 生成底稿Excel
POST   /api/workbooks/trial-balance #试算平衡检查

# 监管案例
POST   /api/regulatory-cases/scrape # 抓取案例
GET    /api/regulatory-cases/      # 案例列表

# 综合报告
POST   /api/reports/generate/word # 生成Word报告
POST   /api/reports/generate/pdf   # 生成PDF报告
GET    /api/reports/dashboard      # 仪表盘数据
GET    /api/reports/anomalies      # 异常检测
```

## AI功能配置

要启用AI分析功能，需要配置MiniMax API Key:

1. 在 https://platform.minimaxi.com 注册账号
2. 获取API Key
3. 在`.env`文件中设置:
```
MINIMAX_API_KEY=your_api_key_here
```

## 开发计划

所有阶段已完成并上线。

## 许可证

MIT License