"""Pydantic schemas for auth module (User / Firm / Role / Permission / Approval / AuditLog)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.models.db.auth import (
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    ROLE_PARTNER,
    ROLE_QC_PARTNER,
    ROLE_SIGNING_PARTNER,
    ROLE_ADMIN,
    ALL_ROLES,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_IN_PROGRESS,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REJECTED,
    APPROVAL_STATUS_WITHDRAWN,
)


# ============================================================
#  Firm
# ============================================================


class FirmBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    short_name: Optional[str] = Field(None, max_length=50)
    license_no: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = Field(None, max_length=500)
    contact_email: Optional[str] = Field(None, max_length=200)
    contact_phone: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class FirmCreate(FirmBase):
    pass


class FirmUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    short_name: Optional[str] = Field(None, max_length=50)
    license_no: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = Field(None, max_length=500)
    contact_email: Optional[str] = Field(None, max_length=200)
    contact_phone: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class FirmResponse(FirmBase):
    id: int
    is_active: bool
    logo_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ============================================================
#  User
# ============================================================


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.\-]+$")
    full_name: str = Field(..., min_length=1, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=50)
    role: str = Field(default=ROLE_ASSISTANT)
    firm_id: Optional[int] = None
    team_member_id: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _role_must_be_known(cls, v: str) -> str:
        if v not in ALL_ROLES:
            raise ValueError(f"role 必须是 {ALL_ROLES} 之一")
        return v


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=50)
    role: Optional[str] = None
    firm_id: Optional[int] = None
    team_member_id: Optional[int] = None
    is_active: Optional[bool] = None
    is_locked: Optional[bool] = None
    notes: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _role_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALL_ROLES:
            raise ValueError(f"role 必须是 {ALL_ROLES} 之一")
        return v


class UserPasswordChange(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class UserPasswordReset(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    firm_id: Optional[int] = None
    username: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str
    team_member_id: Optional[int] = None
    is_active: bool
    is_locked: bool
    failed_login_count: int = 0
    last_login_at: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    password_changed_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ============================================================
#  Login / Token
# ============================================================


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user: UserResponse


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ============================================================
#  Role / Permission (RBAC)
# ============================================================


class RoleBase(BaseModel):
    code: str = Field(..., min_length=2, max_length=40, pattern=r"^[a-z0-9_]+$")
    name: str = Field(..., min_length=1, max_length=100)
    level: int = Field(default=1, ge=1, le=99)
    description: Optional[str] = None


class RoleCreate(RoleBase):
    pass


class RoleResponse(RoleBase):
    id: int
    is_builtin: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PermissionBase(BaseModel):
    code: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9_.\-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    module: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None


class PermissionCreate(PermissionBase):
    pass


class PermissionResponse(PermissionBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RolePermissionAssign(BaseModel):
    role_id: int
    permission_ids: List[int]


# ============================================================
#  Approval Workflow
# ============================================================


class ApprovalStepDefinition(BaseModel):
    """单步骤定义 (创建审批流时由调用方传)."""

    step_no: int = Field(..., ge=1)
    required_role: str
    approver_user_id: Optional[int] = None

    @field_validator("required_role")
    @classmethod
    def _role_known(cls, v: str) -> str:
        if v not in ALL_ROLES:
            raise ValueError(f"required_role 必须是 {ALL_ROLES} 之一")
        return v


class ApprovalWorkflowCreate(BaseModel):
    project_id: Optional[int] = None
    resource_type: str = Field(..., min_length=1, max_length=80)
    resource_id: int
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = None
    steps: Optional[List[ApprovalStepDefinition]] = None  # 不传则使用默认五级模板


class ApprovalDecision(BaseModel):
    action: str = Field(..., pattern=r"^(approve|reject|delegate|comment)$")
    comment: Optional[str] = Field(None, max_length=2000)
    delegate_to_user_id: Optional[int] = None
    # P0 修复 (2026-06-18): 必填乐观锁, 不传时让 ApprovalEngine.decide 抛 InvalidApprovalAction
    # 之前 Optional=None 时 commit-time rowcount=0 兜底, 并发审批 UX 差; 必填后强制
    # 前端先 GET 拿 version 才能 decide, 真正 409 Conflict 在请求阶段就拦住
    expected_version: int = Field(
        ...,
        description="乐观锁版本快照 (来自上次 GET /approvals/{id} 返回的 version). "
        "若实际 version 不一致, 返 409 Conflict. **必填**, 不传则 400.",
    )


class ApprovalWithdrawRequest(BaseModel):
    """撤回审批 — 同样必填乐观锁 (P0 修复)."""

    expected_version: int = Field(
        ...,
        description="乐观锁版本快照. 详见 ApprovalDecision.expected_version. **必填**.",
    )


class ApprovalStepResponse(BaseModel):
    id: int
    workflow_id: int
    step_no: int
    required_role: str
    approver_user_id: Optional[int] = None
    approver_display: Optional[str] = None
    action: Optional[str] = None
    comment: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ApprovalWorkflowResponse(BaseModel):
    id: int
    project_id: Optional[int] = None
    resource_type: str
    resource_id: int
    title: str
    description: Optional[str] = None
    total_steps: int
    current_step: int
    status: str
    initiator_user_id: Optional[int] = None
    initiator_display: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    version: int = 0
    steps: List[ApprovalStepResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


# ============================================================
#  Audit Log
# ============================================================


class AuditLogQuery(BaseModel):
    user_id: Optional[int] = None
    action: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    project_id: Optional[int] = None
    method: Optional[str] = None
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None
    keyword: Optional[str] = None
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    user_display: Optional[str] = None
    user_role: Optional[str] = None
    firm_id: Optional[int] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    project_id: Optional[int] = None
    method: Optional[str] = None
    path: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    status_code: Optional[int] = None
    summary: Optional[str] = None
    payload: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    total: int
    items: List[AuditLogResponse]


# 状态汇总, 给前端选项
APPROVAL_ALL_STATUSES = [
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_IN_PROGRESS,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REJECTED,
    APPROVAL_STATUS_WITHDRAWN,
]

ROLE_LABELS = {
    ROLE_ASSISTANT: "审计员",
    ROLE_MANAGER: "经理",
    ROLE_PARTNER: "项目合伙人",
    ROLE_QC_PARTNER: "质控合伙人",
    ROLE_SIGNING_PARTNER: "签字合伙人",
    ROLE_ADMIN: "系统管理员",
}
