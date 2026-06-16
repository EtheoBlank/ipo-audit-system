"""API routes for team management module.

覆盖：人员 CRUD、项目分配、工作计划生成/查看/任务更新、会议 CRUD+纪要、
日报、卡点、dashboard、管理建议。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.db_models import (
    Blocker,
    DailyReport,
    ManagementRecommendation,
    Meeting,
    ProjectAssignment,
    TeamMember,
    WorkPlan,
    WorkPlanItem,
    BLOCKER_STATUS_OPEN,
    MEMBER_STATUS_ACTIVE,
)
from app.models.db.auth import User
from app.services.auth import get_current_user, get_current_user_optional
from app.services.auth.tenant import (
    ensure_project_in_firm,
    ensure_team_member_in_firm,
    ensure_team_member_visible_query,
)
from app.models.team_management import (
    BlockerCreate,
    BlockerResponse,
    BlockerUpdate,
    DailyReportCreate,
    DailyReportResponse,
    ManagementRecommendationConfirm,
    ManagementRecommendationRequest,
    ManagementRecommendationResponse,
    MemberProgress,
    MeetingCreate,
    MeetingRecordCreate,
    MeetingRecordResponse,
    MeetingResponse,
    MeetingUpdate,
    BlockerSummary,
    ProgressDashboardResponse,
    ProjectAssignmentCreate,
    ProjectAssignmentResponse,
    ProjectProgress,
    TeamMemberCreate,
    TeamMemberResponse,
    TeamMemberUpdate,
    WorkPlanItemResponse,
    WorkPlanItemUpdate,
    WorkPlanResponse,
    WorkPlanUpdate,
)
from app.services.team_management import (
    team_management_service,
)
from app.services.team_management.progress_tracker import ProgressTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/team-management", tags=["项目组管理"])


# ============================================================
#  人员 (TeamMember)
# ============================================================


@router.get("/members", response_model=List[TeamMemberResponse])
async def list_members(
    level: Optional[str] = Query(None, description="按级别过滤"),
    status: Optional[str] = Query(MEMBER_STATUS_ACTIVE, description="按状态过滤"),
    skip: int = 0,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """列出所有人员 — 自动按 firm 隔离 (通过 ProjectAssignment→Project.firm_id 关联)."""
    q = await ensure_team_member_visible_query(current_user)
    if level:
        q = q.where(TeamMember.level == level)
    if status:
        q = q.where(TeamMember.status == status)
    q = q.order_by(TeamMember.level.desc(), TeamMember.full_name).offset(skip).limit(limit)
    res = await db.execute(q)
    return res.scalars().all()


@router.post("/members", response_model=TeamMemberResponse)
async def create_member(
    payload: TeamMemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建人员。"""
    m = TeamMember(**payload.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


@router.get("/members/{member_id}", response_model=TeamMemberResponse)
async def get_member(
    member_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # 多租户隔离: TeamMember 通过 ProjectAssignment→Project.firm_id 间接绑定 firm
    return await ensure_team_member_in_firm(db, member_id, current_user)


@router.put("/members/{member_id}", response_model=TeamMemberResponse)
async def update_member(
    member_id: int,
    payload: TeamMemberUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m = await ensure_team_member_in_firm(db, member_id, current_user)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return m


@router.delete("/members/{member_id}")
async def delete_member(
    member_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m = await ensure_team_member_in_firm(db, member_id, current_user)
    await db.delete(m)
    await db.commit()
    return {"message": "人员已删除"}


# ============================================================
#  项目人员分配 (ProjectAssignment)
# ============================================================


@router.get(
    "/projects/{project_id}/assignments",
    response_model=List[ProjectAssignmentResponse],
)
async def list_project_assignments(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # 多租户隔离: 先确保 project 在 user firm 内, 再查 assignment
    await ensure_project_in_firm(db, project_id, current_user)
    q = (
        select(ProjectAssignment, TeamMember)
        .join(TeamMember, TeamMember.id == ProjectAssignment.member_id)
        .where(ProjectAssignment.project_id == project_id)
        .order_by(ProjectAssignment.role_in_project, TeamMember.full_name)
    )
    res = await db.execute(q)
    out: list[ProjectAssignmentResponse] = []
    for assign, member in res.all():
        out.append(
            ProjectAssignmentResponse(
                id=assign.id,
                project_id=assign.project_id,
                member_id=assign.member_id,
                role_in_project=assign.role_in_project,
                hourly_rate=assign.hourly_rate,
                workload_pct=assign.workload_pct,
                start_date=assign.start_date,
                end_date=assign.end_date,
                created_at=assign.created_at,
                member=TeamMemberResponse.model_validate(member),
            )
        )
    return out


@router.post(
    "/projects/{project_id}/assignments",
    response_model=ProjectAssignmentResponse,
)
async def add_project_assignment(
    project_id: int,
    payload: ProjectAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_project_in_firm(db, project_id, current_user)
    assign = ProjectAssignment(project_id=project_id, **payload.model_dump())
    db.add(assign)
    await db.commit()
    await db.refresh(assign)
    # 重新查带 member
    member = (
        await db.execute(select(TeamMember).where(TeamMember.id == assign.member_id))
    ).scalar_one()
    return ProjectAssignmentResponse(
        id=assign.id,
        project_id=assign.project_id,
        member_id=assign.member_id,
        role_in_project=assign.role_in_project,
        hourly_rate=assign.hourly_rate,
        workload_pct=assign.workload_pct,
        start_date=assign.start_date,
        end_date=assign.end_date,
        created_at=assign.created_at,
        member=TeamMemberResponse.model_validate(member),
    )


@router.delete("/assignments/{assignment_id}")
async def remove_project_assignment(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    a = (
        await db.execute(select(ProjectAssignment).where(ProjectAssignment.id == assignment_id))
    ).scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="分配记录不存在")
    # 多租户隔离: 通过 assignment.project_id 校验 firm 归属
    await ensure_project_in_firm(db, a.project_id, current_user)
    await db.delete(a)
    await db.commit()
    return {"message": "已移除该成员"}


# ============================================================
#  工作计划 (WorkPlan + WorkPlanItem)
# ============================================================


@router.post(
    "/projects/{project_id}/work-plan/generate",
    response_model=WorkPlanResponse,
)
async def generate_work_plan(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI 自动生成（或重新生成）工作计划。"""
    await ensure_project_in_firm(db, project_id, current_user)
    info = await team_management_service.generate_work_plan(db, project_id)
    plan = (await db.execute(select(WorkPlan).where(WorkPlan.id == info["plan_id"]))).scalar_one()
    return plan


@router.get(
    "/projects/{project_id}/work-plan",
    response_model=List[WorkPlanResponse],
)
async def list_work_plans(
    project_id: int,
    status: Optional[str] = Query(None, description="按状态过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """列出项目的所有工作计划（按状态过滤）。"""
    await ensure_project_in_firm(db, project_id, current_user)
    q = select(WorkPlan).where(WorkPlan.project_id == project_id)
    if status:
        q = q.where(WorkPlan.status == status)
    q = q.order_by(WorkPlan.created_at.desc())
    res = await db.execute(q)
    return res.scalars().all()


@router.get("/work-plans/{plan_id}", response_model=WorkPlanResponse)
async def get_work_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    plan = (await db.execute(select(WorkPlan).where(WorkPlan.id == plan_id))).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="工作计划不存在")
    # 多租户隔离
    await ensure_project_in_firm(db, plan.project_id, current_user)
    return plan


@router.put("/work-plans/{plan_id}", response_model=WorkPlanResponse)
async def update_work_plan(
    plan_id: int,
    payload: WorkPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 多租户隔离: 先查 plan, 通过其 project_id 校验 firm
    plan = (await db.execute(select(WorkPlan).where(WorkPlan.id == plan_id))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="工作计划不存在")
    await ensure_project_in_firm(db, plan.project_id, current_user)
    try:
        return await team_management_service.update_work_plan(db, plan_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put(
    "/work-plan-items/{item_id}",
    response_model=WorkPlanItemResponse,
)
async def update_work_plan_item(
    item_id: int,
    payload: WorkPlanItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新计划任务 — 用 Pydantic schema 强约束可写字段白名单。

    禁止改 plan_id / id / created_at / completed_at 等系统字段。
    """
    # 多租户隔离: 通过 WorkPlanItem → WorkPlan → project 校验 firm
    item = (
        await db.execute(select(WorkPlanItem).where(WorkPlanItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="计划任务不存在")
    plan = (
        await db.execute(select(WorkPlan).where(WorkPlan.id == item.plan_id))
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="关联工作计划不存在")
    await ensure_project_in_firm(db, plan.project_id, current_user)
    try:
        return await team_management_service.update_work_plan_item(
            db, item_id, payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ============================================================
#  会议 (Meeting + MeetingRecord)
# ============================================================


@router.get(
    "/projects/{project_id}/meetings",
    response_model=List[MeetingResponse],
)
async def list_meetings(
    project_id: int,
    meeting_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await ensure_project_in_firm(db, project_id, current_user)
    q = select(Meeting).where(Meeting.project_id == project_id)
    if meeting_type:
        q = q.where(Meeting.meeting_type == meeting_type)
    if status:
        q = q.where(Meeting.status == status)
    q = q.order_by(Meeting.scheduled_at.desc())
    res = await db.execute(q)
    return res.scalars().all()


@router.post(
    "/projects/{project_id}/meetings",
    response_model=MeetingResponse,
)
async def create_meeting(
    project_id: int,
    payload: MeetingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_project_in_firm(db, project_id, current_user)
    m = Meeting(project_id=project_id, **payload.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="会议不存在")
    await ensure_project_in_firm(db, m.project_id, current_user)
    return m


@router.put("/meetings/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="会议不存在")
    await ensure_project_in_firm(db, m.project_id, current_user)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return m


@router.put(
    "/meetings/{meeting_id}/record",
    response_model=MeetingRecordResponse,
)
async def submit_meeting_record(
    meeting_id: int,
    payload: MeetingRecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 多租户隔离: 先查 meeting, 通过其 project_id 校验 firm
    m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    await ensure_project_in_firm(db, m.project_id, current_user)
    try:
        return await team_management_service.submit_meeting_record(db, meeting_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ============================================================
#  每日汇报 (DailyReport)
# ============================================================


@router.get(
    "/projects/{project_id}/daily-reports",
    response_model=List[DailyReportResponse],
)
async def list_daily_reports(
    project_id: int,
    member_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await ensure_project_in_firm(db, project_id, current_user)
    q = select(DailyReport).where(DailyReport.project_id == project_id)
    if member_id:
        q = q.where(DailyReport.member_id == member_id)
    if start_date:
        q = q.where(DailyReport.report_date >= start_date)
    if end_date:
        q = q.where(DailyReport.report_date <= end_date)
    q = q.order_by(DailyReport.report_date.desc(), DailyReport.submitted_at.desc()).limit(limit)
    res = await db.execute(q)
    return res.scalars().all()


@router.post(
    "/projects/{project_id}/daily-reports",
    response_model=DailyReportResponse,
)
async def create_daily_report(
    project_id: int,
    member_id: int = Query(..., description="汇报人 id"),
    payload: DailyReportCreate = ...,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_project_in_firm(db, project_id, current_user)
    # 成员也要校验 firm (汇报人必须是同所成员)
    await ensure_team_member_in_firm(db, member_id, current_user)
    r = DailyReport(project_id=project_id, member_id=member_id, **payload.model_dump())
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


# ============================================================
#  卡点 (Blocker)
# ============================================================


@router.get(
    "/projects/{project_id}/blockers",
    response_model=List[BlockerResponse],
)
async def list_blockers(
    project_id: int,
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    member_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await ensure_project_in_firm(db, project_id, current_user)
    q = select(Blocker).where(Blocker.project_id == project_id)
    if status:
        q = q.where(Blocker.status == status)
    if severity:
        q = q.where(Blocker.severity == severity)
    if member_id:
        q = q.where(Blocker.member_id == member_id)
    q = q.order_by(Blocker.raised_at.desc())
    res = await db.execute(q)
    return res.scalars().all()


@router.post(
    "/projects/{project_id}/blockers",
    response_model=BlockerResponse,
)
async def create_blocker(
    project_id: int,
    member_id: int = Query(..., description="提出人 id"),
    payload: BlockerCreate = ...,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_project_in_firm(db, project_id, current_user)
    await ensure_team_member_in_firm(db, member_id, current_user)
    b = Blocker(
        project_id=project_id,
        member_id=member_id,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        related_task_id=payload.related_task_id,
        status=BLOCKER_STATUS_OPEN,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b


@router.put("/blockers/{blocker_id}", response_model=BlockerResponse)
async def update_blocker(
    blocker_id: int,
    payload: BlockerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    b = (await db.execute(select(Blocker).where(Blocker.id == blocker_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="卡点不存在")
    await ensure_project_in_firm(db, b.project_id, current_user)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(b, k, v)
    # 状态 → resolved 时自动写 resolved_at
    if payload.status == "resolved" and not b.resolved_at:
        b.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(b)
    return b


# ============================================================
#  Dashboard
# ============================================================


@router.get(
    "/projects/{project_id}/dashboard",
    response_model=ProgressDashboardResponse,
)
async def get_dashboard(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """项目综合进度看板。"""
    proj = await ensure_project_in_firm(db, project_id, current_user)

    proj_summary = await ProgressTracker.collect_project_summary(db, project_id)
    blocker_summary = await ProgressTracker.collect_blocker_summary(db, project_id)
    members = await ProgressTracker.collect_member_progress(db, project_id)

    return ProgressDashboardResponse(
        project=ProjectProgress(
            project_id=project_id,
            project_name=proj.name,
            total_items=proj_summary["total_items"],
            completed_items=proj_summary["completed_items"],
            in_progress_items=proj_summary["in_progress_items"],
            blocked_items=proj_summary["blocked_items"],
            completion_rate=proj_summary["completion_rate"],
            total_estimated_hours=proj_summary["total_estimated_hours"],
            total_actual_hours=proj_summary["total_actual_hours"],
            open_blockers=blocker_summary["total_open"],
            critical_blockers=blocker_summary["critical"],
            members=[
                MemberProgress(
                    member_id=m.member_id,
                    full_name=m.full_name,
                    level=m.level,
                    total_items=m.total_items,
                    completed_items=m.completed_items,
                    in_progress_items=m.in_progress_items,
                    blocked_items=m.blocked_items,
                    completion_rate=m.completion_rate,
                    hours_logged_7d=m.hours_logged_7d,
                    open_blockers=m.open_blockers,
                    last_report_date=m.last_report_date,
                )
                for m in members
            ],
        ),
        blockers=BlockerSummary(**blocker_summary),
        by_module=proj_summary["by_module"],
        by_status=proj_summary["by_status"],
    )


# ============================================================
#  管理建议 (ManagementRecommendation)
# ============================================================


@router.get(
    "/projects/{project_id}/recommendations",
    response_model=List[ManagementRecommendationResponse],
)
async def list_recommendations(
    project_id: int,
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await ensure_project_in_firm(db, project_id, current_user)
    q = (
        select(ManagementRecommendation)
        .where(ManagementRecommendation.project_id == project_id)
        .order_by(ManagementRecommendation.generated_at.desc())
        .limit(limit)
    )
    res = await db.execute(q)
    return res.scalars().all()


@router.post(
    "/projects/{project_id}/recommendations/generate",
    response_model=ManagementRecommendationResponse,
)
async def generate_recommendation(
    project_id: int,
    payload: Optional[ManagementRecommendationRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI 周期性生成管理建议。"""
    await ensure_project_in_firm(db, project_id, current_user)
    payload = payload or ManagementRecommendationRequest()
    try:
        rec = await team_management_service.generate_recommendations(
            db,
            project_id,
            period_start=payload.period_start,
            period_end=payload.period_end,
        )
        return rec
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/recommendations/{rec_id}/confirm",
    response_model=ManagementRecommendationResponse,
)
async def confirm_recommendation(
    rec_id: int,
    payload: ManagementRecommendationConfirm,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 多租户隔离: 通过 rec.project_id 校验 firm
    rec = (
        await db.execute(
            select(ManagementRecommendation).where(ManagementRecommendation.id == rec_id)
        )
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="建议记录不存在")
    await ensure_project_in_firm(db, rec.project_id, current_user)
    try:
        return await team_management_service.confirm_recommendation(
            db, rec_id, payload.confirmed_by, payload.manager_notes
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
