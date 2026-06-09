"""API routes for workbook generation."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pathlib import Path

from app.core.database import get_db
from app.models.db_models import Project, AccountBalance, ChronologicalAccount, BankStatement
from app.models.audit import WorkbookGenerateRequest, WorkbookGenerateResponse, TrialBalanceRequest, TrialBalanceResponse
from app.services.workbook_generator import WorkbookGenerator
from app.services.trial_balance import TrialBalanceService

router = APIRouter(prefix="/api/workbooks", tags=["底稿生成"])


@router.post("/generate", response_model=WorkbookGenerateResponse)
async def generate_workbook(
    request: WorkbookGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate audit workbook in Excel format."""
    # Get project info
    result = await db.execute(select(Project).where(Project.id == request.project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # Get account balances
    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == request.project_id)
    )
    account_balances = result.scalars().all()

    # Convert to DataFrame
    import pandas as pd
    df_balances = pd.DataFrame([{
        "account_code": ab.account_code,
        "account_name": ab.account_name,
        "balance_direction": ab.balance_direction,
        "beginning_balance": ab.beginning_balance,
        "debit_amount": ab.debit_amount,
        "credit_amount": ab.credit_amount,
        "ending_balance": ab.ending_balance,
    } for ab in account_balances])

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
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模板类型: {request.template_type}"
        )

    output_path = template_generators[request.template_type](df_balances)

    return WorkbookGenerateResponse(
        file_path=str(output_path),
        file_name=output_path.name,
        download_url=f"/api/workbooks/download/{output_path.name}",
    )


@router.get("/download/{filename}")
async def download_workbook(filename: str):
    """Download generated workbook file."""
    from app.core.config import settings

    # Search for file in any project directory
    file_path = None
    for project_dir in settings.OUTPUT_DIR.glob("project_*"):
        potential_path = project_dir / filename
        if potential_path.exists():
            file_path = potential_path
            break

    if not file_path or not file_path.exists():
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
):
    """Check trial balance for a project."""
    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == request.project_id)
    )
    account_balances = result.scalars().all()

    if not account_balances:
        raise HTTPException(status_code=404, detail="未找到科目余额数据")

    import pandas as pd
    df_balances = pd.DataFrame([{
        "account_code": ab.account_code,
        "account_name": ab.account_name,
        "balance_direction": ab.balance_direction,
        "beginning_balance": ab.beginning_balance,
        "debit_amount": ab.debit_amount,
        "credit_amount": ab.credit_amount,
        "ending_balance": ab.ending_balance,
    } for ab in account_balances])

    balance_result = TrialBalanceService.check_balance(df_balances)
    account_summary = TrialBalanceService.get_account_summary(df_balances)

    return TrialBalanceResponse(
        is_balanced=balance_result["is_balanced"],
        total_debit=balance_result["ending"]["debit"],
        total_credit=balance_result["ending"]["credit"],
        difference=balance_result["ending"]["difference"],
        account_details=account_summary,
    )