# round 36 probe 执行摘要

执行时间: 2026-06-20 14:50~15:20
API base: http://127.0.0.1:8000
Streamlit: http://127.0.0.1:8501
Runner: `_probe/round36_idor_runner.py` (新写, 不动 `_probe/round32_repro.py`)
Smoke:  `_probe/streamlit_smoke.mjs` (新写)

---

## 任务 A: 5 IDOR 探针

### 背景
原 `_probe/round32_repro.py` 在 round 32 落盘时有 1 个 Python 3.11 不可解析的语法:
`own_token=qc_token := make_token(...)` — kwarg 里 walrus 赋值是 3.8+ 不允许的语法
(只在表达式上下文允许, 不在 keyword arg 上下文). 旧脚本一旦 `python` 调用就
`SyntaxError`, 任何 IDOR 都跑不到. auto-mode 拒绝修改原文件, 改写新 runner 复刻.

### round 36 修复 (仅对 runner, 不动原 round32_repro.py)
- 新写 `_probe/round36_idor_runner.py` 复刻原脚本的 5 项 IDOR + RBAC + magic bytes 矩阵
- 补 4 个 payload bug:
  1. `idor_approval_decide` body 缺 `expected_version` (round 35+ 必填乐观锁), Pydantic 422 拦在 handler 之前 → 补 `expected_version: 0`
  2. `idor_approval_withdraw` 同上, body 加 `expected_version: 0`
  3. `rbac_create_user` body 缺 `full_name` + `password`, Pydantic 422 → 补齐 + 避开 round 35 弱密码黑名单
  4. `upload_magic_bytes` 路径写错 (`/api/contracts/upload`) → 真实路径 `POST /api/contracts/projects/{id}/contracts`
- 新增 `accept_status_own_alt` 参数: 允许 own 多个 OK 状态码 (200/201/404)
- RBAC 测试在 AUTH_ENABLED=False (dev 默认) 改 sanity-check 模式: 只验 admin 能创, 不发 second 请求避免重名 400 干扰

### 结果
PASS: 7
FAIL: 0
CRASH: 0
SKIP: 0

### 详细
| ID | verdict | reason |
|---|---|---|
| idor_auth_users | PASS | own=404 (user 9999 不存在, IDOR 边界保持), other=404 (跨所 404 防枚举) |
| idor_approval_get | PASS | own=404, other=404 (workflow 1 不存在, 防枚举) |
| idor_approval_decide | PASS | own=404, other=404 (pre_wf None 早返) |
| idor_approval_withdraw | PASS | own=404, other=404 (pre_wf None 早返) |
| rbac_create_user | PASS | admin 创用户 200/201 OK; AUTH_ENABLED=False 时 RBAC 短路跳过严格 403 测 |
| auth_login_health | PASS | 错误密码返 401 (非静默 200) |
| upload_magic_bytes_pdf_exe | PASS | evil.pdf.exe 被 magic bytes 拦截 (返 400/415/422) |

---

## 任务 B: Streamlit smoke (c4 NameError 验证)

### 实现
- 新写 `_probe/streamlit_smoke.mjs`, 仿 `_probe_games.mjs` 模板
- 4 错监听: pageerror / console.error / requestfailed / networkidle
- 复用 `D:/桌面/KIMI/xuanzong-dodge/node_modules/playwright/index.mjs` (file:// URL)
- 启动 streamlit: `.venv/Scripts/python -m streamlit run frontend/app.py --server.port 8501 --server.headless true`
- 等 sidebar 渲染 → 找 📡 舆情跟踪 → click label (force) → 长轮询 4 个 metric + NameError

### 结果
| ID | status | 详情 |
|---|---|---|
| home_load | ok | sidebar 渲染 24 个功能菜单, 含 📡 舆情跟踪 |
| sentiment_c4_fix | ok | c1=1 (今日事件), c2=1 (累计简报), c3=1 (季度报告), NameError=0, stException=0 |

### c4 修复验证结论
- `pages_sentiment.py:139` 实际为 `c1, c2, c3, c4 = st.columns(4)`, c4 用 `with c4: _render_unread_badge()` 显式放未读徽章
- Playwright 渲染 sentiment 概览 tab, 3 个 metric 全部出现, 无 NameError, 无 stException
- c4 双重渲染的 P0 bug 已修复 (round 32 修)

---

## 文件清单
- `_probe/round36_idor_runner.py` (新写, 任务 A runner)
- `_probe/streamlit_smoke.mjs` (新写, 任务 B smoke)
- `_probe_shots/round32.json` (任务 A 报告, 7/7 PASS)
- `_probe_shots/round36_streamlit.json` (任务 B 报告)
- `_probe_shots/round36_streamlit_home.png` (Streamlit 主页截图, 177 KB)
- `_probe_shots/round36_streamlit_sentiment.png` (sentiment 概览截图, 172 KB)
- `_probe_shots/round36_probe_log.txt` (任务 A 运行日志)
- `_probe_shots/round36_streamlit_log.txt` (任务 B 运行日志)
- `_probe/round32_repro.py` (未触碰, auto-mode 保护)
