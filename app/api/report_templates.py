"""报告模板 API (Pack A — Roadmap Phase 20)."""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db.auth import (
    AUDIT_ACTION_CREATE,
    AUDIT_ACTION_DELETE,
    AUDIT_ACTION_EXPORT,
    AUDIT_ACTION_UPDATE,
    ROLE_MANAGER,
    User,
)
from app.models.db.report_template import (
    ALL_REPORT_TYPES,
    REPORT_FORMAT_DOCX,
    REPORT_FORMAT_XLSX,
)
from app.models.report_template import (
    ReportRenderRequest,
    ReportTemplateListResponse,
    ReportTemplateResponse,
    ReportTemplateUpdate,
    TemplateAnalyzeResponse,
)
from app.services.auth import (
    get_current_user,
    record_audit_log,
    require_role,
)
from app.services.report_template import (
    ReportTemplateService,
    analyze_template,
)
from app.utils.upload_safety import read_upload_capped

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/report-templates", tags=["报告模板"])


_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _parse_allowed_exts() -> set:
    return {
        x.strip().lower()
        for x in (settings.REPORT_TEMPLATE_ALLOWED_EXTS or "").split(",")
        if x.strip()
    }


@router.get("", response_model=ReportTemplateListResponse)
async def list_templates(
    firm_id: Optional[int] = None,
    report_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total, items = await ReportTemplateService.list_templates(
        db,
        firm_id=firm_id,
        report_type=report_type,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )
    return ReportTemplateListResponse(
        total=total,
        items=[ReportTemplateResponse.model_validate(t) for t in items],
    )


@router.post("", response_model=ReportTemplateResponse)
async def upload_template(
    template_code: str = Form(...),
    template_name: str = Form(...),
    report_type: str = Form(...),
    output_format: str = Form(REPORT_FORMAT_DOCX),
    version: str = Form("v1"),
    description: Optional[str] = Form(None),
    firm_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(ROLE_MANAGER)),
    db: AsyncSession = Depends(get_db),
):
    if not _SAFE_CODE.match(template_code):
        raise HTTPException(status_code=400, detail="template_code 仅支持字母/数字/_/./-")
    if report_type not in ALL_REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"report_type 必须是 {ALL_REPORT_TYPES}")
    if output_format not in {REPORT_FORMAT_DOCX, REPORT_FORMAT_XLSX}:
        raise HTTPException(status_code=400, detail="output_format 仅支持 docx/xlsx")

    allowed = _parse_allowed_exts()
    content, safe_name, suffix = await read_upload_capped(
        file, allowed_exts=allowed, max_bytes=settings.REPORT_TEMPLATE_MAX_SIZE
    )

    # 与 output_format 一致性校验
    if output_format == REPORT_FORMAT_DOCX and suffix not in {".docx", ".dotx"}:
        raise HTTPException(status_code=400, detail="output_format=docx 必须上传 .docx/.dotx")
    if output_format == REPORT_FORMAT_XLSX and suffix not in {".xlsx", ".xltx"}:
        raise HTTPException(status_code=400, detail="output_format=xlsx 必须上传 .xlsx/.xltx")

    tpl = await ReportTemplateService.create(
        db,
        template_code=template_code,
        template_name=template_name,
        report_type=report_type,
        output_format=output_format,
        template_bytes=content,
        template_filename=safe_name,
        version=version,
        description=description,
        firm_id=firm_id,
        created_by_user_id=current_user.id or None,
        created_by_display=current_user.full_name,
    )
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_CREATE,
        resource_type="report_template",
        resource_id=tpl.id,
        summary=f"上传报告模板 {tpl.template_code} v{tpl.version}",
        payload={
            "report_type": report_type,
            "format": output_format,
            "size": len(content),
        },
    )
    return ReportTemplateResponse.model_validate(tpl)


@router.get("/{template_id}", response_model=ReportTemplateResponse)
async def get_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tpl = await ReportTemplateService.get(db, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return ReportTemplateResponse.model_validate(tpl)


@router.put("/{template_id}", response_model=ReportTemplateResponse)
async def update_template(
    template_id: int,
    payload: ReportTemplateUpdate,
    current_user: User = Depends(require_role(ROLE_MANAGER)),
    db: AsyncSession = Depends(get_db),
):
    tpl = await ReportTemplateService.update(
        db,
        template_id=template_id,
        template_name=payload.template_name,
        description=payload.description,
        is_active=payload.is_active,
    )
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="report_template",
        resource_id=template_id,
        summary=f"修改报告模板 {tpl.template_code}",
        payload=payload.model_dump(exclude_unset=True),
    )
    return ReportTemplateResponse.model_validate(tpl)


@router.delete("/{template_id}")
async def delete_template(
    template_id: int,
    current_user: User = Depends(require_role(ROLE_MANAGER)),
    db: AsyncSession = Depends(get_db),
):
    ok = await ReportTemplateService.delete(db, template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="模板不存在")
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_DELETE,
        resource_type="report_template",
        resource_id=template_id,
        summary="删除报告模板",
    )
    return {"detail": "已删除"}


@router.get("/{template_id}/download")
async def download_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tpl = await ReportTemplateService.get(db, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return StreamingResponse(
        io.BytesIO(tpl.template_bytes),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{tpl.template_filename}"'},
    )


@router.get("/{template_id}/analyze", response_model=TemplateAnalyzeResponse)
async def analyze_template_api(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tpl = await ReportTemplateService.get(db, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    analysis = analyze_template(tpl.template_bytes, tpl.output_format)
    return TemplateAnalyzeResponse(
        placeholders=analysis.placeholders,
        duplicates=analysis.duplicates,
        unknown_tags=analysis.unknown_tags,
        is_valid=analysis.is_valid,
        suggested_context_keys=analysis.suggested_context_keys,
    )


@router.post("/render")
async def render_template(
    payload: ReportRenderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        rendered, out_name, history = await ReportTemplateService.render(
            db,
            template_id=payload.template_id,
            context=payload.context or {},
            project_id=payload.project_id,
            output_filename=payload.output_filename,
            user_id=current_user.id or None,
            user_display=current_user.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_EXPORT,
        resource_type="report_template",
        resource_id=payload.template_id,
        project_id=payload.project_id,
        summary=f"渲染报告 {out_name}",
        payload={"context_keys": list((payload.context or {}).keys())},
    )

    media = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if out_name.endswith(".docx")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return StreamingResponse(
        io.BytesIO(rendered),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )
