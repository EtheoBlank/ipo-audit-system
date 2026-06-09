# IPO 审计系统 - Claude Code 项目文档

## 项目概述

IPO 审计系统是一个专业的 IPO 审计底稿生成与数据分析工具，支持自动化生成审计底稿Excel、数据校验和AI辅助风险分析。

## 技术栈

- **后端**: FastAPI + SQLAlchemy (Async)
- **前端**: Streamlit
- **数据库**: SQLite (开发) / PostgreSQL (生产)
- **AI**: MiniMax API
- **Python**: 3.10+

## 项目结构

```
ipo-audit-system/
├── app/
│   ├── api/ # API 路由
│   │   ├── projects.py         # 项目管理
│   │   ├── workbooks.py # 底稿生成
│   │   └── regulatory_cases.py # 监管案例
│   ├── core/                   # 核心配置
│   │   ├── config.py # 应用配置
│   │   └── database.py       # 数据库连接
│   ├── models/                # 数据模型
│   │   ├── db_models.py      # SQLAlchemy 模型
│   │   └── audit.py          # Pydantic schemas
│   ├── services/              # 业务逻辑
│   │   ├── excel_parser.py    # Excel 解析
│   │   ├── workbook_generator.py # 底稿生成
│   │   ├── trial_balance.py   # 试算平衡
│   │   ├── regulatory_scraper.py # 监管案例抓取
│   │   └── ai_analysis.py     # AI 分析
│   └── main.py               # FastAPI 应用入口
├── frontend/
│   └── app.py                # Streamlit 前端
├── tests/ # 测试
├── docs/                    # 文档
└── pyproject.toml          # 项目配置
```

## 启动命令

### 后端服务
```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### 前端界面
```bash
uv run streamlit run frontend/app.py
```

### 运行测试
```bash
uv run pytest tests/ -v
```

## 主要功能

1. **项目管理**: 创建和管理 IPO 审计项目
2. **数据导入**: Excel 格式的科目余额表、序时账、银行对账单导入
3. **底稿生成**: 自动生成标准化的审计底稿 Excel 文件
4. **试算平衡**: 验证资产负债表平衡和报表勾稽关系
5. **监管案例**: 抓取和检索证监会、交易所的监管案例
6. **AI 分析**: 利用 AI 识别风险点和生成审计建议

## 环境变量

复制 `.env.example` 为 `.env` 并配置以下变量：

- `DATABASE_URL`: 数据库连接字符串
- `MINIMAX_API_KEY`: MiniMax API 密钥 (用于AI功能)

## 开发注意事项

- 使用 `uv` 作为包管理器
- 所有数据库操作使用异步模式 (AsyncSession)
- API 遵循 RESTful规范
- 前端使用 Streamlit 构建