"""Vercel serverless entrypoint for IPO Audit System FastAPI backend.

Why this file exists
--------------------
Vercel's Python runtime looks for an ``app`` variable in ``api/index.py`` and
serves it as an ASGI application. FastAPI is an ASGI framework, so we just
re-export the existing ``app.main:app`` instance — no wrapper needed for the
happy path.

Vercel constraints addressed here
---------------------------------
* **No long-running process** — APScheduler (background sentiment scan) is
  disabled in this entrypoint via the ``ENABLE_SCHEDULER`` env var. The
  scheduler keeps an in-memory job store, which is wiped on every cold start.
  Scheduled work moves to Vercel Cron (see ``vercel.json`` → ``crons``) hitting
  a dedicated ``/api/cron/sentiment`` endpoint instead.
* **No persistent filesystem** — ``ensure_dirs()`` is patched to no-op for
  serverless via ``VERCEL=1``; SQLite defaults are swapped for Postgres in
  ``app.core.config``. Uploads stream through to external storage (Vercel Blob
  or S3) by setting ``STORAGE_BACKEND=vercel_blob``.
* **Cold start latency** — we keep the existing module structure so Vercel
  caches compiled bytecode; ``requirements-vercel.txt`` pins the lean set.

Usage
-----
Vercel imports this module on first request to ``/api/index``. Subsequent
requests are served from the warm container until the 5-min idle timeout.
"""

from __future__ import annotations

import logging
import os

# 必须在 import app.main 之前注入 serverless 标记 — config.py / lifespan 据此分支
os.environ.setdefault("VERCEL", "1")
os.environ.setdefault("ENABLE_SCHEDULER", "0")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("vercel-entrypoint")

# 直接 re-export FastAPI app; Vercel 自动识别 ASGI
# 任何从 app.main 抛出的 import error 都会被 Vercel runtime 捕获并返 500,
# 我们在 lifespan 里已经做了 JWT_SECRET / init_db 的防御, 这里不再重复 try/except
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.main import app  # noqa: E402, F401  (Vercel reads `app`)
from app.services.storage import LocalStorage


# ---------------------------------------------------------------
#  /api/files/{key:path} — LocalStorage 静态代理
# ---------------------------------------------------------------
# STORAGE_BACKEND=local 时, 业务代码返回 ``storage.url(key)`` = ``/api/files/...``,
# 浏览器要能真取到文件, 必须有这个 endpoint. Vercel Blob 后端不走这里 (返回
# 的是 https://blob.vercel-storage.com/... 直接 URL).
#
# 这里直接在 Vercel entrypoint 上挂路由, 不另起 ``api/files/[key].py`` —
# 因为 Vercel Python runtime 对带方括号的 module 文件名处理古怪, 放一起更稳.
@app.get("/api/files/{key:path}")
async def vercel_files_proxy(key: str):
    if settings.STORAGE_BACKEND != "local":
        raise HTTPException(
            status_code=410,
            detail=f"STORAGE_BACKEND={settings.STORAGE_BACKEND!r} 不走 /api/files/ 代理",
        )
    local = LocalStorage()
    path = local._resolve(key)  # noqa: SLF001
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {key}")
    # 防 path traversal
    real = path.resolve()
    for root in (
        settings.UPLOAD_DIR.resolve(),
        settings.OUTPUT_DIR.resolve(),
        settings.TEMPLATE_DIR.resolve(),
        settings.KNOWLEDGE_BASE_DIR.resolve(),
        settings.SENTIMENT_OUTPUT_DIR.resolve(),
        settings.REPORT_TEMPLATE_DIR.resolve(),
        settings.REPORT_OUTPUT_DIR.resolve(),
    ):
        try:
            real.relative_to(root)
            break
        except ValueError:
            continue
    else:
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    return FileResponse(real, filename=path.name)


logger.info("✅ Vercel entrypoint loaded: %s (storage=%s)", app.title, settings.STORAGE_BACKEND)
