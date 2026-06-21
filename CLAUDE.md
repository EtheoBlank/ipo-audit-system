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

## Pack A.2 / B.2 — 本轮增强 (路线图全部完工 + 41 个新增单测覆盖)

| 增强 | 实现位置 | 单测 | 说明 |
|------|----------|------|------|
| **老业务 API 全量加鉴权** | `app/api/*.py` 13 个老路由 | `test_pack_a2_b2.py::TestLegacyApisImport` | 全部接入 `get_current_user` / `get_current_user_optional`; AUTH_ENABLED=false 兼容老调用 |
| **多租户硬隔离** | `app/services/auth/tenant.py` + `Project.firm_id` | `test_pack_a2_b2.py::TestTenantIsolation` | `scope_projects_to_firm(query, user)` / `ensure_project_in_firm(db, pid, user)`; admin 跨租户 |
| **审批乐观锁** | `app/services/auth/approval.py` + `ApprovalWorkflow.version` | `test_pack_a2_b2.py::TestApprovalOptimisticLock` | `decide(expected_version=N)` 不匹配抛 `ApprovalConflict` → HTTP 409 |
| **审计轨迹索引 + 归档** | `app/models/db/auth.py` + `app/services/auth/archive.py` | `test_pack_a2_b2.py::TestAuditLogArchive` | 4 个 (维度, created_at) 复合索引 + `rotate_audit_logs(months=N, confirm=True)` 影子表归档 |
| **DeepSeek 关联方推断** | `app/services/related_parties/ai_inferer.py` | `test_pack_a2_b2.py::TestRelatedPartyAIInferer` | `RelatedPartyAIInferer` 类, DetectorRunRequest.enable_ai_inference=true 启用; 失败自动降级到规则识别 |
| **Word 富格式渲染** | `app/services/report_template/__init__.py` (`_render_docx_xml_blob`) | `test_pack_a2_b2.py::TestReportTemplateRunAware` | XML 段落级 run-aware 替换, 保留字体/字号/加粗/颜色/下划线 |

调用模式速查:

```python
# 多租户硬隔离 — 列表查询
from app.services.auth import scope_projects_to_firm
query = select(Project)
query = scope_projects_to_firm(query, current_user)

# 多租户硬隔离 — 单项目访问 (403 / 404)
from app.services.auth import ensure_project_in_firm
proj = await ensure_project_in_firm(db, project_id, current_user)

# 乐观锁审批 (前端必须先 GET 拿到 version 才能 decide)
payload = ApprovalDecision(action="approve", expected_version=wf.version)
POST /api/auth/approvals/{id}/decide  # 409 时刷新重试

# AuditLog 归档 (admin only)
POST /api/auth/audit-logs/rotate?months=6&confirm=true

# DeepSeek 关联方推断
POST /api/related-parties/detector/run
{ "project_id": 1, "enable_ai_inference": true, "ai_max_candidates": 30 }
```

## 开发注意事项

- 使用 `uv` 作为包管理器
- 所有数据库操作使用异步模式 (AsyncSession)
- API 遵循 RESTful 规范
- 前端使用 Streamlit 构建
- ORM → DataFrame 转换统一使用 `app.utils.db_helpers.account_balances_to_df()`
- 日志使用 `logging.getLogger(__name__)`，启动时由 `app.core.logging.setup_logging()` 统一配置
- 日期时间使用 `datetime.now(timezone.utc)` 替代已弃用的 `datetime.utcnow()`

## 多端同步流程 (GitHub ↔ Hugging Face Space ↔ Vercel)

仓库有三个部署目标，**主推送走 `scripts/sync.sh`**，GitHub → Vercel 是自动的：

| 目标 | 仓库 / URL | 分支策略 | 写入方式 | 谁触发 |
|------|------------|---------|---------|--------|
| **GitHub** (`origin`) | https://github.com/EtheoBlank/ipo-audit-system | master 为稳定主线，feature 分支走 PR | 本地 `sync.sh push-github` | 你 push |
| **Vercel** (无 git remote) | **https://ipo-audit-system-lovat.vercel.app** (FastAPI 后端 `/docs` Swagger + `/api/*`) | 监听 GitHub `master` 自动 build | **Vercel GitHub Integration 自动** | `push origin master` → Vercel 1 分钟内 build+deploy |
| **HF Space** (`hf`) | https://etheozheng-etheoblank.hf.space (Streamlit Web UI) | `main` 是 Space 部署分支（推送即公开 rebuild） | 本地 `sync.sh push-hf` 或 Action `Sync to HF Space` | 你 push 或手动 Action |

> ✅ **日常开发只需要 `git push origin master`**: Vercel 自动 build；要不要顺手同步 HF 由你决定 (HF 仍承载 Streamlit Web UI)。

### 推荐工作流（PR 合到 master 之后）

```bash
# 1. 在 feature 分支开发, 推到 origin 走 PR
git checkout -b feat/xxx
git commit ...
bash scripts/sync.sh push-github          # 等价于 git push origin feat/xxx

# 2. GitHub 网页开 PR, CI 全绿后合并到 master

# 3. 本地同步 master
git checkout master && git pull

# 4. (可选) 同步 HF Space — Streamlit UI 走这里
bash scripts/sync.sh status               # 看 origin/master vs hf/main 谁领先
bash scripts/sync.sh push-hf             # 默认 fast-forward, 安全
# 如果 status 显示 ❌ 分叉 (origin/master 与 hf/main 无共同祖先):
bash scripts/sync.sh push-hf --force-with-lease

# 5. Vercel 自动接管 — 不需要任何操作
#    推 master 30s 后, Vercel dashboard 应出现新 deployment
#    5-10s 后 https://<your-project>.vercel.app/docs 可访问
```

### Vercel 部署的 GitHub 集成 (自动)

**无需 Action**: Vercel ↔ GitHub Integration 是双向绑定的, 在 Vercel Dashboard "Import Project" 时一次性勾选 "GitHub" 即可. 之后:

- `git push origin master` → Vercel 自动 build → 1-3 分钟后 production deployment 完成
- `git push origin feat/xxx` → Vercel 自动生成 Preview Deployment (独立 URL, 不影响生产)
- PR → 评论区自动出现 Vercel bot 链接 (Preview URL + commit 信息)

**回滚**: Vercel Dashboard → Deployments → 选上一个 commit → "Promote to Production" (秒级, 不重 build).

### 兜底方案: 手动 vercel deploy (Vercel Integration 失效时)

```bash
# 一次性登录
vercel login                    # 浏览器走 OAuth, token 存 ~/.vercel

# 一次性 link 项目 (idempotent)
vercel link --yes               # 把当前目录 link 到 Vercel project

# 手动部署
vercel deploy --prod            # 等价于 Vercel GitHub Integration 的 production build
vercel deploy                   # 默认 preview deployment (非 master 分支)
```

或者用 GitHub Action `.github/workflows/deploy-vercel.yml` 作为兜底 — 在 Vercel Integration 失效时 (例如 GitHub App 权限被 revoke) 仍能 deploy.

### 不想本地推 HF？用 GitHub Action

1. 一次性配置: GitHub 仓库 → Settings → Secrets and variables → Actions → **New repository secret**
   - Name: `HF_TOKEN`
   - Value: 去 https://huggingface.co/settings/tokens 生成（建议 **fine-grained**，只勾 `EtheoZheng/EtheoBlank` Space 的 write 权限）
2. 日常同步: 仓库页 → Actions → **Sync to HF Space** → Run workflow → **勾上 `confirm_rebuild`** → Run
3. Action 会自动跑：安全检查 → `uv sync` → smoke import → 比对 remote → 推 hf/main → 输出 Space URL

### 安全护栏（三个强制约束）

1. **绝不提交 `.env`**: `sync.sh` / `deploy-vercel.yml` 都会扫描 `.env` 文件 + `sk-` 字面量；CI 也会跑同样的检查
2. **HF Token / Vercel Token 永远在 GitHub Secret**: 不要贴在 issue / commit message / 文档里
3. **HF Space 公开 rebuild 必须人工确认**: workflow 的 `confirm_rebuild` input 是 last-line 防御，别取消
4. **Vercel 环境变量**: `DATABASE_URL` / `BLOB_READ_WRITE_TOKEN` / `JWT_SECRET` 必须在 Vercel Dashboard 配, 永远不要 commit 到仓库

### 一次性初始化（老 `git_push.sh` 的用途）

`scripts/git_push.sh` 是 **首次把仓库同步给 GitHub** 的批量脚本（分多个 commit 上传大段初始代码），现在仓库已有完整历史，**新工作用 `sync.sh`**。`git_push.sh` 保留仅作历史参考，不应再执行。

### HF Space 一旦出错怎么回滚

```bash
# 找到上一个能跑的 commit
git log --oneline hf/main | head -10

# 强推回滚
git push hf <previous-commit-sha>:main --force-with-lease
```

或用 GitHub Action 的 `force_with_lease` 选项重推旧 commit。

### Vercel 一旦出错怎么回滚

```bash
# Vercel Dashboard → Deployments → 找上一个能跑的 commit → Promote to Production
# (秒级回滚, 不需要重新 build)
```

或 CLI: `vercel rollback` (回滚到上一个 production deployment).
