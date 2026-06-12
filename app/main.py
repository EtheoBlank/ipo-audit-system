"""Main FastAPI application for IPO Audit System."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
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
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    setup_logging(level="DEBUG" if settings.DEBUG else "INFO")
    settings.ensure_dirs()
    await init_db()
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
        description="""
## IPO 审计系统

专业的 IPO 审计底稿生成与数据分析工具。

### 主要功能

- **项目管理**: 创建和管理 IPO 审计项目
- **数据导入**: 支持 Excel 格式的科目余额表、序时账、银行对账单导入
- **底稿生成**: 自动生成标准化的审计底稿 Excel 文件
- **试算平衡**: 验证资产负债表平衡和报表勾稽关系
- **监管案例**: 抓取和检索证监会、交易所的监管案例
- **AI 分析**: 利用 AI识别风险点和生成审计建议
- **销售清单整理 (Sales Ledger)**: 上传散乱文档 → AI 合成结构化销售清单 →
  毛利率/截止性/单价波动/收发存对账/同行业参考分析，并导出多 Sheet Excel
- **收发存盘点 & 减值 (Inventory)**: 上传收发存 → 金额优先生成盘点用表 →
  行业化盘点计划 → 现场拍照 OCR 回填实盘数 → 盘点率/差异统计；
  FIFO 库龄 + 销售清单NRV 跌价 + 上年期初跌价转回
- **函证管理 (Confirmation)**: 从账套自动生成银行/客户/供应商/其他往来询证函统计表 →
  确定发函后锁定发函日期与金额快照（避免多版本混乱）→ 银行询证函按财政部官方模板，
  客户/供应商函证按 CSA 1311/1502/1504 最新审计准则要求函证余额/交易额/票据背书/
  关键合同条款 → 上传回函照片 OCR + AI 解析 → 回函情况自动统计与差异分析
- **法律法规库 (Regulations)**: 自动抓取证监会 / 财政部 / 国家税务总局 / 外管局 /
  人民银行的政策文件、准则、规章、公告与问答口径，支持来源/日期/关键词多维过滤、
  全文搜索、按项目收藏，方便审计师在生成审计说明时即时查规
- **自助知识库 (Knowledge Base)**: 用户上传喜欢的实务书籍 / 案例集 (PDF / EPUB /
  DOCX / TXT / MD) → 系统切块 + 向量化 (TF-IDF 兜底，可切到 MiniMax / DeepSeek 嵌入) →
  生成审计说明时按科目 / 风险点检索相似案例，让 AI 参考真实实务处理方式
- **项目组管理 (Team Management)**: 维护审计师人员库 + 5 级级别（项目负责人/高级经理/
  经理/高级审计员/审计员）；账套导入完成后由 AI 自动生成 IPO 审计工作计划并按级别
  分配任务；支持站会/周会/启动会/复核会纪要 → AI 质量评分；员工日报 + 卡点上报；
  个人 + 项目级可视化进度看板（Streamlit + Altair）；AI 周期性输出管理建议给项目负责人。

### 支持的模板类型

- `account_detail`: 科目明细表
- `income_statement`: 利润表
- `balance_sheet`: 资产负债表
- `cash_flow`: 现金流量表
- `trial_balance`: 试算平衡表
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

    # Health check endpoint
    @app.get("/health", tags=["系统"])
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
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
