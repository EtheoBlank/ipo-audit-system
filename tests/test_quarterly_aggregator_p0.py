"""Round 26 P0 (#5) — 季度窗口聚合排除 is_prior_year + ignored 事件测试.

覆盖:
  - aggregator 排除 is_prior_year=True 的事件
  - aggregator 排除 review_status='ignored' 的事件
  - 正常事件全纳入
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 在 import app 之前设环境变量
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

from app.core.database import Base  # noqa: E402
from app.models.db_models import (  # noqa: E402
    SentimentEvent,
    SentimentQuarterlyReport,
)
from app.services.sentiment.quarterly.aggregator import aggregate_window  # noqa: E402


# ============================================================
#  Fixtures
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


@pytest_asyncio.fixture
async def report(session):
    r = SentimentQuarterlyReport(
        id=1,
        project_id=1,
        fiscal_year=2024,
        period_type="Q1",
        period_end="2024-03-31",
        title="2024 第一季度 跟踪报告",
        daily_briefing_window_start="2024-01-01",
        daily_briefing_window_end="2024-03-31",
    )
    session.add(r)
    await session.commit()
    return r


def _mk_event(
    code,
    project_id=1,
    publish_date="2024-02-15",
    is_prior_year=False,
    review_status="pending",
):
    return SentimentEvent(
        project_id=project_id,
        source_code="rss",
        title=f"事件{code}",
        url=f"http://x/{code}",
        publish_date=publish_date,
        content_hash=f"hash_{code}",
        is_prior_year=is_prior_year,
        review_status=review_status,
    )


# ============================================================
#  P0-5 — aggregator 排除 prior_year + ignored
# ============================================================


class TestAggregatorFilters:
    """P0-5 (2026-06-19): aggregate_window 必须排除:
       - is_prior_year=True (上年同期数据, 不应归到本季度)
       - review_status='ignored' (审计师已标记剔除)
    """

    @pytest.mark.asyncio
    async def test_aggregator_excludes_prior_year(self, session, report):
        """is_prior_year=True 的事件被排除."""
        session.add(_mk_event("A"))  # 正常, 应纳入
        session.add(_mk_event("B", is_prior_year=True))  # 上年同期, 排除
        session.add(_mk_event("C"))  # 正常, 应纳入
        await session.commit()

        _briefings, events = await aggregate_window(session, report)
        codes = [e.title for e in events]
        assert "事件A" in codes
        assert "事件C" in codes
        assert "事件B" not in codes, "is_prior_year=True 应被排除"
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_aggregator_excludes_ignored(self, session, report):
        """review_status='ignored' 的事件被排除."""
        session.add(_mk_event("D", review_status="ignored"))  # 审计师剔除
        session.add(_mk_event("E", review_status="pending"))  # 正常
        session.add(_mk_event("F", review_status="verified"))  # 已核实
        await session.commit()

        _briefings, events = await aggregate_window(session, report)
        titles = [e.title for e in events]
        assert "事件D" not in titles, "review_status='ignored' 应被排除"
        assert "事件E" in titles
        assert "事件F" in titles
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_aggregator_includes_normal(self, session, report):
        """正常事件 (is_prior_year=False + review_status != ignored) 全纳入."""
        session.add(_mk_event("G", review_status="pending"))
        session.add(_mk_event("H", review_status="verified"))
        session.add(_mk_event("I", review_status="reviewing"))
        await session.commit()

        _briefings, events = await aggregate_window(session, report)
        assert len(events) == 3
        codes = sorted([e.title for e in events])
        assert codes == ["事件G", "事件H", "事件I"]