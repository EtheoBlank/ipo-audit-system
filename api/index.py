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
        https://<your-project>.vercel.app/        → FastAPI root
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

from app.main import app  # noqa: E402, F401  (Vercel reads `app`)

logger.info("✅ Vercel entrypoint loaded: %s", app.title)
