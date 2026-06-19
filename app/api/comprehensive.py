"""综合底稿自动生成 — API 路由。

端点：
  POST   /api/comprehensive/templates          上传模板（多所隔离 + 版本管理）
  GET    /api/comprehensive/templates          列出某事务所的模板
  GET    /api/comprehensive/templates/{id}/download  下载模板 .xlsx
  POST   /api/comprehensive/fill               触发一次完整填充流程
  POST   /api/comprehensive/qa-apply           提交问答后回填
  POST   /api/comprehensive/historical         脱敏入库历史底稿
  GET    /api/comprehensive/historical/search  在历史库中检索
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db.auth import User
from app.services.auth import get_current_user, get_current_user_optional
from app.services.auth.tenant import ensure_project_in_firm
from app.services.comprehensive.fill_engine import ComprehensiveFillEngine
from app.services.comprehensive.firm_template_service import (
    FirmTemplateService,
    HistoricalLibraryService,
)
from app.services.comprehensive.schemas import FillReport

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/comprehensive", tags=["综合底稿"])


# ============================================================
# 上传/下载通用工具
# ============================================================

# 仅允许字母/数字/中文/下划线/连字符/点（防路径/头注入）
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._\-一-鿿]{1,128}$")

# xlsx 文件头（PK\x03\x04 标识 zip/xlsx）
_XLSX_MAGIC = b"PK\x03\x04"


async def _read_capped_xlsx(file: UploadFile) -> bytes:
    """读取上传文件，校验大小 / 文件签名。"""
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB 上限",
        )
    if len(data) < 4 or data[:4] != _XLSX_MAGIC:
        raise HTTPException(status_code=400, detail="文件不是有效的 xlsx（签名不匹配）")
    return data


def _safe_id(value: str, name: str) -> str:
    """校验并转义用于 HTTP 头的 ID。"""
    if not _SAFE_ID_RE.match(value):
        raise HTTPException(status_code=400, detail=f"非法 {name}：含不允许的字符")
    return value


# ============================================================
# Schemas
# ============================================================


class TemplateOut(BaseModel):
    id: int
    firm_id: str
    template_id: str
    template_name: str
    version: str
    industry: Optional[str] = None
    audit_period: Optional[str] = None
    is_active: bool
    published_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class FillRequest(BaseModel):
    template_id: str = Field(..., description="模板 ID（可省略 version 取最新）")
    template_version: Optional[str] = None
    project_id: int = Field(..., description="关联项目 ID")


class QARequest(BaseModel):
    report: FillReport
    answers: dict[str, str] = Field(default_factory=dict, description="question_id → 回答")


class HistoricalSearchResponse(BaseModel):
    hits: list[dict[str, Any]]


# ============================================================
# 模板管理
# ============================================================


@router.post("/templates", response_model=TemplateOut)
async def upload_template(
    template_id: str = Form(...),
    version: str = Form("1.0.0"),
    template_name: str = Form(...),
    industry: Optional[str] = Form(None),
    audit_period: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    created_by: Optional[str] = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传一份 .xlsx 模板，自动解析后入库。"""
    firm_id = str(current_user.firm_id)
    _safe_id(firm_id, "firm_id")
    _safe_id(template_id, "template_id")
    _safe_id(version, "version")
    data = await _read_capped_xlsx(file)
    try:
        t = await FirmTemplateService(session).upload(
            firm_id=firm_id,
            template_id=template_id,
            version=version,
            template_bytes=data,
            template_name=template_name,
            industry=industry,
            audit_period=audit_period,
            description=description,
            created_by=created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TemplateOut.model_validate(t)


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    session: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """列出当前事务所的所有激活模板。"""
    firm_id = str(current_user.firm_id) if current_user else ""
    _safe_id(firm_id, "firm_id")
    items = await FirmTemplateService(session).list_for_firm(firm_id)
    return [TemplateOut.model_validate(t) for t in items]


@router.get("/templates/{template_id}/download")
async def download_template(
    template_id: str,
    version: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """下载模板 .xlsx 字节流。"""
    from fastapi.responses import Response

    firm_id = str(current_user.firm_id) if current_user else ""
    firm_id = _safe_id(firm_id, "firm_id")
    template_id = _safe_id(template_id, "template_id")
    if version is not None:
        version = _safe_id(version, "version")
    t = await FirmTemplateService(session).get_latest(firm_id, template_id, version)
    if t is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    # 用 quote() 转义 Content-Disposition 头，防 HTTP 头注入
    safe_filename = quote(f"{t.template_id}_{t.version}.xlsx", safe="")
    return Response(
        content=t.template_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={safe_filename}",
        },
    )


# ============================================================
# 填充
# ============================================================


@router.post("/fill", response_model=FillReport)
async def fill_template(
    req: FillRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """触发一次完整填充流程。"""
    firm_id = str(current_user.firm_id)
    _safe_id(firm_id, "firm_id")
    _safe_id(req.template_id, "template_id")
    schema = await FirmTemplateService(session).parse_to_schema(firm_id, req.template_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="模板未找到")

    # 接入项目数据：根据 project_id 加载 ORM 中的项目 / 科目余额表 / 函证
    ctx = await _build_context_for_project(session, req.project_id, current_user)
    if ctx is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    engine = ComprehensiveFillEngine()
    return await engine.fill(schema, ctx)


async def _build_context_for_project(
    session: AsyncSession,
    project_id: int,
    current_user: Optional[User] = None,
) -> Optional[Any]:
    """从项目 ID 构造 WorkpaperDataContext。

    返回 None 表示项目不存在或跨事务所无权访问。
    """
    from sqlalchemy import select
    from app.models.db_models import (
        AccountBalance,
        ConfirmationCase,
        Project,
    )
    from app.services.comprehensive.field_mapper import WorkpaperDataContext
    from app.utils.db_helpers import account_balances_to_df

    if current_user is not None:
        try:
            project = await ensure_project_in_firm(session, project_id, current_user)
        except HTTPException:
            return None
    else:
        project = await session.get(Project, project_id)
        if project is None:
            return None

    proj = project  # ensure_project_in_firm 已返回 Project 实例

    balances = (
        (
            await session.execute(
                select(AccountBalance).where(AccountBalance.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    ab_df = account_balances_to_df(list(balances)) if balances else None

    cases = (
        (
            await session.execute(
                select(ConfirmationCase).where(ConfirmationCase.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )

    return WorkpaperDataContext(
        project=proj,
        account_balances=ab_df,
        confirmation_cases=list(cases),
    )


@router.post("/qa-apply", response_model=FillReport)
async def apply_qa(
    req: QARequest,
    current_user: User = Depends(get_current_user),
):
    """把人工问答合并到报告中。"""
    engine = ComprehensiveFillEngine()
    return await engine.apply_qa_answers(req.report, req.answers)


# ============================================================
# 历史底稿库
# ============================================================


@router.post("/historical")
async def ingest_historical(
    template_id: str = Form(...),
    industry: Optional[str] = Form(None),
    fiscal_year: Optional[int] = Form(None),
    uploaded_by: Optional[str] = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把一份历史综合底稿脱敏后入库。"""
    firm_id = str(current_user.firm_id)
    _safe_id(firm_id, "firm_id")
    _safe_id(template_id, "template_id")
    data = await _read_capped_xlsx(file)
    rec = await HistoricalLibraryService(session).ingest(
        firm_id=firm_id,
        template_id=template_id,
        workpaper_bytes=data,
        industry=industry,
        fiscal_year=fiscal_year,
        uploaded_by=uploaded_by,
    )
    return {"id": rec.id, "uploaded_at": rec.uploaded_at}


@router.get("/historical/search", response_model=HistoricalSearchResponse)
async def search_historical(
    template_id: str,
    q: str = Query(..., max_length=200, description="查询字符串"),
    top_k: int = Query(5, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """在历史底稿库中按关键词检索（供 WebSearchEngine 注入）。"""
    firm_id = str(current_user.firm_id) if current_user else ""
    _safe_id(firm_id, "firm_id")
    _safe_id(template_id, "template_id")
    hits = await HistoricalLibraryService(session).search(
        firm_id=firm_id,
        template_id=template_id,
        query=q,
        top_k=top_k,
    )
    return {"hits": [h.__dict__ for h in hits]}
