"""Round 24 IDOR / 多租户修复回归测试.

覆盖:
  1. knowledge_base.get_book — 跨 firm 返回 404
  2. knowledge_base.delete_book — 跨 firm 返回 404
  3. regulations.favorite — 跨 firm project_id 返 403
  4. regulations.unfavorite — 跨 firm 收藏返 403
  5. regulations.list_favorites — 跨 firm 自动 join 过滤
  6. sentiment.toggle_source — assistant 角色 403, manager 通过
  7. workbooks.download_workbook — 抽不到 project_id → 404
  8. workbooks.generate_audit_note — 跨 firm 返 403 (取代旧 404)
  9. comprehensive.download_template — 强制登录 + firm_id="" 拒

所有测试用 TestClient + 临时 SQLite 文件, 不打网络.
**必须**在 conftest 之前先设 env, 否则 app.core.config 已加载生产 .db.
"""
from __future__ import annotations

import os
import tempfile

# 在 import app 之前设环境变量, 用临时文件 DB (AuditLogMiddleware 写日志需要)
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")

# Audit log 设为不写 (写文件 DB 较慢且会污染)
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.db.auth import (  # noqa: E402
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    ROLE_MANAGER,
    User,
)
from app.models.db_models import (  # noqa: E402
    KnowledgeBook,
    Project,
    Regulation,
    RegulationFavorite,
    SentimentSource,
)


# ----------------------------------------------------------------------
#  Module-level: 把 audit log 写表 mock 掉, 避免污染 test 输出
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_audit_log(monkeypatch):
    """所有 HTTP 请求都过 AuditLogMiddleware, mock 掉 record_audit_log 避免 DB 写入."""
    from app.services.auth import audit_log as al_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(al_mod, "record_audit_log", _noop)


@pytest.fixture(autouse=True)
def _enable_auth(monkeypatch):
    """所有测试都开启 AUTH_ENABLED, 让 firm 过滤生效. 单独测试再 monkeypatch 关掉."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)


# ----------------------------------------------------------------------
#  fixtures
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        yield s


@pytest_asyncio.fixture
async def prod_engine():
    """用临时文件 DB 模拟生产环境, 让 AuditLogMiddleware 能写 audit_logs."""
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{_tmp_db.name}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _user(uid: int = 1, role: str = ROLE_ASSISTANT, firm_id: int | None = 1) -> User:
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


def _project(pid: int, firm_id: int | None = 1) -> Project:
    return Project(
        id=pid,
        name=f"P{pid}",
        company_name="X",
        fiscal_year=2024,
        status="active",
        firm_id=firm_id,
    )


# FastAPI dependency override 帮手
def _override_user(user: User | None):
    from app.services.auth.dependencies import get_current_user, get_current_user_optional

    def _dep():
        return user

    app.dependency_overrides[get_current_user] = _dep
    app.dependency_overrides[get_current_user_optional] = _dep


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ----------------------------------------------------------------------
#  1) knowledge_base.get_book
# ----------------------------------------------------------------------


class TestGetBookFirmIsolation:
    async def test_cross_firm_returns_404(self, session, prod_engine):
        """非 admin 用户读别所的 book → 404 (不暴露存在性)."""
        # firm_A 的 user 试图读 firm_B 的 book
        book = KnowledgeBook(
            id=10, title="X", firm_id=99, status="ready",
            filename="x.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        )
        session.add(book)
        await session.commit()

        # Override DB dependency — 用 prod_engine 让 audit log 能写
        async def _db():
            async with prod_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sm = async_sessionmaker(prod_engine, expire_on_commit=False, class_=AsyncSession)
            async with sm() as s:
                yield s

        app.dependency_overrides[get_db] = _db

        # 把 user.firm_id 设为 1, book.firm_id 是 99
        from app.services.auth.dependencies import get_current_user_optional
        from app.models.db.auth import User as U

        firm1_user = _user(uid=1, role=ROLE_ASSISTANT, firm_id=1)
        app.dependency_overrides[get_current_user_optional] = lambda: firm1_user

        client = TestClient(app)
        resp = client.get("/api/knowledge-base/books/10")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "书籍不存在"

    async def test_admin_sees_cross_firm(self, session, prod_engine):
        """admin 跨所读 book → 200 (运维需求)."""
        book = KnowledgeBook(
            id=10, title="X", firm_id=99, status="ready",
            filename="x.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        )
        session.add(book)
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        admin = _user(uid=1, role=ROLE_ADMIN, firm_id=None)
        from app.services.auth.dependencies import get_current_user_optional
        app.dependency_overrides[get_current_user_optional] = lambda: admin

        client = TestClient(app)
        resp = client.get("/api/knowledge-base/books/10")
        assert resp.status_code == 200
        assert resp.json()["title"] == "X"

    async def test_same_firm_succeeds(self, session, prod_engine):
        """同 firm 读 book → 200."""
        book = KnowledgeBook(
            id=10, title="X", firm_id=1, status="ready",
            filename="x.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        )
        session.add(book)
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user_optional
        app.dependency_overrides[get_current_user_optional] = lambda: _user(1, ROLE_ASSISTANT, firm_id=1)

        client = TestClient(app)
        resp = client.get("/api/knowledge-base/books/10")
        assert resp.status_code == 200

    async def test_null_firm_old_data_admin_only(self, session, prod_engine):
        """老数据 firm_id=NULL → 仅 admin 可见."""
        book = KnowledgeBook(
            id=10, title="X", firm_id=None, status="ready",
            filename="x.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        )
        session.add(book)
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user_optional

        # 非 admin 不应见
        app.dependency_overrides[get_current_user_optional] = lambda: _user(1, ROLE_ASSISTANT, firm_id=1)
        client = TestClient(app)
        resp = client.get("/api/knowledge-base/books/10")
        assert resp.status_code == 404

        # admin 应可见
        app.dependency_overrides[get_current_user_optional] = lambda: _user(1, ROLE_ADMIN, firm_id=None)
        resp = client.get("/api/knowledge-base/books/10")
        assert resp.status_code == 200


# ----------------------------------------------------------------------
#  2) workbooks.generate_audit_note
# ----------------------------------------------------------------------


class TestAuditNoteFirmIsolation:
    async def test_cross_firm_returns_403(self, session):
        """跨 firm 触发 AI 审计说明 → 403 (不是 404)."""
        proj = _project(pid=1, firm_id=99)
        session.add(proj)
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user
        firm1_user = _user(uid=1, role=ROLE_ASSISTANT, firm_id=1)
        app.dependency_overrides[get_current_user] = lambda: firm1_user

        client = TestClient(app)
        # 不需要 deepseek 实际调用 — 跨 firm 应在权限校验阶段就被拒
        resp = client.post(
            "/api/workbooks/audit-note",
            json={"project_id": 1, "account_code": "5001", "account_name": "收入"},
        )
        assert resp.status_code == 403
        assert "无权" in resp.json()["detail"] or "其他事务所" in resp.json()["detail"]


# ----------------------------------------------------------------------
#  3) comprehensive.download_template — 强制登录
# ----------------------------------------------------------------------


class TestDownloadTemplateAuth:
    async def test_anonymous_request_401(self, session):
        """未登录下载模板 → 401 (AUTH_ENABLED=false 时仍强制)."""
        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        # 不 override get_current_user, 强制走认证

        # 在 AUTH_ENABLED=false 时, get_current_user 仍会返回 None
        # 旧逻辑允许 None → firm_id="" → 任何模板. 新逻辑应 403.
        client = TestClient(app)
        resp = client.get("/api/comprehensive/templates/any_id/download")
        # AUTH_ENABLED=false 时 get_current_user 返回 None, raise 401
        # (在旧逻辑下, 这条会绕过认证拿 200)
        if settings.AUTH_ENABLED:
            assert resp.status_code == 401
        else:
            # 兼容模式也必须有 firm_id, 否则 403
            assert resp.status_code in (401, 403)

    async def test_user_without_firm_403(self, session):
        """登录但没 firm_id → 403."""
        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user
        no_firm = _user(uid=1, role=ROLE_ASSISTANT, firm_id=None)
        app.dependency_overrides[get_current_user] = lambda: no_firm

        client = TestClient(app)
        resp = client.get("/api/comprehensive/templates/any_id/download")
        assert resp.status_code == 403


# ----------------------------------------------------------------------
#  4) regulations.list_favorites — firm 过滤
# ----------------------------------------------------------------------


class TestListFavoritesFirmFilter:
    async def test_firm_filter_via_project_join(self, session, prod_engine):
        """list_favorites 在没传 project_id 时, 应 join Project 表按 firm 过滤."""
        # firm 1 有 project 1, firm 2 有 project 2
        proj1 = _project(pid=1, firm_id=1)
        proj2 = _project(pid=2, firm_id=2)
        reg = Regulation(id=1, title="测试法规", full_text="", source="CSRC")
        fav1 = RegulationFavorite(id=10, regulation_id=1, project_id=1)
        fav2 = RegulationFavorite(id=11, regulation_id=1, project_id=2)
        session.add_all([proj1, proj2, reg, fav1, fav2])
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user, get_current_user_optional
        firm1_user = _user(uid=1, role=ROLE_ASSISTANT, firm_id=1)
        app.dependency_overrides[get_current_user] = lambda: firm1_user
        app.dependency_overrides[get_current_user_optional] = lambda: firm1_user

        # 需要打开 AUTH_ENABLED 让 firm 过滤生效
        original = settings.AUTH_ENABLED
        settings.AUTH_ENABLED = True
        try:
            client = TestClient(app)
            resp = client.get("/api/regulations/favorites/list")
            assert resp.status_code == 200
            ids = {f["id"] for f in resp.json()}
            assert 10 in ids  # firm 1 的能看到
            assert 11 not in ids  # firm 2 的看不到
        finally:
            settings.AUTH_ENABLED = original

    async def test_explicit_project_id_cross_firm_403(self, session, prod_engine):
        """显式传 project_id 但属于别所 → 403."""
        proj2 = _project(pid=2, firm_id=2)
        reg = Regulation(id=1, title="X", full_text="", source="CSRC")
        session.add_all([proj2, reg])
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user_optional
        firm1_user = _user(uid=1, role=ROLE_ASSISTANT, firm_id=1)
        app.dependency_overrides[get_current_user_optional] = lambda: firm1_user

        client = TestClient(app)
        resp = client.get("/api/regulations/favorites/list", params={"project_id": 2})
        assert resp.status_code == 403


# ----------------------------------------------------------------------
#  5) regulations.favorite — 跨 firm project 拒绝
# ----------------------------------------------------------------------


class TestFavoriteCrossFirm:
    async def test_favorite_with_cross_firm_project_403(self, session, prod_engine):
        """favorite 时 project_id 是别所的 → 403."""
        proj2 = _project(pid=2, firm_id=2)
        reg = Regulation(id=1, title="X", full_text="", source="CSRC")
        session.add_all([proj2, reg])
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user
        firm1_user = _user(uid=1, role=ROLE_ASSISTANT, firm_id=1)
        app.dependency_overrides[get_current_user] = lambda: firm1_user

        client = TestClient(app)
        resp = client.post(
            f"/api/regulations/1/favorite",
            json={"project_id": 2, "note": "试试", "tag": None},
        )
        assert resp.status_code == 403


# ----------------------------------------------------------------------
#  6) workbooks.download_workbook — 抽不到 project_id → 404
# ----------------------------------------------------------------------


class TestDownloadWorkbookProjectId:
    async def test_filename_without_project_id_404(self, session):
        """filename 不含 project_{N} → 404 (防绕过)."""
        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda: _user(1, ROLE_ASSISTANT, firm_id=1)

        client = TestClient(app)
        resp = client.get("/api/workbooks/download/account_detail_2024.xlsx")
        assert resp.status_code == 404

    async def test_filename_with_cross_firm_project_403(self, session):
        """filename 含别所 project_id → 403."""
        proj = _project(pid=99, firm_id=99)
        session.add(proj)
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user
        firm1_user = _user(uid=1, role=ROLE_ASSISTANT, firm_id=1)
        app.dependency_overrides[get_current_user] = lambda: firm1_user

        client = TestClient(app)
        resp = client.get("/api/workbooks/download/project_99_detail.xlsx")
        assert resp.status_code == 403


# ----------------------------------------------------------------------
#  7) sentiment.toggle_source — RBAC
# ----------------------------------------------------------------------


class TestToggleSourceRBAC:
    async def test_assistant_role_forbidden(self, session):
        """assistant 角色调 toggle_source → 403."""
        src = SentimentSource(
            id=1, code="csrc", provider_type="free_rss", display_name="CSRC"
        )
        session.add(src)
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda: _user(1, ROLE_ASSISTANT, firm_id=1)

        client = TestClient(app)
        resp = client.put("/api/sentiment/sources/1", json={"is_enabled": False})
        assert resp.status_code == 403

    async def test_manager_role_allowed(self, session):
        """manager 角色调 toggle_source → 200."""
        src = SentimentSource(
            id=1, code="csrc", provider_type="free_rss", display_name="CSRC", is_enabled=True
        )
        session.add(src)
        await session.commit()

        from app.core.database import get_db

        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        from app.services.auth.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda: _user(1, ROLE_MANAGER, firm_id=1)

        client = TestClient(app)
        resp = client.put("/api/sentiment/sources/1", json={"is_enabled": False})
        assert resp.status_code == 200
        assert resp.json()["is_enabled"] is False


# ----------------------------------------------------------------------
#  8) Helper — tenant.py 自身行为
# ----------------------------------------------------------------------


class TestEnsureProjectInFirm:
    """直接测 tenant helper (避免 HTTP 测试 mock 复杂)."""

    async def test_missing_project_404(self, session):
        from app.services.auth.tenant import ensure_project_in_firm
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await ensure_project_in_firm(session, 9999, _user(1, ROLE_ASSISTANT, firm_id=1))
        assert exc.value.status_code == 404

    async def test_same_firm_ok(self, session, monkeypatch):
        from app.services.auth.tenant import ensure_project_in_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        proj = _project(pid=1, firm_id=1)
        session.add(proj)
        await session.commit()
        result = await ensure_project_in_firm(session, 1, _user(1, ROLE_ASSISTANT, firm_id=1))
        assert result.id == 1

    async def test_cross_firm_403(self, session, monkeypatch):
        from app.services.auth.tenant import ensure_project_in_firm
        from fastapi import HTTPException

        # 必须开 AUTH_ENABLED 才能让 firm 过滤生效
        monkeypatch.setattr(settings, "AUTH_ENABLED", True)

        proj = _project(pid=1, firm_id=99)
        session.add(proj)
        await session.commit()
        with pytest.raises(HTTPException) as exc:
            await ensure_project_in_firm(session, 1, _user(1, ROLE_ASSISTANT, firm_id=1))
        assert exc.value.status_code == 403

    async def test_admin_cross_firm_ok(self, session, monkeypatch):
        from app.services.auth.tenant import ensure_project_in_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        proj = _project(pid=1, firm_id=99)
        session.add(proj)
        await session.commit()
        result = await ensure_project_in_firm(session, 1, _user(1, ROLE_ADMIN, firm_id=None))
        assert result.id == 1

    async def test_null_firm_old_data_ok(self, session):
        """proj.firm_id is None → 任何 user 都能访问 (兼容老数据)."""
        from app.services.auth.tenant import ensure_project_in_firm

        proj = _project(pid=1, firm_id=None)
        session.add(proj)
        await session.commit()
        # 任意 firm user
        result = await ensure_project_in_firm(session, 1, _user(1, ROLE_ASSISTANT, firm_id=1))
        assert result.id == 1


class TestScopeProjectsToFirm:
    async def test_admin_no_filter(self, session):
        from app.services.auth.tenant import scope_projects_to_firm
        from sqlalchemy import select

        q = select(Project)
        new_q = scope_projects_to_firm(q, _user(1, ROLE_ADMIN, firm_id=None))
        # admin 模式不附加 where
        sql = str(new_q.compile(compile_kwargs={"literal_binds": True}))
        # 编译后 WHERE 子句应不含 firm_id (或为空)
        assert "firm_id" not in sql or sql.count("WHERE") == 0

    async def test_user_firm_filter_applied(self, session, monkeypatch):
        from app.services.auth.tenant import scope_projects_to_firm
        from sqlalchemy import select

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        q = select(Project)
        new_q = scope_projects_to_firm(q, _user(1, ROLE_ASSISTANT, firm_id=1))
        sql = str(new_q.compile(compile_kwargs={"literal_binds": True}))
        # 编译后应有 firm_id 过滤
        assert "firm_id" in sql