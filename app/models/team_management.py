"""Pydantic schemas for team management module.

按 Base / Create / Update / Response 四类分离模式，遵循项目内其他模块的
``audit.py`` / ``sales_ledger.py`` 风格。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ============================================================
#  通用
# ============================================================


def _maybe_json(raw: Optional[str]) -> Any:
    """把存储在 Text 字段里的 JSON 字符串反序列化。失败时返回原字符串。"""
    if raw is None:
        return None
    if not raw:
        return None
    import json

    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


# ============================================================
#  TeamMember 人员
# ============================================================


class TeamMemberBase(BaseModel):
    """人员基础字段。"""

    full_name: str = Field(..., description="姓名")
    email: Optional[str] = Field(None, description="邮箱")
    phone: Optional[str] = Field(None, description="电话")
    level: str = Field(
        "auditor", description="级别: lead/senior_manager/manager/senior_auditor/auditor"
    )
    specialties: Optional[str] = Field(None, description="擅长领域 (JSON 数组字符串)")
    status: str = Field("active", description="状态: active/inactive")
    joined_at: Optional[str] = Field(None, description="入职日期 YYYY-MM-DD")
    notes: Optional[str] = Field(None, description="备注")


class TeamMemberCreate(TeamMemberBase):
    """创建人员。"""

    pass


class TeamMemberUpdate(BaseModel):
    """更新人员 — 全部 Optional。"""

    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    level: Optional[str] = None
    specialties: Optional[str] = None
    status: Optional[str] = None
    joined_at: Optional[str] = None
    notes: Optional[str] = None


class TeamMemberResponse(TeamMemberBase):
    """人员响应。"""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
#  ProjectAssignment 项目人员分配
# ============================================================


class ProjectAssignmentBase(BaseModel):
    """项目分配基础字段。"""

    member_id: int = Field(..., description="人员 id")
    role_in_project: str = Field("member", description="项目内角色: lead/deputy/reviewer/member")
    hourly_rate: Optional[float] = Field(None, description="小时费率")
    workload_pct: float = Field(100.0, description="投入百分比 0-100")
    start_date: Optional[str] = Field(None, description="入场日期")
    end_date: Optional[str] = Field(None, description="退场日期")


class ProjectAssignmentCreate(ProjectAssignmentBase):
    """项目分配创建。"""

    pass


class ProjectAssignmentResponse(ProjectAssignmentBase):
    """项目分配响应。"""

    id: int
    project_id: int
    member: Optional[TeamMemberResponse] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
#  WorkPlan 工作计划 + Items
# ============================================================


class WorkPlanItemBase(BaseModel):
    """计划任务基础字段。"""

    title: str = Field(..., description="任务标题")
    description: Optional[str] = Field(None, description="任务描述")
    related_module: Optional[str] = Field(
        None, description="关联模块: 底稿/函证/盘点/销售/合同/监管/其他"
    )
    priority: str = Field("medium", description="优先级: high/medium/low")
    estimated_hours: float = Field(0.0, description="预计工时")
    start_date: Optional[str] = Field(None, description="开始日期 YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="截止日期 YYYY-MM-DD")
    recommended_level: Optional[str] = Field(None, description="建议人员级别")


class WorkPlanItemCreate(WorkPlanItemBase):
    """创建计划任务。"""

    member_id: Optional[int] = None
    parent_item_id: Optional[int] = None
    sort_order: int = 0


class WorkPlanItemUpdate(BaseModel):
    """更新计划任务 — 全部 Optional。"""

    title: Optional[str] = None
    description: Optional[str] = None
    member_id: Optional[int] = None
    related_module: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    sort_order: Optional[int] = None


class WorkPlanItemResponse(WorkPlanItemBase):
    """计划任务响应。"""

    id: int
    plan_id: int
    member_id: Optional[int] = None
    status: str
    actual_hours: float
    completed_at: Optional[datetime] = None
    parent_item_id: Optional[int] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime
    assignee: Optional[TeamMemberResponse] = None

    model_config = {"from_attributes": True}


class WorkPlanBase(BaseModel):
    """工作计划基础字段。"""

    name: str = Field(..., description="计划名称")
    notes: Optional[str] = Field(None, description="备注")


class WorkPlanCreate(WorkPlanBase):
    """创建工作计划（通常由 AI 一次性生成，含 items）。"""

    items: list[WorkPlanItemCreate] = Field(default_factory=list)


class WorkPlanUpdate(BaseModel):
    """更新工作计划。"""

    name: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class WorkPlanResponse(WorkPlanBase):
    """工作计划响应。"""

    id: int
    project_id: int
    status: str
    generated_at: datetime
    generated_by: Optional[str] = None
    total_estimated_hours: float
    ai_enabled: bool
    created_at: datetime
    updated_at: datetime
    items: list[WorkPlanItemResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ============================================================
#  Meeting 会议 + MeetingRecord 纪要
# ============================================================


class MeetingBase(BaseModel):
    """会议基础字段。"""

    title: str = Field(..., description="会议标题")
    meeting_type: str = Field("weekly", description="类型: daily/weekly/kickoff/review/adhoc")
    scheduled_at: str = Field(..., description="排期时间 YYYY-MM-DD HH:MM")
    duration_minutes: int = Field(60, description="时长（分钟）")
    location: Optional[str] = Field(None, description="地点")
    agenda: Optional[str] = Field(None, description="议程")
    status: str = Field("scheduled", description="状态: scheduled/ongoing/completed/cancelled")


class MeetingCreate(MeetingBase):
    """创建会议。"""

    pass


class MeetingUpdate(BaseModel):
    """更新会议。"""

    title: Optional[str] = None
    meeting_type: Optional[str] = None
    scheduled_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    agenda: Optional[str] = None
    status: Optional[str] = None


class MeetingRecordCreate(BaseModel):
    """提交会议纪要 — 同步触发 AI 质量评估。"""

    content: str = Field(..., description="纪要正文")
    decisions: Optional[list[dict[str, Any]]] = Field(None, description="决策事项列表")
    action_items: Optional[list[dict[str, Any]]] = Field(None, description="行动项列表")
    attendees: Optional[list[str]] = Field(None, description="与会人列表")
    recorded_by: Optional[str] = Field(None, description="记录人")


class MeetingRecordResponse(BaseModel):
    """会议纪要响应。"""

    id: int
    meeting_id: int
    content: str
    decisions: Any = None
    action_items: Any = None
    attendees: Any = None
    quality_score: Optional[float] = None
    ai_assessment: Any = None
    ai_enabled: bool
    recorded_by: Optional[str] = None
    recorded_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):  # type: ignore[override]
        """在 from ORM 时把 JSON 字符串字段反序列化。"""
        data = {
            "id": obj.id,
            "meeting_id": obj.meeting_id,
            "content": obj.content,
            "decisions": _maybe_json(getattr(obj, "decisions", None)),
            "action_items": _maybe_json(getattr(obj, "action_items", None)),
            "attendees": _maybe_json(getattr(obj, "attendees", None)),
            "quality_score": obj.quality_score,
            "ai_assessment": _maybe_json(getattr(obj, "ai_assessment", None)),
            "ai_enabled": obj.ai_enabled,
            "recorded_by": obj.recorded_by,
            "recorded_at": obj.recorded_at,
        }
        return super().model_validate(data, *args, **kwargs)


class MeetingResponse(MeetingBase):
    """会议响应（含可选纪要）。"""

    id: int
    project_id: int
    created_at: datetime
    updated_at: datetime
    record: Optional[MeetingRecordResponse] = None

    model_config = {"from_attributes": True}


# ============================================================
#  DailyReport 每日汇报
# ============================================================


class DailyReportBase(BaseModel):
    """日报基础字段。"""

    report_date: str = Field(..., description="日期 YYYY-MM-DD")
    completed_work: str = Field(..., description="已完成工作")
    in_progress_work: Optional[str] = Field(None, description="进行中工作")
    blockers_summary: Optional[str] = Field(None, description="卡点摘要")
    next_day_plan: Optional[str] = Field(None, description="次日计划")
    hours_logged: float = Field(0.0, description="实际工时")


class DailyReportCreate(DailyReportBase):
    """提交日报。"""

    pass


class DailyReportResponse(DailyReportBase):
    """日报响应。"""

    id: int
    project_id: int
    member_id: int
    submitted_at: datetime
    member: Optional[TeamMemberResponse] = None

    model_config = {"from_attributes": True}


# ============================================================
#  Blocker 卡点
# ============================================================


class BlockerBase(BaseModel):
    """卡点基础字段。"""

    title: str = Field(..., description="卡点标题")
    description: Optional[str] = Field(None, description="详细描述")
    severity: str = Field("medium", description="严重度: low/medium/high/critical")
    related_task_id: Optional[int] = Field(None, description="关联任务 id")


class BlockerCreate(BlockerBase):
    """创建卡点。"""

    pass


class BlockerUpdate(BaseModel):
    """更新卡点。"""

    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    resolution_notes: Optional[str] = None


class BlockerResponse(BlockerBase):
    """卡点响应。"""

    id: int
    project_id: int
    member_id: int
    status: str
    raised_at: datetime
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    raised_by: Optional[TeamMemberResponse] = None

    model_config = {"from_attributes": True}


# ============================================================
#  ProgressSnapshot 进度快照
# ============================================================


class ProgressSnapshotResponse(BaseModel):
    """进度快照响应。"""

    id: int
    project_id: int
    member_id: Optional[int] = None
    snapshot_date: str
    total_items: int
    completed_items: int
    in_progress_items: int
    blocked_items: int
    completion_rate: float
    hours_done: float
    hours_remaining: float
    open_blockers: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
#  ProgressDashboard 综合进度看板
# ============================================================


class MemberProgress(BaseModel):
    """个人进度摘要（看板用）。"""

    member_id: int
    full_name: str
    level: str
    total_items: int
    completed_items: int
    in_progress_items: int
    blocked_items: int
    completion_rate: float
    hours_logged_7d: float
    open_blockers: int
    last_report_date: Optional[str] = None


class ProjectProgress(BaseModel):
    """项目级进度摘要。"""

    project_id: int
    project_name: str
    total_items: int
    completed_items: int
    in_progress_items: int
    blocked_items: int
    completion_rate: float
    total_estimated_hours: float
    total_actual_hours: float
    open_blockers: int
    critical_blockers: int
    members: list[MemberProgress] = Field(default_factory=list)


class BlockerSummary(BaseModel):
    """卡点摘要。"""

    total_open: int
    critical: int
    high: int
    medium: int
    low: int
    avg_age_hours: float


class ProgressDashboardResponse(BaseModel):
    """项目综合进度看板。"""

    project: ProjectProgress
    blockers: BlockerSummary
    by_module: dict[str, int] = Field(default_factory=dict)  # module -> count
    by_status: dict[str, int] = Field(default_factory=dict)  # status -> count
    recent_snapshots: list[ProgressSnapshotResponse] = Field(default_factory=list)


# ============================================================
#  ManagementRecommendation 管理建议
# ============================================================


class ManagementRecommendationResponse(BaseModel):
    """管理建议响应。"""

    id: int
    project_id: int
    generated_at: datetime
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    findings: Any = None
    priority_actions: Any = None
    recommendations: Optional[str] = None
    ai_enabled: bool
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    is_confirmed: bool
    manager_notes: Optional[str] = None

    model_config = {"from_attributes": True}

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):  # type: ignore[override]
        """反序列化 JSON 字符串字段。"""
        data = {
            "id": obj.id,
            "project_id": obj.project_id,
            "generated_at": obj.generated_at,
            "period_start": obj.period_start,
            "period_end": obj.period_end,
            "findings": _maybe_json(getattr(obj, "findings", None)),
            "priority_actions": _maybe_json(getattr(obj, "priority_actions", None)),
            "recommendations": obj.recommendations,
            "ai_enabled": obj.ai_enabled,
            "confirmed_by": obj.confirmed_by,
            "confirmed_at": obj.confirmed_at,
            "is_confirmed": obj.is_confirmed,
            "manager_notes": obj.manager_notes,
        }
        return super().model_validate(data, *args, **kwargs)


class ManagementRecommendationConfirm(BaseModel):
    """项目负责人确认管理建议。"""

    manager_notes: Optional[str] = Field(None, description="负责人备注")
    confirmed_by: str = Field(..., description="确认人姓名")


class ManagementRecommendationRequest(BaseModel):
    """请求 AI 生成管理建议。"""

    period_start: Optional[str] = Field(None, description="覆盖周期开始")
    period_end: Optional[str] = Field(None, description="覆盖周期结束")
