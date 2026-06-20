"""Auth API — login/logout/refresh/me + Users / Firms / Roles / Permissions / Audit Logs / Approvals."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.auth import (
    AccessTokenResponse,
    ApprovalDecision,
    ApprovalWithdrawRequest,
    ApprovalWorkflowCreate,
    ApprovalWorkflowResponse,
    AuditLogListResponse,
    AuditLogResponse,
    FirmCreate,
    FirmResponse,
    FirmUpdate,
    LoginRequest,
    PermissionCreate,
    PermissionResponse,
    RefreshRequest,
    RoleCreate,
    RolePermissionAssign,
    RoleResponse,
    TokenPair,
    UserCreate,
    UserPasswordChange,
    UserPasswordReset,
    UserResponse,
    UserUpdate,
)
from app.models.db.auth import (
    AUDIT_ACTION_APPROVE,
    AUDIT_ACTION_CREATE,
    AUDIT_ACTION_DELETE,
    AUDIT_ACTION_LOGIN,
    AUDIT_ACTION_LOGOUT,
    AUDIT_ACTION_REJECT,
    AUDIT_ACTION_UPDATE,
    ApprovalWorkflow,
    Firm,
    Permission,
    Role,
    ROLE_QC_PARTNER,
    RolePermission,
    ROLE_ADMIN,
    ROLE_PARTNER,
    User,
)
from app.services.auth import (
    AccountLockedError,
    ApprovalConflict,
    ApprovalEngine,
    AuthenticationError,
    DEFAULT_FIVE_LEVEL_FLOW,
    InvalidApprovalAction,
    audit_log_stats,
    change_password as svc_change_password,
    get_current_user,
    hash_password,
    login as svc_login,
    query_audit_logs,
    record_audit_log,
    refresh_access_token as svc_refresh,
    require_role,
    rotate_audit_logs,
)
from app.services.auth.approval import StepSpec
from app.services.auth.service import reset_password as svc_reset_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["认证与权限"])


# ============================================================
#  Login / Token
# ============================================================


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    ip = request.client.host if request.client else None
    try:
        result = await svc_login(db, payload.username, payload.password, ip=ip)
    except AccountLockedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user: User = result["user"]
    await record_audit_log(
        db,
        user_id=user.id,
        user_display=user.full_name,
        user_role=user.role,
        firm_id=user.firm_id,
        action=AUDIT_ACTION_LOGIN,
        resource_type="auth.user",
        resource_id=user.id,
        method="POST",
        path="/api/auth/login",
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        status_code=200,
        summary=f"登录成功: {user.username}",
    )
    return TokenPair(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        token_type=result["token_type"],
        expires_in=result["expires_in"],
        user=UserResponse.model_validate(user),
    )


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """前端调用以记录登出. JWT 是无状态的, 真正失效靠 token 自然过期."""
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        firm_id=current_user.firm_id,
        action=AUDIT_ACTION_LOGOUT,
        method="POST",
        path="/api/auth/logout",
        ip=request.client.host if request.client else None,
        status_code=200,
        summary="登出",
    )
    return {"detail": "已登出"}


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> AccessTokenResponse:
    try:
        result = await svc_refresh(db, payload.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AccessTokenResponse(**result)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/me/change-password")
async def change_my_password(
    payload: UserPasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await svc_change_password(db, current_user, payload.old_password, payload.new_password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="auth.user",
        resource_id=current_user.id,
        summary="修改密码",
    )
    return {"detail": "密码已更新"}


# ============================================================
#  Firms
# ============================================================


@router.post("/firms", response_model=FirmResponse)
async def create_firm(
    payload: FirmCreate,
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    firm = Firm(**payload.model_dump())
    db.add(firm)
    await db.commit()
    await db.refresh(firm)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_CREATE,
        resource_type="auth.firm",
        resource_id=firm.id,
        summary=f"新建事务所 {firm.name}",
    )
    return FirmResponse.model_validate(firm)


@router.get("/firms", response_model=List[FirmResponse])
async def list_firms(
    is_active: Optional[bool] = None,
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    # P1 IDOR 修复 (2026-06-19): 旧任何登录用户可拉全所 (SaaS 客户名单泄露)
    # 现在 admin 全可见, 非 admin 仅自己 firm
    if current_user.role != ROLE_ADMIN:
        firm = (
            await db.execute(select(Firm).where(Firm.id == current_user.firm_id))
        ).scalar_one_or_none()
        return [FirmResponse.model_validate(firm)] if firm else []
    stmt = select(Firm)
    if is_active is not None:
        stmt = stmt.where(Firm.is_active == is_active)
    stmt = stmt.order_by(Firm.id)
    rows = list((await db.execute(stmt)).scalars().all())
    return [FirmResponse.model_validate(r) for r in rows]


@router.get("/firms/{firm_id}", response_model=FirmResponse)
async def get_firm(
    firm_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P1 IDOR 修复 (2026-06-19): 非 admin 不能读别所 metadata
    if current_user.role != ROLE_ADMIN and firm_id != current_user.firm_id:
        raise HTTPException(status_code=403, detail="无权访问其他事务所")
    firm = (await db.execute(select(Firm).where(Firm.id == firm_id))).scalar_one_or_none()
    if firm is None:
        raise HTTPException(status_code=404, detail="事务所不存在")
    return FirmResponse.model_validate(firm)


@router.put("/firms/{firm_id}", response_model=FirmResponse)
async def update_firm(
    firm_id: int,
    payload: FirmUpdate,
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    firm = (await db.execute(select(Firm).where(Firm.id == firm_id))).scalar_one_or_none()
    if firm is None:
        raise HTTPException(status_code=404, detail="事务所不存在")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(firm, k, v)
    await db.commit()
    await db.refresh(firm)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="auth.firm",
        resource_id=firm_id,
        summary=f"修改事务所 {firm.name}",
        payload=data,
    )
    return FirmResponse.model_validate(firm)


# ============================================================
#  Users
# ============================================================


@router.post("/users", response_model=UserResponse)
async def create_user(
    payload: UserCreate,
    request: Request,
    current_user: User = Depends(require_role(ROLE_QC_PARTNER)),  # 至少质控合伙人
    db: AsyncSession = Depends(get_db),
):
    # 重名检查
    existing = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"用户名 {payload.username} 已存在")
    # P0 IDOR 修复 (2026-06-19): 非 admin 强制 firm_id = 创建者的事务所, 防止跨所建账号
    target_firm_id = payload.firm_id
    if current_user.role != ROLE_ADMIN:
        target_firm_id = current_user.firm_id
    user = User(
        firm_id=target_firm_id,
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        role=payload.role,
        team_member_id=payload.team_member_id,
        notes=payload.notes,
        is_active=True,
        is_locked=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_CREATE,
        resource_type="auth.user",
        resource_id=user.id,
        summary=f"新建用户 {user.username} (role={user.role})",
        payload=payload.model_dump(exclude={"password"}),
        ip=request.client.host if request.client else None,
    )
    return UserResponse.model_validate(user)


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    firm_id: Optional[int] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    keyword: Optional[str] = Query(None, max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User)
    # P0 IDOR 修复 (2026-06-19): 非 admin 强制 firm_id = 自己, 防止跨所枚举用户
    if current_user.role != ROLE_ADMIN:
        firm_id = current_user.firm_id
    if firm_id is not None:
        stmt = stmt.where(User.firm_id == firm_id)
    if role:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if keyword:
        # P0 修复 — 转义 LIKE 通配符防全表扫描 DoS
        from app.services.auth.audit_log import _escape_like

        kw = keyword[:200]
        like = f"%{_escape_like(kw)}%"
        stmt = stmt.where(
            (User.username.ilike(like, escape="\\"))
            | (User.full_name.ilike(like, escape="\\"))
            | (User.email.ilike(like, escape="\\"))
        )
    stmt = stmt.order_by(User.id).offset(skip).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    return [UserResponse.model_validate(r) for r in rows]


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按 ID 查单个用户 — Pack B P0 IDOR 修复 (2026-06-20):
       - 跨 firm 一律 404 (admin 除外)
       - 普通用户不能读同 firm 但他人 (返回 403) — 防止 IDOR 枚举
       - qc_partner 可读同 firm 任意用户
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 跨 firm: 一律 404 (信息隐藏, admin 除外)
    if current_user.role != ROLE_ADMIN and user.firm_id != current_user.firm_id:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 同 firm 但本人不可读他人 — 防 IDOR 枚举
    if (
        current_user.role not in (ROLE_ADMIN, ROLE_QC_PARTNER, ROLE_PARTNER)
        and user.id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="无权查看其他用户信息")
    return UserResponse.model_validate(user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(require_role(ROLE_QC_PARTNER)),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    data = payload.model_dump(exclude_unset=True)

    # 防越权提权 — 修改 role / firm_id / is_locked 时强制 admin
    # 普通 qc_partner 不能把别人改成 admin 或停用其他高级用户
    if {"role", "firm_id"} & set(data.keys()) and current_user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="修改用户角色或事务所归属需要 admin 角色",
        )
    if data.get("is_locked") is not None and current_user.role != ROLE_ADMIN:
        # 仅允许 admin 锁/解锁; qc_partner 想锁可走停用 (is_active=False)
        raise HTTPException(
            status_code=403,
            detail="锁定/解锁用户需要 admin 角色 (qc_partner 可用 is_active=False 停用)",
        )
    # 防自我提权 — 不允许把自己 role 提升到超出当前级别
    if "role" in data and user.id == current_user.id:
        from app.services.auth.rbac import role_at_least

        if role_at_least(data["role"], current_user.role) and data["role"] != current_user.role:
            raise HTTPException(
                status_code=403,
                detail="不能把自己的 role 提升至更高级别",
            )

    for k, v in data.items():
        setattr(user, k, v)
    await db.commit()
    await db.refresh(user)
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="auth.user",
        resource_id=user_id,
        summary=f"修改用户 {user.username}",
        payload=data,
        commit=True,
    )
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}")
async def deactivate_user(
    user_id: int,
    current_user: User = Depends(require_role(ROLE_QC_PARTNER)),
    db: AsyncSession = Depends(get_db),
):
    """软删除 — 仅停用, 不真正删除 (保审计轨迹)."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # P0 IDOR 修复 (2026-06-19): 非 admin 不能跨所停用用户
    if current_user.role != ROLE_ADMIN and user.firm_id != current_user.firm_id:
        raise HTTPException(status_code=403, detail="无权操作其他事务所的用户")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能停用自己")
    user.is_active = False
    await db.commit()
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_DELETE,
        resource_type="auth.user",
        resource_id=user_id,
        summary=f"停用用户 {user.username}",
    )
    return {"detail": "已停用"}


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    payload: UserPasswordReset,
    current_user: User = Depends(require_role(ROLE_QC_PARTNER)),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # P0 IDOR 修复 (2026-06-19): 非 admin 不能跨所重置密码 (可能间接登录)
    if current_user.role != ROLE_ADMIN and user.firm_id != current_user.firm_id:
        raise HTTPException(status_code=403, detail="无权操作其他事务所的用户")
    try:
        await svc_reset_password(db, user, payload.new_password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="auth.user",
        resource_id=user_id,
        summary=f"重置用户 {user.username} 密码",
    )
    return {"detail": "已重置"}


# ============================================================
#  Roles / Permissions (RBAC)
# ============================================================


@router.get("/roles", response_model=List[RoleResponse])
async def list_roles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = list((await db.execute(select(Role).order_by(Role.level))).scalars().all())
    return [RoleResponse.model_validate(r) for r in rows]


@router.post("/roles", response_model=RoleResponse)
async def create_role(
    payload: RoleCreate,
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(Role).where(Role.code == payload.code))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"role {payload.code} 已存在")
    role = Role(**payload.model_dump(), is_builtin=False)
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return RoleResponse.model_validate(role)


@router.get("/permissions", response_model=List[PermissionResponse])
async def list_permissions(
    module: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Permission)
    if module:
        stmt = stmt.where(Permission.module == module)
    stmt = stmt.order_by(Permission.module, Permission.code)
    rows = list((await db.execute(stmt)).scalars().all())
    return [PermissionResponse.model_validate(r) for r in rows]


@router.post("/permissions", response_model=PermissionResponse)
async def create_permission(
    payload: PermissionCreate,
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(Permission).where(Permission.code == payload.code))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"permission {payload.code} 已存在")
    perm = Permission(**payload.model_dump())
    db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return PermissionResponse.model_validate(perm)


@router.post("/roles/{role_id}/permissions")
async def assign_role_permissions(
    role_id: int,
    payload: RolePermissionAssign,
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    if payload.role_id != role_id:
        raise HTTPException(status_code=400, detail="payload.role_id 不一致")
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="role 不存在")
    # 清旧
    existing = list(
        (await db.execute(select(RolePermission).where(RolePermission.role_id == role_id)))
        .scalars()
        .all()
    )
    for rp in existing:
        await db.delete(rp)
    # 加新
    for pid in payload.permission_ids:
        db.add(RolePermission(role_id=role_id, permission_id=pid))
    await db.commit()
    return {"detail": "已更新", "count": len(payload.permission_ids)}


# ============================================================
#  Audit Logs
# ============================================================


@router.get("/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    project_id: Optional[int] = None,
    method: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_role(ROLE_QC_PARTNER)),
    db: AsyncSession = Depends(get_db),
):
    # P0 IDOR 修复 (2026-06-19): 非 admin 强制 firm_id, 防跨所读审计轨迹
    scope_firm_id = None if current_user.role == ROLE_ADMIN else current_user.firm_id
    result = await query_audit_logs(
        db,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        project_id=project_id,
        firm_id=scope_firm_id,
        method=method,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        skip=skip,
        limit=limit,
    )
    return AuditLogListResponse(
        total=result["total"],
        items=[AuditLogResponse.model_validate(r) for r in result["items"]],
    )


# ============================================================
#  Audit Log 运维 (admin) — 归档 / 统计
# ============================================================


@router.get("/audit-logs/stats")
async def get_audit_log_stats(
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """返回 AuditLog 行数 / 时间跨度 / firm 分布 — 给 ops 判断要不要归档."""
    return await audit_log_stats(db)


@router.post("/audit-logs/rotate")
async def rotate_audit_log_archive(
    months: int = Query(6, ge=1, le=120, description="保留近 N 月数据 (默认 6, 之前归档)"),
    confirm: bool = Query(False, description="必须 True 才真正归档 + 删除; 否则 dry-run"),
    batch_size: int = Query(5000, ge=100, le=20000),
    current_user: User = Depends(require_role(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """把 N 月前的 AuditLog 行复制到 ``audit_logs_archive`` + 原表删除.

    安全:
      - 仅 admin 可调
      - 默认 dry-run (confirm=False) — 只返回"会删多少行"
      - 真删除走 raw SQL, 绕开 SQLAlchemy event 的 before_delete 拦截 (归档场景例外)
      - 删除后写一条 AuditLog 自身记录 (action=audit_log_rotate)

    建议在维护窗口跑, 避免并发写 AuditLog.
    """
    result = await rotate_audit_logs(
        db,
        months=months,
        confirm=confirm,
        batch_size=batch_size,
    )
    # 记录归档动作本身 (rotate 完成才记, 避免 rotate 失败也产生噪音)
    if confirm and result.get("archived", 0) > 0:
        await record_audit_log(
            db,
            user_id=getattr(current_user, "id", None),
            user_display=getattr(current_user, "full_name", None),
            user_role=getattr(current_user, "role", None),
            firm_id=getattr(current_user, "firm_id", None),
            action="audit_log_rotate",
            resource_type="audit_log",
            summary=(
                f"归档 {result.get('archived')} 行 (cutoff={result.get('cutoff')}); months={months}"
            ),
            payload=result,
        )
    return result


# ============================================================
#  Approval Workflow
# ============================================================


# 审批 resource_type 白名单 — 防止恶意 caller 污染表 + 索引
# 新增审批资源类型必须先在这里登记
_APPROVAL_RESOURCE_TYPES = {
    "confirmation_case",
    "workbook",
    "report",
    "account_audit",
    "inventory_count_plan",
    "work_plan",
    "comprehensive_workpaper",
    "report_template",
    "related_party_report",  # Pack B 预留
    "prospectus_reconciliation",  # Pack D 预留
}


@router.post("/approvals", response_model=ApprovalWorkflowResponse)
async def create_approval(
    payload: ApprovalWorkflowCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P0 修复 — resource_type 白名单
    if payload.resource_type not in _APPROVAL_RESOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"resource_type 必须是 {sorted(_APPROVAL_RESOURCE_TYPES)} 之一",
        )
    # P0 修复 — 校验关联项目存在
    if payload.project_id is not None:
        from app.models.db_models import Project as _Project

        proj = (
            await db.execute(select(_Project).where(_Project.id == payload.project_id))
        ).scalar_one_or_none()
        if proj is None:
            raise HTTPException(status_code=404, detail=f"项目 {payload.project_id} 不存在")
    steps = (
        [
            StepSpec(
                step_no=s.step_no,
                required_role=s.required_role,
                approver_user_id=s.approver_user_id,
            )
            for s in payload.steps
        ]
        if payload.steps
        else DEFAULT_FIVE_LEVEL_FLOW
    )
    try:
        wf = await ApprovalEngine.create_workflow(
            db,
            initiator=current_user,
            project_id=payload.project_id,
            firm_id=current_user.firm_id,  # round 32 P0 IDOR: firm_id 落库
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            title=payload.title,
            description=payload.description,
            steps=steps,
        )
    except InvalidApprovalAction as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_CREATE,
        resource_type="auth.approval_workflow",
        resource_id=wf.id,
        project_id=payload.project_id,
        summary=f"发起审批: {payload.title}",
        commit=True,
    )
    return ApprovalWorkflowResponse.model_validate(wf)


@router.get("/approvals", response_model=List[ApprovalWorkflowResponse])
async def list_approvals(
    status_filter: Optional[str] = Query(None, alias="status"),
    project_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    initiator_user_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.db_models import Project
    from app.services.auth.tenant import _is_admin, _user_firm_id
    from sqlalchemy import or_

    stmt = select(ApprovalWorkflow).join(
        Project, ApprovalWorkflow.project_id == Project.id, isouter=True
    )
    # 多租户: admin 看全部, 其他人只看自己事务所的项目 (含 firm_id=NULL 的旧数据)
    if not _is_admin(current_user):
        firm_id = _user_firm_id(current_user)
        if firm_id is not None:
            stmt = stmt.where(or_(Project.firm_id == firm_id, Project.firm_id.is_(None)))
    if status_filter:
        stmt = stmt.where(ApprovalWorkflow.status == status_filter)
    if project_id is not None:
        stmt = stmt.where(ApprovalWorkflow.project_id == project_id)
    if resource_type:
        stmt = stmt.where(ApprovalWorkflow.resource_type == resource_type)
    if initiator_user_id is not None:
        stmt = stmt.where(ApprovalWorkflow.initiator_user_id == initiator_user_id)
    stmt = stmt.order_by(ApprovalWorkflow.id.desc()).offset(skip).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    results = []
    for wf in rows:
        wf2 = await ApprovalEngine.get_workflow(db, wf.id)
        if wf2:
            results.append(ApprovalWorkflowResponse.model_validate(wf2))
    return results


@router.get("/approvals/{workflow_id}", response_model=ApprovalWorkflowResponse)
async def get_approval(
    workflow_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wf = await ApprovalEngine.get_workflow(db, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="审批流不存在")
    # P0 IDOR 修复 (round 32, 2026-06-20): 非 admin 不能跨所读审批流
    # 防枚举: 一律返回 404, 不告诉调用方"存在但无权"
    if current_user.role != ROLE_ADMIN and wf.firm_id and wf.firm_id != current_user.firm_id:
        raise HTTPException(status_code=404, detail="审批流不存在")
    return ApprovalWorkflowResponse.model_validate(wf)


@router.post("/approvals/{workflow_id}/decide", response_model=ApprovalWorkflowResponse)
async def decide_approval(
    workflow_id: int,
    payload: ApprovalDecision,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P0 IDOR 修复 (round 32): decide 前先校验 firm, 防止跨所代签
    # 防枚举: 跨 firm 一律 404
    pre_wf = await ApprovalEngine.get_workflow(db, workflow_id)
    if pre_wf is None:
        raise HTTPException(status_code=404, detail="审批流不存在")
    if current_user.role != ROLE_ADMIN and pre_wf.firm_id and pre_wf.firm_id != current_user.firm_id:
        raise HTTPException(status_code=404, detail="审批流不存在")
    try:
        wf = await ApprovalEngine.decide(
            db,
            workflow_id=workflow_id,
            actor=current_user,
            action=payload.action,
            comment=payload.comment,
            delegate_to_user_id=payload.delegate_to_user_id,
            expected_version=payload.expected_version,
        )
    except ApprovalConflict as exc:
        # 并发审批 — 走 409 Conflict, 让前端提示用户刷新后重试
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidApprovalAction as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    action_map = {"approve": AUDIT_ACTION_APPROVE, "reject": AUDIT_ACTION_REJECT}
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=action_map.get(payload.action, AUDIT_ACTION_UPDATE),
        resource_type="auth.approval_workflow",
        resource_id=workflow_id,
        summary=f"审批 {payload.action} (step={wf.current_step}, status={wf.status}, version={wf.version})",
        payload=payload.model_dump(),
    )
    return ApprovalWorkflowResponse.model_validate(wf)


@router.post("/approvals/{workflow_id}/withdraw", response_model=ApprovalWorkflowResponse)
async def withdraw_approval(
    workflow_id: int,
    payload: Optional[ApprovalWithdrawRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P0 IDOR 修复 (round 32): withdraw 前先校验 firm, 防跨所撤回
    # 防枚举: 跨 firm 一律 404
    pre_wf = await ApprovalEngine.get_workflow(db, workflow_id)
    if pre_wf is None:
        raise HTTPException(status_code=404, detail="审批流不存在")
    if current_user.role != ROLE_ADMIN and pre_wf.firm_id and pre_wf.firm_id != current_user.firm_id:
        raise HTTPException(status_code=404, detail="审批流不存在")
    expected_version = payload.expected_version if payload else None
    try:
        wf = await ApprovalEngine.withdraw(
            db,
            workflow_id=workflow_id,
            actor=current_user,
            expected_version=expected_version,
        )
    except ApprovalConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidApprovalAction as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit_log(
        db,
        user_id=current_user.id,
        user_display=current_user.full_name,
        user_role=current_user.role,
        action=AUDIT_ACTION_UPDATE,
        resource_type="auth.approval_workflow",
        resource_id=workflow_id,
        summary=f"撤回审批 (version={wf.version})",
    )
    return ApprovalWorkflowResponse.model_validate(wf)
