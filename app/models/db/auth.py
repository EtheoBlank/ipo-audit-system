"""Authentication / Authorization / Audit Trail ORM models (Pack A).

实现 IPO 项目质控所需的五级签字流 + RBAC + 全量审计轨迹:
  - User / Firm                  — 谁登录
  - Role / Permission / RolePermission — 谁能做什么 (动态 RBAC)
  - ApprovalWorkflow / ApprovalStep    — 五级签字流
  - AuditLog                     — 所有写操作的不可篡改记录

设计决策:
  - User.role 是冗余字符串字段, 与 RolePermission 关系并行存在;
    简单场景直接读 User.role, 复杂场景查 RolePermission。这样既不强迫
    用户配 RBAC 也不限制扩展。
  - User.firm_id 允许 nullable, 单事务所部署时不强制
  - AuditLog.payload 用 Text(JSON 字符串), 兼容 SQLite/PG; 字段长度
    够大可放完整请求体的截断版本
  - 时间字段全部 naive DateTime (沿用现有惯例, 写入前 .replace(tzinfo=None))
"""

from __future__ import annotations

from datetime import datetime, timezone
from app.utils.datetime_helpers import utc_now
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


__all__ = [
    "Firm",
    "User",
    "Role",
    "Permission",
    "RolePermission",
    "ApprovalWorkflow",
    "ApprovalStep",
    "AuditLog",
    # 角色常量 — 五级签字
    "ROLE_ASSISTANT",
    "ROLE_MANAGER",
    "ROLE_PARTNER",
    "ROLE_QC_PARTNER",
    "ROLE_SIGNING_PARTNER",
    "ROLE_ADMIN",
    "ALL_ROLES",
    "ROLE_LEVEL",
    # 审批状态
    "APPROVAL_STATUS_PENDING",
    "APPROVAL_STATUS_IN_PROGRESS",
    "APPROVAL_STATUS_APPROVED",
    "APPROVAL_STATUS_REJECTED",
    "APPROVAL_STATUS_WITHDRAWN",
    "APPROVAL_STATE_ACTIONS",
    # 审计轨迹动作
    "AUDIT_ACTION_CREATE",
    "AUDIT_ACTION_UPDATE",
    "AUDIT_ACTION_DELETE",
    "AUDIT_ACTION_LOGIN",
    "AUDIT_ACTION_LOGOUT",
    "AUDIT_ACTION_APPROVE",
    "AUDIT_ACTION_REJECT",
    "AUDIT_ACTION_EXPORT",
    "AUDIT_ACTION_IMPORT",
]


# === 五级签字角色 (从低到高) ===
ROLE_ASSISTANT = "assistant"  # 审计员
ROLE_MANAGER = "manager"  # 经理
ROLE_PARTNER = "partner"  # 项目合伙人
ROLE_QC_PARTNER = "qc_partner"  # 质控合伙人
ROLE_SIGNING_PARTNER = "signing_partner"  # 签字合伙人
ROLE_ADMIN = "admin"  # 系统管理员 (不在签字流, 但有所有权限)

ALL_ROLES = [
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    ROLE_PARTNER,
    ROLE_QC_PARTNER,
    ROLE_SIGNING_PARTNER,
    ROLE_ADMIN,
]

# 角色级别 (用于 require_role 比较) — admin 视为最高
ROLE_LEVEL = {
    ROLE_ASSISTANT: 1,
    ROLE_MANAGER: 2,
    ROLE_PARTNER: 3,
    ROLE_QC_PARTNER: 4,
    ROLE_SIGNING_PARTNER: 5,
    ROLE_ADMIN: 99,
}


# === 审批流状态 ===
APPROVAL_STATUS_PENDING = "pending"  # 草稿, 未提交
APPROVAL_STATUS_IN_PROGRESS = "in_progress"  # 流转中
APPROVAL_STATUS_APPROVED = "approved"  # 全部通过
APPROVAL_STATUS_REJECTED = "rejected"  # 任一步骤拒绝
APPROVAL_STATUS_WITHDRAWN = "withdrawn"  # 发起人撤回

APPROVAL_STATE_ACTIONS = {"approve", "reject", "delegate", "comment"}


# === 审计轨迹动作类型 ===
AUDIT_ACTION_CREATE = "create"
AUDIT_ACTION_UPDATE = "update"
AUDIT_ACTION_DELETE = "delete"
AUDIT_ACTION_LOGIN = "login"
AUDIT_ACTION_LOGOUT = "logout"
AUDIT_ACTION_APPROVE = "approve"
AUDIT_ACTION_REJECT = "reject"
AUDIT_ACTION_EXPORT = "export"
AUDIT_ACTION_IMPORT = "import"


class Firm(Base):
    """会计师事务所 (多租户根, 单所部署只有一行 ``id=1``)."""

    __tablename__ = "auth_firms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    short_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    license_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    logo_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 报告模板水印
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    # 关系
    users: Mapped[list["User"]] = relationship(back_populates="firm")


class User(Base):
    """系统用户 (审计师本人 — 区别于业务模型里的 ``TeamMember``)。

    与 ``TeamMember`` 的差别:
      - ``TeamMember`` 是项目人员库, 是审计对象, 不一定能登录系统
      - ``User`` 是系统登录账号, 一定能登录, 持有 JWT
      - 可选: ``User.team_member_id`` 关联两者, 让登录用户对应到 ``TeamMember``
    """

    __tablename__ = "auth_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    firm_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("auth_firms.id"), nullable=True, index=True
    )

    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 角色字段是冗余字符串, 简单场景直接读, 复杂 RBAC 走 RolePermission
    role: Mapped[str] = mapped_column(
        String(40), nullable=False, default=ROLE_ASSISTANT, index=True
    )

    # 软关联: 该登录账号对应哪个项目组成员 (可选)
    team_member_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("team_members.id"), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    # 关系
    firm: Mapped[Optional["Firm"]] = relationship(back_populates="users")


class Role(Base):
    """角色 (RBAC 扩展, 高级用户自定义角色). 简单场景直接读 ``User.role`` 即可。"""

    __tablename__ = "auth_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 与 ROLE_LEVEL 对齐
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class Permission(Base):
    """权限定义 (字符串 code, 例如 ``project.create`` / ``confirmation.lock``)."""

    __tablename__ = "auth_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    module: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class RolePermission(Base):
    """角色-权限 N:N."""

    __tablename__ = "auth_role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("auth_roles.id"), nullable=False, index=True
    )
    permission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("auth_permissions.id"), nullable=False, index=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ApprovalWorkflow(Base):
    """审批工作流主表 (一个资源 = 一条流). 五级签字: assistant→manager→partner→qc_partner→signing_partner."""

    __tablename__ = "auth_approval_workflows"
    __table_args__ = (Index("ix_approval_resource", "resource_type", "resource_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )

    resource_type: Mapped[str] = mapped_column(
        String(80), nullable=False
    )  # 如 confirmation_case / workbook / report
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 步骤总数 + 当前步骤
    total_steps: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=APPROVAL_STATUS_PENDING, nullable=False, index=True
    )

    initiator_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("auth_users.id"), nullable=True
    )
    initiator_display: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # 冗余, 避免 join

    # 流程定义 (JSON, 序列化的步骤列表), 可在创建时由用户自定义或走默认五级模板
    definition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 乐观锁版本号 — 每次 decide/withdraw 自增 1; 并发更新时旧 version 写入会失败
    # 在 ORM 层不用 SQLAlchemy 自带 version_id_col (它会要求 INSERT 时也带 version),
    # 改用应用层手动 +1 + WHERE version=? 防并发审批
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 关系
    steps: Mapped[list["ApprovalStep"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="ApprovalStep.step_no"
    )


class ApprovalStep(Base):
    """审批步骤 (一行 = 一个审批人在一个步骤的处理记录)."""

    __tablename__ = "auth_approval_steps"
    __table_args__ = (UniqueConstraint("workflow_id", "step_no", name="uq_workflow_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workflow_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("auth_approval_workflows.id"), nullable=False, index=True
    )

    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    required_role: Mapped[str] = mapped_column(String(40), nullable=False)  # 必须的最低角色
    approver_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("auth_users.id"), nullable=True
    )
    approver_display: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    action: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # approve / reject / delegate / comment
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    workflow: Mapped["ApprovalWorkflow"] = relationship(back_populates="steps")


class AuditLog(Base):
    """审计轨迹 (Audit Trail) — 所有写操作的不可篡改日志.

    设计约束:
      - 仅 append, 不允许 UPDATE / DELETE (SQLAlchemy event 强制 — 见文件末尾)
      - payload 存 JSON 字符串截断版, 完整请求体过大时只存关键字段
      - 不与 User 强外键 (用户可能被删除, 轨迹仍要保留) — 冗余 user_display

    索引设计 (针对长期 100w+ 行性能, 见 query_audit_logs 调用模式):
      - ix_audit_created (created_at) — 时序倒序扫描 (默认排序)
      - ix_audit_resource (resource_type, resource_id) — 资源溯源
      - ix_audit_user_action (user_id, action) — 用户行为审计
      - ix_audit_firm_created (firm_id, created_at desc) — 多租户隔离 + 时间窗
      - ix_audit_project_created (project_id, created_at desc) — 项目级审计窗
      - ix_audit_user_created (user_id, created_at desc) — 单用户时间线
      - ix_audit_action_created (action, created_at desc) — 按动作 + 时间统计
      - ix_audit_path (path) — 路径模糊查 (keyword 过滤)
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_user_action", "user_id", "action"),
        Index("ix_audit_created", "created_at"),
        # 新增 — 多维 (创建时序 + 维度) 复合索引, 防止长期数据增长导致 ORDER BY created_at LIMIT 时退化为全表扫
        Index("ix_audit_firm_created", "firm_id", "created_at"),
        Index("ix_audit_project_created", "project_id", "created_at"),
        Index("ix_audit_user_created", "user_id", "created_at"),
        Index("ix_audit_action_created", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 谁
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    user_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    user_role: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    firm_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 什么
    action: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # HTTP 上下文
    method: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 业务上下文
    summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 字符串
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False, index=True
    )


# ============================================================
# AuditLog append-only 强制 (P0 修复)
# ============================================================
# SQLAlchemy event 拦截 — 即使应用层调用 db.delete(audit_log) / 改字段后 commit
# 都会抛 AuditLogTamperError, 让审计轨迹真正不可篡改.
# DBA 仍可直接 DELETE FROM audit_logs, 这种 case 走 DB 层 REVOKE / WORM 解决.


class AuditLogTamperError(Exception):
    """企图修改或删除 AuditLog 时抛."""


def _audit_log_block_update(mapper, connection, target):  # noqa: ARG001
    raise AuditLogTamperError("AuditLog 是 append-only, 不允许 UPDATE (违反审计轨迹不可篡改原则)")


def _audit_log_block_delete(mapper, connection, target):  # noqa: ARG001
    raise AuditLogTamperError("AuditLog 是 append-only, 不允许 DELETE (违反审计轨迹不可篡改原则)")


# 在模块加载时一次性注册 (惰性 import 防循环)
try:
    from sqlalchemy import event as _sa_event

    _sa_event.listen(AuditLog, "before_update", _audit_log_block_update)
    _sa_event.listen(AuditLog, "before_delete", _audit_log_block_delete)
except Exception:  # noqa: BLE001
    # 极端情况下 event 注册失败 (例如老版 SQLAlchemy) 不阻塞 ORM 加载
    pass
