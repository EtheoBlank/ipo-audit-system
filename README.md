# 🏛️ IPO 审计系统

> 一站式 IPO 审计平台 — 自动化底稿生成 / 数据校验 / AI 风险分析

**👉 在线浏览：https://ipo-audit-system-lovat.vercel.app/**

---

## 📌 它解决什么问题

> IPO 项目的痛点不是"没有数据"，是**数据散、格式乱、链路长、复核累**。

* **被审计单位丢给你一堆合同 / 发票 / 发货单 / 报关单**，没有一张能直接用的销售清单 → **AI 把散乱文档拼成结构化清单**，9 个维度直接出分析。
* **科目余额表来自金蝶 / 用友 / SAP，列名五花八门**，手工映射半天 → **ERP 适配器**自动识别 + 标准化，然后一键生成 5 类标准底稿。
* **盘点又冷又累，现场用纸表回办公室再录，误差大** → **金额优先 + 阈值覆盖** 生成盘点表 → 现场**拍照 OCR + AI 解析自动回填**。
* **函证寄出去半年，谁回了 / 谁差异 / 差多少，全靠 Excel 累计** → 发函即**锁定金额快照**，回函拍照 OCR + AI 比对，自动出差异表。
* **复核会议总在重复"那本书里好像有个类似案例"** → **自助知识库**把书本切块向量化，生成审计说明时**自动检索相似实务**注入 AI prompt。
* **重要审计期间客户突然出新闻没人盯** → **舆情跟踪** 自动抓取 RSS / 监管公告 / 搜索引擎，AI 去重校验后出日报 / 季报。

---

## 🧩 模块全景（13 大模块）

| 模块 | 核心能力 |
|------|---------|
| 📂 项目管理 | 多租户隔离 + 审计周期跟踪 |
| 📊 数据导入 | Excel 科目余额 / 序时账 / 银行对账单（金蝶/用友/SAP 自动识别）|
| 📑 底稿生成 | 5 类标准底稿 Excel 一键导出 |
| ⚖️ 试算平衡 | 单体 + 合并报表勾稽校验 |
| 🤖 AI 分析 | DeepSeek 驱动的风险点识别 + 异常检测 |
| 📰 监管案例 | CSRC / SSE / SZSE / 巨潮资讯聚合 |
| 📚 知识库 | PDF / EPUB / DOCX 切块向量化 + 语义检索 |
| ⚖️ 法规库 | 财政部 / 证监会 / 税务总局 / 外管局 / 央行 自动抓取 |
| 👥 项目组管理 | 5 级人员 + AI 工作计划 + 会议评分 + 进度看板 |
| 📦 收发存盘点 | 金额优先 + 拍照 OCR + 库龄 + NRV 跌价 + 跌价转回 |
| ✉️ 函证管理 | 财政部模板 + 锁定金额快照 + 回函 OCR + AI 比对 |
| 📡 舆情跟踪 | 多源抓取 + AI 去重 + 简报 / 季报 |
| 📋 综合底稿 | 事务所模板上传 + `${placeholder}` 渲染 + QA 引擎 |

---

## 🚀 30 秒快速开始

```bash
# 1. 安装依赖（用 uv，比 pip 快 10x）
uv sync

# 2. 复制环境变量
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY

# 3. 启动后端（FastAPI + Jinja2 Web UI）
uv run uvicorn app.main:app --reload --port 8000

# 4. 打开浏览器
#    Web UI:    http://localhost:8000/
#    API 文档:  http://localhost:8000/docs
#    健康检查:  curl http://localhost:8000/health
```

> 💡 **不想本地装？** 直接打开 **https://ipo-audit-system-lovat.vercel.app/** 试用已部署版本。

---

## 🖼️ 系统架构

```
                    ┌─────────────────────────────────┐
                    │     IPO 审计系统 (FastAPI)      │
                    │                                 │
  Browser ────────►│  ┌──────────┐    ┌────────────┐  │
                    │  │ Jinja2   │    │  REST API  │  │
                    │  │ Web UI   │    │  /api/*    │  │
                    │  └──────────┘    └────────────┘  │
                    │         │              │         │
                    │         ▼              ▼         │
                    │  ┌──────────────────────────┐   │
                    │  │     Service Layer        │   │
                    │  │  业务编排 + AI 调用       │   │
                    │  └──────────────────────────┘   │
                    └──────┬───────────────┬────────────┘
                           │               │
                ┌──────────▼──┐     ┌──────▼──────┐
                │   SQLite /  │     │  DeepSeek   │
                │   Postgres  │     │  AI API     │
                └─────────────┘     └─────────────┘
```

---

## 🛠️ 技术栈

- **后端**: FastAPI + SQLAlchemy 2.0 (Async) + asyncpg / aiosqlite
- **前端**: Jinja2 server-rendered + Tailwind CSS
- **AI**: DeepSeek API
- **数据库**: SQLite（开发）/ PostgreSQL（生产）
- **包管理**: uv

---

## 🔌 API 文档

完整 API 文档见 **https://ipo-audit-system-lovat.vercel.app/docs**（Swagger UI · 240 个 endpoints）

OpenAPI 3.1 规范：https://ipo-audit-system-lovat.vercel.app/openapi.json

---

## 📚 文档

- [使用教程](docs/usage.md)
- [数据格式规范](docs/DATA_FORMAT_SPEC.md)

---

## 📜 License

MIT
