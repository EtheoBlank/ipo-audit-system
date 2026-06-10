"""Main FastAPI application for IPO Audit System."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.config import settings
from app.core.database import init_db
from app.api import projects, workbooks, regulatory_cases, reports, sales_ledger, contracts


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    await init_db()
    print(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 启动成功")
    yield
    # Shutdown
    print(f"👋 {settings.APP_NAME} 关闭")


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

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static files
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    settings.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    # Include routers
    app.include_router(projects.router)
    app.include_router(workbooks.router)
    app.include_router(regulatory_cases.router)
    app.include_router(reports.router)
    app.include_router(sales_ledger.router)
    app.include_router(contracts.router)

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