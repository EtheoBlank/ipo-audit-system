"""多租户硬隔离辅助 (Pack A.2 — Roadmap "跨事务所多租户硬隔离").

核心思想:
  - Project 是所有业务数据的入口 (account_balances / sales_records / contracts / ...
    都通过 project_id 外键挂在 Project 上).
  - 只要保证"用户只能看到 firm_id 匹配的 Project", 下游所有数据自动隔离.
  - 不可避免地, GET /api/projects/{id}/account-balances 这种端点仍然需要在加载
    Project 之前做一次 firm_id 校验 — 这就是本模块提供的 helper.

设计原则:
  - **软隔离**: AUTH_ENABLED=false 或 user.firm_id is None 时, 完全跳过过滤
    (兼容老数据 + 单租户部署).
  - **硬隔离**: AUTH_ENABLED=true + user.firm_id 已设 时, 任何越权访问抛 403.
  - **管理员豁免**: admin 角色可以跨事务所 (后台运维场景).

调用模式 (推荐):

  from app.services.auth.tenant import scope_projects_to_firm, ensure_project_in_firm

  @router.get("/projects/")
  async def list_projects(
      current_user: Optional[User] = Depends(get_current_user_optional),
      db: AsyncSession = Depends(get_db),
  ):
      query = select(Project)
      query = scope_projects_to_firm(query, current_user)  # 自动按 firm 过滤
      return (await db.execute(query)).scalars().all()

  @router.get("/projects/{project_id}")
  async def get_project(
      project_id: int,
      current_user: Optional[User] = Depends(get_current_user_optional),
      db: AsyncSession = Depends(get_db),
  ):
      proj = await ensure_project_in_firm(db, project_id, current_user)
      return proj

历史数据迁移:
  - 老 Project 的 firm_id=NULL — 视为"全局可见" (向后兼容)
  - 想加入硬隔离, ops 跑一次 UPDATE projects SET firm_id=? WHERE ...
  - 或新建 Project 时强制带 firm_id (POST /api/projects/ 写入 current_user.firm_id)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.config import settings
from app.models.db.auth import ROLE_ADMIN, User
from app.models.db_models import Project

logger = logging.getLogger(__name__)


def _is_admin(user: Optional[User]) -> bool:
    return bool(user and getattr(user, "role", "") == ROLE_ADMIN)


def _user_firm_id(user: Optional[User]) -> Optional[int]:
    """取用户 firm_id; AUTH_ENABLED=false 时返 None (跳过过滤)."""
    if not settings.AUTH_ENABLED:
        return None
    if user is None:
        return None
    return getattr(user, "firm_id", None)


def scope_projects_to_firm(query: Select, user: Optional[User]) -> Select:
    """给 SELECT Project 的查询加 firm_id 过滤.

    规则:
      - admin 角色: 不过滤 (跨事务所运维)
      - AUTH_ENABLED=false 或 user.firm_id is None: 不过滤 (软兼容)
      - 否则: WHERE projects.firm_id == user.firm_id OR projects.firm_id IS NULL
        (允许看老的全局数据 + 自己事务所数据)

    Args:
        query: 已经 select(Project) 的查询对象
        user: 当前登录用户 (None 表示匿名)

    Returns: 加了 where 子句的新查询对象
    """
    if _is_admin(user):
        return query
    firm_id = _user_firm_id(user)
    if firm_id is None:
        return query
    return query.where(or_(Project.firm_id == firm_id, Project.firm_id.is_(None)))


async def ensure_project_in_firm(
    db: AsyncSession,
    project_id: int,
    user: Optional[User],
) -> Project:
    """加载 project, 同时校验 user 有权访问. 失败抛 403/404.

    场景:
      - 项目不存在 → 404
      - admin 或软隔离场景 → 直接返回
      - 项目 firm_id is None (老数据) → 允许任意 firm 访问 (向后兼容)
      - 项目 firm_id 不为空 且 与 user.firm_id 不一致 → 403

    Returns: Project ORM 对象
    """
    proj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    if _is_admin(user):
        return proj
    user_firm = _user_firm_id(user)
    if user_firm is None:
        # AUTH_ENABLED=false 或匿名 — 软兼容
        return proj
    if proj.firm_id is None:
        # 老数据无所属事务所, 兼容性放过 (建议 ops 跑迁移把 firm_id 补上)
        return proj
    if proj.firm_id != user_firm:
        logger.warning(
            "跨事务所访问被拒: user=%s firm=%s project=%s project_firm=%s",
            getattr(user, "username", None),
            user_firm,
            project_id,
            proj.firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问其他事务所的项目数据",
        )
    return proj


def project_default_firm_id(user: Optional[User]) -> Optional[int]:
    """新建 Project 时, 默认 firm_id 取自 current_user.

    AUTH_ENABLED=false / 匿名 / admin 都返 None (admin 应该显式传 firm_id).
    """
    if not settings.AUTH_ENABLED:
        return None
    if user is None or _is_admin(user):
        return None
    return getattr(user, "firm_id", None)


__all__ = [
    "scope_projects_to_firm",
    "ensure_project_in_firm",
    "project_default_firm_id",
]
