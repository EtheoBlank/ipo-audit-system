"""Related Parties API (Pack B). /api/related-parties/*"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._helpers import get_project_or_404
from app.core.database import get_db
from app.models.db.auth import (
    AUDIT_ACTION_CREATE,
    AUDIT_ACTION_DELETE,
    AUDIT_ACTION_IMPORT,
    AUDIT_ACTION_UPDATE,
    ROLE_ASSISTANT,
    User,
)
from app.models.db.notification import (
    NOTIF_MODULE_RELATED_PARTY,
    NOTIF_SEVERITY_CRITICAL,
    NOTIF_SEVERITY_NOTICE,
    NOTIF_SEVERITY_WARN,
)
from app.models.db.related_parties import (
    PeerCompetitionAssessment,
    ProspectusDisclosureGap,
    RelatedParty,
    RelatedPartyCapitalOccupation,
    RelatedPartyRelation,
    RelatedPartyTransaction,
)
from app.models.related_parties import (
    CapitalOccupationCreate,
    CapitalOccupationResponse,
    DetectorRunRequest,
    DetectorRunResponse,
    DisclosureCheckRequest,
    DisclosureCheckResponse,
    DisclosureGapResponse,
    FairnessCheckRequest,
    FairnessCheckResponse,
    PeerCompetitionAssessRequest,
    PeerCompetitionResponse,
    RelatedPartyCreate,
    RelatedPartyListResponse,
    RelatedPartyResponse,
    RelatedPartyUpdate,
    RelationCreate,
    RelationResponse,
    TransactionCreate,
    TransactionResponse,
)
from app.services.auth import (
    get_current_user,
    record_audit_log,
    require_role,
)
from app.services.notification import NotificationService
from app.services.related_parties import (
    CapitalOccupationService,
    DisclosureChecker,
    PeerCompetitionService,
    RelatedPartyDetector,
    TransactionAnalyzer,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/related-parties", tags=["关联方专项"])


# ============================================================
#  主数据 CRUD
# ============================================================


@router.post("/projects/{project_id}/parties", response_model=RelatedPartyResponse)
async def create_party(
    project_id: int,
    payload: RelatedPartyCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    rp = RelatedParty(
        project_id=project_id,
        **payload.model_dump(),
        is_confirmed=True,
        created_by_user_id=current_user.id or None,
        created_by_display=current_user.full_name,
    )
    db.add(rp)
    await db.commit()
    await db.refresh(rp)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_CREATE,
        resource_type="related_party",
        resource_id=rp.id,
        project_id=project_id,
        summary=f"新建关联方 {rp.name} ({rp.party_type})",
    )
    return RelatedPartyResponse.model_validate(rp)


@router.get("/projects/{project_id}/parties", response_model=RelatedPartyListResponse)
async def list_parties(
    project_id: int,
    party_type: Optional[str] = None,
    is_confirmed: Optional[bool] = None,
    keyword: Optional[str] = Query(None, max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    conds = [RelatedParty.project_id == project_id]
    if party_type:
        conds.append(RelatedParty.party_type == party_type)
    if is_confirmed is not None:
        conds.append(RelatedParty.is_confirmed == is_confirmed)
    if keyword:
        from app.services.auth.audit_log import _escape_like

        like = f"%{_escape_like(keyword[:200])}%"
        conds.append(RelatedParty.name.ilike(like, escape="\\"))
    where = and_(*conds)
    total = int(
        (await db.execute(select(func.count(RelatedParty.id)).where(where))).scalar_one() or 0
    )
    stmt = (
        select(RelatedParty)
        .where(where)
        .order_by(desc(RelatedParty.created_at))
        .offset(skip)
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return RelatedPartyListResponse(
        total=total, items=[RelatedPartyResponse.model_validate(r) for r in rows]
    )


@router.put("/parties/{party_id}", response_model=RelatedPartyResponse)
async def update_party(
    party_id: int,
    payload: RelatedPartyUpdate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    rp = (
        await db.execute(select(RelatedParty).where(RelatedParty.id == party_id))
    ).scalar_one_or_none()
    if rp is None:
        raise HTTPException(404, "关联方不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(rp, k, v)
    await db.commit()
    await db.refresh(rp)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="related_party",
        resource_id=party_id,
        project_id=rp.project_id,
        summary=f"修改关联方 {rp.name}",
    )
    return RelatedPartyResponse.model_validate(rp)


@router.delete("/parties/{party_id}")
async def delete_party(
    party_id: int,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    rp = (
        await db.execute(select(RelatedParty).where(RelatedParty.id == party_id))
    ).scalar_one_or_none()
    if rp is None:
        raise HTTPException(404, "关联方不存在")
    pid, name = rp.project_id, rp.name
    await db.delete(rp)
    await db.commit()
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_DELETE,
        resource_type="related_party",
        resource_id=party_id,
        project_id=pid,
        summary=f"删除关联方 {name}",
    )
    return {"detail": "已删除"}


# ============================================================
#  关系图
# ============================================================


@router.post("/projects/{project_id}/relations", response_model=RelationResponse)
async def create_relation(
    project_id: int,
    payload: RelationCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    rel = RelatedPartyRelation(project_id=project_id, **payload.model_dump())
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return RelationResponse.model_validate(rel)


@router.get("/projects/{project_id}/relations", response_model=List[RelationResponse])
async def list_relations(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = list(
        (
            await db.execute(
                select(RelatedPartyRelation).where(RelatedPartyRelation.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    return [RelationResponse.model_validate(r) for r in rows]


# ============================================================
#  识别引擎
# ============================================================


@router.post("/projects/{project_id}/detector/run", response_model=DetectorRunResponse)
async def run_detector(
    project_id: int,
    payload: Optional[DetectorRunRequest] = None,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    req = payload or DetectorRunRequest(project_id=project_id)
    req.project_id = project_id
    result = await RelatedPartyDetector.run(
        db, req, user_id=current_user.id or None, user_display=current_user.full_name
    )
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_IMPORT,
        resource_type="related_party.detector",
        project_id=project_id,
        summary=f"关联方识别 候选 {result.new_candidates} 条",
    )
    if result.new_candidates > 0:
        await NotificationService.push(
            db,
            module=NOTIF_MODULE_RELATED_PARTY,
            type="related_party.candidates_detected",
            title=f"识别到 {result.new_candidates} 个关联方候选",
            body="请在前端 '关联方专项' 页面复核并确认",
            project_id=project_id,
            severity=NOTIF_SEVERITY_NOTICE,
        )
    return result


# ============================================================
#  关联交易
# ============================================================


@router.post("/projects/{project_id}/transactions", response_model=TransactionResponse)
async def create_transaction(
    project_id: int,
    payload: TransactionCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    tx = RelatedPartyTransaction(project_id=project_id, **payload.model_dump())
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    return TransactionResponse.model_validate(tx)


@router.get("/projects/{project_id}/transactions", response_model=List[TransactionResponse])
async def list_transactions(
    project_id: int,
    party_id: Optional[int] = None,
    period_end: Optional[str] = None,
    transaction_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conds = [RelatedPartyTransaction.project_id == project_id]
    if party_id:
        conds.append(RelatedPartyTransaction.party_id == party_id)
    if period_end:
        conds.append(RelatedPartyTransaction.period_end == period_end)
    if transaction_type:
        conds.append(RelatedPartyTransaction.transaction_type == transaction_type)
    stmt = (
        select(RelatedPartyTransaction)
        .where(and_(*conds))
        .order_by(desc(RelatedPartyTransaction.created_at))
        .offset(skip)
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [TransactionResponse.model_validate(r) for r in rows]


@router.post(
    "/projects/{project_id}/transactions/check-fairness",
    response_model=FairnessCheckResponse,
)
async def check_fairness(
    project_id: int,
    payload: FairnessCheckRequest,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    result = await TransactionAnalyzer.check_fairness(db, payload, project_id=project_id)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="related_party.transactions.fairness",
        project_id=project_id,
        summary=f"公允性测试: 评估 {result.assessed}, 公允 {result.fair}, 不公允 {result.not_fair}",
    )
    if result.not_fair > 0:
        await NotificationService.push(
            db,
            module=NOTIF_MODULE_RELATED_PARTY,
            type="related_party.unfair_transaction",
            title=f"{result.not_fair} 笔关联交易公允性偏离 > 10%",
            body=result.notes or "",
            project_id=project_id,
            severity=NOTIF_SEVERITY_WARN,
        )
    return result


# ============================================================
#  资金占用
# ============================================================


@router.post(
    "/projects/{project_id}/capital-occupations",
    response_model=CapitalOccupationResponse,
)
async def create_capital_occupation(
    project_id: int,
    payload: CapitalOccupationCreate,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    co = RelatedPartyCapitalOccupation(project_id=project_id, **payload.model_dump())
    db.add(co)
    await db.commit()
    await db.refresh(co)
    return CapitalOccupationResponse.model_validate(co)


@router.get(
    "/projects/{project_id}/capital-occupations",
    response_model=List[CapitalOccupationResponse],
)
async def list_capital_occupations(
    project_id: int,
    party_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conds = [RelatedPartyCapitalOccupation.project_id == project_id]
    if party_id:
        conds.append(RelatedPartyCapitalOccupation.party_id == party_id)
    stmt = select(RelatedPartyCapitalOccupation).where(and_(*conds))
    rows = list((await db.execute(stmt)).scalars().all())
    return [CapitalOccupationResponse.model_validate(r) for r in rows]


@router.get("/projects/{project_id}/capital-occupations/auto-compute")
async def auto_compute_occupation(
    project_id: int,
    party_id: int,
    period_start: str,
    period_end: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从序时账自动算最大占用. 不入库, 仅返回供前端预览."""
    await get_project_or_404(db, project_id)
    return await CapitalOccupationService.compute_max_occupation(
        db,
        project_id=project_id,
        party_id=party_id,
        period_start=period_start,
        period_end=period_end,
    )


# ============================================================
#  同业竞争
# ============================================================


@router.post(
    "/projects/{project_id}/peer-competition/assess",
    response_model=PeerCompetitionResponse,
)
async def assess_peer_competition(
    project_id: int,
    payload: PeerCompetitionAssessRequest,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        result = await PeerCompetitionService.assess(
            db,
            project_id=project_id,
            party_id=payload.party_id,
            issuer_keywords=payload.issuer_business_keywords,
            user_id=current_user.id or None,
            user_display=current_user.full_name,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if result.risk_level in ("high", "critical"):
        await NotificationService.push(
            db,
            module=NOTIF_MODULE_RELATED_PARTY,
            type="related_party.peer_competition_risk",
            title=f"同业竞争风险: {result.risk_level} (重合度 {result.overlap_score})",
            body=f"关联方 ID {payload.party_id}, 命中关键词: {result.overlap_keywords}",
            project_id=project_id,
            severity=NOTIF_SEVERITY_CRITICAL
            if result.risk_level == "critical"
            else NOTIF_SEVERITY_WARN,
        )
    return PeerCompetitionResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/peer-competition",
    response_model=List[PeerCompetitionResponse],
)
async def list_peer_competition(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = list(
        (
            await db.execute(
                select(PeerCompetitionAssessment).where(
                    PeerCompetitionAssessment.project_id == project_id
                )
            )
        )
        .scalars()
        .all()
    )
    return [PeerCompetitionResponse.model_validate(r) for r in rows]


# ============================================================
#  招股书披露 diff
# ============================================================


@router.post(
    "/projects/{project_id}/disclosure/check",
    response_model=DisclosureCheckResponse,
)
async def check_disclosure(
    project_id: int,
    payload: DisclosureCheckRequest,
    current_user: User = Depends(require_role(ROLE_ASSISTANT)),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    payload.project_id = project_id
    result = await DisclosureChecker.diff(
        db, project_id=project_id, prospectus_party_names=payload.prospectus_party_names
    )
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_IMPORT,
        resource_type="related_party.disclosure",
        project_id=project_id,
        summary=(
            f"披露 diff: critical {result.total_critical}, "
            f"review {result.total_review}, matched {result.matched}"
        ),
    )
    if result.total_critical > 0:
        await NotificationService.push(
            db,
            module=NOTIF_MODULE_RELATED_PARTY,
            type="related_party.disclosure_critical",
            title=f"{result.total_critical} 个关联方系统识别但招股书未披露",
            body="必须立即补充招股书 '关联方及关联交易' 章节",
            project_id=project_id,
            severity=NOTIF_SEVERITY_CRITICAL,
        )
    return result


@router.get(
    "/projects/{project_id}/disclosure/gaps",
    response_model=List[DisclosureGapResponse],
)
async def list_disclosure_gaps(
    project_id: int,
    gap_status: Optional[str] = None,
    resolved: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conds = [ProspectusDisclosureGap.project_id == project_id]
    if gap_status:
        conds.append(ProspectusDisclosureGap.gap_status == gap_status)
    if resolved is not None:
        conds.append(ProspectusDisclosureGap.resolved == resolved)
    rows = list(
        (await db.execute(select(ProspectusDisclosureGap).where(and_(*conds)))).scalars().all()
    )
    return [DisclosureGapResponse.model_validate(r) for r in rows]
