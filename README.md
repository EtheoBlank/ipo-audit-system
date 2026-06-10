<div align="center">

# 🏛️ IPO Audit System

**专业的 IPO 审计底稿生成与数据分析工具集** — 把 AI 直接搬上审计师的桌面，让"找底稿、做分析、出报告"从小时级压缩到分钟级。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![DeepSeek](https://img.shields.io/badge/AI-DeepSeek-4D6BFE?logo=openai&logoColor=white)](https://platform.deepseek.com/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/)

> "上传散乱文档 → AI 自动合成底稿 → 收入循环分析 → 一键导出 Excel"
> 让 AI 干 AI 擅长的事，让审计师做审计师擅长的事。

</div>

---

## ✨ 项目亮点

| 模块 | 你能做什么 |
|------|-----------|
| 📦 **销售清单整理** | 上传合同/发票/发货单/报关单 → DeepSeek 抽取成结构化销售清单 → 客户×产品×月度三维毛利率 + 截止性 + 单价波动 + 函证覆盖率 + DSO + 退折返影响 + 收发存对账 + 同行业 AI 参考 |
| 📄 **收入合同分析** | 上传合同图片 / 扫描件 → OCR → **CAS 14 五步法** + 7 字段要点提取 + 风险扫描（回购条款、寄售、重大融资成分…） |
| 🤖 **AI 风险分析** | 基于监管案例库 + 风险模型识别潜在问题 |
| 📊 **底稿生成** | 一键生成科目明细表、利润表、资产负债表、现金流量表、试算平衡表 |
| ⚖️ **试算平衡** | 资产负债表平衡、报表勾稽、银行对账 |
| 🔍 **监管案例库** | 抓取证监会 / 交易所问询函、处罚决定 |
| 📋 **异常检测** | 多维度异常扫描 |
| 📄 **综合报告** | Word / PDF 一键导出 |

---

## 🖼️ 架构一览

```
┌──────────────────────────────────────────────────────────┐
│                    Streamlit  前端                        │
│   项目管理  销售清单  合同分析  AI 风险  底稿  报告     │
└──────────────────────────────────────────────────────────┘
                          │  HTTP (REST)
                          ▼
┌──────────────────────────────────────────────────────────┐
│                    FastAPI  后端                          │
│   /api/projects  /api/sales-ledger  /api/contracts      │
│   /api/workbooks /api/reports     /api/regulatory-cases │
└──────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼────────────────────┐
        ▼                 ▼                    ▼
┌──────────────┐  ┌──────────────────┐  ┌────────────────┐
│  AI 服务层    │  │   业务引擎        │  │   数据层       │
│ DeepSeek    │  │ ERP 适配器        │  │ SQLAlchemy     │
│ JSON Mode   │  │ 试算平衡引擎      │  │ SQLite / PG    │
│ 流式响应     │  │ 底稿生成器        │  │ 文件存储       │
│             │  │ 收入循环分析器    │  │ uploads/       │
│             │  │ 合同五步法分析器  │  │ outputs/       │
└──────────────┘  └──────────────────┘  └────────────────┘
```

---

## 🚀 30 秒快速开始

```bash
# 1. 克隆
git clone https://github.com/<your-name>/ipo-audit-system.git
cd ipo-audit-system

# 2. 装依赖（推荐使用 uv，比 pip 快 10×）
uv sync

# 3. 配置 API Key —— 注意 .env 不会被 Git 追踪
cp .env.example .env
# 用编辑器打开 .env，至少填入：
#   DEEPSEEK_API_KEY=your_deepseek_api_key_here
#   MINIMAX_API_KEY=your_minimax_key  (可选，AI 风险分析用)

# 4. 启动后端 (端口 8000)
uv run uvicorn app.main:app --reload --port 8000

# 5. 另开一个终端，启动前端 (端口 8501)
uv run streamlit run frontend/app.py
```

打开浏览器：
- 🌐 Web 界面：http://localhost:8501
- 📚 API 文档：http://localhost:8000/docs

---

## 📦 销售清单整理（核心模块）

> 收入循环审计最缺的那张表 —— 审计师在 IPO 底稿中除了报表与序时账，最常用的一张**贯穿收入循环的明细台账**。

### 它是什么

一份完整的销售清单需要包括：
- 收入金额（不含税 / 含税 / 价税合计 / 税率 / 税额）
- 具体发货时间、收入确认时间、签收/验收时间
- 销售数量、销售单价、销售产品编号（能与收发存勾稽）
- 成本金额（毛利率分析）
- 与销售直接对应的费用：运费 / 报关费 / 其他直接费用
- 退换货金额、折扣折让、销售返利（毛利真实性）
- 函证状态 / 编号 / 差异（审计轨迹闭环）

实务中，被审计单位往往只能给合同、发票、发货单、报关单等**散乱文档**。本模块做的就是：

1. **上传** Word / PDF / Excel / 扫描件
2. **AI 合成** —— DeepSeek 把散乱文档抽取为结构化销售清单
3. **核对修改** —— 前端表格让人工复核（支持字段级修改 + 已核对标记）
4. **收入分析** —— 9 个维度一键产出
5. **导出** —— 多 Sheet Excel 工作簿

### 9 个分析维度

| 维度 | 实务意义 |
|------|---------|
| 总览 KPI | 记录数、总收入、总成本、毛利率 |
| 客户 / 产品 / 月度 毛利率透视 | 找出赚钱的和亏钱的 |
| 客户 × 产品 × 月度 三维交叉 | 透视表定位异常 |
| ✉️ 函证覆盖率 | 按客户计算，辅助函证样本设计（目标 > 80%） |
| ⏱️ DSO 分客户 | 应收账款周转天数，识别回款风险 |
| ↩️ 退折返对毛利影响 | 真实毛利率 vs 报表毛利率 |
| 🕒 收入确认时点差异 | 发货→签收链条跨期风险 |
| ⚠️ 截止性测试 | 年末 ± N 天跨期调整识别 |
| 📈 单价波动 | 同一产品+客户单价波动超阈值报警 |
| 🏭 同行业 AI 参考 | DeepSeek 根据行业给出区间（**仅参考**） |

### API 速查

```bash
# 上传 + 解析
POST /api/sales-ledger/projects/{id}/sales-documents

# AI 合成
POST /api/sales-ledger/projects/{id}/sales-records/synthesize

# 查询 / 修改
GET   /api/sales-ledger/projects/{id}/sales-records
PUT   /api/sales-ledger/sales-records/{rid}

# 收入分析
POST /api/sales-ledger/projects/{id}/revenue-analysis

# 导出 Excel
GET   /api/sales-ledger/projects/{id}/export
```

---

## 📄 收入合同分析

> 专为审计师按 **CAS 14（企业会计准则第 14 号——收入）** 五步法分析合同而设计。

### 流程

```
📷 上传合同图片/扫描PDF   →   OCR（paddleocr / easyocr / tesseract）
                                ↓
                          📝 纯文本（也支持直接粘贴）
                                ↓
                  DeepSeek 分析：
                    ├── 7 字段要点提取
                    └── CAS 14 五步法结构化输出
                                ↓
                  🔍 本地风险扫描
                    ├── 回购条款
                    ├── 寄售/代销
                    ├── 重大融资成分
                    ├── 可变对价
                    ├── 超长账期
                    └── 补充协议 / Side Letter
```

### CAS 14 五步法

| 步骤 | 内容 |
|------|------|
| ① 合同识别 | 是否存在商业实质、是否已审批、生效 / 到期日 |
| ② 合同变更 | 补充协议、变更条款、是否构成新合同 / 原合同组成部分 |
| ③ 履约义务分拆 | 各项 PO + 时点/时段 + 确认依据 |
| ④ 交易价格 | 固定金额、可变对价、重大融资成分、非现金对价 |
| ⑤ 收入确认 | 时点/时段 + 方法（产出法/投入法/客户验收）+ 所需证据 |
| ⑥ 审计关注 | AI 给出的关键风险点提示 |

### API 速查

```bash
# 上传图片/PDF (自动 OCR)
POST /api/contracts/projects/{id}/contracts

# 直接传文本
POST /api/contracts/projects/{id}/contracts/text

# 跑五步法 + 要点提取
POST /api/contracts/contracts/{cid}/analyze

# 列表 / 详情
GET  /api/contracts/projects/{id}/contracts
GET  /api/contracts/contracts/{cid}
```

---

## 🛠️ 技术栈

| 层 | 选型 | 为什么 |
|----|------|--------|
| 后端框架 | **FastAPI** | 异步、自动 OpenAPI 文档、类型提示 |
| 前端框架 | **Streamlit** | 0 前端代码，专注业务 |
| ORM | **SQLAlchemy 2.0** | 异步、Mapped[] 类型安全 |
| 数据库 | **SQLite** (开发) / **PostgreSQL** (生产) | 零配置起步 |
| AI | **DeepSeek** (`deepseek-chat`) | 国产、价格低、JSON Mode 稳定 |
| OCR | **PaddleOCR** (主) / **EasyOCR** / **Tesseract** | 中文识别优 |
| 文档解析 | **python-docx** / **pdfplumber** / **pandas** | 主流格式全覆盖 |
| 报表生成 | **openpyxl** / **python-docx** / **reportlab** | Excel/Word/PDF |
| 包管理 | **uv** | 比 pip 快 10×，锁文件一致 |

---

## 🗂️ 项目结构

```
ipo-audit-system/
├── app/
│   ├── api/                          # FastAPI 路由
│   │   ├── projects.py               # 项目管理
│   │   ├── workbooks.py              # 底稿生成
│   │   ├── regulatory_cases.py       # 监管案例
│   │   ├── reports.py                # 综合报告
│   │   ├── sales_ledger.py           # 销售清单整理
│   │   └── contracts.py              # 收入合同分析
│   ├── core/                         # 核心配置
│   │   ├── config.py                 # pydantic-settings 配置
│   │   └── database.py               # 异步 SQLAlchemy
│   ├── models/                       # 数据模型
│   │   ├── db_models.py              # ORM 模型
│   │   ├── audit.py                  # Pydantic schemas
│   │   ├── sales_ledger.py           # 销售清单 schemas
│   │   └── contracts.py              # 合同 schemas
│   ├── services/                     # 业务服务
│   │   ├── erp_adapters.py           # 金蝶/用友/SAP 适配
│   │   ├── excel_parser.py           # Excel 解析
│   │   ├── workbook_generator.py     # 底稿 Excel 生成
│   │   ├── trial_balance.py          # 试算平衡
│   │   ├── regulatory_scraper.py     # 监管案例抓取
│   │   ├── ai_analysis.py            # AI 风险分析
│   │   ├── report_generator.py       # 综合报告
│   │   ├── sales_ledger/             # 销售清单子模块
│   │   │   ├── deepseek_client.py
│   │   │   ├── document_parser.py
│   │   │   ├── synthesizer.py
│   │   │   ├── analyzer.py
│   │   │   └── excel_exporter.py
│   │   └── contract_analysis/        # 合同分析子模块
│   │       ├── ocr.py
│   │       └── analyzer.py
│   └── main.py                       # FastAPI 入口
├── frontend/
│   ├── app.py                        # Streamlit 主入口 + 导航
│   ├── pages_sales_ledger.py         # 销售清单页面
│   └── pages_contracts.py            # 合同分析页面
├── tests/                            # pytest 测试
├── docs/                             # 文档
│   └── DATA_FORMAT_SPEC.md
├── uploads/                          # 用户上传文件 (gitignore)
├── outputs/                          # 生成的底稿 (gitignore)
├── pyproject.toml                    # 依赖声明
├── .env.example                      # 环境变量模板 (无 key)
├── .gitignore                        # 忽略 .env、数据库、上传文件
└── README.md                         # 你正在读的这份
```

---

## 🤖 AI 配置

本项目使用 **DeepSeek** 作为主力 AI（支持 JSON Mode，价格亲民）。

| 用途 | 模型 | Key 变量 |
|------|------|---------|
| 销售清单合成、行业参考、合同五步法 | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| 通用 AI 风险分析 | MiniMax | `MINIMAX_API_KEY` |

> ⚠️ **API Key 安全**
> - API Key **必须**放在 `.env` 中，**绝不**提交到 Git
> - 仓库的 `.gitignore` 已忽略 `.env`
> - `.env.example` 仅为模板，**所有密钥字段留空**

---

## 🧪 测试

```bash
uv run pytest tests/ -v
```

---

## 🗺️ 路线图

- [x] 第一阶段：数据接入（科目余额表 / 序时账 / 银行对账单）
- [x] 第二阶段：底稿生成（5 种标准底稿）
- [x] 第三阶段：监管案例库
- [x] 第四阶段：AI 风险分析
- [x] 第五阶段：试算平衡
- [x] 第六阶段：综合报告
- [x] 第七阶段：销售清单整理（含 CAS 14 / IFRS 15 思路）
- [x] 第八阶段：收入合同分析（OCR + CAS 14 五步法 + 风险扫描）
- [ ] 第九阶段：函证自动化（生成询证函 + 跟踪回函）
- [ ] 第十阶段：内控穿行测试模板化
- [ ] 第十一阶段：跨期调整 / 合同资产 / 合同负债

---

## 🤝 贡献

欢迎 PR！建议方向：
- 增加新的 ERP 适配器
- 优化 AI prompt 模板
- 增加新的审计程序
- 改进文档/翻译

---

## 📜 许可证

[MIT](LICENSE)

---

<div align="center">

**如果这个项目帮到了你，请给一个 ⭐ — 这是对开源作者最大的鼓励**

Made with ❤️ by auditors + AI

</div>
