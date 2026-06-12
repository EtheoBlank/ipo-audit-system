"""Main FastAPI application for IPO Audit System."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db
from app.core.logging import setup_logging
from app.api import (
    projects,
    workbooks,
    regulatory_cases,
    reports,
    sales_ledger,
    contracts,
    inventory,
    confirmations,
    regulations,
    knowledge_base,
    comprehensive,
    team_management,
    sentiment,
    # Pack A — 新模块
    auth as auth_api,
    notifications as notifications_api,
    account_audit as account_audit_api,
    report_templates as report_templates_api,
)

logger = logging.getLogger(__name__)


# ============================================================
#  Pack A — Audit Log 中间件
# ============================================================


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _exclude_paths() -> set:
    return {
        p.strip()
        for p in (settings.AUDIT_LOG_EXCLUDE_PATHS or "").split(",")
        if p.strip()
    }


class AuditLogMiddleware(BaseHTTPMiddleware):
    """简单审计中间件 — 仅记录写操作 (按配置).

    业务路由内部已经 ``record_audit_log`` 落了精细日志, 这里再补一条粗粒度日志
    (覆盖未在路由里手动 record 的端点 + 异常未捕获的兜底).
    避免重复: 关键端点已 record 的, 这里只多一条 method/path/status, 不会带 payload.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        excludes = _exclude_paths()
        if any(path.startswith(p) for p in excludes):
            return await call_next(request)

        if settings.AUDIT_LOG_WRITE_ONLY and request.method.upper() not in _WRITE_METHODS:
            return await call_next(request)

        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")

        response: Response
        error_detail = None
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            error_detail = str(exc)[:2000]
            raise
        finally:
            # 写日志 — 失败被吞 (audit_log 内部自带 try/except)
            try:
                from app.services.auth.audit_log import record_audit_log

                user = getattr(request.state, "user", None)
                user_id = getattr(user, "id", None) if user else None
                user_display = getattr(user, "full_name", None) if user else None
                user_role = getattr(user, "role", None) if user else None
                firm_id = getattr(user, "firm_id", None) if user else None
                async with AsyncSessionLocal() as db:
                    await record_audit_log(
                        db,
                        user_id=user_id,
                        user_display=user_display,
                        user_role=user_role,
                        firm_id=firm_id,
                        action="http",
                        method=request.method,
                        path=path,
                        ip=ip,
                        user_agent=ua,
                        status_code=getattr(response, "status_code", None) if error_detail is None else 500,
                        summary=f"{request.method} {path}",
                        error_detail=error_detail,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("AuditLogMiddleware 写日志异常 (已吞)")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    setup_logging(level="DEBUG" if settings.DEBUG else "INFO")
    settings.ensure_dirs()

    # Pack A — 生产护栏: JWT_SECRET 不能用 dev 默认值
    if settings.AUTH_ENABLED and not settings.DEBUG:
        # P0 第 2 轮修复 — 用 == 严格比较 (不再 startswith 假阳), + 长度校验
        _DEV_JWT_DEFAULTS = {
            "ipo-audit-dev-only-change-in-prod-please-use-secrets-token-urlsafe-32",
            "please-generate-a-random-secret-with-secrets-token-urlsafe-48-bytes",
            "",
        }
        if (
            settings.JWT_SECRET in _DEV_JWT_DEFAULTS
            or len(settings.JWT_SECRET or "") < 32
        ):
            logger.error(
                "❌ 生产模式 (DEBUG=False) + AUTH_ENABLED=true, 但 JWT_SECRET "
                "仍是 dev 默认值或长度不足 32 字节。请在 .env 设置强随机串后再启动: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
            raise RuntimeError(
                "JWT_SECRET must be set to a strong random string (>=32 bytes) in production"
            )
        # 启动自检: encode → decode roundtrip
        try:
            from app.services.auth.jwt import create_access_token, decode_token
            _t = create_access_token(user_id=0, username="__startup_check__", role="admin")
            _p = decode_token(_t)
            assert _p.get("sub") == "0", "JWT roundtrip 自检失败"
        except Exception as exc:  # noqa: BLE001
            logger.exception("JWT encode/decode 自检失败 — JWT 配置异常: %s", exc)
            raise RuntimeError(f"JWT configuration broken: {exc}") from exc

    await init_db()

    # Pack A — Auth bootstrap (创建默认事务所 + 内置角色/权限; admin 仅在 AUTH_ENABLED=true 创建)
    try:
        from app.services.auth import bootstrap_auth
        await bootstrap_auth()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auth bootstrap 启动失败 (非致命): %s", exc)

    # 舆情跟踪调度器 (APScheduler) — v0.2 新增
    try:
        from app.services.sentiment.scheduler import start_scheduler
        await start_scheduler()
    except Exception as exc:  # 调度器挂掉不能让整个 app 起不来
        logger.exception("舆情调度器启动失败: %s", exc)
    logger.info("🚀 %s v%s 启动成功", settings.APP_NAME, settings.APP_VERSION)
    yield
    # Shutdown
    try:
        from app.services.sentiment.scheduler import stop_scheduler
        await stop_scheduler()
    except Exception:
        logger.exception("舆情调度器停止失败")
    logger.info("👋 %s 关闭", settings.APP_NAME)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        description="""
## IPO 审计系统

专业的 IPO 审计底稿生成与数据分析工具。

### 主要功能

- **多用户 / 权限 / 审计轨迹 (Pack A — Phase 18)**: 完整 5 级签字流
  (审计员 → 经理 → 项目合伙人 → 质控合伙人 → 签字合伙人) + JWT 认证 +
  审计轨迹全量记录 + 通用通知中心
- **长期资产发生额审定 (Pack A — 用户特别要求)**: 固定资产/在建工程/
  无形资产/长投/商誉/使用权资产等长期资产科目, 不只期初期末出审定数,
  本期借/贷方发生额逐笔出审定数 + 审计调整, 底稿自动恒等式校验
- **报告模板自定义化 (Pack A — Phase 20)**: 事务所上传 Word/Excel
  模板, 系统按 ``${placeholder}`` 注入数据生成定制品牌报告
- **项目管理**: 创建和管理 IPO 审计项目
- **数据导入**: 支持 Excel 格式的科目余额表、序时账、银行对账单导入
- **底稿生成**: 自动生成标准化的审计底稿 Excel 文件
- **试算平衡**: 验证资产负债表平衡和报表勾稽关系
- **监管案例**: 抓取和检索证监会、交易所的监管案例
- **AI 分析**: 利用 AI 识别风险点和生成审计建议
- **销售清单整理 (Sales Ledger)**: 上传散乱文档 → AI 合成结构化销售清单 →
  毛利率/截止性/单价波动/收发存对账/同行业参考分析，并导出多 Sheet Excel
- **收发存盘点 & 减值 (Inventory)**: 上传收发存 → 金额优先生成盘点用表 →
  行业化盘点计划 → 现场拍照 OCR 回填实盘数 → 盘点率/差异统计；
  FIFO 库龄 + 销售清单NRV 跌价 + 上年期初跌价转回
- **函证管理 (Confirmation)**: 财政部模板询证函 + 回函 OCR + 差异统计
- **法律法规库 / 自助知识库 / 项目组管理 / 舆情跟踪 / 综合底稿** 等
        """,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS middleware — restrict origins in production
    allowed_origins = [
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    ]
    if settings.DEBUG:
        # In debug mode, also allow localhost on any port for dev convenience
        allowed_origins.extend([
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "http://localhost:3000",
        ])
        allowed_origins = list(set(allowed_origins))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Pack A — 审计轨迹中间件 (放在 CORS 之后, 业务路由之前)
    app.add_middleware(AuditLogMiddleware)

    # Include routers
    app.include_router(projects.router)
    app.include_router(workbooks.router)
    app.include_router(regulatory_cases.router)
    app.include_router(reports.router)
    app.include_router(sales_ledger.router)
    app.include_router(comprehensive.router)
    app.include_router(contracts.router)
    app.include_router(inventory.router)
    app.include_router(confirmations.router)
    app.include_router(regulations.router)
    app.include_router(knowledge_base.router)
    app.include_router(team_management.router)
    app.include_router(sentiment.router)
    # Pack A — 新模块
    app.include_router(auth_api.router)
    app.include_router(notifications_api.router)
    app.include_router(account_audit_api.router)
    app.include_router(report_templates_api.router)

    # Health check endpoint
    @app.get("/health", tags=["系统"])
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "auth_enabled": settings.AUTH_ENABLED,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
