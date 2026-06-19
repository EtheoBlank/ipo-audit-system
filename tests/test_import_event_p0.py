"""P0 (2026-06-19) — sentiment import_event RBAC + severity 校验.

验证 3 个行为:
  1. Assistant 调 import_event → 403 (需 manager+)
  2. severity='critical' 在白名单 → 200
  3. severity='bogus' → 400
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
from app.models.db_models import Project
from app.models.sentiment import SentimentEventImport


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


def _assert_400(exc_info) -> None:
    assert exc_info.value.status_code == 400, (
        f"期望 400, 实际 {exc_info.value.status_code}: {exc_info.value.detail}"
    )


def _payload(severity: str = "info") -> SentimentEventImport:
    return SentimentEventImport(
        project_id=1,
        title="测试舆情事件",
        url="https://example.com/news/1",
        publish_date="2024-06-19",
        severity=severity,
    )


# ============================================================
#  Tests
# ============================================================


class TestImportEventRBAC:
    """P0-7 — import_event RBAC + severity 白名单."""

    @pytest.mark.asyncio
    async def test_import_event_requires_manager(self, db_session: AsyncSession):
        """Assistant 调 import_event → 403 (require_role(MANAGER)).

        注: 直接调函数不会触发 Depends, 这里用 FastAPI 的 Depends 模拟完整路径,
        或调用 require_role 工厂的内部 checker.
        """
        from fastapi import HTTPException

        from app.api.sentiment import import_event
        from app.services.auth.dependencies import require_role
        from app.services.auth.rbac import role_at_least

        # 静态校验: import_event 端点的 current_user 已改为 require_role(MANAGER)
        # 验证 sentinel: 在 endpoint signature 里应能找到 require_role 引用
        import inspect

        from app.services.auth.dependencies import require_role as _req_role
        from app.models.db.auth import ROLE_MANAGER as _MGR

        sig = inspect.signature(import_event)
        # 找 default 是 require_role(ROLE_MANAGER) 的参数 (current_user)
        # FastAPI 包装成 Depends(<require_role.<locals>._checker>), 闭包名带 require_role
        has_mgr_check = False
        for pname, param in sig.parameters.items():
            if pname == "current_user":
                if param.default is not inspect.Parameter.empty:
                    default_repr = repr(param.default)
                    # 闭包 _checker 在 require_role.<locals>._checker 里
                    if "require_role" in default_repr:
                        has_mgr_check = True
        assert has_mgr_check, (
            "import_event 必须把 current_user 依赖改为 require_role(ROLE_MANAGER), "
            f"实际: {sig.parameters.get('current_user')}"
        )

        # 行为校验: Assistant 角色不满足 require_role(MANAGER)
        assistant = _user(1, role=ROLE_ASSISTANT, firm_id=10)
        assert not role_at_least(assistant.role, _MGR), (
            "Assistant 不应满足 require_role(MANAGER)"
        )

        # Manager 满足
        manager = _user(2, role=ROLE_MANAGER, firm_id=10)
        assert role_at_least(manager.role, _MGR), (
            "Manager 应满足 require_role(MANAGER)"
        )

        # Admin 满足 (admin 视为最高)
        admin = _user(99, role=ROLE_ADMIN, firm_id=10)
        assert role_at_least(admin.role, _MGR), (
            "Admin 应满足 require_role(MANAGER)"
        )

        # require_role 工厂的内部 checker 在 Assistant 调用时应抛 403
        checker = _req_role(_MGR)
        with pytest.raises(HTTPException) as ei:
            await checker(user=assistant)
        _assert_403(ei)

    @pytest.mark.asyncio
    async def test_import_event_severity_validated(self, db_session: AsyncSession):
        """Manager 调 import_event, severity='critical' 在白名单 → 200."""
        from app.api.sentiment import import_event

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        manager = _user(2, role=ROLE_MANAGER, firm_id=10)
        result = await import_event(
            body=_payload("critical"),
            db=db_session,
            current_user=manager,
        )
        assert result is not None
        assert result.severity == "critical"
        assert result.project_id == 1

    @pytest.mark.asyncio
    async def test_import_event_severity_invalid_rejected(self, db_session: AsyncSession):
        """Manager 调 import_event, severity='bogus' → 400 (不在 ALL_NOTIF_SEVERITIES 白名单)."""
        from app.api.sentiment import import_event

        db_session.add(_project(1, firm_id=10))
        await db_session.commit()

        manager = _user(2, role=ROLE_MANAGER, firm_id=10)
        with pytest.raises(Exception) as ei:
            await import_event(
                body=_payload("bogus"),
                db=db_session,
                current_user=manager,
            )
        _assert_400(ei)
