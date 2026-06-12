"""Inventory API routes (收发存 / 盘点 / 跌价).

All endpoints live under /api/inventory and operate on a project_id.

Routes:
  - 收发存 -----------------------------------------------------------
    POST   /projects/{pid}/movements                     上传收发存 Excel
    GET    /projects/{pid}/movements                     查询收发存
    DELETE /projects/{pid}/movements                     清空指定期间

  - 盘点用表 ---------------------------------------------------------
    POST   /projects/{pid}/count-sheets/generate         生成（金额优先+阈值覆盖）
    POST   /projects/{pid}/count-sheets/simulate         模拟不同阈值，预览覆盖率
    GET    /projects/{pid}/count-sheets                  查询盘点用表
    DELETE /projects/{pid}/count-sheets                  清空盘点用表
    PUT    /count-sheets/{sid}                           手工修改实盘数

  - 盘点计划 ---------------------------------------------------------
    POST   /projects/{pid}/count-plan                    生成行业化计划
    PUT    /count-plans/{plan_id}/revise                 与AI对话式修改
    GET    /projects/{pid}/count-plan                    查询当前计划

  - 盘点照片 ---------------------------------------------------------
    POST   /projects/{pid}/count-photos                  上传照片 → OCR + 回填
    GET    /projects/{pid}/count-completion              盘点率 + 差异统计

  - 库龄 / 跌价 / 转回 -----------------------------------------------
    POST   /projects/{pid}/impairments/compute           算库龄 + 跌价 + 转回
    GET    /projects/{pid}/impairments                   查询结果
    POST   /projects/{pid}/impairments/prior             上传上年期末已计提跌价

  - 导出 -------------------------------------------------------------
    GET    /projects/{pid}/export                        一键导出整个工作簿
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import (
    InventoryCodeMapping,
    InventoryCountPhoto,
    InventoryCountPlan,
    InventoryCountSheet,
    InventoryImpairment,
    InventoryMovement,
    Project,
    SalesRecord,
)
from app.models.inventory import (
    CodeMappingItem,
    CodeMappingResponse,
    CodeMappingUploadRequest,
    CompletionStatsResponse,
    CountPhotoUploadResponse,
    CountPlanGenerateRequest,
    CountPlanResponse,
    CountPlanReviseRequest,
    CountSheetGenerateRequest,
    CountSheetGenerateResponse,
    CountSheetRowResponse,
    CountSheetSimulateRequest,
    ImpairmentComputeRequest,
    ImpairmentComputeResponse,
    ImpairmentRowResponse,
    InventoryImportResponse,
    InventoryMovementResponse,
    PriorImpairmentUpload,
)
from app.services.inventory import (
    CountPhotoProcessor,
    CountPlanGenerator,
    CountSheetBuilder,
    CountSheetStrategy,
    InventoryAgingEngine,
    InventoryExporter,
    InventoryImporter,
    InventoryImportError,
)
from app.services.sales_ledger.deepseek_client import DeepSeekClient
from app.utils.upload_safety import (
    read_upload_capped,
    unique_save_path,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inventory", tags=["收发存盘点&减值"])


# ---- helpers -----------------------------------------------------------


def _deepseek_client() -> DeepSeekClient:
    return DeepSeekClient(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_API_BASE,
        model=settings.DEEPSEEK_MODEL,
    )


async def _get_project_or_404(db: AsyncSession, project_id: int) -> Project:
    res = await db.execute(select(Project).where(Project.id == project_id))
    proj = res.scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
    return proj


def _default_period_end(proj: Project) -> date:
    return date(proj.fiscal_year, 12, 31)


# ============================================================
# 收发存导入
# ============================================================


@router.post(
    "/projects/{project_id}/movements",
    response_model=InventoryImportResponse,
)
async def upload_movements(
    project_id: int,
    file: UploadFile = File(...),
    period_end: Optional[date] = Query(None, description="报告期截止日；默认取项目 fiscal_year 的 12-31"),
    is_prior_year: bool = Query(False, description="是否为上年同期数据（用于跌价转回）"),
    replace: bool = Query(True, description="导入前是否清空相同期间数据"),
    db: AsyncSession = Depends(get_db),
):
    """上传收发存 Excel/CSV。自动识别金蝶/用友/SAP/手工模板。"""
    proj = await _get_project_or_404(db, project_id)
    pe = period_end or _default_period_end(proj)
    pe_str = pe.isoformat()

    # 校验文件大小、后缀白名单、文件名净化
    try:
        content, safe_name, _suffix = await read_upload_capped(
            file, allowed_exts={".xlsx", ".xls", ".csv"},
        )
        df = InventoryImporter.parse_bytes(content, safe_name)
    except InventoryImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if replace:
        await db.execute(
            delete(InventoryMovement).where(
                InventoryMovement.project_id == project_id,
                InventoryMovement.period_end == pe_str,
                InventoryMovement.is_prior_year == is_prior_year,
            )
        )

    inserted = 0
    total_ending = 0.0
    for _, row in df.iterrows():
        m = InventoryMovement(
            project_id=project_id,
            material_code=str(row["material_code"]).strip(),
            material_name=str(row["material_name"]).strip(),
            category=str(row.get("category", "")) or None,
            spec=str(row.get("spec", "")) or None,
            unit=str(row.get("unit", "")) or None,
            warehouse=str(row.get("warehouse", "")) or None,
            batch_no=str(row.get("batch_no", "")) or None,
            inbound_date=row.get("inbound_date") if row.get("inbound_date") is not None else None,
            period_end=pe_str,
            is_prior_year=is_prior_year,
            opening_qty=float(row.get("opening_qty", 0) or 0),
            opening_amount=float(row.get("opening_amount", 0) or 0),
            inbound_qty=float(row.get("inbound_qty", 0) or 0),
            inbound_amount=float(row.get("inbound_amount", 0) or 0),
            outbound_qty=float(row.get("outbound_qty", 0) or 0),
            outbound_amount=float(row.get("outbound_amount", 0) or 0),
            ending_qty=float(row.get("ending_qty", 0) or 0),
            ending_amount=float(row.get("ending_amount", 0) or 0),
            unit_cost=float(row.get("unit_cost", 0) or 0),
            source=file.filename,
        )
        db.add(m)
        inserted += 1
        total_ending += m.ending_amount
    await db.commit()
    return InventoryImportResponse(
        project_id=project_id,
        period_end=pe_str,
        is_prior_year=is_prior_year,
        imported_count=inserted,
        total_ending_amount=round(total_ending, 2),
    )


@router.get(
    "/projects/{project_id}/movements",
    response_model=list[InventoryMovementResponse],
)
async def list_movements(
    project_id: int,
    period_end: Optional[date] = Query(None),
    is_prior_year: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    q = select(InventoryMovement).where(InventoryMovement.project_id == project_id)
    if period_end:
        q = q.where(InventoryMovement.period_end == period_end.isoformat())
    if is_prior_year is not None:
        q = q.where(InventoryMovement.is_prior_year == is_prior_year)
    res = await db.execute(q.order_by(InventoryMovement.ending_amount.desc()))
    return list(res.scalars().all())


@router.delete("/projects/{project_id}/movements")
async def clear_movements(
    project_id: int,
    period_end: Optional[date] = Query(None, description="不填则清空全部期间"),
    is_prior_year: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    stmt = delete(InventoryMovement).where(InventoryMovement.project_id == project_id)
    if period_end:
        stmt = stmt.where(InventoryMovement.period_end == period_end.isoformat())
    if is_prior_year is not None:
        stmt = stmt.where(InventoryMovement.is_prior_year == is_prior_year)
    res = await db.execute(stmt)
    await db.commit()
    return {"deleted": res.rowcount or 0}


# ============================================================
# 盘点用表
# ============================================================


async def _fetch_period_movements(
    db: AsyncSession, project_id: int, period_end: date
) -> list[InventoryMovement]:
    res = await db.execute(
        select(InventoryMovement).where(
            InventoryMovement.project_id == project_id,
            InventoryMovement.period_end == period_end.isoformat(),
            InventoryMovement.is_prior_year == False,  # noqa: E712
        )
    )
    return list(res.scalars().all())


@router.post(
    "/projects/{project_id}/count-sheets/generate",
    response_model=CountSheetGenerateResponse,
)
async def generate_count_sheet(
    project_id: int,
    req: CountSheetGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """生成盘点用表（金额优先 + 阈值覆盖）。"""
    proj = await _get_project_or_404(db, project_id)
    pe = req.period_end or _default_period_end(proj)
    movements = await _fetch_period_movements(db, project_id, pe)
    if not movements:
        raise HTTPException(
            status_code=400,
            detail=f"项目下没有 {pe.isoformat()} 的收发存数据。请先上传。",
        )

    strategy = CountSheetStrategy(
        coverage_threshold=req.coverage_threshold,
        b_sample_ratio=req.b_sample_ratio,
        c_sample_ratio=req.c_sample_ratio,
        high_value_warehouses=req.high_value_warehouses,
        must_include_categories=req.must_include_categories,
        must_include_codes=req.must_include_codes,
        min_unit_amount=req.min_unit_amount,
        random_seed=req.random_seed,
        materiality=req.materiality,
        b_sample_method=req.b_sample_method,
        reverse_sample_ratio=req.reverse_sample_ratio,
    )
    result = CountSheetBuilder.build(movements, strategy)

    saved_rows: list[InventoryCountSheet] = []
    if req.persist:
        # 保护已盘点数据：除非 force_overwrite_counted=True，否则只删除"未盘点"的旧行；
        # 已盘点（counted_qty IS NOT NULL）的行保留，并在重新生成时跳过同 material_code 的新行
        stmt_del = delete(InventoryCountSheet).where(InventoryCountSheet.project_id == project_id)
        if req.plan_id is not None:
            stmt_del = stmt_del.where(InventoryCountSheet.plan_id == req.plan_id)
        if not req.force_overwrite_counted:
            stmt_del = stmt_del.where(InventoryCountSheet.counted_qty.is_(None))
        await db.execute(stmt_del)

        # 拉取仍保留的（已盘）行，以便在 add 时跳过
        keep_q = select(InventoryCountSheet.material_code, InventoryCountSheet.warehouse, InventoryCountSheet.batch_no).where(
            InventoryCountSheet.project_id == project_id,
            InventoryCountSheet.counted_qty.is_not(None),
        )
        if req.plan_id is not None:
            keep_q = keep_q.where(InventoryCountSheet.plan_id == req.plan_id)
        existing_keys = {
            (str(r[0] or ""), str(r[1] or ""), str(r[2] or ""))
            for r in (await db.execute(keep_q)).all()
        }

        skipped = 0
        for row in result.rows:
            key = (str(row.get("material_code", "")), str(row.get("warehouse", "")), str(row.get("batch_no", "")))
            if key in existing_keys:
                skipped += 1
                continue
            s = InventoryCountSheet(
                project_id=project_id,
                plan_id=req.plan_id,
                **row,
            )
            db.add(s)
            saved_rows.append(s)
        await db.commit()
        if skipped:
            logger.info("generate_count_sheet: 保留了 %d 行已盘点数据，未覆盖", skipped)
        for s in saved_rows:
            await db.refresh(s)
        # 返回时把"已盘点保留"的行也带回去
        if existing_keys:
            kept_res = await db.execute(
                select(InventoryCountSheet).where(
                    InventoryCountSheet.project_id == project_id,
                    InventoryCountSheet.counted_qty.is_not(None),
                ).order_by(InventoryCountSheet.sample_tier, InventoryCountSheet.coverage_rank)
            )
            saved_rows = list(kept_res.scalars().all()) + saved_rows
        row_resp = [CountSheetRowResponse.model_validate(s) for s in saved_rows]
    else:
        # 预览模式：返回伪 id=0 的行
        row_resp = [
            CountSheetRowResponse(
                id=0, project_id=project_id, plan_id=req.plan_id,
                counted_qty=None, counted_at=None, counted_by=None, remark=None,
                **row,
            )
            for row in result.rows
        ]

    return CountSheetGenerateResponse(
        project_id=project_id,
        total_amount=result.total_amount,
        covered_amount=result.covered_amount,
        coverage_ratio=result.coverage_ratio,
        total_items=result.total_items,
        selected_items=result.selected_items,
        tier_summary=result.tier_summary,
        strategy_desc=result.strategy.describe(),
        rows=row_resp,
    )


@router.post("/projects/{project_id}/count-sheets/simulate")
async def simulate_count_sheet(
    project_id: int,
    req: CountSheetSimulateRequest,
    db: AsyncSession = Depends(get_db),
):
    """对多档阈值做平行测算，方便用户在前端拉滑条选择。"""
    proj = await _get_project_or_404(db, project_id)
    pe = req.period_end or _default_period_end(proj)
    movements = await _fetch_period_movements(db, project_id, pe)
    if not movements:
        raise HTTPException(status_code=400, detail=f"项目下没有 {pe.isoformat()} 的收发存数据。")

    strategies = [
        CountSheetStrategy(
            coverage_threshold=t,
            b_sample_ratio=req.b_sample_ratio,
            c_sample_ratio=req.c_sample_ratio,
        )
        for t in req.thresholds
    ]
    rows = CountSheetBuilder.simulate(movements, strategies)
    return {"project_id": project_id, "scenarios": rows}


@router.get(
    "/projects/{project_id}/count-sheets",
    response_model=list[CountSheetRowResponse],
)
async def list_count_sheets(
    project_id: int,
    plan_id: Optional[int] = Query(None),
    only_unchecked: bool = Query(False, description="仅返回未盘点的"),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    q = select(InventoryCountSheet).where(InventoryCountSheet.project_id == project_id)
    if plan_id is not None:
        q = q.where(InventoryCountSheet.plan_id == plan_id)
    if only_unchecked:
        q = q.where(InventoryCountSheet.counted_qty.is_(None))
    q = q.order_by(InventoryCountSheet.sample_tier, InventoryCountSheet.coverage_rank)
    res = await db.execute(q)
    return list(res.scalars().all())


@router.delete("/projects/{project_id}/count-sheets")
async def clear_count_sheets(
    project_id: int,
    plan_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    stmt = delete(InventoryCountSheet).where(InventoryCountSheet.project_id == project_id)
    if plan_id is not None:
        stmt = stmt.where(InventoryCountSheet.plan_id == plan_id)
    res = await db.execute(stmt)
    await db.commit()
    return {"deleted": res.rowcount or 0}


@router.put("/count-sheets/{sheet_id}", response_model=CountSheetRowResponse)
async def update_count_sheet(
    sheet_id: int,
    counted_qty: Optional[float] = Query(None),
    counted_by: Optional[str] = Query(None),
    remark: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    import math as _math
    if counted_qty is not None and (not _math.isfinite(counted_qty) or counted_qty < 0):
        raise HTTPException(422, "counted_qty 必须为非负有限数值")
    res = await db.execute(select(InventoryCountSheet).where(InventoryCountSheet.id == sheet_id))
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="盘点行不存在")
    if counted_qty is not None:
        s.counted_qty = counted_qty
        # DateTime 列是 naive — 去掉 tz 以兼容 SQLite/PG
        s.counted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if counted_by is not None:
        s.counted_by = counted_by[:100]
    if remark is not None:
        s.remark = remark[:500]
    await db.commit()
    await db.refresh(s)
    return s


# ============================================================
# 盘点计划
# ============================================================


@router.post(
    "/projects/{project_id}/count-plan",
    response_model=CountPlanResponse,
)
async def generate_count_plan(
    project_id: int,
    req: CountPlanGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    proj = await _get_project_or_404(db, project_id)
    pe = req.period_end or _default_period_end(proj)
    industry = (req.industry or proj.industry or "").strip()

    gen = CountPlanGenerator()  # baseline 不需要 AI
    draft = gen.baseline(
        company_name=proj.company_name,
        industry=industry,
        period_end=pe,
        count_days_before=req.count_days_before,
        count_days_after=req.count_days_after,
        team=req.team or None,
    )

    # 同项目 + 同基准日已有则覆盖
    res = await db.execute(
        select(InventoryCountPlan).where(
            InventoryCountPlan.project_id == project_id,
            InventoryCountPlan.period_end == pe.isoformat(),
        )
    )
    existing = res.scalar_one_or_none()
    if existing:
        for k, v in draft.to_db_kwargs().items():
            setattr(existing, k, v)
        plan = existing
    else:
        plan = InventoryCountPlan(project_id=project_id, **draft.to_db_kwargs())
        db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.put(
    "/count-plans/{plan_id}/revise",
    response_model=CountPlanResponse,
)
async def revise_count_plan(
    plan_id: int,
    req: CountPlanReviseRequest,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(InventoryCountPlan).where(InventoryCountPlan.id == plan_id))
    plan = res.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="盘点计划不存在")

    # 解开 DB 字段 → CountPlanDraft
    from app.services.inventory.count_plan import CountPlanDraft
    try:
        team = json.loads(plan.team or "[]")
    except json.JSONDecodeError:
        team = []
    try:
        revisions = json.loads(plan.revision_log or "[]")
    except json.JSONDecodeError:
        revisions = []
    draft = CountPlanDraft(
        title=plan.title,
        industry=plan.industry or "",
        period_end=plan.period_end,
        count_date_start=plan.count_date_start or "",
        count_date_end=plan.count_date_end or "",
        objectives=plan.objectives or "",
        scope=plan.scope or "",
        team=team,
        procedures=plan.procedures or "",
        special_notes=plan.special_notes or "",
        risks=plan.risks or "",
        revision_log=revisions,
    )

    # 取项目名（仅给 AI 用）
    proj_res = await db.execute(select(Project).where(Project.id == plan.project_id))
    proj = proj_res.scalar_one_or_none()
    company = proj.company_name if proj else ""

    gen = CountPlanGenerator(client=_deepseek_client())
    new_draft = await gen.revise(draft, req.instruction, company_name=company)

    for k, v in new_draft.to_db_kwargs().items():
        setattr(plan, k, v)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.get(
    "/projects/{project_id}/count-plan",
    response_model=Optional[CountPlanResponse],
)
async def get_count_plan(
    project_id: int,
    period_end: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    proj = await _get_project_or_404(db, project_id)
    pe = period_end or _default_period_end(proj)
    res = await db.execute(
        select(InventoryCountPlan).where(
            InventoryCountPlan.project_id == project_id,
            InventoryCountPlan.period_end == pe.isoformat(),
        )
    )
    return res.scalar_one_or_none()


# ============================================================
# 盘点照片 → OCR → 回填
# ============================================================


@router.post(
    "/projects/{project_id}/count-photos",
    response_model=CountPhotoUploadResponse,
)
async def upload_count_photo(
    project_id: int,
    file: UploadFile = File(...),
    plan_id: Optional[int] = Query(None),
    counted_by: Optional[str] = Query(None, description="盘点人；若 OCR 已识别则可不填"),
    note: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """上传盘点照片，OCR 后 AI 解析实盘数量并回填到盘点用表。"""
    await _get_project_or_404(db, project_id)

    settings.ensure_dirs()
    target_dir = settings.UPLOAD_DIR / f"inventory_photos/project_{project_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    # 大小限制 + 后缀白名单（防 SVG/JS、防伪装、防穿越）+ 唯一文件名（含 UUID）
    content, safe_name, suffix = await read_upload_capped(
        file,
        allowed_exts={".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp", ".pdf"},
    )
    target = unique_save_path(target_dir, safe_name)
    with open(target, "wb") as f:
        f.write(content)

    media_type = (
        "application/pdf" if suffix == ".pdf"
        else f"image/{suffix.lstrip('.') or 'jpeg'}"
    )

    processor = CountPhotoProcessor(client=_deepseek_client())

    # OCR — 失败时**不允许**静默吞掉，必须 422 让用户重传清晰图
    try:
        engine, ocr_text = processor.ocr(str(target), safe_name)
    except Exception as exc:  # OCRError or unexpected
        logger.warning("OCR 失败 (photo) project=%s file=%s: %s", project_id, safe_name, exc)
        # 删除已写入的文件，避免 orphan
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            status_code=422,
            detail=f"OCR 失败，无法识别该盘点照片：{exc}。请重新拍摄更清晰、对焦正确的照片，或先安装 OCR 引擎 (paddleocr 推荐)。",
        ) from exc

    # AI parse — 用现有 sheet 集合约束 material_code 白名单（防 prompt injection）
    q = select(InventoryCountSheet).where(InventoryCountSheet.project_id == project_id)
    if plan_id is not None:
        q = q.where(InventoryCountSheet.plan_id == plan_id)
    sheets = list((await db.execute(q)).scalars().all())
    known_codes = {str(getattr(s, "material_code", "") or "").strip().lower() for s in sheets}

    parse = await processor.parse_text(ocr_text, known_codes=known_codes)

    matched, unmatched = processor.match_to_sheets(parse.parsed_rows, sheets)

    # 回填 — DateTime 列是 naive，去掉 tz 避免 PG/SQLite 不一致
    counted_at_aware = parse.counted_at or datetime.now(timezone.utc)
    counted_at = counted_at_aware.replace(tzinfo=None) if counted_at_aware.tzinfo else counted_at_aware
    by = (counted_by or parse.counted_by or "").strip() or None
    for sheet, row in matched:
        sheet.counted_qty = float(row.counted_qty)
        sheet.counted_at = counted_at
        if by:
            sheet.counted_by = by
        if row.remark:
            sheet.remark = (sheet.remark or "") + ("|" if sheet.remark else "") + row.remark

    # 保存照片记录
    photo = InventoryCountPhoto(
        project_id=project_id,
        plan_id=plan_id,
        filename=safe_name,
        media_type=media_type,
        file_path=str(target),
        ocr_engine=engine,
        ocr_text=ocr_text,
        parsed_rows=json.dumps(
            [vars(r) for r in parse.parsed_rows], ensure_ascii=False, default=str
        ),
        matched_count=len(matched),
        unmatched_count=len(unmatched),
        counted_by=by,
        counted_at=counted_at,
        note=note,
        processed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)

    return CountPhotoUploadResponse(
        photo_id=photo.id,
        project_id=project_id,
        ocr_engine=engine,
        parsed_row_count=len(parse.parsed_rows),
        matched_count=len(matched),
        unmatched_count=len(unmatched),
        counted_by=by,
        counted_at=counted_at,
        unmatched_rows=[vars(u) for u in unmatched],
    )


@router.get(
    "/projects/{project_id}/count-completion",
    response_model=CompletionStatsResponse,
)
async def count_completion(
    project_id: int,
    plan_id: Optional[int] = Query(None),
    materiality: float = Query(0.0, ge=0.0, description="重要性水平金额；用于把差异分'超过/未超过'两组"),
    period_end: Optional[date] = Query(None, description="不填则用项目当年 12-31，用于拉应盘存货统计应盘未盘"),
    db: AsyncSession = Depends(get_db),
):
    proj = await _get_project_or_404(db, project_id)
    pe = period_end or _default_period_end(proj)
    q = select(InventoryCountSheet).where(InventoryCountSheet.project_id == project_id)
    if plan_id is not None:
        q = q.where(InventoryCountSheet.plan_id == plan_id)
    sheets = list((await db.execute(q)).scalars().all())

    # 应盘未盘：从本期收发存里取所有期末有金额的物料作为"应盘总体"
    population = await _fetch_period_movements(db, project_id, pe)

    stats = CountPhotoProcessor.completion_stats(
        sheets,
        materiality=materiality,
        population_movements=population,
    )
    return stats


# ============================================================
# 库龄 / 跌价 / 转回
# ============================================================


@router.post(
    "/projects/{project_id}/impairments/compute",
    response_model=ImpairmentComputeResponse,
)
async def compute_impairments(
    project_id: int,
    req: ImpairmentComputeRequest,
    db: AsyncSession = Depends(get_db),
):
    proj = await _get_project_or_404(db, project_id)
    pe = req.period_end or _default_period_end(proj)

    movements = await _fetch_period_movements(db, project_id, pe)
    if not movements:
        raise HTTPException(status_code=400, detail=f"项目下没有 {pe.isoformat()} 的本期收发存。")

    sales: list[SalesRecord] = []
    if req.use_sales_for_nrv:
        sales_res = await db.execute(
            select(SalesRecord).where(SalesRecord.project_id == project_id)
        )
        sales = list(sales_res.scalars().all())

    # 上年期初已计提（如果用户没单独上传，从 InventoryImpairment 表里找上年的）
    prior_map: dict[str, float] = {}
    prior_qty_map: dict[str, float] = {}
    if req.include_reversal:
        # 物料编码跨年映射：上年 old_code → 本年 new_code
        mapping_res = await db.execute(
            select(InventoryCodeMapping).where(InventoryCodeMapping.project_id == project_id)
        )
        code_map = {m.old_code: m.new_code for m in mapping_res.scalars().all()}

        prior_res = await db.execute(
            select(InventoryImpairment).where(
                InventoryImpairment.project_id == project_id,
                # 上年 period_end ≠ 本期
                InventoryImpairment.period_end != pe.isoformat(),
            )
        )
        # 对同物料，按 period_end 字符串字典序（YYYY-MM-DD 可比）取 < 本期的最大值
        prior_period: dict[str, str] = {}
        pe_iso = pe.isoformat()
        for r in prior_res.scalars().all():
            if r.period_end >= pe_iso:
                continue
            # 把上年编码翻译为本年编码（找不到映射则原样保留）
            code_key = code_map.get(r.material_code, r.material_code)
            prev = prior_period.get(code_key)
            if prev is None or r.period_end > prev:
                prior_map[code_key] = float(r.impairment_current or 0)
                prior_qty_map[code_key] = float(r.ending_qty or 0)
                prior_period[code_key] = r.period_end

    industry = proj.industry or "默认"
    # 若用户未显式指定 sell_cost_rate，按行业自动选默认（化工/机械等高费率行业避免 5% 一刀切）
    if req.sell_cost_rate is None:
        from app.services.inventory.aging_engine import sell_cost_rate_for
        sc_rate = sell_cost_rate_for(industry)
    else:
        sc_rate = req.sell_cost_rate
    engine = InventoryAgingEngine(
        industry=industry,
        sell_cost_rate=sc_rate,
        completion_cost_rate=req.completion_cost_rate,
    )
    result = engine.compute(
        movements,
        datetime(pe.year, pe.month, pe.day),
        sales_records=sales,
        prior_impairments=prior_map,
        prior_qty=prior_qty_map,
        manual_nrv=req.manual_nrv,
    )

    if req.persist:
        # 清空相同 period_end 的旧记录
        await db.execute(
            delete(InventoryImpairment).where(
                InventoryImpairment.project_id == project_id,
                InventoryImpairment.period_end == pe.isoformat(),
            )
        )
        for row in result.rows:
            db.add(InventoryImpairment(project_id=project_id, **row.to_db_kwargs()))
        await db.commit()

    return ImpairmentComputeResponse(
        project_id=project_id,
        summary=result.summary,
        rows=[ImpairmentRowResponse(**r.to_db_kwargs()) for r in result.rows],
    )


@router.get(
    "/projects/{project_id}/impairments",
    response_model=list[ImpairmentRowResponse],
)
async def list_impairments(
    project_id: int,
    period_end: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    q = select(InventoryImpairment).where(InventoryImpairment.project_id == project_id)
    if period_end:
        q = q.where(InventoryImpairment.period_end == period_end.isoformat())
    res = await db.execute(q.order_by(InventoryImpairment.impairment_current.desc()))
    rows = list(res.scalars().all())
    return [
        ImpairmentRowResponse(
            material_code=r.material_code,
            material_name=r.material_name,
            category=r.category,
            period_end=r.period_end,
            ending_qty=r.ending_qty,
            book_unit_cost=r.book_unit_cost,
            book_amount=r.book_amount,
            age_le_90=r.age_le_90,
            age_91_180=r.age_91_180,
            age_181_365=r.age_181_365,
            age_366_730=r.age_366_730,
            age_gt_730=r.age_gt_730,
            weighted_avg_age=r.weighted_avg_age,
            nrv_unit_price=r.nrv_unit_price,
            nrv_source=r.nrv_source,
            nrv_amount=r.nrv_amount,
            estimated_sell_cost=r.estimated_sell_cost,
            impairment_current=r.impairment_current,
            impairment_opening=r.impairment_opening,
            impairment_reversal=r.impairment_reversal,
            impairment_provision=r.impairment_provision,
            net_impairment_change=r.net_impairment_change,
            method=r.method,
            note=r.note,
            reversal_to_cogs=float(getattr(r, "reversal_to_cogs", 0) or 0),
            reversal_to_loss=float(getattr(r, "reversal_to_loss", 0) or 0),
        )
        for r in rows
    ]


@router.post("/projects/{project_id}/impairments/prior")
async def upload_prior_impairments(
    project_id: int,
    payload: PriorImpairmentUpload,
    period_end: date = Query(..., description="上年期末日，如 2023-12-31"),
    db: AsyncSession = Depends(get_db),
):
    """上传上年期末已计提跌价（仅用于跌价转回计算）。

    每个 material_code → 已计提金额。
    """
    await _get_project_or_404(db, project_id)
    pe_str = period_end.isoformat()
    # 清空相同期间
    await db.execute(
        delete(InventoryImpairment).where(
            InventoryImpairment.project_id == project_id,
            InventoryImpairment.period_end == pe_str,
        )
    )
    saved = 0
    for code, amount in payload.items.items():
        db.add(InventoryImpairment(
            project_id=project_id,
            material_code=str(code),
            material_name="(上年已计提)",
            period_end=pe_str,
            impairment_current=float(amount or 0),
            impairment_opening=float(amount or 0),
            method="prior_upload",
        ))
        saved += 1
    await db.commit()
    return {"project_id": project_id, "saved": saved, "period_end": pe_str}


# ============================================================
# 物料编码跨年映射 (旧编码 → 新编码)
# ============================================================


@router.post(
    "/projects/{project_id}/code-mappings",
    response_model=list[CodeMappingResponse],
)
async def upload_code_mappings(
    project_id: int,
    req: CodeMappingUploadRequest,
    db: AsyncSession = Depends(get_db),
):
    """上传物料编码跨年映射（旧编码 → 新编码）。

    在跌价转回计算时，会自动把上年的 InventoryImpairment.material_code（旧编码）
    翻译为本年的新编码后再去匹配本年期末数据。这样物料编码变更后，
    上年期末跌价不会"凭空消失"。
    """
    await _get_project_or_404(db, project_id)
    if req.replace:
        await db.execute(
            delete(InventoryCodeMapping).where(InventoryCodeMapping.project_id == project_id)
        )

    saved: list[InventoryCodeMapping] = []
    seen_old: set[str] = set()
    for it in req.items:
        # 避免一次上传里同 old_code 重复
        if it.old_code in seen_old:
            continue
        seen_old.add(it.old_code)
        m = InventoryCodeMapping(
            project_id=project_id,
            old_code=it.old_code,
            new_code=it.new_code,
            note=it.note,
        )
        db.add(m)
        saved.append(m)
    await db.commit()
    for m in saved:
        await db.refresh(m)
    return saved


@router.get(
    "/projects/{project_id}/code-mappings",
    response_model=list[CodeMappingResponse],
)
async def list_code_mappings(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    res = await db.execute(
        select(InventoryCodeMapping).where(InventoryCodeMapping.project_id == project_id)
        .order_by(InventoryCodeMapping.old_code)
    )
    return list(res.scalars().all())


@router.delete("/projects/{project_id}/code-mappings")
async def clear_code_mappings(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    res = await db.execute(
        delete(InventoryCodeMapping).where(InventoryCodeMapping.project_id == project_id)
    )
    await db.commit()
    return {"deleted": res.rowcount or 0}


# ============================================================
# 一键导出
# ============================================================


@router.get("/projects/{project_id}/export")
async def export_inventory(
    project_id: int,
    period_end: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    proj = await _get_project_or_404(db, project_id)
    pe = period_end or _default_period_end(proj)

    movements = (await db.execute(
        select(InventoryMovement).where(
            InventoryMovement.project_id == project_id,
            InventoryMovement.period_end == pe.isoformat(),
            InventoryMovement.is_prior_year == False,  # noqa: E712
        )
    )).scalars().all()

    count_sheets = (await db.execute(
        select(InventoryCountSheet).where(InventoryCountSheet.project_id == project_id)
        .order_by(InventoryCountSheet.sample_tier, InventoryCountSheet.coverage_rank)
    )).scalars().all()

    plan = (await db.execute(
        select(InventoryCountPlan).where(
            InventoryCountPlan.project_id == project_id,
            InventoryCountPlan.period_end == pe.isoformat(),
        )
    )).scalar_one_or_none()

    completion = CountPhotoProcessor.completion_stats(count_sheets) if count_sheets else None

    impairments = (await db.execute(
        select(InventoryImpairment).where(
            InventoryImpairment.project_id == project_id,
            InventoryImpairment.period_end == pe.isoformat(),
        ).order_by(InventoryImpairment.impairment_current.desc())
    )).scalars().all()

    summary = None
    if impairments:
        summary = {
            "items": len(impairments),
            "book_amount": round(sum(r.book_amount for r in impairments), 2),
            "ending_impairment": round(sum(r.impairment_current for r in impairments), 2),
            "current_provision": round(sum(r.impairment_provision for r in impairments), 2),
            "current_reversal": round(sum(r.impairment_reversal for r in impairments), 2),
            "net_change": round(sum(r.net_impairment_change for r in impairments), 2),
        }

    blob = InventoryExporter.build(
        movements=list(movements),
        count_sheets=list(count_sheets),
        plan=plan,
        completion=completion,
        impairments=list(impairments),
        summary=summary,
    )
    filename = f"inventory_project_{project_id}_{pe.isoformat()}.xlsx"
    return StreamingResponse(
        io.BytesIO(blob),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
