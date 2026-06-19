"""API routes for project management."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
import asyncio
import pandas as pd
import uuid

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import (
    Project,
    AccountBalance,
    ChronologicalAccount,
    BankStatement,
    IMPORT_KIND_ACCOUNT_BALANCES,
    IMPORT_KIND_CHRONOLOGICAL,
    IMPORT_KIND_BANK_STATEMENTS,
)
from app.models.audit import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    AccountBalanceResponse,
    ChronologicalAccountResponse,
    BankStatementResponse,
)
from app.models.db.auth import User
from app.services.auth import (
    ensure_project_in_firm,
    get_current_user,
    get_current_user_optional,
    project_default_firm_id,
    scope_projects_to_firm,
)
from app.services.excel_parser import ExcelParser


def _write_bytes(path, data: bytes) -> None:
    """同步写文件, 供 asyncio.to_thread 调用."""
    with open(path, "wb") as f:
        f.write(data)

router = APIRouter(prefix="/api/projects", tags=["项目管理"])


async def _trigger_work_plan_on_import(
    db: AsyncSession, project_id: int, import_kind: str, count: int
) -> None:
    """账套导入完成后异步触发 AI 生成工作计划。

    失败 try/except 兜底 — 不阻塞主导入流程。
    """
    try:
        from app.services.team_management import team_management_service

        await team_management_service.on_accounts_imported(db, project_id, import_kind, count)
    except Exception:  # noqa: BLE001
        # 主流程不应被工作计划的失败拖垮
        import logging

        logging.getLogger(__name__).exception("账套导入钩子触发工作计划生成失败，不影响主流程")


@router.post("/", response_model=ProjectResponse)
async def create_project(
    project: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new audit project."""
    payload = project.model_dump()
    # 多租户硬隔离: 创建项目时若 user 有 firm_id 且 payload 未显式指定, 自动落 firm.
    # admin 角色 / AUTH_ENABLED=false 时跳过.
    if "firm_id" not in payload or payload.get("firm_id") is None:
        default_firm = project_default_firm_id(current_user)
        if default_firm is not None:
            payload["firm_id"] = default_firm
    db_project = Project(**payload)
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    return db_project


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List all audit projects."""
    query = select(Project)
    if status:
        query = query.where(Project.status == status)
    # 多租户硬隔离: 按当前 user.firm_id 过滤 (admin 不过滤)
    query = scope_projects_to_firm(query, current_user)
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get project by ID."""
    # 多租户硬隔离: 跨事务所访问抛 403
    return await ensure_project_in_firm(db, project_id, current_user)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    project_update: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update project information."""
    project = await ensure_project_in_firm(db, project_id, current_user)

    for key, value in project_update.model_dump(exclude_unset=True).items():
        setattr(project, key, value)

    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a project."""
    project = await ensure_project_in_firm(db, project_id, current_user)

    await db.delete(project)
    await db.commit()
    return {"message": "项目已删除"}


# ============ 科目余额表导入 ============
@router.post("/{project_id}/account-balances")
async def upload_account_balances(
    project_id: int,
    file: UploadFile = File(...),
    erp_type: str = Query(
        "标准模板",
        description="ERP系统类型: 金蝶K3 Cloud, 金蝶云星空, 用友NC, 用友U8, 用友YonBIP, SAP, SAP ECC, 标准模板",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload and parse account balance Excel file with ERP type auto-detection.

    Args:
        project_id: Project ID
        file: Excel file to upload
        erp_type: ERP system type (auto-detected if not specified)
    """
    from app.services.erp_adapters import ERPAdapterFactory, ERPType

    # 多租户硬隔离: 校验项目归属
    await ensure_project_in_firm(db, project_id, current_user)

    # Get ERP adapter
    # Map erp_type string to ERPType enum
    erp_type_enum = None
    for et in ERPType:
        if et.value == erp_type or et.name == erp_type:
            erp_type_enum = et
            break

    # Parse Excel with adapter
    # 用 UUID 防止同名并发上传相互覆盖
    temp_path = settings.UPLOAD_DIR / f"temp_{uuid.uuid4().hex}_{file.filename or 'upload'}"
    content = await file.read()
    # P0 性能 (2026-06-19): 同步 write/read_excel 在 async 端点内阻塞事件循环
    # 改 asyncio.to_thread, 释放 worker 接收其他请求
    await asyncio.to_thread(_write_bytes, temp_path, content)

    try:
        raw_df = await asyncio.to_thread(pd.read_excel, temp_path)

        # Auto-detect ERP type if not specified
        if erp_type_enum is None:
            erp_type_enum = ERPAdapterFactory.detect_erp_type(raw_df)

        adapter = ERPAdapterFactory.get_adapter(erp_type_enum)
        df = adapter.parse_account_balance(raw_df)

    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析科目余额表失败: {exc}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()

    # Save to database
    balances = []
    for _, row in df.iterrows():
        balance = AccountBalance(
            project_id=project_id,
            account_code=str(row.get("account_code", "")),
            account_name=str(row.get("account_name", "")),
            balance_direction=str(row.get("balance_direction", "借")),
            beginning_balance=float(row.get("beginning_balance", 0)),
            debit_amount=float(row.get("debit_amount", 0)),
            credit_amount=float(row.get("credit_amount", 0)),
            ending_balance=float(row.get("ending_balance", 0)),
        )
        db.add(balance)
        balances.append(balance)

    await db.commit()
    await _trigger_work_plan_on_import(db, project_id, IMPORT_KIND_ACCOUNT_BALANCES, len(balances))
    return {"message": f"成功导入 {len(balances)} 条科目余额记录"}


@router.get("/{project_id}/account-balances", response_model=List[AccountBalanceResponse])
async def get_account_balances(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get account balances for a project."""
    await ensure_project_in_firm(db, project_id, current_user)
    result = await db.execute(select(AccountBalance).where(AccountBalance.project_id == project_id))
    return result.scalars().all()


# ============ 序时账导入 ============
@router.post("/{project_id}/chronological-accounts")
async def upload_chronological_accounts(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload and parse chronological account Excel file."""
    await ensure_project_in_firm(db, project_id, current_user)

    df = await ExcelParser.parse_chronological_account(file)

    accounts = []
    for _, row in df.iterrows():
        account = ChronologicalAccount(
            project_id=project_id,
            voucher_date=str(row.get("voucher_date", "")),
            voucher_no=str(row.get("voucher_no", "")),
            account_code=str(row.get("account_code", "")),
            account_name=str(row.get("account_name", "")),
            debit_amount=float(row.get("debit_amount", 0)),
            credit_amount=float(row.get("credit_amount", 0)),
            summary=str(row.get("summary", "")) if row.get("summary") else None,
            auxiliary_accounting=str(row.get("auxiliary_accounting", ""))
            if row.get("auxiliary_accounting")
            else None,
        )
        db.add(account)
        accounts.append(account)

    await db.commit()
    await _trigger_work_plan_on_import(db, project_id, IMPORT_KIND_CHRONOLOGICAL, len(accounts))
    return {"message": f"成功导入 {len(accounts)} 条序时账记录"}


@router.get(
    "/{project_id}/chronological-accounts", response_model=List[ChronologicalAccountResponse]
)
async def get_chronological_accounts(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get chronological accounts for a project."""
    await ensure_project_in_firm(db, project_id, current_user)
    result = await db.execute(
        select(ChronologicalAccount).where(ChronologicalAccount.project_id == project_id)
    )
    return result.scalars().all()


# ============ 银行对账单导入 ============
@router.post("/{project_id}/bank-statements")
async def upload_bank_statements(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload and parse bank statement Excel file."""
    await ensure_project_in_firm(db, project_id, current_user)

    df = await ExcelParser.parse_bank_statement(file)

    statements = []
    for _, row in df.iterrows():
        statement = BankStatement(
            project_id=project_id,
            statement_date=str(row.get("statement_date", "")),
            voucher_no=str(row.get("voucher_no", "")),
            description=str(row.get("description", "")),
            debit_amount=float(row.get("debit_amount", 0)),
            credit_amount=float(row.get("credit_amount", 0)),
            balance=float(row.get("balance", 0)),
            bank_account=str(row.get("bank_account", "")) if row.get("bank_account") else None,
        )
        db.add(statement)
        statements.append(statement)

    await db.commit()
    await _trigger_work_plan_on_import(db, project_id, IMPORT_KIND_BANK_STATEMENTS, len(statements))
    return {"message": f"成功导入 {len(statements)} 条银行对账单记录"}


@router.get("/{project_id}/bank-statements", response_model=List[BankStatementResponse])
async def get_bank_statements(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get bank statements for a project."""
    await ensure_project_in_firm(db, project_id, current_user)
    result = await db.execute(select(BankStatement).where(BankStatement.project_id == project_id))
    return result.scalars().all()
