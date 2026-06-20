"""API routes for workbook generation."""

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api._helpers import get_project_or_404

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import Project, AccountBalance
from app.models.db.auth import User
from app.models.audit import (
    WorkbookGenerateRequest,
    WorkbookGenerateResponse,
    TrialBalanceRequest,
    TrialBalanceResponse,
)
from app.services.auth import (
    ensure_project_in_firm,
    get_current_user,
    get_current_user_optional,
)
from app.services.workbook_generator import WorkbookGenerator
from app.services.trial_balance import TrialBalanceService
from app.services.audit_note_generator import (
    AuditNoteContext,
    audit_note_generator,
)
from app.utils.db_helpers import account_balances_to_df

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workbooks", tags=["底稿生成"])


@router.post("/generate", response_model=WorkbookGenerateResponse)
async def generate_workbook(
    request: WorkbookGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate audit workbook in Excel format."""
    # 多租户硬隔离 — 跨事务所访问 403
    project = await ensure_project_in_firm(db, request.project_id, current_user)

    # Get account balances
    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == request.project_id)
    )
    account_balances = result.scalars().all()

    df_balances = account_balances_to_df(account_balances)

    # Generate workbook
    generator = WorkbookGenerator(
        project_id=request.project_id,
        company_name=project.company_name,
        fiscal_year=project.fiscal_year,
    )

    template_generators = {
        "account_detail": generator.generate_account_detail,
        "income_statement": generator.generate_income_statement,
        "balance_sheet": generator.generate_balance_sheet,
        "cash_flow": generator.generate_cash_flow,
        "trial_balance": generator.generate_trial_balance,
    }

    if request.template_type not in template_generators:
        raise HTTPException(status_code=400, detail=f"不支持的模板类型: {request.template_type}")

    # P1 (2026-06-19): openpyxl wb.save() 同步 IO, to_thread 释放 event loop.
    output_path = await asyncio.to_thread(
        template_generators[request.template_type], df_balances
    )

    return WorkbookGenerateResponse(
        file_path=str(output_path),
        file_name=output_path.name,
        download_url=f"/api/workbooks/download/{output_path.name}",
    )


@router.get("/download/{filename}")
async def download_workbook(
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download generated workbook file.

    Security:
      - only allow alphanumeric + underscore/dash/dot filenames
      - resolve against the output root to prevent path traversal
      - 多租户硬隔离: 从 filename 抽出 project_id 后, 用 ensure_project_in_firm
        校验 current_user.firm_id 是否能访问; 抽不到 project_id 直接 404 防绕过
    """
    import re

    # Reject obviously malicious filenames (path traversal, special chars)
    if not re.match(r"^[\w.\-]+$", filename):
        raise HTTPException(status_code=400, detail="非法文件名")

    # 多租户硬隔离: 从 filename 抽 project_id, 校验 firm 归属
    m = re.search(r"project_(\d+)", filename)
    if not m:
        raise HTTPException(status_code=404, detail="文件不存在")
    await ensure_project_in_firm(db, int(m.group(1)), current_user)

    # Search for file in any project directory
    file_path: Path | None = None
    for project_dir in settings.OUTPUT_DIR.glob("project_*"):
        potential_path = (project_dir / filename).resolve()
        # Ensure resolved path is still under OUTPUT_DIR (no path traversal)
        if potential_path.is_relative_to(settings.OUTPUT_DIR.resolve()) and potential_path.exists():
            file_path = potential_path
            break

    if not file_path:
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/trial-balance", response_model=TrialBalanceResponse)
async def check_trial_balance(
    request: TrialBalanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Check trial balance for a project."""
    # round 31 修 round 29 xfail 捕到的 P0 IDOR + KeyError:
    #   1) 缺 ensure_project_in_firm → 跨所可读别所科目余额
    #   2) 用 balance_result["ending"] 但 TrialBalanceService.check_balance 返回
    #      嵌套 "standalone.ending", KeyError → 500
    await ensure_project_in_firm(db, request.project_id, current_user)
    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == request.project_id)
    )
    account_balances = result.scalars().all()

    if not account_balances:
        raise HTTPException(status_code=404, detail="未找到科目余额数据")

    df_balances = account_balances_to_df(account_balances)

    balance_result = TrialBalanceService.check_balance(df_balances)
    account_summary = TrialBalanceService.get_account_summary(df_balances)

    standalone_ending = balance_result["standalone"]["ending"]
    return TrialBalanceResponse(
        is_balanced=balance_result["is_balanced"],
        total_debit=standalone_ending["debit"],
        total_credit=standalone_ending["credit"],
        difference=standalone_ending["difference"],
        account_details=account_summary,
    )


# ============================================================
#  审计说明生成 (调用知识库 + 法规库 + AI)
# ============================================================


class AuditNoteRequest(BaseModel):
    project_id: int
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    balance_amount: Optional[float] = None
    industry: Optional[str] = None
    audit_objective: Optional[str] = Field(
        default=None, description="例如 '收入截止性' / '存货跌价' / '应收账款可回收性'"
    )
    risk_description: Optional[str] = None
    kb_category: Optional[str] = Field(
        default=None, description="只在某类知识库中检索，例如 '案例集'"
    )
    kb_top_k: int = Field(default=4, ge=1, le=10)
    include_regulations: bool = True


class AuditNoteResponse(BaseModel):
    note: str
    ai_enabled: bool
    references_kb: List[dict]
    references_regulations: List[dict]


@router.post("/audit-note", response_model=AuditNoteResponse)
async def generate_audit_note(
    req: AuditNoteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为指定底稿/科目生成审计说明 — 自动调用知识库 + 法规库 + AI。

    返回的 ``note`` 是 markdown，前端可直接渲染或复制到 Excel 备注。
    ``references_kb`` / ``references_regulations`` 列出引用依据，便于回溯。
    """
    # 多租户硬隔离: 替换原来的 select(Project)+404, 跨事务所访问直接 403
    project = await ensure_project_in_firm(db, req.project_id, current_user)

    industry = req.industry or project.industry

    ctx = AuditNoteContext(
        project_id=req.project_id,
        account_code=req.account_code,
        account_name=req.account_name,
        balance_amount=req.balance_amount,
        industry=industry,
        audit_objective=req.audit_objective,
        risk_description=req.risk_description,
    )

    # 如果调用方没传 balance_amount，但提供了 account_code，自动从库里捞一笔
    if req.balance_amount is None and req.account_code:
        ab = (
            (
                await db.execute(
                    select(AccountBalance).where(
                        AccountBalance.project_id == req.project_id,
                        AccountBalance.account_code == req.account_code,
                    )
                )
            )
            .scalars()
            .first()
        )
        if ab:
            ctx.balance_amount = ab.ending_balance
            if not ctx.account_name:
                ctx.account_name = ab.account_name

    result = await audit_note_generator.generate(
        db,
        ctx,
        kb_top_k=req.kb_top_k,
        kb_category=req.kb_category,
        include_regulations=req.include_regulations,
    )
    return AuditNoteResponse(
        note=result.note,
        ai_enabled=result.ai_enabled,
        references_kb=result.references_kb,
        references_regulations=result.references_regulations,
    )


# ----------------------------------------------------------------------
# 批量给底稿写审计说明
# ----------------------------------------------------------------------


class AuditNoteBatchRequest(BaseModel):
    project_id: int
    workbook_file: str = Field(..., description="已生成的底稿文件名 (从 generate 接口拿到)")
    account_codes: Optional[List[str]] = Field(
        default=None,
        description="只给这些科目生成；不传则取期末余额绝对值前 20 大科目",
    )
    kb_category: Optional[str] = None
    audit_objective: Optional[str] = None
    include_regulations: bool = True
    top_n_by_balance: int = Field(default=20, ge=1, le=100)


class AuditNoteBatchResponse(BaseModel):
    workbook_file: str
    download_url: str
    notes_count: int
    ai_enabled: bool


@router.post("/audit-note/batch", response_model=AuditNoteBatchResponse)
async def generate_audit_notes_batch(
    req: AuditNoteBatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为指定底稿批量生成审计说明，并写回 Excel 末尾的"审计说明"sheet。"""
    # P0 IDOR + 多租户 (2026-06-19): 直查 Project 后无 firm 校验
    project = await get_project_or_404(db, req.project_id, current_user=current_user)

    # 1) 找到底稿文件
    import re as _re

    if not _re.match(r"^[\w.\-一-龥]+$", req.workbook_file):
        raise HTTPException(status_code=400, detail="非法文件名")
    candidate = (settings.OUTPUT_DIR / f"project_{req.project_id}" / req.workbook_file).resolve()
    if not candidate.is_relative_to(settings.OUTPUT_DIR.resolve()) or not candidate.exists():
        raise HTTPException(status_code=404, detail="底稿文件不存在")

    # 2) 选定要生成说明的科目
    q = select(AccountBalance).where(AccountBalance.project_id == req.project_id)
    if req.account_codes:
        q = q.where(AccountBalance.account_code.in_(req.account_codes))
    rows = (await db.execute(q)).scalars().all()
    if not req.account_codes:
        rows = sorted(rows, key=lambda r: abs(r.ending_balance or 0), reverse=True)
        rows = rows[: req.top_n_by_balance]
    if not rows:
        raise HTTPException(status_code=404, detail="未找到可生成说明的科目")

    # 3) 并发生成 — P0 性能修复 (2026-06-19)
    # 旧逻辑: 串行 for 循环, 30+ 科目 60s+; 现 asyncio.Semaphore + gather
    import asyncio as _asyncio
    sem = _asyncio.Semaphore(5)

    async def _gen_one(ab):
        async with sem:
            ctx = AuditNoteContext(
                project_id=req.project_id,
                account_code=ab.account_code,
                account_name=ab.account_name,
                balance_amount=ab.ending_balance,
                industry=project.industry,
                audit_objective=req.audit_objective,
            )
            return await audit_note_generator.generate(
                db,
                ctx,
                kb_category=req.kb_category,
                include_regulations=req.include_regulations,
            )

    results = await _asyncio.gather(
        *[_gen_one(ab) for ab in rows],
        return_exceptions=True,
    )

    notes_payload: list[dict] = []
    ai_enabled = False
    for ab, result in zip(rows, results):
        if isinstance(result, Exception):
            logger.warning("生成审计说明失败 %s: %s", ab.account_code, result)
            notes_payload.append(
                {
                    "account_code": ab.account_code,
                    "account_name": ab.account_name,
                    "note": f"(AI 生成失败: {type(result).__name__})",
                    "references_kb": [],
                    "references_regulations": [],
                }
            )
            continue
        ai_enabled = ai_enabled or result.ai_enabled
        notes_payload.append(
            {
                "account_code": ab.account_code,
                "account_name": ab.account_name,
                "note": result.note,
                "references_kb": result.references_kb,
                "references_regulations": result.references_regulations,
            }
        )

    # 4) 写回 Excel
    gen = WorkbookGenerator(
        project_id=req.project_id,
        company_name=project.company_name,
        fiscal_year=project.fiscal_year,
    )
    # P1 (2026-06-19): openpyxl wb.save() 同步 IO, to_thread 释放 event loop.
    await asyncio.to_thread(gen.write_audit_notes_sheet, candidate, notes_payload)

    return AuditNoteBatchResponse(
        workbook_file=candidate.name,
        download_url=f"/api/workbooks/download/{candidate.name}",
        notes_count=len(notes_payload),
        ai_enabled=ai_enabled,
    )
