"""P0 (2026-06-19) — delete_member 软删除 + 硬删除 admin-only.

验证 3 个行为:
  1. 默认 DELETE /members/{id} 走软删除 (is_active=False, deactivated_at 非空)
  2. 软删后 ProjectAssignment / DailyReport 仍可查 (member_id 引用保留)
  3. 硬删除需 admin + header X-Confirm-Hard-Delete=yes; 非 admin 触发硬删返回 403
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTH_ENABLED", "true")

from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.db.auth import (
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    User,
)
from app.models.db_models import (
    DailyReport,
    Project,
    ProjectAssignment,
    TeamMember,
)


# ============================================================
#  Fixtures
# ============================================================


@pytest_asyncio.fixture
async def db_session(monkeypatch) -> AsyncSession:
    """独立 SQLite 内存 DB."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sm() as s:
        yield s
    await engine.dispose()


def _user(uid: int, role: str = ROLE_ASSISTANT, firm_id: Optional[int] = None) -> User:
    return User(
        id=uid,
        username=f"u{uid}",
        full_name=f"用户{uid}",
        role=role,
        is_active=True,
        is_locked=False,
        password_hash="!",
        firm_id=firm_id,
    )


def _project(pid: int, firm_id: int = 1) -> Project:
    return Project(
        id=pid,
        name=f"P{pid}",
        company_name=f"C{pid}",
        fiscal_year=2024,
        status="active",
        firm_id=firm_id,
    )


def _assert_403(exc_info) -> None:
    assert exc_info.value.status_code == 403, (
        f"期望 403, 实际 {exc_info.value.status_code}: {exc_info.value.detail}"
    )


# ============================================================
#  Tests
# ============================================================


class TestDeleteMemberSoftDelete:
    """P0-6 — 软删除行为."""

    @pytest.mark.asyncio
    async def test_delete_member_soft_deletes(self, db_session: AsyncSession):
        """默认 delete_member 走软删除: is_active=False, deactivated_at 非空."""
        from app.api.team_management import delete_member

        db_session.add(_project(1, firm_id=10))
        m = TeamMember(
            id=1,
            full_name="张三",
            level="auditor",
            status="active",
            is_active=True,
        )
        db_session.add(m)
        db_session.add(
            ProjectAssignment(
                id=1, project_id=1, member_id=1, role_in_project="auditor"
            )
        )
        await db_session.commit()
        await db_session.refresh(m)

        user = _user(1, role=ROLE_MANAGER, firm_id=10)
        result = await delete_member(member_id=1, db=db_session, current_user=user)

        assert result.get("soft_deleted") is True
        # 校验 DB 状态
        await db_session.refresh(m)
        assert m.is_active is False
        assert m.deactivated_at is not None
        assert m.deactivated_by == 1  # current_user.id
        assert m.status == "inactive"  # 同步 status 字段

    @pytest.mark.asyncio
    async def test_delete_member_keeps_history(self, db_session: AsyncSession):
        """软删后, ProjectAssignment / DailyReport 的 member_id 仍可查."""
        from sqlalchemy import select

        from app.api.team_management import delete_member

        db_session.add(_project(1, firm_id=10))
        m = TeamMember(
            id=1,
            full_name="李四",
            level="auditor",
            status="active",
            is_active=True,
        )
        db_session.add(m)
        db_session.add(
            ProjectAssignment(
                id=1, project_id=1, member_id=1, role_in_project="auditor"
            )
        )
        db_session.add(
            DailyReport(
                id=1, project_id=1, member_id=1, report_date="2024-06-15",
                completed_work="今日盘点", hours_logged=8.0,
            )
        )
        await db_session.commit()

        user = _user(1, role=ROLE_MANAGER, firm_id=10)
        await delete_member(member_id=1, db=db_session, current_user=user)

        # ProjectAssignment 仍可查
        assign = (
            await db_session.execute(
                select(ProjectAssignment).where(ProjectAssignment.member_id == 1)
            )
        ).scalar_one_or_none()
        assert assign is not None, "软删后 ProjectAssignment 应仍存在"
        # DailyReport 仍可查
        report = (
            await db_session.execute(
                select(DailyReport).where(DailyReport.member_id == 1)
            )
        ).scalar_one_or_none()
        assert report is not None, "软删后 DailyReport 应仍存在"

    @pytest.mark.asyncio
    async def test_hard_delete_requires_admin(self, db_session: AsyncSession):
        """硬删除需 admin + X-Confirm-Hard-Delete=yes header; 非 admin → 403."""
        from app.api.team_management import delete_member

        db_session.add(_project(1, firm_id=10))
        m = TeamMember(
            id=1,
            full_name="王五",
            level="auditor",
            status="active",
            is_active=True,
        )
        db_session.add(m)
        # 不挂 assignment (避免硬删除时 ORM 级联触发 NOT NULL 约束冲突;
        # 该约束真实存在 — ProjectAssignment.member_id NOT NULL, 这本身
        # 印证 P0 修复的动机: 硬删除会破坏关联, 必须 admin 确认)
        await db_session.commit()

        # 非 admin 调硬删 → 403
        manager = _user(1, role=ROLE_MANAGER, firm_id=10)
        with pytest.raises(Exception) as ei:
            await delete_member(
                member_id=1,
                db=db_session,
                current_user=manager,
                x_confirm_hard_delete="yes",
            )
        _assert_403(ei)

        # admin 调硬删 → 成功, hard_deleted=True
        admin = _user(99, role=ROLE_ADMIN, firm_id=10)
        result = await delete_member(
            member_id=1,
            db=db_session,
            current_user=admin,
            x_confirm_hard_delete="yes",
        )
        assert result.get("hard_deleted") is True

        # 行已删
        from sqlalchemy import select

        gone = (
            await db_session.execute(select(TeamMember).where(TeamMember.id == 1))
        ).scalar_one_or_none()
        assert gone is None
