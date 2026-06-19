"""Pack D — IPO 专属 API. /api/ipo-specials/*"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.db.auth import ROLE_ASSISTANT, User
from app.models.db.ipo_specials import (
    CustomerSupplierOverlap,
    FeedbackLetter,
    FeedbackQuestion,
    PeerCompany,
    PeriodComparisonReport,
    Prospectus,
    ProspectusKeyMetric,
    SubmissionChecklistItem,
)
from app.services.auth import get_current_user, record_audit_log, require_role
from app.services.auth.tenant import ensure_project_in_firm
from app.services.ipo_specials import (
    DEFAULT_SUBMISSION_CHECKLIST,
    FeedbackSLAMonitor,
    OverlapDetector,
    PeerBenchmarkAnalyzer,
    PeriodAnomalyDetector,
    ProspectusReconciler,
    RevenueCutoffTester,
    WalkthroughSampler,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ipo-specials", tags=["IPO 专属 (Pack D)"])


# ============================================================
#  Phase 16 — 内控穿行
# ============================================================


class FlowchartRequest(BaseModel):
    steps: List[Dict[str, Any]] = Field(..., min_length=1)


@router.post("/walkthrough/mermaid-flowchart")
async def gen_flowchart(
    payload: FlowchartRequest,
    current_user: User = Depends(get_current_user),
):
    mermaid = WalkthroughSampler.to_mermaid_flowchart(payload.steps)
    return {"mermaid": mermaid}


class SampleRequest(BaseModel):
    cycle_code: str
    items: List[Dict[str, Any]]
    n: int = Field(default=3, ge=1, le=20)


@router.post("/walkthrough/sample")
async def sample_walkthrough(
    payload: SampleRequest,
    current_user: User = Depends(get_current_user),
):
    samples = WalkthroughSampler.select_samples(payload.items, payload.cycle_code, payload.n)
    return {"samples": samples, "count": len(samples)}


# ============================================================
#  Phase 17 — 截止性
# ============================================================


class CutoffJudgeRequest(BaseModel):
    ship_date: Optional[str] = None
    revenue_confirm_date: Optional[str] = None
    period_end: str
    cutoff_days: int = Field(default=5, ge=1, le=30)


@router.post("/revenue-cutoff/judge")
async def cutoff_judge(
    payload: CutoffJudgeRequest,
    current_user: User = Depends(get_current_user),
):
    judgement, diff_days = RevenueCutoffTester.judge(
        payload.ship_date,
        payload.revenue_confirm_date,
        payload.period_end,
        payload.cutoff_days,
    )
    return {
        "judgement": judgement,
        "diff_days": diff_days,
        "adjustment_required": judgement != "normal",
    }


# ============================================================
#  招股书勾稽
# ============================================================


@router.post("/prospectus/projects/{project_id}/upload")
async def upload_prospectus(
    project_id: int,
    version: str = "v1",
    filename: Optional[str] = None,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    """记录招股书 (实际文件可走 report_templates / file upload, 这里只登记 metadata)."""
    await ensure_project_in_firm(db, project_id, current_user)
    from datetime import date

    # 先把旧版本置为非 current
    rows = list(
        (await db.execute(select(Prospectus).where(Prospectus.project_id == project_id)))
        .scalars()
        .all()
    )
    for r in rows:
        r.is_current = False
    p = Prospectus(
        project_id=project_id,
        version=version,
        filename=filename,
        upload_date=str(date.today()),
        is_current=True,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action="create",
        resource_type="prospectus",
        resource_id=p.id,
        project_id=project_id,
        summary=f"上传招股书 {version}",
    )
    return {"id": p.id, "version": p.version, "is_current": p.is_current}


@router.get("/prospectus/projects/{project_id}/list")
async def list_prospectuses(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    rows = list(
        (await db.execute(select(Prospectus).where(Prospectus.project_id == project_id)))
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "version": r.version,
            "upload_date": r.upload_date,
            "is_current": r.is_current,
            "filename": r.filename,
        }
        for r in rows
    ]


class MetricSubmitRequest(BaseModel):
    metric_code: str
    metric_name: str
    period_label: str
    prospectus_value: float
    system_value: Optional[float] = None
    unit: str = "元"


@router.post("/prospectus/{prospectus_id}/metrics")
async def add_metric(
    prospectus_id: int,
    payload: MetricSubmitRequest,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    p = (
        await db.execute(select(Prospectus).where(Prospectus.id == prospectus_id))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "招股书不存在")
    # IDOR fix (P0): 校验招股书所属 project 在 user 事务所内 — 否则 403
    await ensure_project_in_firm(db, p.project_id, current_user)
    m = ProspectusKeyMetric(prospectus_id=prospectus_id, **payload.model_dump())
    m = await ProspectusReconciler.reconcile_metric(db, prospectus_id=prospectus_id, metric=m)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return {
        "id": m.id,
        "metric_code": m.metric_code,
        "is_matched": m.is_matched,
        "diff_amount": m.diff_amount,
        "diff_pct": m.diff_pct,
    }


# ============================================================
#  三年一期对比 — 异动检测
# ============================================================


class PeriodMetricCreate(BaseModel):
    report_type: str
    metric_code: str
    metric_name: str
    value_period_1: float = 0.0
    value_period_2: float = 0.0
    value_period_3: float = 0.0
    value_period_h1: float = 0.0


@router.post("/period-comparison/projects/{project_id}/metrics")
async def add_period_metric(
    project_id: int,
    payload: PeriodMetricCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    yoy = 0.0
    if payload.value_period_2 != 0:
        yoy = (payload.value_period_3 - payload.value_period_2) / abs(payload.value_period_2) * 100
    p = PeriodComparisonReport(
        project_id=project_id,
        **payload.model_dump(),
        yoy_change_pct=round(yoy, 2),
    )
    p.anomaly_flag = PeriodAnomalyDetector.detect_anomaly(p)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return {
        "id": p.id,
        "yoy_change_pct": p.yoy_change_pct,
        "anomaly_flag": p.anomaly_flag,
    }


@router.get("/period-comparison/projects/{project_id}/list")
async def list_period_metrics(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    rows = list(
        (
            await db.execute(
                select(PeriodComparisonReport).where(
                    PeriodComparisonReport.project_id == project_id
                )
            )
        )
        .scalars()
        .all()
    )
    return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]


# ============================================================
#  客户/供应商重叠
# ============================================================


class OverlapDetectRequest(BaseModel):
    customer_names: List[str]
    supplier_names: List[str]
    fuzzy_threshold: float = Field(default=0.75, ge=0.5, le=1.0)


@router.post("/overlap/projects/{project_id}/detect")
async def detect_overlap(
    project_id: int,
    payload: OverlapDetectRequest,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    overlaps = await OverlapDetector.find_overlaps(
        db,
        project_id=project_id,
        customer_names=payload.customer_names,
        supplier_names=payload.supplier_names,
        fuzzy_threshold=payload.fuzzy_threshold,
    )
    # 入库
    for o in overlaps:
        db.add(
            CustomerSupplierOverlap(
                project_id=project_id,
                party_name=o["customer_name"],
                match_type=o["match_type"],
                fuzzy_score=o["fuzzy_score"],
                customer_sales=0,
                supplier_purchases=0,
                explanation_required=True,
            )
        )
    await db.commit()
    return {"overlaps_found": len(overlaps), "details": overlaps}


@router.get("/overlap/projects/{project_id}/list")
async def list_overlaps(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    rows = list(
        (
            await db.execute(
                select(CustomerSupplierOverlap).where(
                    CustomerSupplierOverlap.project_id == project_id
                )
            )
        )
        .scalars()
        .all()
    )
    return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]


# ============================================================
#  可比公司
# ============================================================


class PeerCompanyCreate(BaseModel):
    stock_code: Optional[str] = None
    short_name: str
    full_name: Optional[str] = None
    industry_code: Optional[str] = None
    main_business: Optional[str] = None
    market_cap: Optional[float] = None


@router.post("/peer-companies/projects/{project_id}")
async def add_peer(
    project_id: int,
    payload: PeerCompanyCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    p = PeerCompany(project_id=project_id, **payload.model_dump())
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return {"id": p.id, "short_name": p.short_name}


@router.get("/peer-companies/projects/{project_id}/list")
async def list_peers(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    rows = list(
        (await db.execute(select(PeerCompany).where(PeerCompany.project_id == project_id)))
        .scalars()
        .all()
    )
    return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]


class BenchmarkRequest(BaseModel):
    issuer_value: float
    peer_values: List[float]


@router.post("/peer-companies/benchmark")
async def benchmark(
    payload: BenchmarkRequest,
    current_user: User = Depends(get_current_user),
):
    return PeerBenchmarkAnalyzer.issuer_vs_peers(payload.issuer_value, payload.peer_values)


# ============================================================
#  反馈意见 / 问询函
# ============================================================


class FeedbackLetterCreate(BaseModel):
    letter_no: str
    issuer: str
    issue_date: str
    received_date: str
    reply_deadline: str
    sla_days: int = 30
    title: Optional[str] = None


@router.post("/feedback/projects/{project_id}/letters")
async def add_letter(
    project_id: int,
    payload: FeedbackLetterCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    fl = FeedbackLetter(project_id=project_id, **payload.model_dump())
    db.add(fl)
    await db.commit()
    await db.refresh(fl)
    return {"id": fl.id, "status": fl.status}


@router.get("/feedback/projects/{project_id}/letters")
async def list_letters(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    rows = list(
        (await db.execute(select(FeedbackLetter).where(FeedbackLetter.project_id == project_id)))
        .scalars()
        .all()
    )
    items = []
    for r in rows:
        d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        days_left = FeedbackSLAMonitor.days_to_deadline(r.reply_deadline)
        d["days_to_deadline"] = days_left
        d["urgency"] = FeedbackSLAMonitor.urgency_level(days_left)
        items.append(d)
    return items


class FeedbackQuestionCreate(BaseModel):
    letter_id: int
    question_no: str
    question_text: str
    related_module: Optional[str] = None
    severity: str = "warn"


@router.post("/feedback/questions")
async def add_question(
    payload: FeedbackQuestionCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    # IDOR fix (P0): 校验问询函所属 project 在 user 事务所内 — 否则 403
    letter = (
        await db.execute(
            select(FeedbackLetter).where(FeedbackLetter.id == payload.letter_id)
        )
    ).scalar_one_or_none()
    if letter is None:
        raise HTTPException(404, "反馈意见不存在")
    await ensure_project_in_firm(db, letter.project_id, current_user)
    q = FeedbackQuestion(**payload.model_dump())
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return {"id": q.id}


# ============================================================
#  申报清单
# ============================================================


@router.post("/submission/projects/{project_id}/init-checklist")
async def init_checklist(
    project_id: int,
    board_type: str = "main_board",
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    """用内置模板初始化项目的申报材料清单."""
    await ensure_project_in_firm(db, project_id, current_user)
    # 清旧
    existing = list(
        (
            await db.execute(
                select(SubmissionChecklistItem).where(
                    SubmissionChecklistItem.project_id == project_id,
                    SubmissionChecklistItem.board_type == board_type,
                )
            )
        )
        .scalars()
        .all()
    )
    for e in existing:
        if not e.is_uploaded:
            await db.delete(e)

    existing_codes = {e.item_code for e in existing if e.is_uploaded}
    added = 0
    for code, name, required in DEFAULT_SUBMISSION_CHECKLIST:
        if code in existing_codes:
            continue
        db.add(
            SubmissionChecklistItem(
                project_id=project_id,
                board_type=board_type,
                item_code=code,
                item_name=name,
                is_required=required,
                is_uploaded=False,
            )
        )
        added += 1
    await db.commit()
    return {
        "added": added,
        "board_type": board_type,
        "total_default": len(DEFAULT_SUBMISSION_CHECKLIST),
    }


@router.get("/submission/projects/{project_id}/checklist")
async def get_checklist(
    project_id: int,
    board_type: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_project_in_firm(db, project_id, current_user)
    conds = [SubmissionChecklistItem.project_id == project_id]
    if board_type:
        conds.append(SubmissionChecklistItem.board_type == board_type)
    from sqlalchemy import and_

    rows = list(
        (await db.execute(select(SubmissionChecklistItem).where(and_(*conds)))).scalars().all()
    )
    return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]


class ChecklistItemUpdate(BaseModel):
    is_uploaded: bool
    upload_date: Optional[str] = None
    file_path: Optional[str] = None
    notes: Optional[str] = None


@router.put("/submission/checklist/{item_id}")
async def update_checklist_item(
    item_id: int,
    payload: ChecklistItemUpdate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    item = (
        await db.execute(
            select(SubmissionChecklistItem).where(SubmissionChecklistItem.id == item_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "清单项不存在")
    # IDOR fix (P0): 校验清单项所属 project 在 user 事务所内 — 否则 403
    await ensure_project_in_firm(db, item.project_id, current_user)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(item, k, v)
    if payload.is_uploaded:
        item.uploaded_by_display = current_user.full_name
    await db.commit()
    await db.refresh(item)
    return {
        "id": item.id,
        "item_name": item.item_name,
        "is_uploaded": item.is_uploaded,
        "upload_date": item.upload_date,
    }
