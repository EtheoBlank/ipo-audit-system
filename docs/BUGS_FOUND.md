# Bug 审查报告 (2026-06-16)

8 个 agent 并行审查全 216 个源文件,共发现 **~600 个 issue**。
本轮修完约 **60 个 P0** + **少量 P1**,本文件记录剩余 issue,供下轮继续。

## 已修 (60+ P0)

### 安全 (P0)
- `app/services/auth/rbac.py`: RBAC fail-open → fail-closed
- `app/services/auth/jwt.py`: 强制算法白名单 + iss 必填
- `app/services/auth/archive.py`: SQL f-string → 参数化 binding
- `app/services/auth/service.py`: refresh token 撤销检查 + change_password 清零失败计数
- `app/services/auth/password.py`: PBKDF2 迭代 200k → 600k (OWASP 2023)
- `app/services/auth/audit_log.py`: 敏感字段自动脱敏 (password/token/api_key 等 ~20 字段)
- `app/main.py`: CORS allow_methods=['*'] → 显式白名单
- `app/services/excel_parser.py` + `app/services/sales_ledger/document_parser.py`: 文件名 sanitize + is_relative_to 校验
- `app/services/contract_analysis/ocr.py` + `app/services/inventory/photo_processor.py`: file_path 必须在内 upload_dir
- `app/services/sentiment/sources/announce_adapter.py`: cninfo HTTP → HTTPS
- `app/services/sentiment/sources/paid_adapters.py`: Tavily/SerpAPI api_key → Bearer header (不再走 body/URL)
- `frontend/pages_confirmations.py` + `pages_inventory.py` + `pages_knowledge_base.py` + `pages_regulations.py`: 自写 _api → 共享 _http.api_request (带 auth)
- `frontend/_components/safe_render.py` (新增) + 7 个页面: st.markdown user-controlled text → safe_inline_text / safe_link / safe_url

### 正确性 (P0/P1)
- `app/services/audit_cycles/__init__.py` + `app/services/team_management/progress_tracker.py`: datetime.utcnow → datetime.now(timezone.utc) (Python 3.12 弃用)
- `app/services/ai_analysis.py` + `ai_analysis_engine.py`: 收入计算改 credit_amount + 5001/5002/5051/5301 白名单 (之前把所有 5xxx 当收入)
- `app/services/sales_ledger/synthesizer.py`: _parse_float 不再用 re.sub (破坏科学计数)
- `app/services/knowledge_base/service.py`: delete_book 路径校验 str.startswith → Path.is_relative_to (前缀穿透防护)
- `app/services/comprehensive/builtin_rules.py`: ar_risk_low_turnover 改为 between [0, 90]
- `app/services/sentiment/llm_client.py`: API key 长度阈值 16 → 32
- `app/services/team_management/quality_assessor.py` + `recommendation_generator.py`: user prompt 注入防御 + 截断
- `app/services/contract_analysis/analyzer.py` + `app/services/inventory/photo_processor.py`: AI system prompt 加防 prompt injection
- `app/services/workbook_generator.py`:
  - income_statement 不再丢弃过滤结果 (之前利润表全 0)
  - balance_sheet 按 1/2/3 前缀拆分资产/负债/权益写数据 (之前空表)
  - _row ratio_base 改用 audited (之前占比全 '-')

### 前端 (P0)
- `.streamlit/config.toml`: maxUploadSize = 50 (防上传大文件 OOM)
- `frontend/_components/project_picker.py` + `frontend/app.py`: 去掉 4 个 @st.cache_data (跨用户缓存泄漏)
- `frontend/pages_sentiment.py` + `pages_sales_ledger.py` + `pages_contracts.py`: session_state 加 project_id/contract_id 前缀 (跨项目污染)
- `frontend/pages_inventory.py` + `pages_knowledge_base.py`: 删除/清空操作加二次确认 (P0 反馈: 不可逆操作走手动确认)
- `frontend/pages_account_audit.py`: period_end 校验改为严格 YYYY-MM-DD 正则

### 测试
- `tests/test_auth.py`: 适配 iss 必填
- `tests/test_sentiment.py`: 适配 _is_real_key 长度阈值

---

## 未修 - 剩余 P0 (~60 个)

### 业务逻辑错误
- `app/services/team_management/__init__.py`: import 失败时整包不可用
- `app/services/related_parties/__init__.py`: 同上
- ~~`app/services/audit_cycles/__init__.py` line 432: 月度递推 off-by-one (i=1 时 month 跳到上一年 12 月)~~ ✅ 2026-06-17 修复
- ~~`app/services/audit_cycles/__init__.py` line 219: double_declining 月折旧率计算公式错~~ ❌ 2026-06-17 经核实公式正确 (NBV*0.2/12 = NBV*2/life_months), 锁定为 regression guard
- ~~`app/services/audit_cycles/__init__.py` line 181: PayrollReconciler discrepancy 公式用 50% 经验值永远 True~~ ✅ 2026-06-17 修复
- ~~`app/services/confirmation/stats_builder.py` line 426: ending_balance 用本期发生额近似, 严重漏函证 (多年挂账函证为 0)~~ ✅ 2026-06-17 修复 — 改用 balance_by_code 按本期发生比例分摊到对方级, 无活动 → "(未指定对方)" 桶
- ~~`app/services/confirmation/response_processor.py` line 130: AI 输出 response_status 无 enum 校验, 任意字符串入库~~ ✅ 2026-06-17 修复 — 加白名单 {match/partial/mismatch/reject/unclear} + _sanitize_response_status 静态方法
- ~~`app/services/sales_ledger/analyzer.py` line 280: _summary profit 没扣 return/discount/rebate, 毛利率被高估~~ ✅ 2026-06-17 修复
- ~~`app/services/workbook_generator.py` line 281: cash_flow 仍是空表~~ ✅ 2026-06-17 修复 — 完整重写, 加 account_balances 参数, 经营/投资/筹资三段 + 期末现金 + 校验
- ~~`app/services/regulatory_scraper.py` line 44: CSRC URL 路径拼音乱码, 永远 404~~ ✅ 2026-06-17 修复 — 跳巨潮 (cninfo) 统一接口
- ~~`app/services/regulatory_scraper.py` line 84: SSE URL 拼接错, 永远 404~~ ✅ 2026-06-17 修复 — 跳巨潮
- ~~`app/services/regulatory_scraper.py` line 125: SZSE JSON 响应被当 HTML 用 BS4 解析, 永远 0 条~~ ✅ 2026-06-17 修复 — 巨潮 + 原 URL fallback 用 response.json()

### 性能 / 资源
- `app/services/inventory/aging_engine.py` line 379: 100w 行 movements 内存峰值 1GB
- `app/services/knowledge_base/retriever.py` line 99: 纯 Python cosine 循环 1 万次 ~10 秒
- `app/services/knowledge_base/embedder.py` line 84: TFIDF 4096 词表截断, 长书召回率低 (本轮只加注释, 需重写)
- `app/services/sentiment/scraper_service.py` line 332: 串行循环 10 项目 5 分钟
- `app/services/erp_adapters.py` line 263: 重复列名 rename 抛 ValueError

### 业务口径
- ~~`app/services/erp_adapters.py` line 236: balance_direction 推导错, 所有负债 ending>0 判为 '借'~~ ✅ 2026-06-17 修复 — 加 BaseERPAdapter.infer_balance_direction 按 account_code 前缀 (1=资产借/2=负债贷/3=权益贷/...) 推导
- ~~`app/services/trial_balance_engine.py` line 46: current_period 没按方向过滤 sum~~ ✅ 2026-06-17 修复 — current_period debit/credit 按 balance_direction 过滤求和
- `app/services/knowledge_base/chunker.py` line 92: 章节切换时 _flush 用旧 chapter ❌ 2026-06-17 经实测当前实现正确 (第二个章节首段不会被标旧章节), 保持不变

### 模型/数据
- `app/models/db/db_models.py` (~30 处): 所有 monetary 字段 Float → Numeric(20, 2) — **需 Alembic DB 迁移**
- `app/models/db/db_models.py` (多表): 缺 CheckConstraint (status / direction / stage / probability)
- `app/models/db_models.py` line 731: ConfirmationCase cascade='all, delete-orphan' 删证据
- `app/models/db_models.py` line 1838: TeamMember 缺 firm_id (跨 firm 成员可被分配)
- `app/services/auth/tenant.py` line 74/97: firm_id IS NULL 旧数据全局可见 (向后兼容但高危)

---

## 未修 - P1 (~250 个, 抽样)

### API
- `app/api/confirmations.py` (~20 处): `get_project_or_404` 只校验存在, 没 `ensure_project_in_firm` — **Pack A.2 已部分覆盖, 待复查**
- `app/api/inventory.py` (~10 处): 同上
- `app/api/report_templates.py` (5 处): 同上
- `app/api/knowledge_base.py`: list_books 跨 firm 共享 (可能 OK 因为知识库是公共)
- `app/api/regulatory_cases.py` line 87: search_by_keywords 全表拉内存匹配

### 前端
- `frontend/_components/period_picker.py` line 44: is_valid_period 仅校验长度 8
- `frontend/pages_audit_cycles.py` 多处: URL 路径疑似拼错 (404)
- `frontend/pages_contracts.py` line 187: OCR 文本未脱敏显示 (合同含身份证等)
- `frontend/pages_team_management.py` line 309: yesterday 默认但 max_value=date.today()
- `frontend/pages_team_management.py` line 92/173/399: date_input value=None 启动错
- 多处 width='stretch' 已弃用 (已基本清, 还有 5 处左右)

### 服务层
- `app/services/ai_analysis.py` line 60: 每次新建 httpx.AsyncClient
- `app/services/ai_analysis_engine.py` line 47: except 后返回 json.dumps({"error": ...}) 污染下游
- `app/services/audit_note_generator.py` line 102: 调私有方法 _call_minimax
- `app/services/inventory/count_plan.py` line 254: user_instruction 拼 prompt 可注入 (本轮 AI 加了防御, 但 service 层仍无 schema 校验)
- `app/services/sales_ledger/document_parser.py` line 47: temp_path 删失败静默
- `app/services/sentiment/dedup.py` line 28: title 未 lower, 漏去重
- `app/services/sentiment/http_client.py` line 53: follow_redirects=True 无域名白名单 (SSRF)
- `app/services/knowledge_base/embedder.py` line 174: DeepSeekEmbedder 假设 OpenAI 协议但字段名差异
- `app/services/comprehensive/qa_engine.py` line 150: Python hash 随机化, question_id 不稳定

### 模型/DB
- `app/services/auth/dependencies.py` line 95: AUTH_ENABLED=false 时合成 admin id=0
- `app/services/auth/jwt.py` line 183: jose 失败降级 stdlib 可能算法不一致
- `app/services/audit_cycles/__init__.py` line 297: CIPTransferChecker 0 除 crash
- `app/services/inventory/count_sheet.py` line 200: MUS 抽样全 0 时 ValueError
- `app/services/inventory/photo_processor.py` line 285: id() 比对 used set (跨 session 错位)
- `app/services/related_parties/ai_inferer.py` line 197: supplier 谓词含 '应付' 误伤 '应付职工薪酬'

---

## 未修 - P2/P3 (~200 个, 暂略)

主要为:
- iterrows 性能 (10 万行慢 30+ 秒) — 改用 itertuples / numpy
- 字面量 emoji (✅/❌) 在某些字体显示乱码
- dataclass `_to_float` 欧式数字格式 '1.000,50' 解析为 1.0005
- print() 而非 logger (监管案例服务)
- 空 `__init__.py` 无 re-export
- dataclass 默认值 None 启动崩溃

---

## 测试覆盖

484 个测试全部通过 (2026-06-16)。
本轮未引入新测试 (因修的都是 P0 紧急项, 后续回归测试由 `tests/test_pack_a2_b2.py` 增量)。

## 后续建议优先级

1. **P0 业务逻辑**: 6-8 个 services 算法错 (audit_cycles / confirmation stats / sales_ledger profit) — 影响审计结论
2. **Float → Numeric**: DB 迁移 + 全栈代码改 Decimal (一两周工作量)
3. **API IDOR 复查**: 70+ 端点要逐个加 `ensure_project_in_firm` (Pack A.2 部分覆盖)
4. **P1 性能**: TFIDF 词表 / cosine 向量化 / ERP adapter — 影响大规模数据
5. **前端 UX**: session_state / 删除确认 / N+1 — 影响用户操作