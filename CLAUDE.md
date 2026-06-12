# IPO 审计系统 - Claude Code 项目文档

## 项目概述

IPO 审计系统是一个专业的 IPO 审计底稿生成与数据分析工具，支持自动化生成审计底稿Excel、数据校验、AI辅助风险分析、销售清单整理和收入合同分析。

## 技术栈

- **后端**: FastAPI + SQLAlchemy 2.0 (Async)
- **前端**: Streamlit
- **数据库**: SQLite (开发, aiosqlite) / PostgreSQL (生产)
- **AI**: MiniMax API + DeepSeek API (JSON Mode)
- **Python**: 3.10+

## 项目结构

```
ipo-audit-system/
├── app/
│   ├── api/                   # API 路由
│   │   ├── projects.py        # 项目管理 + 数据导入
│   │   ├── workbooks.py       # 底稿生成
│   │   ├── regulatory_cases.py # 监管案例
│   │   ├── reports.py         # 综合报告/仪表盘/异常检测
│   │   ├── sales_ledger.py    # 销售清单整理
│   │   ├── contracts.py       # 收入合同分析
│   │   ├── regulations.py     # 法律法规库
│   │   ├── knowledge_base.py  # 自助知识库
│   │   ├── inventory.py       # 收发存盘点&减值&跌价转回
│   │   ├── confirmations.py   # 函证管理
│   │   └── team_management.py # 项目组管理（人员/计划/日报/会议/卡点/建议）
│   ├── core/                  # 核心配置
│   │   ├── config.py          # 应用配置 (pydantic-settings)
│   │   ├── database.py        # 异步数据库连接
│   │   └── logging.py         # 日志配置
│   ├── models/                # 数据模型
│   │   ├── db_models.py       # SQLAlchemy ORM 模型
│   │   ├── audit.py           # Pydantic schemas (底稿/试算平衡)
│   │   ├── sales_ledger.py    # 销售清单 Pydantic schemas
│   │   ├── contracts.py       # 合同 Pydantic schemas
│   │   └── team_management.py # 项目组管理 Pydantic schemas
│   ├── schemas/               # 额外 Pydantic schemas (保留)
│   ├── services/              # 业务逻辑
│   │   ├── excel_parser.py    # Excel 解析
│   │   ├── workbook_generator.py # 底稿生成
│   │   ├── trial_balance.py   # 试算平衡服务
│   │   ├── trial_balance_engine.py # 试算平衡引擎
│   │   ├── regulatory_scraper.py # 监管案例抓取
│   │   ├── regulation_scraper.py # 法规自动抓取 (CSRC/MOF/STA/SAFE/PBOC)
│   │   ├── audit_note_generator.py # 审计说明生成 (KB+法规+AI)
│   │   ├── ai_analysis.py     # MiniMax AI 分析
│   │   ├── ai_analysis_engine.py # 风险识别/异常检测引擎
│   │   ├── erp_adapters.py    # ERP 适配器 (金蝶/用友/SAP)
│   │   ├── report_generator.py # Word/PDF 报告生成
│   │   ├── knowledge_base/    # 自助知识库子包
│   │   │   ├── document_loader.py # PDF/EPUB/DOCX/TXT/MD 解析
│   │   │   ├── chunker.py         # 中文文本切块
│   │   │   ├── embedder.py        # TF-IDF / MiniMax / DeepSeek 嵌入
│   │   │   ├── retriever.py       # 语义+关键词混合检索
│   │   │   └── service.py         # 高层服务 (index/search/delete)
│   │   ├── sales_ledger/      # 销售清单子包
│   │   │   ├── deepseek_client.py # DeepSeek API 客户端
│   │   │   ├── document_parser.py # 文档解析
│   │   │   ├── synthesizer.py # AI 合成
│   │   │   ├── analyzer.py    # 收入分析
│   │   │   └── excel_exporter.py # Excel 导出
│   │   ├── contract_analysis/ # 合同分析子包
│   │   │   ├── analyzer.py    # CAS 14 五步法分析
│   │   │   └── ocr.py         # OCR 识别
│   │   ├── inventory/         # 收发存盘点&减值子包 (成本)
│   │       ├── importer.py        # 收发存 Excel 导入(金蝶/用友/SAP)
│   │       ├── count_sheet.py     # 盘点用表生成(金额优先+阈值覆盖)
│   │       ├── count_plan.py      # 行业化盘点计划 + AI 对话修改
│   │       ├── aging_engine.py    # FIFO 库龄 + NRV 跌价 + 跌价转回
│   │       ├── photo_processor.py # 盘点照片 OCR + AI 解析 + 回填
│   │       └── excel_exporter.py  # 多sheet 导出
│   │   ├── team_management/   # 项目组管理子包
│   │   │   ├── work_plan_generator.py       # AI 工作计划生成 (账套导入后自动触发)
│   │   │   ├── quality_assessor.py          # 会议纪要 AI 质量评分
│   │   │   ├── recommendation_generator.py  # AI 管理建议生成
│   │   │   ├── progress_tracker.py          # 进度聚合 (项目级 + 人员级)
│   │   │   └── service.py                   # 高层编排
│   ├── utils/                 # 工具函数
│   │   └── db_helpers.py      # ORM → DataFrame 转换
│   └── main.py                # FastAPI 应用入口
├── frontend/
│   ├── app.py                 # Streamlit 主界面
│   ├── pages_sales_ledger.py  # 销售清单页面
│   └── pages_contracts.py     # 合同分析页面
├── tests/                     # 测试
├── docs/                      # 文档
│   └── DATA_FORMAT_SPEC.md    # 数据格式规范
└── pyproject.toml             # 项目配置
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

### 代码检查
```bash
uv run ruff check app/ frontend/
uv run ruff format app/ frontend/
```

## 主要功能

1. **项目管理**: 创建和管理 IPO 审计项目
2. **数据导入**: Excel 格式的科目余额表、序时账、银行对账单导入（支持金蝶/用友/SAP 自动识别）
3. **底稿生成**: 自动生成标准化的审计底稿 Excel 文件（科目明细表/利润表/资产负债表/现金流量表/试算平衡表）
4. **试算平衡**: 验证资产负债表平衡和报表勾稽关系（含合并报表抵销）
5. **监管案例**: 抓取和检索证监会、交易所的监管案例
6. **AI 分析**: MiniMax API 识别风险点和生成审计建议
7. **销售清单整理**: 上传散乱文档 → DeepSeek AI 合成结构化销售清单 → 毛利率/截止性/单价波动分析
8. **收入合同分析**: OCR 识别 + CAS 14 五步法分析 + 风险扫描
9. **法律法规库 (Regulations)**: 自动抓取证监会 / 财政部 / 国家税务总局 / 外管局 /
   人民银行的政策、准则、规章、问答口径；多维过滤 + 全文搜索 + 按项目收藏
10. **自助知识库 (Knowledge Base)**: 上传实务书籍 (PDF/EPUB/DOCX/TXT/MD) → 切块 +
    向量化 (TF-IDF / MiniMax / DeepSeek 三种 provider) → 生成审计说明时按
    科目 / 风险点自动检索相似案例
11. **项目组管理 (Team Management)**: 5 级人员库 + AI 工作计划 + 会议评分 +
    日报 + 卡点 + 进度看板 + AI 管理建议
12. **收发存盘点 & 减值**: 金额优先 + 阈值覆盖 + 拍照 OCR 回填 + FIFO 库龄 +
    NRV 跌价 + 跌价转回
13. **函证管理**: 财政部模板 + 锁定金额快照 + 回函 OCR + AI 解析 + 差异统计
14. **舆情跟踪**: 多源抓取 + AI 去重校验 + 简报 / 季报 + 全局红点
15. **综合底稿**: 事务所模板上传 + 字段映射 + AI 填充 + QA 引擎 + 规则引擎
16. **多用户 / 权限 / 审计轨迹 (Pack A — Phase 18)**: JWT 认证 + 5 级签字流
    (assistant→manager→partner→qc_partner→signing_partner) + RBAC 角色权限
    + 全量 AuditLog (所有写操作不可篡改记录) + 通用通知中心 + 后台事件机制
17. **长期资产发生额审定 (Pack A — 用户特别要求)**: 固定资产/在建工程/无形资产/
    长投/商誉/使用权资产/递延所得税资产等长期资产科目, 本期借/贷方发生额逐笔
    出审定数 + 审计调整, 底稿自动恒等式校验 (期初+借-贷=期末)
18. **报告模板自定义化 (Pack A — Phase 20)**: 事务所上传 Word/Excel 模板,
    ``${placeholder}`` 占位符 + context 注入渲染, 支持嵌套字段 ``${a.b.c}``

## Pack A 新模块文件结构

```
app/
├── models/db/                    # 模块化 ORM (新模块统一放这里)
│   ├── __init__.py
│   ├── auth.py                   # User/Firm/Role/Permission/ApprovalWorkflow/AuditLog
│   ├── notification.py           # 通用通知
│   ├── account_audit.py          # AccountMovementAudit + 长期资产前缀清单
│   └── report_template.py        # ReportTemplate / ReportRenderHistory
├── models/
│   ├── auth.py                   # Pydantic schemas
│   ├── notification.py
│   ├── account_audit.py
│   └── report_template.py
├── services/
│   ├── auth/                     # JWT + password + RBAC + approval + bootstrap
│   ├── notification/             # push / mark_read / unread_count
│   ├── background/               # 事件分发
│   ├── account_audit/            # 长期资产审定服务
│   └── report_template/          # docxtpl 风格 ${placeholder} 渲染
├── api/
│   ├── auth.py                   # /api/auth/login, /users, /audit-logs, /approvals 等
│   ├── notifications.py          # /api/notifications/unread, /list, /mark-read
│   ├── account_audit.py          # /api/account-audit/projects/{pid}/...
│   └── report_templates.py       # /api/report-templates/...
└── main.py                       # 加 AuditLogMiddleware + 4 个新 router + bootstrap_auth

frontend/
├── pages_auth.py                 # 登录 + 用户/事务所/角色权限/审计轨迹/审批流
├── pages_notification.py         # 通知中心
├── pages_account_audit.py        # 长期资产发生额审定 (st.data_editor)
└── pages_report_templates.py     # 报告模板上传 / 预览 / 渲染
```

## 环境变量

复制 `.env.example` 为 `.env` 并配置以下变量：

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | 数据库连接字符串 |
| `MINIMAX_API_KEY` | MiniMax API 密钥 (AI风险分析) |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 (销售清单/合同分析) |
| `CORS_ORIGINS` | CORS 允许的前端来源 (逗号分隔) |
| `KB_EMBEDDING_PROVIDER` | 知识库嵌入 provider: `tfidf` / `minimax` / `deepseek` (默认 tfidf) |
| `KB_CHUNK_SIZE` / `KB_CHUNK_OVERLAP` | 知识库切块字符数 / 重叠 |
| `KNOWLEDGE_BASE_DIR` | 知识库书籍原文件目录 (默认 `./uploads/knowledge_base`) |
| `MOF_URL` / `STA_URL` / `SAFE_URL` / `PBOC_URL` | 法规来源根 URL (一般不需要改) |
| `REGULATION_MAX_PAGES` | 单栏目抓取最大页数 (默认 5) |
| **Pack A** | |
| `AUTH_ENABLED` | JWT 认证开关; `false`=兼容现网无认证 (默认), `true`=启用 |
| `JWT_SECRET` | JWT 签名密钥; 生产必须改成 >=32 字节随机串 |
| `JWT_ACCESS_EXPIRE_MINUTES` | Access token 过期分钟 (默认 60) |
| `JWT_REFRESH_EXPIRE_DAYS` | Refresh token 过期天数 (默认 7) |
| `BCRYPT_ROUNDS` | bcrypt 哈希轮数 (默认 12) |
| `AUTH_MAX_FAILED_LOGIN` | 失败次数自动锁定 (默认 10, 0=不锁定) |
| `AUTH_BOOTSTRAP_ADMIN_USERNAME/PASSWORD/FULL_NAME` | 首次启动自动创建的管理员账号 |
| `AUTH_BOOTSTRAP_FIRM_NAME` | 默认事务所名称 |
| `AUDIT_LOG_WRITE_ONLY` | true=只记 POST/PUT/DELETE; false=记所有请求 |
| `AUDIT_LOG_PAYLOAD_MAX_CHARS` | payload 截断长度 (默认 4000, 0=不存) |
| `AUDIT_LOG_EXCLUDE_PATHS` | 不落库的 path 前缀 (逗号分隔) |
| `LONG_TERM_ASSET_EXTRA_INCLUDES/EXCLUDES` | 长期资产科目前缀全局额外加/减 |
| `REPORT_TEMPLATE_DIR` / `REPORT_OUTPUT_DIR` | 报告模板存目录 + 渲染输出目录 |
| `REPORT_TEMPLATE_MAX_SIZE` | 模板上传大小上限 (默认 20MB) |
| `REPORT_TEMPLATE_ALLOWED_EXTS` | 模板允许扩展 (默认 `.docx,.xlsx,.dotx,.xltx`) |

## Pack A 默认登录

首次启动 (DB 没用户时) 自动创建:
  - 用户名: `admin`
  - 密码: `Admin@1234`
  - 角色: `admin` (拥有所有权限)

⚠️ **生产部署必须立即登录后修改密码**, 并且把 `JWT_SECRET` 改成强随机串。

## 开发注意事项

- 使用 `uv` 作为包管理器
- 所有数据库操作使用异步模式 (AsyncSession)
- API 遵循 RESTful 规范
- 前端使用 Streamlit 构建
- ORM → DataFrame 转换统一使用 `app.utils.db_helpers.account_balances_to_df()`
- 日志使用 `logging.getLogger(__name__)`，启动时由 `app.core.logging.setup_logging()` 统一配置
- 日期时间使用 `datetime.now(timezone.utc)` 替代已弃用的 `datetime.utcnow()`
