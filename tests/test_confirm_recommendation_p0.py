"""Round 25 P0 (#14) — ``ManagementRecommendationConfirm`` 输入校验测试.

覆盖:
  - manager_notes 长度上限 2000 → Pydantic ValidationError
  - manager_notes 纯空白 → 422
  - manager_notes 控制字符 (除 \\n) → 422
  - service 层 ``confirm_recommendation`` 把 sha256(notes)[:16] 写入 ``notes_hash``
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 在 import app 之前设环境变量, 用临时文件 DB
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.models.team_management import ManagementRecommendationConfirm  # noqa: E402


# ============================================================
# Fixtures
# ============================================================


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


# ============================================================
#  Pydantic 校验
# ============================================================


class TestManagerNotesPydantic:
    """P0 (round25 #14): manager_notes 输入校验."""

    def test_normal_notes_pass(self):
        c = ManagementRecommendationConfirm(manager_notes="已与项目负责人沟通确认。")
        assert c.manager_notes == "已与项目负责人沟通确认。"

    def test_none_notes_pass(self):
        c = ManagementRecommendationConfirm(manager_notes=None)
        assert c.manager_notes is None

    def test_notes_max_length_enforced(self):
        """5000 字符 → ValidationError."""
        long_notes = "测试" * 2500  # 5000 chars
        with pytest.raises(ValidationError) as exc_info:
            ManagementRecommendationConfirm(manager_notes=long_notes)
        # 错误指向 manager_notes 字段
        errors = exc_info.value.errors()
        assert any("manager_notes" in str(e.get("loc", ())) for e in errors)

    def test_notes_exactly_2000_chars_pass(self):
        """边界值: 2000 字符应该通过."""
        notes = "x" * 2000
        c = ManagementRecommendationConfirm(manager_notes=notes)
        assert len(c.manager_notes) == 2000

    def test_notes_pure_whitespace_rejected(self):
        """纯空格 / 纯 \\n / 纯 \\t 一律拒绝."""
        for bad in ["   ", "\n\n", "\t\t", " \n \t "]:
            with pytest.raises(ValidationError) as exc_info:
                ManagementRecommendationConfirm(manager_notes=bad)
            errors = exc_info.value.errors()
            assert any(
                "manager_notes" in str(e.get("loc", ())) and "纯空白" in str(e.get("msg", ""))
                for e in errors
            ), f"应拒绝纯空白: {bad!r}, errors={errors}"

    def test_notes_control_chars_rejected(self):
        """含 \\x00 / \\x1f 等控制字符 → 拒绝 (除 \\n)."""
        for bad in ["恶意\x00文本", "末尾\r回车", "制表\t是允许的吗"]:
            # \r 和 \t 都在禁止集合里
            with pytest.raises(ValidationError) as exc_info:
                ManagementRecommendationConfirm(manager_notes=bad)
            errors = exc_info.value.errors()
            assert any(
                "manager_notes" in str(e.get("loc", ()))
                and "控制字符" in str(e.get("msg", ""))
                for e in errors
            ), f"应拒绝含控制字符: {bad!r}, errors={errors}"

    def test_notes_newline_allowed(self):
        """\\n 是合法的 (审计备注多行很常见)."""
        c = ManagementRecommendationConfirm(manager_notes="第一行\n第二行")
        assert "\n" in c.manager_notes

    def test_notes_del_char_rejected(self):
        """0x7F (DEL) 也算非法."""
        with pytest.raises(ValidationError):
            ManagementRecommendationConfirm(manager_notes="恶意\x7f文本")


# ============================================================
#  Service 层 notes_hash 落库
# ============================================================


class TestConfirmRecommendationNotesHash:
    """P0 (round25 #14): 服务端持久化 ``notes_hash`` (sha256 hex 前 16 位)."""

    @pytest.mark.asyncio
    async def test_notes_hash_recorded(self, session: AsyncSession):
        """校验 confirm 后 DB 里 ``notes_hash`` 与 manager_notes 摘要一致."""
        from app.models.db_models import ManagementRecommendation, Project
        from app.services.team_management.service import TeamManagementService

        proj = Project(name="T", company_name="X", fiscal_year=2024)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        rec = ManagementRecommendation(
            project_id=proj.id,
            generated_at=datetime.now(timezone.utc),
            recommendations="AI 建议: 加强应收账款函证覆盖率。",
            ai_enabled=True,
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)

        notes = "已与项目负责人电话沟通, 同意按建议执行。"

        # 模拟 confirmed_by_user (User ORM 字段子集, service 只要 full_name/username/id)
        fake_user = SimpleNamespace(
            id=42, username="u42", full_name="项目负责人 张三"
        )
        svc = TeamManagementService()
        confirmed = await svc.confirm_recommendation(session, rec.id, fake_user, notes)

        assert confirmed.is_confirmed is True
        assert confirmed.confirmed_by_user_id == 42
        assert confirmed.manager_notes == notes
        assert confirmed.notes_hash is not None
        # sha256 hex 16 字符
        assert len(confirmed.notes_hash) == 16
        # 内容 hash 一致
        expected = hashlib.sha256(notes.encode("utf-8")).hexdigest()[:16]
        assert confirmed.notes_hash == expected

    @pytest.mark.asyncio
    async def test_notes_hash_skipped_when_notes_none(self, session: AsyncSession):
        """未传 manager_notes 时 notes_hash 保持 None, 不抛错."""
        from app.models.db_models import ManagementRecommendation, Project
        from app.services.team_management.service import TeamManagementService

        proj = Project(name="T2", company_name="X", fiscal_year=2024)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        rec = ManagementRecommendation(
            project_id=proj.id, generated_at=datetime.now(timezone.utc),
            recommendations="x", ai_enabled=True,
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)

        fake_user = SimpleNamespace(id=7, username="u7", full_name="用户七")
        confirmed = await TeamManagementService().confirm_recommendation(
            session, rec.id, fake_user, None
        )

        assert confirmed.is_confirmed is True
        assert confirmed.manager_notes is None
        assert confirmed.notes_hash is None
