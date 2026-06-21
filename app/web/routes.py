"""HTML routes for the Vercel-deployed web UI.

Server-rendered HTML via Jinja2. Each endpoint either:
- Renders a template (GET)
- Handles a form POST then redirects (POST)

No client-side JS framework. Tailwind via CDN.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import Project, AccountBalance
from app.models.db.auth import User
from app.services.auth import get_current_user_optional
from app.services.excel_parser import ExcelParser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Web UI"])

# 模板目录 — 用项目内的 templates/web/. Vercel fs 只读, 但 read-only 不影响
# Jinja2 模板加载 (它在启动时一次性 compile).
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------
#  GET / — Dashboard
# ---------------------------------------------------------------
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """首页 — 项目总数 / 科目余额总记录数 / 知识库等统计."""
    project_count = (await db.execute(select(func.count(Project.id)))).scalar() or 0
    account_count = (await db.execute(select(func.count(AccountBalance.id)))).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "app_name": settings.APP_NAME,
            "app_version": settings.APP_VERSION,
            "project_count": project_count,
            "account_count": account_count,
            "current_path": "/",
        },
    )


# ---------------------------------------------------------------
#  GET /projects — 项目列表
# ---------------------------------------------------------------
@router.get("/projects", response_class=HTMLResponse, include_in_schema=False)
async def list_projects(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> HTMLResponse:
    rows = (await db.execute(select(Project).order_by(Project.id.desc()).limit(100))).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="projects/list.html",
        context={
            "app_name": settings.APP_NAME,
            "current_path": "/projects",
            "projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "company_name": getattr(p, "company_name", None) or "—",
                    "industry": getattr(p, "industry", None) or "—",
                    "fiscal_year": getattr(p, "fiscal_year", None) or "—",
                    "created_at": p.created_at,
                    "status": "活跃" if not getattr(p, "archived", False) else "已归档",
                }
                for p in rows
            ],
        },
    )


# ---------------------------------------------------------------
#  GET/POST /projects/new — 新建项目
# ---------------------------------------------------------------
@router.get("/projects/new", response_class=HTMLResponse, include_in_schema=False)
async def new_project_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="projects/new.html",
        context={
            "app_name": settings.APP_NAME,
            "current_path": "/projects",
            "error": None,
            "form": {},
        },
    )


@router.post("/projects/new", include_in_schema=False)
async def new_project_submit(
    name: str = Form(...),
    company_name: str = Form(""),
    industry: str = Form(""),
    fiscal_year: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """创建项目 — 重定向到详情页.

    简化版: 不调用完整 API endpoint, 直接 ORM 插入. 不走多租户 (AUTH_ENABLED=false).
    """
    project = Project(
        name=name.strip(),
        company_name=company_name.strip() or None,
        industry=industry.strip() or None,
        fiscal_year=fiscal_year.strip() or None,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


# ---------------------------------------------------------------
#  GET /projects/{id} — 项目详情
# ---------------------------------------------------------------
@router.get("/projects/{project_id}", response_class=HTMLResponse, include_in_schema=False)
async def project_detail(
    request: Request,
    project_id: int,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    account_count = (await db.execute(
        select(func.count(AccountBalance.id)).where(AccountBalance.project_id == project_id)
    )).scalar() or 0
    return templates.TemplateResponse(
        request=request,
        name="projects/detail.html",
        context={
            "app_name": settings.APP_NAME,
            "current_path": "/projects",
            "project": {
                "id": project.id,
                "name": project.name,
                "company_name": getattr(project, "company_name", None) or "—",
                "industry": getattr(project, "industry", None) or "—",
                "fiscal_year": getattr(project, "fiscal_year", None) or "—",
                "created_at": project.created_at,
            },
            "account_count": account_count,
        },
    )


# ---------------------------------------------------------------
#  GET/POST /projects/{id}/import — Excel 上传
# ---------------------------------------------------------------
@router.get("/projects/{project_id}/import", response_class=HTMLResponse, include_in_schema=False)
async def import_form(request: Request, project_id: int, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    return templates.TemplateResponse(
        request=request,
        name="projects/import.html",
        context={
            "app_name": settings.APP_NAME,
            "current_path": "/projects",
            "project": {"id": project.id, "name": project.name},
            "error": None,
            "imported": None,
        },
    )


@router.post("/projects/{project_id}/import", include_in_schema=False)
async def import_submit(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Excel 上传 → 解析 → 入库 → 渲染结果.

    Vercel serverless 注意: /tmp 是唯一可写位置. 写本地盘仅作临时, 函数退出
    后文件消失. 真正数据持久在 DB (SQLite 在 /tmp/ipo_audit.db)."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")

    # 保存到 /tmp
    tmp_dir = Path("/tmp/uploads")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename or 'upload.xlsx'}"
    tmp_path = tmp_dir / safe_name
    try:
        with tmp_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        # 解析 Excel → 入库
        parser = ExcelParser()
        rows = await parser.parse_account_balances(str(tmp_path), project_id=project_id, db=db)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Excel 导入失败")
        return templates.TemplateResponse(
            request=request,
            name="projects/import.html",
            context={
                "app_name": settings.APP_NAME,
                "current_path": "/projects",
                "project": {"id": project.id, "name": project.name},
                "error": f"导入失败: {exc}",
                "imported": None,
            },
            status_code=400,
        )
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return templates.TemplateResponse(
        request=request,
        name="projects/import.html",
        context={
            "app_name": settings.APP_NAME,
            "current_path": "/projects",
            "project": {"id": project.id, "name": project.name},
            "error": None,
            "imported": rows,
        },
    )


# ---------------------------------------------------------------
#  GET /projects/{id}/trial-balance — 试算平衡
# ---------------------------------------------------------------
@router.get("/projects/{project_id}/trial-balance", response_class=HTMLResponse, include_in_schema=False)
async def trial_balance_view(
    request: Request,
    project_id: int,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """直接 ORM 查询 AccountBalance, 渲染表格.

    不调用 TrialBalanceService (那是 DataFrame 工具, 不接 AsyncSession).
    用户可读懂就够了, 严格勾稽校验走 /api/reports/trial-balance endpoint.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")

    rows = (await db.execute(
        select(AccountBalance)
        .where(AccountBalance.project_id == project_id)
        .order_by(AccountBalance.subject_code)
        .limit(200)
    )).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="projects/trial_balance.html",
        context={
            "app_name": settings.APP_NAME,
            "current_path": "/projects",
            "project": {"id": project.id, "name": project.name},
            "rows": [
                {
                    "subject_code": r.subject_code,
                    "subject_name": r.subject_name,
                    "opening_debit": getattr(r, "opening_debit", 0) or 0,
                    "opening_credit": getattr(r, "opening_credit", 0) or 0,
                    "current_debit": getattr(r, "current_debit", 0) or 0,
                    "current_credit": getattr(r, "current_credit", 0) or 0,
                }
                for r in rows
            ],
            "error": None,
        },
    )


# ---------------------------------------------------------------
#  GET /knowledge-base — 知识库 (占位 — 走 API 检索)
# ---------------------------------------------------------------
@router.get("/knowledge-base", response_class=HTMLResponse, include_in_schema=False)
async def knowledge_base(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="knowledge_base.html",
        context={
            "app_name": settings.APP_NAME,
            "current_path": "/knowledge-base",
            "results": [],
            "query": "",
        },
    )
