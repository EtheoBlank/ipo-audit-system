"""Contract analysis API (under /api/contracts).

Endpoints:
  POST /projects/{id}/contracts             upload image/PDF/文本 → OCR 落库
  POST /projects/{id}/contracts/text        纯文本上传（用户自跑 OCR 后的兜底）
  GET  /projects/{id}/contracts             列表
  GET  /contracts/{cid}                     详情
  POST /contracts/{cid}/analyze             触发五步法/要点抽取
  DELETE /contracts/{cid}                   删除
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._helpers import get_project_or_404
from app.core.config import settings
from app.core.database import get_db
from app.models.contracts import (
    ContractAnalysisRequest,
    ContractAnalysisResponse,
    ContractDocumentResponse,
)
from app.models.db_models import ContractDocument
from app.models.db.auth import User
from app.services.auth import get_current_user, get_current_user_optional
from app.services.contract_analysis import ContractAnalyzer, ContractOCR, OCRError
from app.services.sales_ledger import DeepSeekClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contracts", tags=["收入合同"])


# ---------- helpers ------------------------------------------------------


def _to_response(c: ContractDocument) -> ContractDocumentResponse:
    key_points = _maybe_json(c.key_points)
    five_step = _maybe_json(c.five_step_analysis)
    risk_flags = json.loads(c.risk_flags) if c.risk_flags else None
    return ContractDocumentResponse(
        id=c.id,
        project_id=c.project_id,
        filename=c.filename,
        media_type=c.media_type or "",
        ocr_engine=c.ocr_engine,
        ocr_text=c.ocr_text or "",
        note=c.note,
        key_points=key_points,
        five_step_analysis=five_step,
        risk_flags=risk_flags,
        uploaded_at=c.uploaded_at,
        analyzed_at=c.analyzed_at,
    )


def _maybe_json(s: Optional[str]):
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, json.JSONDecodeError):
        return None


# ---------- upload (image / PDF) ----------------------------------------


@router.post(
    "/projects/{project_id}/contracts",
    response_model=ContractDocumentResponse,
)
async def upload_contract(
    project_id: int,
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a contract image / scanned PDF. OCR is run server-side."""
    await get_project_or_404(db, project_id)

    save_dir: Path = settings.UPLOAD_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    temp_path = save_dir / f"contract_{file.filename}"
    content = await file.read()
    temp_path.write_bytes(content)

    try:
        engine, ocr_text = ContractOCR.run(temp_path, file.filename or "")
    except OCRError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    doc = ContractDocument(
        project_id=project_id,
        filename=file.filename or "contract",
        media_type=file.content_type or "application/octet-stream",
        ocr_engine=engine,
        ocr_text=ocr_text,
        note=note,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _to_response(doc)


# ---------- upload (text only) ------------------------------------------


@router.post(
    "/projects/{project_id}/contracts/text",
    response_model=ContractDocumentResponse,
)
async def upload_contract_text(
    project_id: int,
    filename: str = Body(..., embed=True),
    text: str = Body(..., embed=True),
    note: Optional[str] = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save user-pasted contract text directly (no OCR)."""
    await get_project_or_404(db, project_id)
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="合同文本不能为空")

    doc = ContractDocument(
        project_id=project_id,
        filename=filename or "contract.txt",
        media_type="text/plain",
        ocr_engine="manual",
        ocr_text=text,
        note=note,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _to_response(doc)


# ---------- list / detail / delete --------------------------------------


@router.get(
    "/projects/{project_id}/contracts",
    response_model=List[ContractDocumentResponse],
)
async def list_contracts(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await get_project_or_404(db, project_id)
    rows = (
        (
            await db.execute(
                select(ContractDocument)
                .where(ContractDocument.project_id == project_id)
                .order_by(ContractDocument.uploaded_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_response(c) for c in rows]


@router.get("/contracts/{contract_id}", response_model=ContractDocumentResponse)
async def get_contract(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    c = (
        await db.execute(select(ContractDocument).where(ContractDocument.id == contract_id))
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    return _to_response(c)


@router.delete("/contracts/{contract_id}")
async def delete_contract(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = (
        await db.execute(select(ContractDocument).where(ContractDocument.id == contract_id))
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    await db.delete(c)
    await db.commit()
    return {"message": "已删除"}


# ---------- analysis ----------------------------------------------------


@router.post(
    "/contracts/{contract_id}/analyze",
    response_model=ContractAnalysisResponse,
)
async def analyze_contract(
    contract_id: int,
    req: ContractAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run CAS 14 five-step analysis and/or 7-field key-point extraction."""
    c = (
        await db.execute(select(ContractDocument).where(ContractDocument.id == contract_id))
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="合同不存在")
    if not c.ocr_text:
        raise HTTPException(status_code=400, detail="该合同没有可用的文本，请重新上传")

    client = DeepSeekClient(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_API_BASE,
        model=settings.DEEPSEEK_MODEL,
    )
    if not client.is_configured:
        raise HTTPException(
            status_code=400,
            detail="DEEPSEEK_API_KEY 未配置，请在 .env 中填入后重启。",
        )

    analyzer = ContractAnalyzer(client)
    key_points = await analyzer.key_points(c.ocr_text) if req.run_key_points else None
    five_step = await analyzer.five_step(c.ocr_text) if req.run_five_step else None
    risk_flags = ContractAnalyzer.scan_risks(key_points, five_step, c.ocr_text)

    # Persist results
    from datetime import datetime, timezone

    if key_points is not None:
        c.key_points = json.dumps(key_points, ensure_ascii=False)
    if five_step is not None:
        c.five_step_analysis = json.dumps(five_step, ensure_ascii=False)
    c.risk_flags = json.dumps(risk_flags, ensure_ascii=False)
    c.analyzed_at = datetime.now(timezone.utc)
    await db.commit()

    return ContractAnalysisResponse(
        contract_id=c.id,
        project_id=c.project_id,
        key_points=key_points,
        five_step_analysis=five_step,
        risk_flags=risk_flags,
        analyzed_at=c.analyzed_at,
    )
