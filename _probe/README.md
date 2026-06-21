# ipo-audit probes (端到端验证)

`tests/_helpers/` 提供单元 / 集成 fixture, 本目录补充**真实运行**的端到端探针.

## 类别 (对齐 probe-testing skill)

| 类别 | 文件 | 验证目标 |
|---|---|---|
| 场景 F (Bug 重现) | `round32_repro.py` | round 32 修的 13 P0 在真实 HTTP 下的拦截效果 |
| 场景 D (玩家视角) | `streamlit_smoke.mjs` (TODO) | Streamlit 前端 c4 NameError / 删模板二次确认 等 UI 修复 |
| 场景 E (LLM 压测) | `llm_stress.py` (TODO) | DeepSeek / 内部 LLM 端点 p50/p95/dedup |

## 用法

```bash
# 1. 启 FastAPI
cd D:/ipo_audit_link
.venv/Scripts/python -m uvicorn app.main:app --port 8000 &

# 2. 跑 round 32 探针
.venv/Scripts/python _probe/round32_repro.py

# 报告落 _probe_shots/round32.json
```

## 不替代单元测试

单元测试 (1137+) 覆盖**逻辑** (auth 校验 / 库龄算法 / 边际条件); 探针覆盖**集成**:
- 真实 HTTP 而非 TestClient
- 真实进程间通信 (FastAPI lifespan, 中间件, CORS)
- 真实 Streamlit rerun 流 (前端 roundtrip)

## 铁律 (来自 probe-testing skill)

1. **三件套错误监听**: pageerror / console.error / requestfailed
2. **截图证据 + JSON 汇总**: `_probe_shots/<id>.png` + `_probe_shots/_results.json`
3. **dev server 约定**: FastAPI 8000, Streamlit 8501, Playwright 用 KIMI 已装路径
4. **失败立刻停**: CRASH/FAIL 不静默吞
