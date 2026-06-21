# 🏛️ IPO 审计系统

> 一站式 IPO 审计平台 — 自动化底稿生成 / 数据校验 / AI 风险分析

**👉 在线浏览：https://ipo-audit-system-lovat.vercel.app/**

---

## 项目简介

IPO 审计系统是一个专业的 IPO 审计底稿生成与数据分析工具，支持自动化生成审计底稿 Excel、数据校验、AI 辅助风险分析、销售清单整理和收入合同分析。

13 大模块 · 60+ API · 全异步全栈 · FastAPI + Jinja2 Web UI

## 主要功能

| 模块 | 说明 |
|------|------|
| 项目管理 | 创建和管理 IPO 审计项目 |
| 数据导入 | Excel 科目余额表 / 序时账 / 银行对账单（金蝶/用友/SAP 自动识别） |
| 底稿生成 | 自动生成标准化的审计底稿 Excel |
| 试算平衡 | 验证资产负债表平衡和报表勾稽关系 |
| AI 分析 | 风险点识别 + 异常检测 |
| 监管案例 | 证监会 / 交易所监管案例库 |
| 知识库 | 实务书籍向量化检索 |
| 法规库 | 财政部 / 证监会 / 税务总局 / 外管局法规自动抓取 |
| 项目组管理 | 人员 / 计划 / 日报 / 会议 / 卡点 / 建议 |
| 收发存盘点 | 金额优先 + 阈值覆盖 + 拍照 OCR 回填 + 库龄 + 跌价 |
| 函证管理 | 财政部模板 + 锁定金额快照 + 回函 OCR + AI 比对 |
| 舆情跟踪 | 多源抓取 + AI 去重 + 简报 / 季报 |
| 综合底稿 | 事务所模板上传 + 字段映射 + AI 填充 + QA 引擎 |

## 技术栈

- **后端**: FastAPI + SQLAlchemy 2.0 (Async) + asyncpg / aiosqlite
- **前端**: Jinja2 server-rendered + Tailwind CSS
- **AI**: DeepSeek API
- **部署**: Vercel serverless

## API 文档

完整 API 文档见 https://ipo-audit-system-lovat.vercel.app/docs（Swagger UI · 240 个 endpoints）。

OpenAPI 3.1 规范：https://ipo-audit-system-lovat.vercel.app/openapi.json

## 本地运行

```bash
# 1. 安装依赖（用 uv）
uv sync

# 2. 复制环境变量
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 等

# 3. 启动后端
uv run uvicorn app.main:app --reload --port 8000

# 4. 打开浏览器
# Web UI: http://localhost:8000/
# API 文档: http://localhost:8000/docs
```

## License

MIT
