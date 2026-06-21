"""Vercel serverless entrypoint for IPO Audit System FastAPI backend.

Vercel Python runtime 在 ``api/index.py`` 顶层找 ``app`` 变量作为 ASGI 应用.
FastAPI 本身就是 ASGI, 这里直接 re-export ``app.main:app`` 即可, 不需要 wrapper.

Vercel 部署前置条件 (本文件之外):
  * Vercel Dashboard Import GitHub repo
  * 环境变量: DATABASE_URL (生产推荐 Neon Postgres asyncpg)
  * vercel.json 已声明 Python 3.11 runtime + rewrites 到 /api/index

注意:
  * 本文件不修改 app.core.config / app.main 的现有字段, 只追加 ``VERCEL`` env.
  * Vercel 默认 URL 路径全部 rewrite 到这里 (除 /api/cron/*):
        https://<your-project>.vercel.app/        → FastAPI root (本文件加的 HTML 落地页)
        https://<your-project>.vercel.app/docs    → Swagger UI
        https://<your-project>.vercel.app/api/*   → 业务 API
"""

from __future__ import annotations

import logging
import os

# 标记 serverless 模式 — 当前 main.py 的 lifespan 还没读这个 env,
# 留作后续扩展用 (例如禁 scheduler / 切 Postgres 默认值).
os.environ.setdefault("VERCEL", "1")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("vercel-entrypoint")

from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402, F401  (Vercel reads `app`)


# ---------------------------------------------------------------
#  Vercel 落地页 — GET /
# ---------------------------------------------------------------
# 默认 FastAPI 没有 root handler, 直接访问 Vercel URL 返回 404.
# 这里加一个静态 HTML 首页, 把用户引导到:
#   * /docs        — Swagger UI (开发者调 API 用)
#   * /api/health  — 健康检查
#   * Streamlit UI — HF Space 上的完整 Web 界面 (面向最终用户)
#
# HTML 内嵌, 不依赖静态文件目录 (Vercel fs 只读), 单文件可读.
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    hf_url = "https://etheozheng-etheoblank.hf.space"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{settings.APP_NAME} — Vercel API</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC",
                 "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: #e4e4e7; min-height: 100vh; margin: 0;
    display: flex; align-items: center; justify-content: center;
  }}
  .card {{
    background: rgba(255,255,255,0.05); backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 16px;
    padding: 48px 56px; max-width: 720px; width: 90%;
    box-shadow: 0 20px 60px rgba(0,0,0,0.4);
  }}
  h1 {{ margin: 0 0 8px; font-size: 32px; color: #fff; }}
  .ver {{ font-size: 14px; color: #94a3b8; margin-bottom: 24px; }}
  .ver span {{ background: #3370FF; padding: 2px 8px; border-radius: 4px; color: #fff; margin-left: 6px; font-weight: 600; }}
  p {{ line-height: 1.7; color: #cbd5e1; }}
  .links {{ margin-top: 32px; display: grid; gap: 12px; }}
  a.btn {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 20px; border-radius: 10px; text-decoration: none;
    background: rgba(51, 112, 255, 0.15);
    border: 1px solid rgba(51, 112, 255, 0.3);
    color: #fff; transition: all 0.15s;
  }}
  a.btn:hover {{ background: rgba(51, 112, 255, 0.25); transform: translateY(-1px); }}
  a.btn.primary {{ background: rgba(16, 185, 129, 0.15); border-color: rgba(16, 185, 129, 0.4); }}
  a.btn.primary:hover {{ background: rgba(16, 185, 129, 0.25); }}
  a.btn .label {{ font-weight: 600; font-size: 16px; }}
  a.btn .desc {{ font-size: 13px; color: #94a3b8; margin-top: 2px; }}
  a.btn .arrow {{ font-size: 20px; opacity: 0.6; }}
  footer {{ margin-top: 32px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.1);
            font-size: 12px; color: #64748b; }}
</style>
</head>
<body>
  <div class="card">
    <h1>🏛️ {settings.APP_NAME}</h1>
    <div class="ver">Vercel API 入口<span>v{settings.APP_VERSION}</span></div>

    <p>这是 <strong>FastAPI 后端</strong> 的部署入口。如果你看到的是技术 API 文档,
       说明你是开发者,可以直接调用 <code>/api/*</code> 路由。</p>
    <p>如果你是<strong>最终用户</strong>(审计师 / 项目经理),
       请使用下面的 Streamlit Web UI —— 那是完整的产品界面。</p>

    <div class="links">
      <a class="btn primary" href="{hf_url}" target="_blank" rel="noopener">
        <div>
          <div class="label">🤗 打开 Streamlit Web UI</div>
          <div class="desc">完整产品界面 — 推荐给最终用户使用</div>
        </div>
        <span class="arrow">→</span>
      </a>
      <a class="btn" href="{base}/docs">
        <div>
          <div class="label">📚 Swagger API 文档</div>
          <div class="desc">240 个 endpoints — 给开发者调试用</div>
        </div>
        <span class="arrow">→</span>
      </a>
      <a class="btn" href="{base}/openapi.json">
        <div>
          <div class="label">🔧 OpenAPI 3.1 规范</div>
          <div class="desc">机器可读的 API schema, 用于 codegen / Postman</div>
        </div>
        <span class="arrow">→</span>
      </a>
      <a class="btn" href="{base}/health">
        <div>
          <div class="label">🩺 健康检查</div>
          <div class="desc">验证后端服务存活</div>
        </div>
        <span class="arrow">→</span>
      </a>
    </div>

    <footer>
      Deployed on Vercel · GitHub push to <code>master</code> triggers auto-rebuild ·
      Repo: <a href="https://github.com/EtheoBlank/ipo-audit-system" style="color:#94a3b8">EtheoBlank/ipo-audit-system</a>
    </footer>
  </div>
</body>
</html>"""


logger.info("✅ Vercel entrypoint loaded: %s (db=%s)", app.title, settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL[:30])