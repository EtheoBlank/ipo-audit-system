# IPO 审计系统

专业的 IPO 审计底稿生成与数据分析工具。

## 功能特性

###核心功能

-**📁 数据导入**: 支持 Excel 格式的科目余额表、序时账、银行对账单导入
- **📊 底稿生成**: 自动生成标准化的审计底稿 Excel 文件
- **⚖️ 试算平衡**: 验证资产负债表平衡和报表勾稽关系
- **🔍 监管案例**: 抓取和检索证监会、交易所的监管案例
- **🤖 AI 分析**: 利用 AI 识别风险点和生成审计建议

### 支持的底稿模板

| 模板类型 | 说明 |
|---------|------|
| `account_detail` | 科目明细表 |
| `income_statement` | 利润表 |
| `balance_sheet` | 资产负债表 |
| `cash_flow` | 现金流量表 |
| `trial_balance` | 试算平衡表 |

## 技术架构

```
┌─────────────────────────────────────────┐
│           用户界面 (Web/Streamlit)        │
└─────────────────────────────────────────┘
                    │
       ┌───────────┴───────────┐
        ▼ ▼
┌───────────────┐       ┌───────────────┐
│ FastAPI 后端  │       │  Streamlit 前端 │
└───────────────┘       └───────────────┘
        │
        ▼
┌─────────────────────────────────────────┐
│              服务层 (Services)           │
│  ExcelParser | WorkbookGenerator        │
│  TrialBalanceService | AIAnalysis       │
└─────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────┐
│              数据层 (SQLAlchemy)         │
│        SQLite / PostgreSQL              │
└─────────────────────────────────────────┘
```

## 快速开始

### 环境要求

- Python 3.10+
- uv 包管理器

### 安装

```bash
# 克隆项目
git clone https://github.com/yourusername/ipo-audit-system.git
cd ipo-audit-system

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入必要的配置
```

### 启动后端服务

```bash
# 开发模式
uv run uvicorn app.main:app --reload --port 8000

# 生产模式
uv run gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

### 启动前端界面

```bash
uv run streamlit run frontend/app.py
```

## 项目结构

```
ipo-audit-system/
├── app/
│   ├── api/              # API路由
│   │   ├── projects.py   # 项目管理接口
│   │   ├── workbooks.py   # 底稿生成接口
│   │   └── regulatory_cases.py  # 监管案例接口
│   ├── core/             # 核心配置
│   │   ├── config.py     # 应用配置
│   │   └── database.py  # 数据库配置
│   ├── models/          # 数据模型
│   │   ├── db_models.py # SQLAlchemy 模型
│   │   └── audit.py     # Pydantic schemas
│   ├── services/        # 业务逻辑
│   │   ├── excel_parser.py      # Excel 解析
│   │   ├── workbook_generator.py # 底稿生成
│   │   ├── trial_balance.py     # 试算平衡
│   │   ├── regulatory_scraper.py # 监管案例抓取
│   │   └── ai_analysis.py       # AI 分析
│   └── main.py          # FastAPI 应用入口
├── frontend/
│   └── app.py           # Streamlit 前端
├── tests/              # 测试文件
├── docs/               # 文档
└── pyproject.toml      # 项目配置
```

## API 文档

启动服务后访问:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要接口

#### 项目管理

```bash
# 创建项目
POST /api/projects/

# 获取项目列表
GET /api/projects/

# 获取单个项目
GET /api/projects/{project_id}
```

#### 数据导入

```bash
# 导入科目余额表
POST /api/projects/{project_id}/account-balances

# 导入序时账
POST /api/projects/{project_id}/chronological-accounts

# 导入银行对账单
POST /api/projects/{project_id}/bank-statements
```

#### 底稿生成

```bash
# 生成底稿
POST /api/workbooks/generate

# 试算平衡检查
POST /api/workbooks/trial-balance
```

## 开发计划

- [x] 第一阶段：数据接入系统
- [x] 第二阶段：底稿生成系统
- [ ] 第三阶段：监管案例库系统
- [ ] 第四阶段：AI 分析引擎
- [ ] 第五阶段：试算平衡系统
- [ ] 第六阶段：综合报告生成

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License