"""长期资产发生额审定 API (Pack A — 用户特别要求).

端点前缀: ``/api/account-audit``

主要功能:
  - 项目级总览 (跨长期资产科目)
  - 单科目汇总 (恒等式校验)
  - 发生额行 CRUD (单笔 / 批量 / 争议)
  - 从序时账初始化审定记录
  - Excel 批量上传 + Excel 导出审定明细
  - 长期资产范围 (科目前缀) 项目级覆盖
"""

from __future__ import annotations

import io
import logging
from typing import List, Optional

import pandas as pd
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.account_audit import (
    AccountAuditOverview,
    AccountAuditSummary,
    EffectivePrefixesResponse,
    MovementAuditBulkItem,
    MovementAuditBulkRequest,
    MovementAuditBulkResponse,
    MovementAuditDisputeRequest,
    MovementAuditRowResponse,
    MovementAuditUpdate,
    MovementListResponse,
    ScopeOverrideCreate,
    ScopeOverrideResponse,
)
from app.models.db.account_audit import (
    DEFAULT_LONG_TERM_ASSET_PREFIXES,
)
from app.models.db.auth import (
    AUDIT_ACTION_CREATE,
    AUDIT_ACTION_DELETE,
    AUDIT_ACTION_IMPORT,
    AUDIT_ACTION_UPDATE,
    ROLE_ASSISTANT,
    User,
)
from app.services.account_audit import (
    AccountAuditService,
    get_effective_prefixes,
)
from app.services.auth import (
    get_current_user,
    record_audit_log,
    require_role,
)
from app.services.auth.tenant import ensure_project_in_firm
from app.services.notification import NotificationService
from app.models.db.notification import (
    NOTIF_MODULE_ACCOUNT_AUDIT,
    NOTIF_SEVERITY_NOTICE,
    NOTIF_SEVERITY_WARN,
)
from app.utils.upload_safety import read_upload_capped

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account-audit", tags=["长期资产发生额审定"])


# ============================================================
#  科目范围覆盖
# ============================================================


@router.get(
    "/projects/{project_id}/effective-prefixes",
    response_model=EffectivePrefixesResponse,
)
async def get_effective_prefixes_api(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    overrides = await AccountAuditService.list_scope_overrides(db, project_id)
    effective = await get_effective_prefixes(db, project_id)
    return EffectivePrefixesResponse(
        default_prefixes=list(DEFAULT_LONG_TERM_ASSET_PREFIXES),
        project_includes=[o.account_prefix for o in overrides if o.action == "include"],
        project_excludes=[o.account_prefix for o in overrides if o.action == "exclude"],
        effective_prefixes=effective,
    )


@router.post(
    "/projects/{project_id}/scope-overrides",
    response_model=ScopeOverrideResponse,
)
async def add_scope_override(
    project_id: int,
    payload: ScopeOverrideCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    ov = await AccountAuditService.add_scope_override(
        db,
        project_id=project_id,
        account_prefix=payload.account_prefix,
        action=payload.action,
        reason=payload.reason,
        created_by_user_id=current_user.id or None,
    )
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_CREATE,
        resource_type="account_audit.scope",
        resource_id=ov.id,
        project_id=project_id,
        summary=f"长期资产范围 {payload.action} {payload.account_prefix}",
        payload=payload.model_dump(),
    )
    return ScopeOverrideResponse.model_validate(ov)


@router.delete("/projects/{project_id}/scope-overrides/{override_id}")
async def remove_scope_override(
    project_id: int,
    override_id: int,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    ok = await AccountAuditService.remove_scope_override(
        db, project_id=project_id, override_id=override_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="覆盖记录不存在")
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_DELETE,
        resource_type="account_audit.scope",
        resource_id=override_id,
        project_id=project_id,
        summary="删除长期资产范围覆盖",
    )
    return {"detail": "已删除"}


# ============================================================
#  初始化
# ============================================================


@router.post("/projects/{project_id}/initialize")
async def initialize_from_chronological(
    project_id: int,
    period_end: str = Query(..., description="期末日期 YYYY-MM-DD"),
    replace_pending: bool = Query(True, description="替换 pending 行"),
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    """从序时账抽长期资产发生额, 初始化审定记录."""
    await ensure_project_in_firm(db, project_id, current_user)
    try:
        result = await AccountAuditService.initialize_from_chronological(
            db,
            project_id=project_id,
            period_end=period_end,
            replace_pending=replace_pending,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("initialize 失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"初始化失败: {exc}") from exc

    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_IMPORT,
        resource_type="account_audit",
        project_id=project_id,
        summary=f"初始化长期资产发生额审定 期末={period_end}",
        payload=result,
    )
    await NotificationService.push(
        db,
        module=NOTIF_MODULE_ACCOUNT_AUDIT,
        type="account_audit.initialized",
        title=f"长期资产发生额审定已初始化 ({period_end})",
        body=f"扫描 {result['scanned']} 条序时账, 新增 {result['inserted']} 条待审定",
        project_id=project_id,
        severity=NOTIF_SEVERITY_NOTICE,
    )
    return result


# ============================================================
#  发生额行查询
# ============================================================


@router.get(
    "/projects/{project_id}/movements",
    response_model=MovementListResponse,
)
async def list_movements(
    project_id: int,
    account_code: Optional[str] = None,
    period_end: Optional[str] = None,
    direction: Optional[str] = Query(None, pattern=r"^(debit|credit)$"),
    status_filter: Optional[str] = Query(None, alias="status"),
    voucher_no: Optional[str] = None,
    keyword: Optional[str] = Query(None, max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    result = await AccountAuditService.list_movements(
        db,
        project_id=project_id,
        account_code=account_code,
        period_end=period_end,
        direction=direction,
        status=status_filter,
        voucher_no=voucher_no,
        keyword=keyword,
        skip=skip,
        limit=limit,
    )
    return MovementListResponse(
        total=result["total"],
        items=[MovementAuditRowResponse.model_validate(r) for r in result["items"]],
    )


@router.put("/movements/{movement_id}", response_model=MovementAuditRowResponse)
async def update_movement(
    movement_id: int,
    payload: MovementAuditUpdate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await AccountAuditService.audit_row(
            db,
            movement_id=movement_id,
            audited_amount=payload.audited_amount,
            adjustment_reason=payload.adjustment_reason,
            working_paper_ref=payload.working_paper_ref,
            note=payload.note,
            status=payload.status,
            user_id=current_user.id or None,
            user_display=current_user.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # IDOR fix (P0): 校验 row 所属 project 在 user 事务所内 — 否则 403
    await ensure_project_in_firm(db, row.project_id, current_user)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="account_audit.movement",
        resource_id=movement_id,
        project_id=row.project_id,
        summary=f"审定 {row.account_code} {row.voucher_no} {row.direction} 调整={row.adjustment_amount}",
        payload=payload.model_dump(),
    )
    return MovementAuditRowResponse.model_validate(row)


@router.post("/movements/{movement_id}/dispute", response_model=MovementAuditRowResponse)
async def dispute_movement(
    movement_id: int,
    payload: MovementAuditDisputeRequest,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await AccountAuditService.dispute_row(
            db,
            movement_id=movement_id,
            reason=payload.reason,
            user_id=current_user.id or None,
            user_display=current_user.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # IDOR fix (P0): 校验 row 所属 project 在 user 事务所内 — 否则 403
    await ensure_project_in_firm(db, row.project_id, current_user)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="account_audit.movement",
        resource_id=movement_id,
        project_id=row.project_id,
        summary=f"争议 {row.account_code} {row.voucher_no}",
        payload=payload.model_dump(),
    )
    await NotificationService.push(
        db,
        module=NOTIF_MODULE_ACCOUNT_AUDIT,
        type="account_audit.disputed",
        title=f"发生额争议: {row.account_code} {row.voucher_no}",
        body=payload.reason[:300],
        project_id=row.project_id,
        severity=NOTIF_SEVERITY_WARN,
        resource_type="account_audit.movement",
        resource_id=movement_id,
    )
    return MovementAuditRowResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/bulk-audit",
    response_model=MovementAuditBulkResponse,
)
async def bulk_audit(
    project_id: int,
    payload: MovementAuditBulkRequest,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    result = await AccountAuditService.bulk_audit(
        db,
        project_id=project_id,
        period_end=payload.period_end,
        items=payload.rows,
        user_id=current_user.id or None,
        user_display=current_user.full_name,
    )
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_IMPORT,
        resource_type="account_audit",
        project_id=project_id,
        summary=f"批量审定 期末={payload.period_end} 匹配{result['matched']} 更新{result['updated']}",
        payload=result,
    )
    return MovementAuditBulkResponse(**result)


# ============================================================
#  Excel 批量上传 (备选, 字段映射 vs JSON)
# ============================================================


_BULK_REQUIRED_COLUMNS = ("account_code", "voucher_no", "direction", "audited_amount")


@router.post(
    "/projects/{project_id}/bulk-audit-upload",
    response_model=MovementAuditBulkResponse,
)
async def bulk_audit_upload(
    project_id: int,
    period_end: str = Query(..., description="期末日期 YYYY-MM-DD"),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    content, safe_name, suffix = await read_upload_capped(
        file, allowed_exts={".xlsx", ".xls", ".csv"}
    )
    try:
        if suffix == ".csv":
            df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
        else:
            df = pd.read_excel(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Excel/CSV 解析失败: {exc}") from exc

    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in _BULK_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"上传文件缺列: {missing}, 必须包含 {list(_BULK_REQUIRED_COLUMNS)}",
        )

    rows: List[MovementAuditBulkItem] = []
    parse_errors: List[str] = []
    for idx, r in df.iterrows():
        try:
            rows.append(
                MovementAuditBulkItem(
                    account_code=str(r["account_code"]).strip(),
                    voucher_no=str(r["voucher_no"]).strip(),
                    voucher_line_no=int(r.get("voucher_line_no", 1) or 1),
                    direction=str(r["direction"]).strip().lower(),
                    audited_amount=float(r["audited_amount"]),
                    adjustment_reason=str(r.get("adjustment_reason", "") or "") or None,
                    working_paper_ref=str(r.get("working_paper_ref", "") or "") or None,
                    note=str(r.get("note", "") or "") or None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"行 {idx + 2}: {exc}")

    result = await AccountAuditService.bulk_audit(
        db,
        project_id=project_id,
        period_end=period_end,
        items=rows,
        user_id=current_user.id or None,
        user_display=current_user.full_name,
    )
    result["errors"] = list(result.get("errors", [])) + parse_errors
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_IMPORT,
        resource_type="account_audit",
        project_id=project_id,
        summary=f"Excel 批量审定 文件={safe_name} 期末={period_end}",
        payload={"matched": result["matched"], "updated": result["updated"]},
    )
    return MovementAuditBulkResponse(**result)


# ============================================================
#  汇总
# ============================================================


@router.get(
    "/projects/{project_id}/accounts/{account_code}/summary",
    response_model=AccountAuditSummary,
)
async def account_summary(
    project_id: int,
    account_code: str,
    period_end: str = Query(..., description="期末日期"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    summary = await AccountAuditService.account_summary(
        db,
        project_id=project_id,
        account_code=account_code,
        period_end=period_end,
    )
    return summary


@router.get(
    "/projects/{project_id}/overview",
    response_model=AccountAuditOverview,
)
async def project_overview(
    project_id: int,
    period_end: str = Query(..., description="期末日期"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    overview = await AccountAuditService.project_overview(
        db, project_id=project_id, period_end=period_end
    )
    # 不平账户超过 0 → 推一条 critical 通知
    if overview.accounts_unbalanced > 0:
        await NotificationService.push(
            db,
            module=NOTIF_MODULE_ACCOUNT_AUDIT,
            type="account_audit.unbalanced",
            title=f"{overview.accounts_unbalanced} 个长期资产科目恒等式不平 ({period_end})",
            body="期末审定 vs 期初+借-贷 不平, 请复核",
            project_id=project_id,
            severity=NOTIF_SEVERITY_WARN,
        )
    return overview


# ============================================================
#  Excel 导出
# ============================================================


@router.get("/projects/{project_id}/export")
async def export_movements(
    project_id: int,
    period_end: str = Query(..., description="期末日期"),
    account_code: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出长期资产审定明细 Excel."""
    await ensure_project_in_firm(db, project_id, current_user)
    result = await AccountAuditService.list_movements(
        db,
        project_id=project_id,
        account_code=account_code,
        period_end=period_end,
        skip=0,
        limit=1000,
    )

    rows = [
        {
            "科目编码": r.account_code,
            "科目名称": r.account_name,
            "凭证日期": r.voucher_date,
            "凭证号": r.voucher_no,
            "行号": r.voucher_line_no,
            "方向": r.direction,
            "摘要": r.summary or "",
            "对方科目": r.counter_account or "",
            "账面金额": r.book_amount,
            "审定金额": r.audited_amount,
            "审计调整": r.adjustment_amount,
            "调整原因": r.adjustment_reason or "",
            "底稿索引": r.working_paper_ref or "",
            "状态": r.status,
            "审定人": r.audited_by_display or "",
            "审定时间": r.audited_at.isoformat() if r.audited_at else "",
        }
        for r in result["items"]
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        if rows:
            pd.DataFrame(rows).to_excel(writer, sheet_name="发生额审定", index=False)
        else:
            pd.DataFrame(columns=["科目编码", "凭证号", "提示"]).to_excel(
                writer, sheet_name="发生额审定", index=False
            )
    buf.seek(0)
    fname = (
        f"long_term_asset_audit_p{project_id}"
        f"{('_' + account_code) if account_code else ''}_{period_end}.xlsx"
    )
    return StreamingResponse(
        buf,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
