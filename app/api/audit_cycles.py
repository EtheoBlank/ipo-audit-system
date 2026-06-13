"""Pack C — 10 个审计循环统一 API. /api/audit-cycles/*

为控制端点数量, 每个循环只暴露:
  - 上传 / 录入 (走标准 ORM POST)
  - 查询 (走 GET list)
  - 核心计算 (走专用 POST, 如折旧重算 / ECL / NPV 等)

详细 CRUD 略, 用户可走 /docs 看 ORM 模型对应字段。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._helpers import get_project_or_404
from app.core.database import get_db
from app.models.db.audit_cycles import (
    AssetImpairmentTest,
    ConstructionInProgress,
    ECLAssessment,
    ExpenseRecord,
    FixedAsset,
    GoingConcernAssessment,
    IncomeTaxReconciliation,
    IntangibleAsset,
    LeaseContract,
    LongTermInvestment,
    PayableAging,
    PayrollRecord,
    ProvisionEstimate,
    RDCapitalizationAssessment,
    SubsequentEvent,
    Supplier,
)
from app.models.db.auth import ROLE_ASSISTANT, User
from app.services.audit_cycles import (
    CIPTransferChecker,
    DepreciationCalculator,
    ECLCalculator,
    ExpensesAnomalyDetector,
    GoingConcernAssessor,
    GoodwillImpairmentCalculator,
    IncomeTaxRecalculator,
    LeaseAmortizer,
    PayrollReconciler,
    RDCapitalizationAssessor,
    SubsequentEventClassifier,
)
from app.services.auth import get_current_user, record_audit_log, require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audit-cycles", tags=["审计循环 (Pack C)"])


# ============================================================
#  通用计算端点 — 不依赖 ORM, 直接 in/out
# ============================================================


class EntertainmentDeductionRequest(BaseModel):
    sales_revenue: float = Field(..., gt=0)
    entertainment_amount: float = Field(..., ge=0)


@router.post("/expenses/entertainment-deduction-limit")
async def entertainment_deduction_limit(
    payload: EntertainmentDeductionRequest,
    current_user: User = Depends(get_current_user),
):
    """业务招待费 60% / 1‰ 扣除限额计算."""
    return ExpensesAnomalyDetector.entertainment_deduction_limit(
        sales_revenue=payload.sales_revenue,
        entertainment_amount=payload.entertainment_amount,
    )


class DepreciationCalcRequest(BaseModel):
    original_cost: float
    salvage_rate: float = 0.05
    useful_life_months: int
    method: str = Field(default="straight_line", pattern=r"^(straight_line|double_declining)$")
    net_book_value: Optional[float] = None


@router.post("/fixed-assets/depreciation-calc")
async def depreciation_calc(
    payload: DepreciationCalcRequest,
    current_user: User = Depends(get_current_user),
):
    """单笔折旧月计算 (不入库)."""
    if payload.method == "double_declining":
        monthly = DepreciationCalculator.double_declining_monthly(
            payload.net_book_value or payload.original_cost,
            payload.useful_life_months,
        )
    else:
        monthly = DepreciationCalculator.straight_line_monthly(
            payload.original_cost, payload.salvage_rate, payload.useful_life_months
        )
    return {"monthly_depreciation": monthly, "annual_depreciation": round(monthly * 12, 2)}


class RDAssessRequest(BaseModel):
    technical_feasibility: bool = False
    intent_to_complete: bool = False
    ability_to_use_or_sell: bool = False
    future_economic_benefit: bool = False
    resources_sufficient: bool = False
    cost_measurable: bool = False


@router.post("/intangible/rd-capitalization-check")
async def rd_capitalization_check(
    payload: RDAssessRequest,
    current_user: User = Depends(get_current_user),
):
    all_met, missing = RDCapitalizationAssessor.assess(**payload.model_dump())
    return {"can_capitalize": all_met, "missing_conditions": missing}


class RDSuperDeductionRequest(BaseModel):
    rd_expense: float = Field(..., ge=0)
    manufacturing: bool = True


@router.post("/intangible/rd-super-deduction")
async def rd_super_deduction(
    payload: RDSuperDeductionRequest,
    current_user: User = Depends(get_current_user),
):
    return RDCapitalizationAssessor.rd_super_deduction(payload.rd_expense, payload.manufacturing)


class GoodwillNPVRequest(BaseModel):
    annual_cashflows: List[float] = Field(..., min_length=1, max_length=20)
    discount_rate: float = Field(default=0.10, gt=0, lt=1)


@router.post("/long-term-investment/goodwill-npv")
async def goodwill_npv(
    payload: GoodwillNPVRequest,
    current_user: User = Depends(get_current_user),
):
    npv = GoodwillImpairmentCalculator.npv(payload.annual_cashflows, payload.discount_rate)
    return {
        "npv": npv,
        "years": len(payload.annual_cashflows),
        "discount_rate": payload.discount_rate,
    }


class GoodwillImpairmentRequest(BaseModel):
    book_value_with_goodwill: float
    recoverable_amount: float


@router.post("/long-term-investment/goodwill-impairment-amount")
async def goodwill_impairment_amount(
    payload: GoodwillImpairmentRequest,
    current_user: User = Depends(get_current_user),
):
    impairment = GoodwillImpairmentCalculator.impairment_required(
        payload.book_value_with_goodwill, payload.recoverable_amount
    )
    return {
        "impairment_required": impairment,
        "is_impaired": impairment > 0,
    }


class LeasePVRequest(BaseModel):
    payment: float = Field(..., gt=0)
    periods: int = Field(..., gt=0, le=360)
    annual_rate: float = Field(default=0.05, ge=0, lt=1)


@router.post("/leases/present-value")
async def lease_present_value(
    payload: LeasePVRequest,
    current_user: User = Depends(get_current_user),
):
    monthly_rate = payload.annual_rate / 12
    pv = LeaseAmortizer.present_value(payload.payment, payload.periods, monthly_rate)
    return {"present_value": pv}


@router.post("/leases/contracts/{contract_id}/build-schedule")
async def build_lease_schedule(
    contract_id: int,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    try:
        records = await LeaseAmortizer.build_schedule(db, contract_id=contract_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action="create",
        resource_type="lease.amortization_schedule",
        resource_id=contract_id,
        summary=f"构建租赁摊销表 — {len(records)} 期",
    )
    return {"contract_id": contract_id, "periods": len(records)}


class TaxReconcileRequest(BaseModel):
    pretax_profit: float
    permanent_diff: float = 0.0
    temporary_diff: float = 0.0
    losses_used: float = 0.0
    nominal_rate: float = 0.25


@router.post("/income-tax/reconcile")
async def income_tax_reconcile(
    payload: TaxReconcileRequest,
    current_user: User = Depends(get_current_user),
):
    return IncomeTaxRecalculator.reconcile(**payload.model_dump())


class ECLComputeRequest(BaseModel):
    receivable: float = Field(..., ge=0)
    aging_days: int = Field(..., ge=0)
    pd: Optional[float] = Field(None, ge=0, le=1)
    lgd: float = Field(default=0.45, ge=0, le=1)


@router.post("/accounting-estimates/ecl-compute")
async def ecl_compute(
    payload: ECLComputeRequest,
    current_user: User = Depends(get_current_user),
):
    stage = ECLCalculator.stage_for_aging_days(payload.aging_days)
    ecl = ECLCalculator.compute_ecl(payload.receivable, stage, payload.pd, payload.lgd)
    return {
        "stage": stage,
        "default_pd": ECLCalculator.default_pd_for_stage(stage),
        "ecl_amount": ecl,
        "ecl_pct_of_receivable": (
            round(ecl / payload.receivable * 100, 2) if payload.receivable > 0 else 0
        ),
    }


class SubsequentEventClassifyRequest(BaseModel):
    event_description: str
    event_date: str
    balance_sheet_date: str


@router.post("/subsequent-events/classify")
async def subsequent_events_classify(
    payload: SubsequentEventClassifyRequest,
    current_user: User = Depends(get_current_user),
):
    et = SubsequentEventClassifier.classify(
        payload.event_description, payload.event_date, payload.balance_sheet_date
    )
    return {"event_type": et, "adjustment_required": et == "adjusting"}


class GoingConcernRequest(BaseModel):
    operating_cashflow_12m: float = 0.0
    interest_expense_12m: float = 0.0
    debt_due_12m: float = 0.0
    cash_balance: float = 0.0
    available_credit: float = 0.0


@router.post("/subsequent-events/going-concern")
async def going_concern_assess(
    payload: GoingConcernRequest,
    current_user: User = Depends(get_current_user),
):
    level, note = GoingConcernAssessor.assess(**payload.model_dump())
    return {"risk_level": level, "conclusion": note}


@router.post("/expenses/scan-anomalies/{project_id}")
async def scan_expense_anomalies(
    project_id: int,
    period_end: Optional[str] = None,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    res = await ExpensesAnomalyDetector.scan(db, project_id=project_id, period_end=period_end)
    return res


@router.post("/payroll/reconcile/{project_id}")
async def payroll_reconcile(
    project_id: int,
    period_yyyymm: str,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    rec = await PayrollReconciler.reconcile(db, project_id=project_id, period_yyyymm=period_yyyymm)
    return {
        "id": rec.id,
        "is_balanced": rec.is_balanced,
        "payroll_total": rec.payroll_total,
        "discrepancy_amount": rec.discrepancy_amount,
        "notes": rec.discrepancy_notes,
    }


@router.post("/fixed-assets/depreciation-recalc/{asset_id}")
async def fixed_asset_recalc(
    asset_id: int,
    period_yyyymm: str,
    book_depreciation: float,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    asset = (
        await db.execute(select(FixedAsset).where(FixedAsset.id == asset_id))
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(404, f"资产 {asset_id} 不存在")
    try:
        rec = await DepreciationCalculator.recalc_asset(
            db,
            project_id=asset.project_id,
            asset_id=asset_id,
            period_yyyymm=period_yyyymm,
            book_depreciation=book_depreciation,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "id": rec.id,
        "recalc_depreciation": rec.recalc_depreciation,
        "diff_amount": rec.diff_amount,
        "diff_pct": rec.diff_pct,
        "has_material_diff": rec.has_material_diff,
    }


@router.get("/cip/{cip_id}/transfer-check")
async def cip_transfer_check(
    cip_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cip = (
        await db.execute(select(ConstructionInProgress).where(ConstructionInProgress.id == cip_id))
    ).scalar_one_or_none()
    if cip is None:
        raise HTTPException(404, "CIP 不存在")
    ready, reason = CIPTransferChecker.is_ready_for_transfer(cip)
    return {"ready_for_transfer": ready, "reason": reason}


# ============================================================
#  ORM 列表端点 — 通用 list 模板, 每个表一个
# ============================================================


def _make_list_endpoint(model_cls, prefix: str, name: str):
    """工厂函数 — 给一张表生成 GET /list 端点."""

    @router.get(f"/{prefix}/projects/{{project_id}}/list", name=f"list_{name}")
    async def _list(
        project_id: int,
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await get_project_or_404(db, project_id)
        stmt = select(model_cls).where(model_cls.project_id == project_id).offset(skip).limit(limit)
        rows = list((await db.execute(stmt)).scalars().all())
        # 简化序列化 — 走 SQLAlchemy 默认 __dict__
        out = []
        for r in rows:
            d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
            out.append(d)
        return {"total": len(out), "items": out}

    return _list


# 给所有 10 个循环的核心 ORM 注册 list 端点
_list_suppliers = _make_list_endpoint(Supplier, "payables/suppliers", "suppliers")
_list_payable_aging = _make_list_endpoint(PayableAging, "payables/aging", "payable_aging")
_list_expenses = _make_list_endpoint(ExpenseRecord, "expenses", "expenses")
_list_payroll = _make_list_endpoint(PayrollRecord, "payroll", "payroll")
_list_fa = _make_list_endpoint(FixedAsset, "fixed-assets/assets", "fixed_assets")
_list_cip = _make_list_endpoint(ConstructionInProgress, "fixed-assets/cip", "cip")
_list_intangible = _make_list_endpoint(IntangibleAsset, "intangible/assets", "intangible_assets")
_list_rd = _make_list_endpoint(
    RDCapitalizationAssessment, "intangible/rd-assessments", "rd_assessments"
)
_list_lti = _make_list_endpoint(LongTermInvestment, "long-term-investment/items", "lti")
_list_lease = _make_list_endpoint(LeaseContract, "leases/contracts", "lease_contracts")
_list_tax = _make_list_endpoint(
    IncomeTaxReconciliation, "income-tax/reconciliations", "tax_reconciliations"
)
_list_ecl = _make_list_endpoint(ECLAssessment, "accounting-estimates/ecl", "ecl")
_list_imp = _make_list_endpoint(
    AssetImpairmentTest, "accounting-estimates/impairment", "impairment"
)
_list_prov = _make_list_endpoint(ProvisionEstimate, "accounting-estimates/provisions", "provisions")
_list_subseq = _make_list_endpoint(SubsequentEvent, "subsequent-events/events", "subseq_events")
_list_gc = _make_list_endpoint(
    GoingConcernAssessment, "subsequent-events/going-concern", "going_concern"
)
