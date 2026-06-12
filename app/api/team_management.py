"""API routes for team management module.

覆盖：人员 CRUD、项目分配、工作计划生成/查看/任务更新、会议 CRUD+纪要、
日报、卡点、dashboard、管理建议。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.db_models import (
    Blocker,
    DailyReport,
    ManagementRecommendation,
    Meeting,
    MeetingRecord,
    Project,
    ProjectAssignment,
    TeamMember,
    WorkPlan,
    WorkPlanItem,
    BLOCKER_STATUS_OPEN,
    BLOCKER_STATUS_IN_PROGRESS,
    BLOCKER_STATUS_ESCALATED,
    WORK_PLAN_STATUS_DRAFT,
    WORK_PLAN_STATUS_ACTIVE,
    WORK_PLAN_STATUS_COMPLETED,
    TASK_STATUS_DONE,
    MEMBER_STATUS_ACTIVE,
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
    WorkPlanCreate,
    WorkPlanItemResponse,
    WorkPlanResponse,
    WorkPlanUpdate,
)
from app.services.team_management import (
    team_management_service,
    work_plan_generator,
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
):
    """列出所有人员。"""
    q = select(TeamMember)
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
):
    """创建人员。"""
    m = TeamMember(**payload.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


@router.get("/members/{member_id}", response_model=TeamMemberResponse)
async def get_member(member_id: int, db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(TeamMember).where(TeamMember.id == member_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="人员不存在")
    return m


@router.put("/members/{member_id}", response_model=TeamMemberResponse)
async def update_member(
    member_id: int,
    payload: TeamMemberUpdate,
    db: AsyncSession = Depends(get_db),
):
    m = (await db.execute(select(TeamMember).where(TeamMember.id == member_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="人员不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return m


@router.delete("/members/{member_id}")
async def delete_member(member_id: int, db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(TeamMember).where(TeamMember.id == member_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="人员不存在")
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
async def list_project_assignments(project_id: int, db: AsyncSession = Depends(get_db)):
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
):
    proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
    assign = ProjectAssignment(project_id=project_id, **payload.model_dump())
    db.add(assign)
    await db.commit()
    await db.refresh(assign)
    # 重新查带 member
    member = (await db.execute(select(TeamMember).where(TeamMember.id == assign.member_id))).scalar_one()
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
async def remove_project_assignment(assignment_id: int, db: AsyncSession = Depends(get_db)):
    a = (await db.execute(select(ProjectAssignment).where(ProjectAssignment.id == assignment_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="分配记录不存在")
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
):
    """AI 自动生成（或重新生成）工作计划。"""
    proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
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
):
    """列出项目的所有工作计划（按状态过滤）。"""
    q = select(WorkPlan).where(WorkPlan.project_id == project_id)
    if status:
        q = q.where(WorkPlan.status == status)
    q = q.order_by(WorkPlan.created_at.desc())
    res = await db.execute(q)
    return res.scalars().all()


@router.get("/work-plans/{plan_id}", response_model=WorkPlanResponse)
async def get_work_plan(plan_id: int, db: AsyncSession = Depends(get_db)):
    plan = (await db.execute(select(WorkPlan).where(WorkPlan.id == plan_id))).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="工作计划不存在")
    return plan


@router.put("/work-plans/{plan_id}", response_model=WorkPlanResponse)
async def update_work_plan(
    plan_id: int,
    payload: WorkPlanUpdate,
    db: AsyncSession = Depends(get_db),
):
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
):
    """更新计划任务 — 用 Pydantic schema 强约束可写字段白名单。

    禁止改 plan_id / id / created_at / completed_at 等系统字段。
    """
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
):
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
):
    proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
    m = Meeting(project_id=project_id, **payload.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(meeting_id: int, db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="会议不存在")
    return m


@router.put("/meetings/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    db: AsyncSession = Depends(get_db),
):
    m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="会议不存在")
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
):
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
):
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
):
    proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
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
):
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
):
    proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")
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
):
    b = (await db.execute(select(Blocker).where(Blocker.id == blocker_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="卡点不存在")
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
async def get_dashboard(project_id: int, db: AsyncSession = Depends(get_db)):
    """项目综合进度看板。"""
    proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="项目不存在")

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
):
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
):
    """AI 周期性生成管理建议。"""
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
):
    try:
        return await team_management_service.confirm_recommendation(
            db, rec_id, payload.confirmed_by, payload.manager_notes
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
