---
title: IPO 审计系统
emoji: 📊
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: 自动化 IPO 审计底稿生成与数据分析工具 · FastAPI + Streamlit
---

> 上面的 YAML 是 [Hugging Face Spaces (Docker SDK)](https://huggingface.co/docs/hub/spaces-config-reference) 的元数据,GitHub 渲染时会被当成普通代码块忽略,不影响阅读。

<div align="center">

# 🏛️ IPO Audit System

### 让 AI 干 AI 擅长的事,让审计师做审计师擅长的事

**一站式 IPO 审计平台 ·  13 大模块 ·  60+ API ·  全异步全栈**
*把"找底稿、做分析、出报告"从「以小时计」压到「以分钟计」*

[![Python](https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![SQLAlchemy](https://img.shields.io/badge/ORM-SQLAlchemy%202.0-D71F00?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![DeepSeek](https://img.shields.io/badge/AI-DeepSeek-4D6BFE?logo=openai&logoColor=white)](https://platform.deepseek.com/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000)](https://github.com/astral-sh/ruff)
[![Hugging Face Space](https://img.shields.io/badge/%F0%9F%A4%97%20Live%20Demo-HF%20Space-orange?logo=huggingface&logoColor=white)](https://huggingface.co/spaces/EtheoZheng/EtheoBlank)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/EtheoBlank/ipo-audit-system/pulls)

[🤗 在线体验](https://huggingface.co/spaces/EtheoZheng/EtheoBlank) ·
[📖 使用教程](docs/usage.md) ·
[🚀 30 秒快速开始](#-30-秒快速开始) ·
[🧩 模块全景](#-模块全景13-大模块) ·
[🔌 API 速查](#-api-路由速查) ·
[🗺️ 路线图](#️-路线图)

</div>

---

## 📌 它解决什么问题

> IPO 项目的痛点不是"没有数据",是**数据散、格式乱、链路长、复核累**。

* **被审计单位丢给你一堆合同 / 发票 / 发货单 / 报关单**, 没有一张能直接用的销售清单 →
  **AI 把散乱文档拼成结构化清单**, 9 个维度直接出分析。
* **科目余额表来自金蝶 / 用友 / SAP, 列名五花八门**, 手工映射半天 →
  **ERP 适配器**自动识别 + 标准化, 然后一键生成 5 类标准底稿。
* **盘点又冷又累, 现场用纸表回办公室再录, 误差大** →
  **金额优先+阈值覆盖** 生成盘点表 → 现场**拍照 OCR + AI 解析自动回填**。
* **函证寄出去半年, 谁回了 / 谁差异 / 差多少, 全靠 Excel 累计** →
  发函即**锁定金额快照**, 回函拍照 OCR + AI 比对, 自动出差异表。
* **复核会议总在重复"那本书里好像有个类似案例"** →
  **自助知识库**把书本切块向量化, 生成审计说明时**自动检索相似实务**注入 AI prompt。
* **重要审计期间客户突然出新闻没人盯** →
  **舆情跟踪**每天定时扫多源, AI 去重 + 校验后生成简报 / 季报 Word。

---

## 🧩 模块全景(13 大模块)

| # | 模块 | 主要能力 | 关键技术 |
|---|------|---------|---------|
| 1 | 📁 **项目管理** | 项目创建 / 公司基础信息 / 状态流转 / 审计师分配 | FastAPI + SQLAlchemy 2.0 |
| 2 | 📤 **数据导入** | 科目余额表 / 序时账 / 银行对账单导入,**金蝶 / 用友 / SAP / 手工模板自动识别** | `app/services/erp_adapters.py` |
| 3 | 📊 **底稿生成** | 5 类标准底稿 Excel: 科目明细表 / 利润表 / 资产负债表 / 现金流量表 / 试算平衡表 | openpyxl + 模板化 |
| 4 | ⚖️ **试算平衡** | 资产负债表平衡 / 报表勾稽 / 银行对账 / **合并报表抵销** | 规则引擎 |
| 5 | 📦 **销售清单整理** | 散乱文档 → DeepSeek 合成结构化清单 → **9 个维度收入循环分析** + Excel | DeepSeek JSON Mode |
| 6 | 📄 **收入合同分析** | OCR(PaddleOCR/EasyOCR/Tesseract) + **CAS 14 五步法** + 6 类风险扫描 | OCR + DeepSeek |
| 7 | 📦 **收发存盘点&减值** | 金额优先+阈值覆盖盘点表 / **行业化盘点计划** / 现场**拍照 OCR 回填** / FIFO 库龄 / NRV 跌价 / 上年跌价转回 | Paddle/EasyOCR + AI |
| 8 | ✉️ **函证管理** | 银行 / 客户 / 供应商 / 其他往来询证函生成 → 金额快照锁定 → 回函**拍照 OCR + AI 解析** → 差异自动统计 | 财政部官方模板 + CSA 1311/1502/1504 |
| 9 | 🔍 **监管案例库** | 抓取证监会 / 沪深交易所问询函 / 处罚决定, 关键词检索 | BeautifulSoup + Selenium |
| 10 | 📚 **法律法规库** | 自动抓取 **证监会 / 财政部 / 国家税务总局 / 外管局 / 人民银行** 政策 / 准则 / 规章 / 问答口径, 多维过滤 + 全文搜索 + 项目级收藏 | 多站点适配器 |
| 11 | 🧠 **自助知识库** | 上传实务书籍(PDF/EPUB/DOCX/TXT/MD) → 切块 + 向量化(**TF-IDF / MiniMax / DeepSeek 三 provider**) → 生成审计说明时**按科目/风险点自动检索相似案例**注入 AI prompt | 向量检索 |
| 12 | 👥 **项目组管理** | 5 级人员库(项目负责人 / 高级经理 / 经理 / 高级审计员 / 审计员)+ **AI 自动按账套规模生成 IPO 工作计划** + 站会/周会/启动会/复核会**AI 质量评分(0-100)** + 日报 + 卡点 + **个人/项目级可视化进度看板** + AI 周期性管理建议 | Streamlit + Altair |
| 13 | 📡 **舆情跟踪** | 多源(免费 RSS + 官方公告 + 可选付费 API)+ APScheduler 定时扫描 + AI 去重校验 + 简报 / 季报 .docx 生成 + **全局未读红点** | APScheduler + feedparser + AI |
| ✨ | 📋 **综合底稿(Comprehensive)** | 上传事务所自有底稿模板 → **字段映射 + 填充引擎 + QA 引擎 + 规则引擎 + Web 搜索** 一键全量底稿生成 | 多引擎流水线 |

每个模块都设计了**降级路径**: AI 不可用时退回标准模板 / 规则引擎, **绝不 500**。

---

## 🚀 30 秒快速开始

```bash
# 1️⃣ 克隆
git clone https://github.com/EtheoBlank/ipo-audit-system.git
cd ipo-audit-system

# 2️⃣ 装依赖(推荐用 uv,比 pip 快 10×)
uv sync

# 3️⃣ 配置 API Key —— 注意 .env 已被 .gitignore
cp .env.example .env
# 编辑器打开 .env,至少填:
#   DEEPSEEK_API_KEY=sk-xxxxxxxx   # 销售清单 / 合同分析 / 库存 / 函证 OCR 后 AI 解析
#   MINIMAX_API_KEY=xxxx           # 可选,AI 风险分析

# 4️⃣ 启动后端(端口 8000)
uv run uvicorn app.main:app --reload --port 8000

# 5️⃣ 另开终端,启动前端(端口 8501)
uv run streamlit run frontend/app.py
```

浏览器打开:
- 🌐 **Web 界面**: <http://localhost:8501>
- 📚 **API 文档(Swagger)**: <http://localhost:8000/docs>
- 📕 **API 文档(ReDoc)**: <http://localhost:8000/redoc>
- ❤️ **健康检查**: <http://localhost:8000/health>

> 💡 **不想本地装?** 直接打开 [**🌐 在线体验**](https://etheozheng-etheoblank.hf.space) 试用已部署到 Hugging Face Space 的版本(功能完全相同,首次冷启 ~30s)。

> 完整使用教程含截图位 + 故障排查 FAQ,请看 [docs/usage.md](docs/usage.md)

---

## 🖼️ 系统架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Streamlit  前端  (frontend/)                    │
│ 项目 · 导入 · 底稿 · 试算平衡 · 销售清单 · 合同 · 收发存 · 函证 · 监管案例   │
│ 法规库 · 知识库 · 项目组 · 舆情(全局红点) · 综合底稿 · AI 风险 · 综合报告  │
└─────────────────────────────────────────────────────────────────────────┘
                                  │  HTTP (REST + OpenAPI)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           FastAPI  后端  (app/api/)                       │
│   60+ 路由 · 全异步 · 自动 OpenAPI · CORS 白名单 · 统一 logging            │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
       ┌──────────────────────────┼─────────────────────────────┐
       ▼                          ▼                             ▼
┌──────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   AI 服务层   │         │    业务引擎层      │         │     数据层       │
│              │         │                  │         │                 │
│ DeepSeek     │         │ ERP 适配器         │         │ SQLAlchemy 2.0  │
│  · 销售合成   │         │  · 金蝶/用友/SAP   │         │  · async ORM    │
│  · 合同五步法 │         │                  │         │  · Mapped[]     │
│  · 库存解析   │         │ 试算平衡引擎       │         │                 │
│  · 函证解析   │         │ 底稿生成器         │         │ SQLite (开发)   │
│              │         │ 收入循环分析器     │         │ PostgreSQL(生产)│
│ MiniMax      │         │ 合同五步法分析器   │         │                 │
│  · 风险分析   │         │ 库龄/NRV/跌价      │         │ 文件存储         │
│              │         │ 函证统计 + 差异    │         │  · uploads/     │
│ 向量嵌入      │         │ AI 工作计划        │         │  · outputs/     │
│ TF-IDF /     │         │ AI 质量评分        │         │  · knowledge_   │
│ MiniMax /    │         │ 舆情去重 + 校验    │         │    base/        │
│ DeepSeek     │         │ 综合底稿流水线      │         │                 │
│              │         │ APScheduler 调度   │         │                 │
└──────────────┘         └──────────────────┘         └─────────────────┘
```

---

## 🗂️ 项目结构

```
ipo-audit-system/
├── app/                                # ───── 后端 ─────
│   ├── api/                            # 14 个 FastAPI 路由模块
│   │   ├── projects.py                 #  项目管理
│   │   ├── workbooks.py                #  底稿生成
│   │   ├── reports.py                  #  综合报告/异常检测
│   │   ├── regulatory_cases.py         #  监管案例
│   │   ├── sales_ledger.py             #  销售清单整理
│   │   ├── contracts.py                #  收入合同分析
│   │   ├── inventory.py                #  收发存盘点&减值
│   │   ├── confirmations.py            #  函证管理
│   │   ├── regulations.py              #  法律法规库
│   │   ├── knowledge_base.py           #  自助知识库
│   │   ├── team_management.py          #  项目组管理
│   │   ├── sentiment.py                #  舆情跟踪
│   │   └── comprehensive.py            #  综合底稿(模板化)
│   ├── core/                           # 核心配置
│   │   ├── config.py                   #  pydantic-settings 全局配置
│   │   ├── database.py                 #  异步 SQLAlchemy session
│   │   └── logging.py                  #  统一 logging 配置
│   ├── models/                         # 数据模型
│   │   ├── db_models.py                #  全部 SQLAlchemy ORM (单文件聚合)
│   │   ├── audit.py                    #  底稿 / 试算平衡 Pydantic schemas
│   │   ├── sales_ledger.py             #  销售清单 schemas
│   │   ├── contracts.py                #  合同 schemas
│   │   ├── inventory.py                #  收发存 / 盘点 / 跌价 schemas
│   │   ├── confirmation.py             #  函证 schemas
│   │   ├── sentiment.py                #  舆情 schemas
│   │   └── team_management.py          #  项目组 schemas
│   ├── services/                       # 业务逻辑(按模块分子包)
│   │   ├── excel_parser.py             #  Excel 解析
│   │   ├── erp_adapters.py             #  金蝶/用友/SAP 适配
│   │   ├── workbook_generator.py       #  底稿生成
│   │   ├── trial_balance.py            #  试算平衡
│   │   ├── trial_balance_engine.py     #  试算平衡引擎(含合并抵销)
│   │   ├── regulatory_scraper.py       #  监管案例抓取
│   │   ├── regulatory_case_service.py
│   │   ├── regulation_scraper.py       #  法规自动抓取(CSRC/MOF/STA/SAFE/PBOC)
│   │   ├── ai_analysis.py              #  MiniMax AI 分析
│   │   ├── ai_analysis_engine.py       #  风险识别 + 异常检测引擎
│   │   ├── audit_note_generator.py     #  审计说明(KB + 法规 + AI)
│   │   ├── report_generator.py         #  Word / PDF 报告生成
│   │   ├── sales_ledger/               # 销售清单子包
│   │   │   ├── deepseek_client.py
│   │   │   ├── document_parser.py
│   │   │   ├── synthesizer.py          #  AI 合成
│   │   │   ├── analyzer.py             #  9 维度分析
│   │   │   └── excel_exporter.py
│   │   ├── contract_analysis/          # 合同分析子包
│   │   │   ├── ocr.py                  #  Paddle/Easy/Tesseract 兜底
│   │   │   └── analyzer.py             #  CAS 14 五步法
│   │   ├── inventory/                  # 收发存子包
│   │   │   ├── importer.py             #  ERP 自动识别
│   │   │   ├── count_sheet.py          #  金额优先+阈值覆盖盘点表
│   │   │   ├── count_plan.py           #  行业化盘点计划 + AI 对话修改
│   │   │   ├── aging_engine.py         #  FIFO 库龄 + NRV 跌价 + 转回
│   │   │   ├── photo_processor.py      #  现场盘点照片 OCR 回填
│   │   │   └── excel_exporter.py
│   │   ├── confirmation/               # 函证子包
│   │   │   ├── letter_generator.py     #  财政部 / CSA 模板
│   │   │   ├── response_processor.py   #  回函 OCR + AI 解析
│   │   │   ├── stats_builder.py        #  发函 / 回函 / 差异统计
│   │   │   └── excel_exporter.py
│   │   ├── knowledge_base/             # 知识库子包
│   │   │   ├── document_loader.py      #  PDF/EPUB/DOCX/TXT/MD
│   │   │   ├── chunker.py              #  中文切块
│   │   │   ├── embedder.py             #  TF-IDF / MiniMax / DeepSeek
│   │   │   ├── retriever.py            #  语义 + 关键词混合
│   │   │   └── service.py
│   │   ├── team_management/            # 项目组子包
│   │   │   ├── work_plan_generator.py  #  账套导入后自动触发
│   │   │   ├── quality_assessor.py     #  会议纪要 AI 评分
│   │   │   ├── recommendation_generator.py # AI 管理建议
│   │   │   ├── progress_tracker.py     #  进度聚合
│   │   │   └── service.py
│   │   ├── sentiment/                  # 舆情子包
│   │   │   ├── scheduler.py            #  APScheduler 定时
│   │   │   ├── http_client.py
│   │   │   ├── llm_client.py
│   │   │   ├── dedup.py                #  事件去重
│   │   │   ├── scraper_service.py
│   │   │   ├── notifier.py             #  红点通知
│   │   │   ├── sources/                # 多源适配器
│   │   │   │   ├── rss_adapter.py
│   │   │   │   ├── announce_adapter.py
│   │   │   │   ├── regulator_adapter.py
│   │   │   │   ├── paid_adapters.py    # Tavily / Bocha / SerpAPI (可选)
│   │   │   │   └── manual_adapter.py
│   │   │   ├── briefing/               # 简报
│   │   │   │   ├── detector.py
│   │   │   │   ├── generator.py
│   │   │   │   ├── verifier.py         #  AI 自校验
│   │   │   │   └── word_exporter.py
│   │   │   └── quarterly/              # 季报
│   │   │       ├── aggregator.py
│   │   │       ├── financial_input.py
│   │   │       ├── generator.py
│   │   │       ├── verifier.py
│   │   │       ├── trigger.py
│   │   │       └── word_exporter.py
│   │   └── comprehensive/              # 综合底稿子包
│   │       ├── template_parser.py      #  模板解析
│   │       ├── field_mapper.py         #  字段映射
│   │       ├── fill_engine.py          #  填充引擎
│   │       ├── qa_engine.py            #  QA 引擎
│   │       ├── rule_engine.py          #  规则引擎
│   │       ├── web_search_engine.py    #  联网检索
│   │       ├── firm_template_service.py
│   │       ├── builtin_rules.py
│   │       └── schemas.py
│   ├── utils/                          # 工具函数
│   │   ├── db_helpers.py               #  ORM → DataFrame
│   │   └── upload_safety.py            #  上传文件安全校验
│   └── main.py                         # FastAPI 入口 + lifespan(含调度器启停)
│
├── frontend/                           # ───── 前端 ─────
│   ├── app.py                          # Streamlit 主入口 + 侧边栏导航 + 全局红点
│   ├── pages_sales_ledger.py
│   ├── pages_contracts.py
│   ├── pages_inventory.py
│   ├── pages_confirmations.py
│   ├── pages_regulations.py
│   ├── pages_knowledge_base.py
│   ├── pages_team_management.py
│   ├── pages_sentiment.py
│   └── pages_comprehensive.py
│
├── tests/                              # ───── 测试 ─────
│   ├── test_services.py                # 基础服务
│   ├── test_debug.py                   # ERP 适配器 / 解析综合
│   ├── test_p0_regressions.py          # P0 回归
│   ├── test_sales_ledger* / test_contracts*  (按模块组织)
│   ├── test_inventory.py / smoke_inventory.py
│   ├── test_confirmation_p0.py
│   ├── test_sentiment.py
│   ├── test_team_management.py
│   ├── test_comprehensive_parser.py / test_comprehensive_frontend.py / test_e2e_comprehensive.py
│   ├── test_field_mapper.py / test_fill_engine.py / test_qa_engine.py / test_rule_engine.py
│   ├── test_firm_template_service.py / test_web_search_engine.py
│   └── ...
│
├── docs/                               # ───── 文档 ─────
│   ├── usage.md                              # 完整使用教程(含 FAQ)
│   ├── DATA_FORMAT_SPEC.md                   # 数据格式规范
│   ├── COMPREHENSIVE_WORKPAPER_TEMPLATE_SPEC.md  # 综合底稿模板规范
│   └── screenshots/                          # 截图(待补)
│
├── scripts/                            # 辅助脚本
│   └── git_push.sh
│
├── .github/workflows/ci.yml            # CI: py 3.10/3.11/3.12 矩阵 + ruff + pytest
├── .env.example                        # 环境变量模板(无 key)
├── .gitignore                          # 忽略 .env / 数据库 / uploads / outputs
├── .pre-commit-config.yaml             # pre-commit 钩子
├── CLAUDE.md                           # 项目级 AI 协作指令
├── LICENSE                             # MIT
├── pyproject.toml                      # uv / hatch 配置 + 依赖声明
├── uv.lock                             # uv 锁文件
└── README.md                           # 你正在读的这份 ✨
```

---

## 🔌 API 路由速查

| 前缀 | 模块 | 关键端点示例 |
|------|------|--------|
| `/api/projects` | 项目管理 | `POST /` 新建 · `GET /{id}` 详情 · `POST /{id}/import` 导入 |
| `/api/workbooks` | 底稿生成 | `POST /{project_id}/generate?template=account_detail` |
| `/api/reports` | 综合报告 | `POST /{project_id}/summary` · `GET /{project_id}/dashboard` |
| `/api/sales-ledger` | 销售清单 | `POST /projects/{id}/sales-documents` 上传 · `POST /sales-records/synthesize` AI 合成 · `POST /revenue-analysis` 9 维度 · `GET /export` 导出 |
| `/api/contracts` | 合同分析 | `POST /projects/{id}/contracts` 上传 OCR · `POST /contracts/{cid}/analyze` 五步法 |
| `/api/inventory` | 收发存 | `POST /projects/{id}/import` · `POST /count-sheets/generate` · `POST /count-sheets/upload-photo` · `POST /aging/run` · `POST /impairment/run` |
| `/api/confirmations` | 函证 | `POST /projects/{id}/generate-letters` · `POST /lock-snapshot` · `POST /responses/upload-photo` · `GET /stats` |
| `/api/regulatory-cases` | 监管案例 | `GET /search?q=` · `POST /refresh` |
| `/api/regulations` | 法规库 | `GET /search?source=&date_range=&q=` · `POST /favorite` |
| `/api/knowledge-base` | 知识库 | `POST /books` 上传 · `POST /search` 检索 · `DELETE /books/{id}` |
| `/api/team-management` | 项目组 | `POST /members` · `POST /work-plan/generate` · `POST /meetings/{id}/score` · `GET /progress` · `GET /recommendations` |
| `/api/sentiment` | 舆情 | `GET /notifications/unread` · `POST /scan/trigger` · `POST /briefings/generate` · `POST /quarterly/generate` |
| `/api/comprehensive` | 综合底稿 | `POST /templates/upload` · `POST /generate-all` |
| `/health` | 系统 | 健康检查 |

完整 API 文档自动生成: 启动后访问 <http://localhost:8000/docs>

---

## 🤗 部署到 Hugging Face Spaces

> **🌐 实际访问地址(短域名):** <https://etheozheng-etheoblank.hf.space>
>
> **🔗 Space 主页(标准 URL):** <https://huggingface.co/spaces/EtheoZheng/EtheoBlank>
>
> ✅ **部署已验证:** Streamlit 启动 ~7s,FastAPI 健康检查通过,SQLite 持久化到 `/data` 卷,DeepSeek AI 已配置。点开短域名即可使用 IPO 审计系统的全部 Web 功能 —— 不需要 clone 代码、不需要装 Python。

### 架构

单容器双进程(Docker SDK 模式),只对外暴露 **7860** 一个端口:

| 进程 | 容器内端口 | 暴露 | 说明 |
|---|---|---|---|
| Streamlit | 7860 | ✅ | 用户浏览器入口(HF Space 唯一外露) |
| uvicorn  | 8000 | ❌ | FastAPI 后端,仅供 Streamlit 服务端调用 |

Streamlit → FastAPI 是**服务端到服务端** HTTP 调用,不经浏览器,所以**完全不存在 CORS 跨域问题**,也不需要反向代理。

### 已有 Space 的快速体验

1. 打开 <https://huggingface.co/spaces/EtheoZheng/EtheoBlank>
2. 等 Streamlit 加载完成(冷启动 30s~1min)
3. 直接用 — 数据导入 / 试算平衡 / 底稿生成 / 监管案例 / 法规库 / 知识库 / 项目组管理 / 收发存盘点等**全部本地能用的功能**在这里都能用

### 已知限制 (Demo 性质)

| 限制 | 影响 | 说明 |
|---|---|---|
| **未装 OCR (`paddleocr`)** | "收发存盘点 → 拍照 OCR 回填" 模块无法运行 | paddlepaddle 4GB+,塞进镜像不现实;代码已是 lazy import + 降级,启动不会爆 |
| **未装 Selenium** | "监管案例" 模块的 Selenium 抓取路径被禁用,走 BeautifulSoup+httpx 路径 | Chromium 二进制 HF Space 装不上 |
| **只外露 7860** | 容器内 FastAPI 的 `/docs` `/redoc` `/health` 无法从公网访问 | 调试可在本地 `docker run -p 8000:8000` 临时打开 |
| **睡眠机制** | Space 48 小时无访问会进入 sleep 状态,下次访问需 30s 冷启 | HF Space 免费档限制 |

> ✅ **持久化已启用**: 本 Space 已挂载 HF 持久化卷 (bucket `EtheoBlank-storage`) 到容器内 `/data`, `DATABASE_URL` / `UPLOAD_DIR` / `OUTPUT_DIR` / `TEMPLATE_DIR` / `KNOWLEDGE_BASE_DIR` / `SENTIMENT_OUTPUT_DIR` 都指向 `/data/*`,**项目数据 / 上传文件 / 知识库原书在容器重建后仍保留**。

### 自行部署到自己的 HF Space

适合要自定义、跑真实数据、或想长期 demo 的用户:

1. **fork 本仓库** 到你自己的 GitHub 账号
2. 在 [huggingface.co/new-space](https://huggingface.co/new-space) 创建 Space:
   - **Space SDK**: `Docker`
   - **Space hardware**: `CPU basic` (免费档足够,首次构建会下载 `paddlepaddle` 之外的纯 Python 依赖)
   - **Repository**: 选你 fork 后的仓库
3. 等首次构建完成(5~10 分钟,看 `uv sync` 速度)
4. 配置环境变量(可选,只有用 AI 模块才需要):
   - 进 Space 的 `Settings` → `Variables and secrets`
   - `DEEPSEEK_API_KEY` = 你的 DeepSeek Key
   - `MINIMAX_API_KEY` = 你的 MiniMax Key
   - `KB_EMBEDDING_PROVIDER` = `minimax` 或 `deepseek`(默认 `tfidf` 不需要 key)
5. 访问你的 Space URL,功能与官方 demo 一致

### 本地用 Docker 验证

```bash
# 1. 构建
docker build -t ipo-audit:test .

# 2. 启动(同时映射 7860 和 8000,后者用于本地看 API 文档)
docker run -p 7860:7860 -p 8000:8000 --name ipo-audit-test ipo-audit:test

# 3. 浏览器访问
#    Web UI:  http://localhost:7860
#    API 文档: http://localhost:8000/docs
#    健康检查: curl http://localhost:8000/health

# 4. 清理
docker stop ipo-audit-test && docker rm ipo-audit-test
```

### 部署相关文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 镜像构建 — uv 装依赖 + 复制源码 |
| `.dockerignore` | 排除 .venv / .git / 真实 .env / 测试代码 等 |
| `scripts/start_hf_space.sh` | 容器内入口 — 后台 uvicorn + 前台 streamlit |
| `.streamlit/config.toml` | Streamlit 配置 — headless / 7860 / 关 XSRF(嵌 iframe 需要) |
| `frontend/app.py` | `API_BASE_URL` 改成读 `os.environ.get(...)` |

---

## 🛠️ 技术栈

| 层 | 选型 | 为什么 |
|----|------|--------|
| 后端框架 | **FastAPI** | 异步、自动 OpenAPI 文档、类型提示 |
| 前端框架 | **Streamlit** | 0 前端代码,专注业务 |
| ORM | **SQLAlchemy 2.0**(`Mapped[]`)| 异步、类型安全 |
| 数据库 | **SQLite**(`aiosqlite`,开发)/ **PostgreSQL**(生产)| 零配置起步 |
| AI 主模型 | **DeepSeek** (`deepseek-chat`,JSON Mode)| 国产、价格低、JSON Mode 稳定 |
| AI 备选 | **MiniMax** | 风险分析 |
| OCR | **PaddleOCR**(主)/ **EasyOCR** / **Tesseract**(兜底)| 中文识别优 |
| 文档解析 | **python-docx** / **pdfplumber** / **ebooklib** / **pandas** | 主流格式全覆盖 |
| 报表生成 | **openpyxl** / **python-docx** / **reportlab** | Excel / Word / PDF |
| 调度 | **APScheduler** | 舆情定时扫描 |
| RSS | **feedparser** | 多源舆情聚合 |
| 重试 | **tenacity** | 抓取 / API 调用兜底 |
| 可视化 | **Altair** | 进度看板 |
| 包管理 | **uv** | 比 pip 快 10×、锁文件一致 |
| CI/CD | **GitHub Actions** | py 3.10/3.11/3.12 矩阵 + ruff + pytest |
| Lint | **ruff** | 比 flake8/black 快 100× |

---

## 🤖 AI 与 API Key 配置

| 用途 | 模型 | Key 变量 | 是否必需 |
|------|------|---------|---------|
| 销售清单合成 / 行业参考 / 合同五步法 / 库存 OCR 后解析 / 函证回函解析 / 工作计划生成 / 会议评分 / 管理建议 | `deepseek-chat` (JSON Mode) | `DEEPSEEK_API_KEY` | ★ 强烈建议 |
| 通用 AI 风险分析 | MiniMax | `MINIMAX_API_KEY` | 可选 |
| 知识库向量嵌入 | TF-IDF(默认 / 无依赖)/ MiniMax / DeepSeek | `KB_EMBEDDING_PROVIDER` | TF-IDF 不需 Key |
| 舆情付费源(可选) | Tavily / Bocha / SerpAPI | `TAVILY_API_KEY` 等 | 全部留空即仅用免费源 |

> ⚠️ **API Key 安全**
> * `.env` 已被 `.gitignore`,**绝不**提交到 Git
> * `.env.example` 仅作模板,所有密钥字段留空
> * 任何对外服务的费用由用户自行承担,本项目不做担保或推荐

---

## 🧪 测试 & 代码质量

```bash
# 全部测试
uv run pytest tests/ -v

# 按模块跑
uv run pytest tests/test_sales_ledger* -v
uv run pytest tests/test_inventory.py tests/smoke_inventory.py -v
uv run pytest tests/test_p0_regressions.py -v

# 覆盖率
uv run pytest --cov=app --cov-report=html tests/

# Lint + 格式化
uv run ruff check app/ frontend/ tests/
uv run ruff format app/ frontend/ tests/

# pre-commit(可选)
uv run pre-commit install
uv run pre-commit run --all-files
```

---

## 📚 完整文档

- 📖 **使用教程**(含截图位 + FAQ): [docs/usage.md](docs/usage.md)
- 📋 **数据格式规范**: [docs/DATA_FORMAT_SPEC.md](docs/DATA_FORMAT_SPEC.md)
- 🧾 **综合底稿模板规范**: [docs/COMPREHENSIVE_WORKPAPER_TEMPLATE_SPEC.md](docs/COMPREHENSIVE_WORKPAPER_TEMPLATE_SPEC.md)
- 🤖 **项目级 AI 协作指令**: [CLAUDE.md](CLAUDE.md)

---

## 🗺️ 路线图

### ✅ 已完成

- [x] **Phase 1**: 数据接入(科目余额表 / 序时账 / 银行对账单)
- [x] **Phase 2**: 底稿生成(5 种标准底稿)
- [x] **Phase 3**: 监管案例库
- [x] **Phase 4**: AI 风险分析(MiniMax)
- [x] **Phase 5**: 试算平衡(含合并报表抵销)
- [x] **Phase 6**: 综合报告(Word / PDF)
- [x] **Phase 7**: 销售清单整理(9 维度分析 + CAS 14 / IFRS 15 思路)
- [x] **Phase 8**: 收入合同分析(OCR + CAS 14 五步法 + 6 类风险扫描)
- [x] **Phase 9**: 法律法规库(5 大监管机构自动抓取)
- [x] **Phase 10**: 自助知识库(三 provider 向量检索 + RAG)
- [x] **Phase 11**: 项目组管理(AI 工作计划 + 会议评分 + 进度看板)
- [x] **Phase 12**: 收发存盘点 & 减值(金额优先 + 拍照 OCR 回填 + NRV)
- [x] **Phase 13**: 函证管理(财政部模板 + 回函 OCR + 差异统计)
- [x] **Phase 14**: 舆情跟踪(简报 + 季报 + 全局红点)
- [x] **Phase 15**: 综合底稿(事务所模板化全量生成)
- [x] **DevOps**: GitHub Actions 矩阵 CI + pre-commit + ruff

### 🔭 路线图

- [ ] **Phase 16**: 内控穿行测试模板化
- [ ] **Phase 17**: 跨期调整 / 合同资产 / 合同负债自动化
- [ ] **Phase 18**: 多用户 / 权限 / 审计轨迹(audit trail)
- [x] **Phase 19**: 容器化(Docker 一键起栈 + [HF Space 部署](https://huggingface.co/spaces/EtheoZheng/EtheoBlank))
- [ ] **Phase 20**: 报告模板自定义化(事务所品牌)

---

## 🤝 贡献

欢迎 PR! 建议方向:

- 增加新的 ERP 适配器(浪潮 / 速达 / 易飞 ...)
- 优化 AI prompt 模板(更高的合成准确率 / 更省 token)
- 增加新的审计程序(应付循环 / 费用循环 / 长投 / 在建工程 ...)
- 文档 / 翻译 / 截图补全
- 容器化 / K8s manifest

提交流程:

```bash
git checkout -b feat/your-feature
# ... 改代码 ...
uv run ruff check app/ frontend/
uv run pytest tests/ -v
git commit -m "feat(xxx): your change"
git push origin feat/your-feature
# 在 GitHub 上发起 PR
```

---

## 📜 许可证

[MIT](LICENSE) — 你可以**免费商用 / 修改 / 二次分发**,只需保留版权声明。

---

<div align="center">

### 如果这个项目帮到了你, 请给一个 ⭐
*这是对开源作者最大的鼓励*

**Made with ❤️ by auditors + AI**

[⬆ 回到顶部](#-ipo-audit-system)

</div>
