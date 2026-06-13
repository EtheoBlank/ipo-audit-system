"""启动时引导 — 创建默认事务所 + 默认管理员 (如果没有).

幂等: 多次调用不会重复创建. 由 ``app/main.py`` lifespan 调用.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.db.auth import (
    Firm,
    Permission,
    ROLE_ADMIN,
    Role,
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    ROLE_PARTNER,
    ROLE_QC_PARTNER,
    ROLE_SIGNING_PARTNER,
    ROLE_LEVEL,
    User,
)
from app.services.auth.password import hash_password

logger = logging.getLogger(__name__)


_DEFAULT_ROLES = [
    (ROLE_ASSISTANT, "审计员", "执行底稿编制、数据录入"),
    (ROLE_MANAGER, "经理", "复核审计程序, 分配任务"),
    (ROLE_PARTNER, "项目合伙人", "项目级签字 + 风险审批"),
    (ROLE_QC_PARTNER, "质控合伙人", "事务所质控 + 合规审批"),
    (ROLE_SIGNING_PARTNER, "签字合伙人", "最终签字 + 出报告"),
    (ROLE_ADMIN, "系统管理员", "系统配置 / 用户 / 全部权限"),
]


_DEFAULT_PERMISSIONS = [
    # auth
    ("auth.user.read", "查看用户", "auth"),
    ("auth.user.write", "新建 / 修改用户", "auth"),
    ("auth.user.delete", "停用用户", "auth"),
    ("auth.firm.write", "维护事务所信息", "auth"),
    ("auth.audit_log.read", "查询审计轨迹", "auth"),
    # project
    ("project.read", "查看项目", "project"),
    ("project.write", "新建 / 修改项目", "project"),
    ("project.delete", "删除项目", "project"),
    ("project.import", "导入账套数据", "project"),
    # workbook
    ("workbook.generate", "生成底稿", "workbook"),
    ("workbook.export", "导出底稿", "workbook"),
    # account_audit (长期资产发生额审定)
    ("account_audit.read", "查看发生额审定", "account_audit"),
    ("account_audit.write", "录入审定数据", "account_audit"),
    ("account_audit.bulk", "批量上传审定", "account_audit"),
    ("account_audit.scope", "调整长期资产范围", "account_audit"),
    # approval
    ("approval.create", "发起审批", "approval"),
    ("approval.decide", "审批决策 (按角色级别)", "approval"),
    # notification
    ("notification.read", "查看通知", "notification"),
    # report_template
    ("report_template.read", "查看报告模板", "report_template"),
    ("report_template.write", "上传 / 修改模板", "report_template"),
    ("report_template.render", "渲染报告", "report_template"),
]


async def _ensure_firm(db: AsyncSession) -> Firm:
    name = settings.AUTH_BOOTSTRAP_FIRM_NAME or "默认事务所"
    firm = (await db.execute(select(Firm).where(Firm.name == name))).scalar_one_or_none()
    if firm is not None:
        return firm
    firm = Firm(name=name, is_active=True, notes="系统引导自动创建")
    db.add(firm)
    await db.commit()
    await db.refresh(firm)
    logger.info("已创建默认事务所: %s (id=%s)", firm.name, firm.id)
    return firm


async def _ensure_roles(db: AsyncSession) -> None:
    existing_codes = {r.code for r in (await db.execute(select(Role))).scalars().all()}
    added = 0
    for code, name, desc in _DEFAULT_ROLES:
        if code in existing_codes:
            continue
        db.add(
            Role(
                code=code,
                name=name,
                level=ROLE_LEVEL.get(code, 1),
                description=desc,
                is_builtin=True,
            )
        )
        added += 1
    if added:
        await db.commit()
        logger.info("已创建 %s 个内置角色", added)


async def _ensure_permissions(db: AsyncSession) -> None:
    existing_codes = {p.code for p in (await db.execute(select(Permission))).scalars().all()}
    added = 0
    for code, name, module in _DEFAULT_PERMISSIONS:
        if code in existing_codes:
            continue
        db.add(Permission(code=code, name=name, module=module))
        added += 1
    if added:
        await db.commit()
        logger.info("已创建 %s 条内置权限", added)


async def _ensure_admin(db: AsyncSession, firm: Firm) -> Optional[User]:
    # 已有任何 admin 用户 → 跳过
    existing = (await db.execute(select(User).where(User.role == ROLE_ADMIN))).scalars().first()
    if existing is not None:
        return existing
    username = settings.AUTH_BOOTSTRAP_ADMIN_USERNAME or "admin"
    password = settings.AUTH_BOOTSTRAP_ADMIN_PASSWORD or "Admin@1234"
    full_name = settings.AUTH_BOOTSTRAP_ADMIN_FULL_NAME or "系统管理员"
    user = User(
        firm_id=firm.id,
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        role=ROLE_ADMIN,
        is_active=True,
        is_locked=False,
        notes="系统引导自动创建, 请尽快修改密码",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # 安全: 不打印明文密码到日志 (任何能读 log 的人都拿到)
    logger.warning(
        "已创建默认管理员账号: %s — 请通过 .env 中 AUTH_BOOTSTRAP_ADMIN_PASSWORD 修改密码, 并尽快登录后再次改动!",
        username,
    )
    return user


async def bootstrap_auth() -> None:
    """启动时执行. 失败不抛 (不能阻塞 app 启动).

    安全规则:
      - 内置角色 / 权限 / 默认事务所 永远创建 (无敏感数据)
      - 默认 admin 账户**仅在 AUTH_ENABLED=true 时创建**:
        AUTH_ENABLED=false 时根本用不到登录, 建了 admin 反而是攻击面;
        当用户从 false 切到 true 时, 下次启动会自动建出来.
      - 生产部署若使用默认密码且 AUTH_ENABLED=true, 会抛错拒绝启动
        (调用方需要在 .env 中明确设强密码).
    """
    try:
        async with AsyncSessionLocal() as db:
            firm = await _ensure_firm(db)
            await _ensure_roles(db)
            await _ensure_permissions(db)
            # 仅在认证启用时才创建默认 admin
            if settings.AUTH_ENABLED:
                # 生产保护: 若 AUTH_ENABLED=true 且密码仍是 example 默认值, 拒绝
                if not settings.DEBUG and settings.AUTH_BOOTSTRAP_ADMIN_PASSWORD in {
                    "Admin@1234",
                    "",
                    "__SET_ME_BEFORE_AUTH_ENABLED__",
                }:
                    logger.error(
                        "生产模式 (DEBUG=False) + AUTH_ENABLED=true, 但 AUTH_BOOTSTRAP_ADMIN_PASSWORD "
                        "仍是默认值。出于安全考虑跳过创建默认管理员 — 请通过 .env 设置强密码后再启动。"
                    )
                    return
                await _ensure_admin(db, firm)
            else:
                logger.info("AUTH_ENABLED=false, 跳过创建默认管理员 (开启认证时再建)。")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auth bootstrap 失败 (非致命, 将继续启动): %s", exc)
