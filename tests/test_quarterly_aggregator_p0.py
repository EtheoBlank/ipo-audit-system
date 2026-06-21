"""Round 26 P0 (#5) — 季度窗口聚合排除 is_prior_year + ignored 事件测试.

覆盖:
  - aggregator 排除 is_prior_year=True 的事件
  - aggregator 排除 review_status='ignored' 的事件
  - 正常事件全纳入
  - 同季度事件被聚合
  - 空窗口返回空
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
    SentimentDailyBriefing,
    SentimentEvent,
    SentimentQuarterlyReport,
)
from app.services.sentiment.quarterly.aggregator import (  # noqa: E402
    aggregate_window,
    lock_references,
)


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
    content_text="",
):
    return SentimentEvent(
        project_id=project_id,
        source_code="rss",
        title=f"事件{code}",
        url=f"http://x/{code}",
        publish_date=publish_date,
        content_text=content_text or f"事件{code}的内容",
        content_hash=f"hash_{code}",
        is_prior_year=is_prior_year,
        review_status=review_status,
    )


def _mk_briefing(code, project_id=1, briefing_date="2024-02-15"):
    return SentimentDailyBriefing(
        project_id=project_id,
        briefing_date=briefing_date,
        title=f"简报{code}",
        ai_summary=f"这是简报{code}的摘要",
        event_count=1,
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
    async def test_aggregate_window_excludes_prior_year(self, session, report):
        """is_prior_year=True 事件被排除."""
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
    async def test_aggregate_window_excludes_ignored(self, session, report):
        """review_status='ignored' 事件被排除."""
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
    async def test_aggregate_window_includes_normal(self, session, report):
        """正常事件 (is_prior_year=False + review_status != ignored) 全纳入."""
        session.add(_mk_event("G", review_status="pending"))
        session.add(_mk_event("H", review_status="verified"))
        session.add(_mk_event("I", review_status="reviewing"))
        await session.commit()

        _briefings, events = await aggregate_window(session, report)
        assert len(events) == 3
        codes = sorted([e.title for e in events])
        assert codes == ["事件G", "事件H", "事件I"]

    @pytest.mark.asyncio
    async def test_aggregate_window_groups_by_quarter(self, session, report):
        """同季度事件被聚合, 不同日期都进入同一窗口."""
        # Q1 窗口: 2024-01-01 ~ 2024-03-31
        session.add(_mk_event("JAN", publish_date="2024-01-15"))
        session.add(_mk_event("FEB", publish_date="2024-02-20"))
        session.add(_mk_event("MAR", publish_date="2024-03-25"))
        # 边界: 窗口外 (4月) 不应纳入
        session.add(_mk_event("APR", publish_date="2024-04-05"))
        # 边界: 上年末 (去年12月) 不应纳入
        session.add(_mk_event("DEC_LAST_YEAR", publish_date="2023-12-31"))
        await session.commit()

        _briefings, events = await aggregate_window(session, report)
        titles = [e.title for e in events]
        # 3 个 Q1 内的事件应全部进入
        assert "事件JAN" in titles
        assert "事件FEB" in titles
        assert "事件MAR" in titles
        assert len(events) == 3, f"窗口外事件被错误纳入: {titles}"

    @pytest.mark.asyncio
    async def test_aggregate_window_empty_returns_empty(self, session, report):
        """无事件 / 全部被排除 → 返回 ([], [])."""
        # 没有任何事件
        briefings, events = await aggregate_window(session, report)
        assert briefings == []
        assert events == []

        # 全部 prior_year / ignored → 也应返回空
        session.add(_mk_event("P", is_prior_year=True))
        session.add(_mk_event("I", review_status="ignored"))
        await session.commit()

        briefings, events = await aggregate_window(session, report)
        assert briefings == []
        assert events == []

    @pytest.mark.asyncio
    async def test_aggregate_window_includes_briefings_in_range(self, session, report):
        """简报按 briefing_date 落在窗口内, 全纳入 (无 ignored 字段)."""
        session.add(_mk_briefing("B1", briefing_date="2024-01-10"))
        session.add(_mk_briefing("B2", briefing_date="2024-03-30"))
        # 窗口外
        session.add(_mk_briefing("OUT", briefing_date="2024-04-01"))
        await session.commit()

        briefings, _events = await aggregate_window(session, report)
        assert len(briefings) == 2
        # 升序
        assert briefings[0].title == "简报B1"
        assert briefings[1].title == "简报B2"


class TestLockReferences:
    """lock_references 把 briefing/event id 写回 report (JSON 快照)."""

    @pytest.mark.asyncio
    async def test_lock_writes_ids_json(self, session, report):
        """把 id 序列化成 JSON 写回 report 字段."""
        # 不同 briefing_date 避开 (project_id, briefing_date) 唯一约束
        b1 = _mk_briefing("B1", briefing_date="2024-02-10")
        b2 = _mk_briefing("B2", briefing_date="2024-02-20")
        e1 = _mk_event("E1", publish_date="2024-02-15")
        session.add_all([b1, b2, e1])
        await session.commit()
        await session.refresh(b1)
        await session.refresh(b2)
        await session.refresh(e1)

        await lock_references(session, report, [b1, b2], [e1])

        import json

        assert json.loads(report.referenced_briefing_ids_json) == [b1.id, b2.id]
        assert json.loads(report.referenced_event_ids_json) == [e1.id]
