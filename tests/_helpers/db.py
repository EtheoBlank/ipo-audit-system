"""数据库 / Session helpers.

提供 async engine、session、事务回滚 fixture. 默认走 in-memory SQLite, 速度优先.
如需 Postgres 特性 (JSONB、FOR UPDATE), 改 ``DB_URL`` env var.

用法::

    from tests._helpers.db import async_session, transactional_db

    async def test_x(async_session):
        # 自动 begin + rollback, 不污染 DB
        result = await async_session.execute(select(User))
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 内存 SQLite 默认. CI 用 Postgres 时: DB_URL=postgresql+asyncpg://...
DEFAULT_DB_URL = os.getenv(
    "TEST_DB_URL",
    "sqlite+aiosqlite:///:memory:",
)


@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """每个 test 一个新 engine + schema. 慢但隔离干净."""
    from app.models.db_models import Base

    engine = create_async_engine(DEFAULT_DB_URL, echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_session_factory(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="function")
async def async_session(async_session_factory) -> AsyncIterator[AsyncSession]:
    """yield session, 自动 rollback (不持久化任何数据).

    用法::

        async def test_x(async_session):
            user = User(...)
            async_session.add(user)
            await async_session.flush()
            # 退出 fixture 时 rollback, DB 干净
    """
    async with async_session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()
