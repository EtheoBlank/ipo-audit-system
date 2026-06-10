"""Sales-ledger API routes.

Endpoints (all under /api/sales-ledger via app.main):
  POST   /projects/{id}/sales-documents             upload + parse a document
  GET    /projects/{id}/sales-documents             list uploaded documents
  DELETE /sales-documents/{doc_id}                  delete a document (cascade)
  POST   /projects/{id}/sales-records/synthesize    run DeepSeek synthesis
  GET    /projects/{id}/sales-records               list sales records
  PUT    /sales-records/{rid}                       human-corrected edit
  DELETE /sales-records/{rid}                       remove a row
  POST   /projects/{id}/revenue-analysis            run revenue analysis
  GET    /projects/{id}/export                      download Excel workbook
"""

from __future__ import annotations

import io
import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import Project, SalesDocument, SalesRecord
from app.models.sales_ledger import (
    AnalysisRequest,
    AnalysisResponse,
    SalesDocumentResponse,
    SalesRecordCreate,
    SalesRecordResponse,
    SalesRecordUpdate,
    SynthesisRequest,
    SynthesisResponse,
)
from app.services.sales_ledger import (
    DeepSeekClient,
    DocumentParser,
    DocumentParserError,
    RevenueAnalyzer,
    SalesLedgerExporter,
    SalesLedgerSynthesizer,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sales-ledger", tags=["销售清单"])


# ---------- helpers ------------------------------------------------------


def _deepseek_client() -> DeepSeekClient:
    return DeepSeekClient(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_API_BASE,
        model=settings.DEEPSEEK_MODEL,
    )


async def _get_project_or_404(db: AsyncSession, project_id: int) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _record_to_response(r: SalesRecord) -> SalesRecordResponse:
    revenue = float(r.revenue_amount or 0)
    cost = float(r.cost_amount or 0)
    direct = float(r.shipping_fee or 0) + float(r.customs_fee or 0) + float(r.other_direct_fee or 0)
    profit = revenue - cost - direct
    return SalesRecordResponse(
        id=r.id,
        project_id=r.project_id,
        document_id=r.document_id,
        contract_no=r.contract_no or "",
        customer_name=r.customer_name or "",
        product_code=r.product_code or "",
        product_name=r.product_name or "",
        invoice_no=r.invoice_no,
        currency=r.currency or "CNY",
        tax_rate=float(r.tax_rate or 0),
        tax_amount=float(r.tax_amount or 0),
        gross_amount=float(r.gross_amount or 0),
        quantity=float(r.quantity or 0),
        unit_price=float(r.unit_price or 0),
        revenue_amount=revenue,
        cost_amount=cost,
        shipping_fee=float(r.shipping_fee or 0),
        customs_fee=float(r.customs_fee or 0),
        other_direct_fee=float(r.other_direct_fee or 0),
        return_amount=float(r.return_amount or 0),
        discount_amount=float(r.discount_amount or 0),
        rebate_amount=float(r.rebate_amount or 0),
        ship_date=r.ship_date,
        receipt_date=r.receipt_date,
        revenue_confirm_date=r.revenue_confirm_date,
        confirmation_status=r.confirmation_status or "未发函",
        confirmation_ref=r.confirmation_ref,
        confirmation_diff=float(r.confirmation_diff or 0),
        source=r.source,
        confidence=float(r.confidence or 1.0),
        is_verified=bool(r.is_verified),
        created_at=r.created_at,
        updated_at=r.updated_at,
        gross_profit=round(profit, 2),
        gross_margin=round((profit / revenue) if revenue else 0.0, 4),
    )


# ---------- document upload ---------------------------------------------


@router.post(
    "/projects/{project_id}/sales-documents",
    response_model=SalesDocumentResponse,
)
async def upload_sales_document(
    project_id: int,
    file: UploadFile = File(...),
    note: Optional[str] = Query(None, description="可选备注"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a Word/PDF/Excel source document and store its parsed text.

    The parsed text is kept in the `raw_text` column so the synthesizer can
    re-run on it without re-parsing the file.
    """
    await _get_project_or_404(db, project_id)

    try:
        doc_type, raw_text = await DocumentParser.parse(file, settings.UPLOAD_DIR)
    except DocumentParserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    doc = SalesDocument(
        project_id=project_id,
        filename=file.filename or "unknown",
        doc_type=doc_type,
        raw_text=raw_text,
        note=note,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@router.get(
    "/projects/{project_id}/sales-documents",
    response_model=List[SalesDocumentResponse],
)
async def list_sales_documents(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    result = await db.execute(
        select(SalesDocument)
        .where(SalesDocument.project_id == project_id)
        .order_by(SalesDocument.uploaded_at.desc())
    )
    return result.scalars().all()


@router.delete("/sales-documents/{doc_id}")
async def delete_sales_document(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SalesDocument).where(SalesDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await db.delete(doc)
    await db.commit()
    return {"message": "已删除"}


# ---------- synthesis ---------------------------------------------------


@router.post(
    "/projects/{project_id}/sales-records/synthesize",
    response_model=SynthesisResponse,
)
async def synthesize_sales_records(
    project_id: int,
    req: SynthesisRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run DeepSeek over the project's uploaded documents to build sales
    records. Existing records (matched by contract_no+product_code) will be
    updated; new ones inserted."""
    project = await _get_project_or_404(db, project_id)

    q = select(SalesDocument).where(SalesDocument.project_id == project_id)
    if req.document_ids:
        q = q.where(SalesDocument.id.in_(req.document_ids))
    docs = (await db.execute(q)).scalars().all()
    if not docs:
        raise HTTPException(
            status_code=400,
            detail=(
                "项目下没有可用的销售文档。请先上传合同/发票/发货单/报关单等文档。"
                "如果没有这类文档，请提供能反映每笔销售（金额、发货时间、收入确认时间、"
                "数量、单价、产品编号）的任意 Excel/Word/PDF。"
            ),
        )

    client = _deepseek_client()
    if not client.is_configured:
        raise HTTPException(
            status_code=400,
            detail="DEEPSEEK_API_KEY 未配置，请在 .env 中填入后重启服务。",
        )

    synthesizer = SalesLedgerSynthesizer(client)
    raw_records = await synthesizer.synthesize(docs, extra_user_hint=req.extra_hint)

    # ---- upsert into DB keyed by (contract_no, product_code) ------------
    upserted: list[SalesRecord] = []
    for raw in raw_records:
        ship, receipt, confirm = SalesLedgerSynthesizer.coerce_dates(raw)
        nums = SalesLedgerSynthesizer.coerce_numbers(raw)
        contract_no = (raw.get("contract_no") or "").strip()
        product_code = (raw.get("product_code") or "").strip()
        customer = (raw.get("customer_name") or "").strip()
        invoice_no = (raw.get("invoice_no") or "").strip() or None
        currency = (raw.get("currency") or "CNY").strip()
        if not customer or not product_code:
            # Without these two we can't meaningfully persist a row.
            continue

        common = {
            "customer_name": customer,
            "product_name": (raw.get("product_name") or "").strip(),
            "invoice_no": invoice_no,
            "currency": currency,
            "tax_rate": nums["tax_rate"],
            "tax_amount": nums["tax_amount"],
            "gross_amount": nums["gross_amount"],
            "quantity": nums["quantity"],
            "unit_price": nums["unit_price"],
            "revenue_amount": nums["revenue_amount"],
            "cost_amount": nums["cost_amount"],
            "shipping_fee": nums["shipping_fee"],
            "customs_fee": nums["customs_fee"],
            "other_direct_fee": nums["other_direct_fee"],
            "return_amount": nums["return_amount"],
            "discount_amount": nums["discount_amount"],
            "rebate_amount": nums["rebate_amount"],
            "ship_date": ship,
            "receipt_date": receipt,
            "revenue_confirm_date": confirm,
            "source": (raw.get("source") or raw.get("source_doc") or "")[:255],
            "document_id": raw.get("document_id"),
        }

        existing_q = select(SalesRecord).where(
            SalesRecord.project_id == project_id,
            SalesRecord.contract_no == contract_no,
            SalesRecord.product_code == product_code,
        )
        existing = (await db.execute(existing_q)).scalar_one_or_none()
        if existing:
            for field, val in common.items():
                setattr(existing, field, val)
            upserted.append(existing)
        else:
            new = SalesRecord(project_id=project_id, contract_no=contract_no, product_code=product_code, **common)
            new.confidence = 0.8
            db.add(new)
            upserted.append(new)

    await db.commit()
    for r in upserted:
        await db.refresh(r)
    return SynthesisResponse(
        project_id=project_id,
        synthesized_count=len(upserted),
        records=[_record_to_response(r) for r in upserted],
    )


@router.get(
    "/projects/{project_id}/sales-records",
    response_model=List[SalesRecordResponse],
)
async def list_sales_records(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id)
    result = await db.execute(
        select(SalesRecord)
        .where(SalesRecord.project_id == project_id)
        .order_by(SalesRecord.revenue_confirm_date.is_(None), SalesRecord.revenue_confirm_date)
    )
    return [_record_to_response(r) for r in result.scalars().all()]


@router.put("/sales-records/{record_id}", response_model=SalesRecordResponse)
async def update_sales_record(
    record_id: int,
    payload: SalesRecordUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SalesRecord).where(SalesRecord.id == record_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="销售记录不存在")
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, val)
    if payload.model_dump(exclude_unset=True):
        record.is_verified = True
    await db.commit()
    await db.refresh(record)
    return _record_to_response(record)


@router.delete("/sales-records/{record_id}")
async def delete_sales_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SalesRecord).where(SalesRecord.id == record_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="销售记录不存在")
    await db.delete(record)
    await db.commit()
    return {"message": "已删除"}


# ---------- analysis ----------------------------------------------------


@router.post(
    "/projects/{project_id}/revenue-analysis",
    response_model=AnalysisResponse,
)
async def revenue_analysis(
    project_id: int,
    req: AnalysisRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id)

    records = (
        await db.execute(
            select(SalesRecord).where(SalesRecord.project_id == project_id)
        )
    ).scalars().all()
    if not records:
        raise HTTPException(
            status_code=400,
            detail=(
                "项目下还没有销售记录。请先在『AI 合成』步骤生成销售清单，"
                "或在数据库中手工录入后再分析。"
            ),
        )

    industry = (req.industry or project.industry or "").strip()
    client = _deepseek_client() if req.run_industry_benchmark else None

    analyzer = RevenueAnalyzer(
        records=records,
        client=client if (client and client.is_configured) else None,
        industry=industry,
    )
    result = await analyzer.arun(
        period_end=req.period_end or date(project.fiscal_year, 12, 31),
        cut_off_window_days=req.cut_off_window_days,
        price_volatility_pct=req.price_volatility_pct,
        run_industry_benchmark=req.run_industry_benchmark and bool(industry),
    )
    data = result.to_dict()
    return AnalysisResponse(project_id=project_id, **data)


# ---------- export ------------------------------------------------------


@router.get("/projects/{project_id}/export")
async def export_sales_ledger(
    project_id: int,
    run_analysis: bool = Query(True, description="是否在导出前重算分析"),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id)
    records = (
        await db.execute(
            select(SalesRecord).where(SalesRecord.project_id == project_id)
        )
    ).scalars().all()

    analysis: dict | None = None
    if run_analysis and records:
        analyzer = RevenueAnalyzer(records=records, industry=project.industry or "")
        analysis = analyzer.run().to_dict()

    blob = SalesLedgerExporter.build(records, analysis=analysis)
    filename = f"sales_ledger_project_{project_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(blob),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
