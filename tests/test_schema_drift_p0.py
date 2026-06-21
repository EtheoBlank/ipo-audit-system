"""P0 (2026-06-19) — Schema drift auto-migrate (Round 30).

生产 DB 严重 schema drift — 历史 ORM 加列但没建. 验证:
  1. team_members 缺 is_active / deactivated_at / deactivated_by → init_db 后补齐
  2. audit_logs 缺 19 列 → init_db 后补齐
  3. 通用 _auto_migrate_all_columns 兜底所有 Base.metadata 注册的列
  4. 反复调 init_db 幂等 (不丢数据, 不抛)
  5. 已有数据保留
  6. 表缺失 → create_all(checkfirst=True) 兜底建表
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTH_ENABLED", "true")

import pytest
import pytest_asyncio
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, init_db
from app.models.db.auth import AuditLog
from app.models.db_models import TeamMember


# ============================================================
#  Helpers
# ============================================================


@pytest_asyncio.fixture
async def drift_engine():
    """模拟生产漂移 DB: create_all 但删列, 让 drift 真实存在."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    await engine.dispose()


def _table_names(sync_conn) -> set[str]:
    inspector = sa_inspect(sync_conn)
    return set(inspector.get_table_names())


def _column_names(sync_conn, table_name: str) -> set[str]:
    inspector = sa_inspect(sync_conn)
    return {c["name"] for c in inspector.get_columns(table_name)}


async def _col_names(async_conn, table_name: str) -> set[str]:
    """async-friendly 列检查."""
    def _do(sync_conn):
        return _column_names(sync_conn, table_name)
    return await async_conn.run_sync(_do)


async def _table_names_async(async_conn) -> set[str]:
    def _do(sync_conn):
        return _table_names(sync_conn)
    return await async_conn.run_sync(_do)


# ============================================================
#  Tests
# ============================================================


class TestTeamMembersSoftDeleteColumns:
    """P0-1 — team_members 启动后必有 is_active / deactivated_at / deactivated_by."""

    @pytest.mark.asyncio
    async def test_team_members_has_is_active_after_init(self, drift_engine, monkeypatch):
        """init_db 后 team_members 表有 is_active / deactivated_at / deactivated_by."""
        # 先只建一张裸 team_members (id + full_name + level), 模拟漂移前的列
        async with drift_engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE team_members ("
                "id INTEGER PRIMARY KEY, "
                "full_name VARCHAR(100) NOT NULL, "
                "level VARCHAR(50) DEFAULT 'auditor' NOT NULL)"
            )

        # 把 init_db 的 engine 切到我们的 drift engine
        from app.core import database as db_mod

        monkeypatch.setattr(db_mod, "engine", drift_engine)
        await init_db()

        async with drift_engine.begin() as conn:
            cols = await _col_names(conn, "team_members")

        assert "is_active" in cols, "team_members 缺 is_active (软删除列)"
        assert "deactivated_at" in cols, "team_members 缺 deactivated_at"
        assert "deactivated_by" in cols, "team_members 缺 deactivated_by"


class TestAuditLogsFullColumns:
    """P0-2 — audit_logs 启动后必有全部 19 列."""

    @pytest.mark.asyncio
    async def test_audit_logs_has_all_columns_after_init(self, drift_engine, monkeypatch):
        """init_db 后 audit_logs 表 19 列齐全 (id + 18 业务列)."""
        # 模拟生产漂移: 只建 id 列
        async with drift_engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE audit_logs (id INTEGER PRIMARY KEY)"
            )

        from app.core import database as db_mod

        monkeypatch.setattr(db_mod, "engine", drift_engine)
        await init_db()

        async with drift_engine.begin() as conn:
            cols = await _col_names(conn, "audit_logs")

        expected = {
            "user_id", "user_display", "user_role", "firm_id",
            "action", "resource_type", "resource_id", "project_id",
            "method", "path", "ip", "user_agent", "status_code",
            "summary", "payload", "error_detail", "created_at",
        }
        missing = expected - cols
        assert not missing, f"audit_logs 缺列: {missing}"

        # ORM 能成功 INSERT 一行 (验证列类型/约束无误)
        async with drift_engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: sync_conn.execute(
                    AuditLog.__table__.insert().values(
                        action="create", summary="漂移自愈测试"
                    )
                ).rowcount
            )


class TestAutoMigrateIdempotent:
    """P0-5 — 反复 init_db 不抛、不丢数据."""

    @pytest.mark.asyncio
    async def test_idempotent_init_db_no_data_loss(self, drift_engine, monkeypatch):
        """跑两次 init_db, 第二次不抛错, 数据保留."""
        from app.core import database as db_mod

        monkeypatch.setattr(db_mod, "engine", drift_engine)
        await init_db()
        # 插一条 team_members 记录
        async with drift_engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: sync_conn.execute(
                    TeamMember.__table__.insert().values(
                        id=1, full_name="张三", level="auditor", is_active=True
                    )
                )
            )
        # 再跑一次 init_db — 应该幂等
        await init_db()
        # 数据应仍在
        async with drift_engine.begin() as conn:
            def _check(sync_conn):
                return sync_conn.execute(
                    TeamMember.__table__.select().where(TeamMember.__table__.c.id == 1)
                ).fetchall()
            rows = await conn.run_sync(_check)
        assert len(rows) == 1, "二次 init_db 不应丢数据"
        assert rows[0].full_name == "张三"


class TestAutoMigratePreservesData:
    """已有数据保留."""

    @pytest.mark.asyncio
    async def test_auto_migrate_preserves_existing_data(self, drift_engine, monkeypatch):
        """先插老数据 (漂移前), 再 init_db, 数据应完整保留."""
        # 1) 模拟漂移前: 只建老列
        async with drift_engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE team_members ("
                "id INTEGER PRIMARY KEY, "
                "full_name VARCHAR(100) NOT NULL, "
                "level VARCHAR(50) DEFAULT 'auditor' NOT NULL, "
                "status VARCHAR(20) DEFAULT 'active' NOT NULL, "
                "created_at DATETIME)"
            )
            await conn.exec_driver_sql(
                "INSERT INTO team_members (id, full_name, level, status) "
                "VALUES (1, '李四', 'manager', 'active')"
            )

        from app.core import database as db_mod

        monkeypatch.setattr(db_mod, "engine", drift_engine)
        await init_db()

        # 数据应保留 + 新列已补
        async with drift_engine.begin() as conn:
            def _check(sync_conn):
                rows = sync_conn.execute(
                    TeamMember.__table__.select().where(TeamMember.__table__.c.id == 1)
                ).fetchall()
                cols = _column_names(sync_conn, "team_members")
                return rows, cols
            rows, cols = await conn.run_sync(_check)

        assert len(rows) == 1, "漂移后老数据应保留"
        assert rows[0].full_name == "李四"
        assert rows[0].status == "active"
        # 新列已补
        assert "is_active" in cols
        assert "deactivated_at" in cols
        # 老行的新列默认值 (BOOLEAN DEFAULT 1)
        assert rows[0].is_active in (1, True), f"老行 is_active 应默认 1, 实际 {rows[0].is_active}"


class TestAutoMigrateHandlesMissingTable:
    """表缺失 → 自动 create_all + 补列."""

    @pytest.mark.asyncio
    async def test_auto_migrate_handles_missing_table(self, drift_engine, monkeypatch):
        """完全空 DB → init_db 建全表 (包括 ORM 中已注册的 audit_logs / ipo_ic_walkthrough_steps / blockers)."""
        from app.core import database as db_mod

        monkeypatch.setattr(db_mod, "engine", drift_engine)
        await init_db()

        async with drift_engine.begin() as conn:
            tables = await _table_names_async(conn)

        # 关键表 — 这些是生产漂移命门
        assert "team_members" in tables, "team_members 表未建"
        assert "audit_logs" in tables, "audit_logs 表未建"
        assert "ipo_ic_walkthrough_steps" in tables, "ipo_ic_walkthrough_steps 表未建"
        assert "blockers" in tables, "blockers 表未建"

        # 各表必备列
        async with drift_engine.begin() as conn:
            tm_cols = await _col_names(conn, "team_members")
            al_cols = await _col_names(conn, "audit_logs")
            ic_cols = await _col_names(conn, "ipo_ic_walkthrough_steps")
            bl_cols = await _col_names(conn, "blockers")

        assert "is_active" in tm_cols
        assert "action" in al_cols
        assert "walkthrough_id" in ic_cols
        assert "title" in bl_cols


class TestAutoMigrateAllColumnsGeneric:
    """通用 _auto_migrate_all_columns 兜底 — 任何 Base.metadata 注册的表都覆盖."""

    @pytest.mark.asyncio
    async def test_auto_migrate_covers_all_registered_tables(self, drift_engine, monkeypatch):
        """通用 helper 应处理 Base.metadata 全部注册的表 (覆盖率验证)."""
        # 准备: 只建 audit_logs 表, 缺 18 列
        async with drift_engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE audit_logs (id INTEGER PRIMARY KEY)"
            )

        from app.core import database as db_mod

        monkeypatch.setattr(db_mod, "engine", drift_engine)

        # 直接调通用 helper (不调 init_db 全流程)
        async with drift_engine.begin() as conn:
            await conn.run_sync(db_mod._sync_auto_migrate_all_columns)

        async with drift_engine.begin() as conn:
            cols = await _col_names(conn, "audit_logs")

        # 至少 action / created_at / path / summary 应被补
        for required in ("action", "created_at", "path", "summary", "user_id"):
            assert required in cols, f"通用 helper 未补 {required}"