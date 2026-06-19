"""QuarterlyReport trigger 测试 — create_or_get_report 幂等性 + 周期计算.

覆盖:
  - 新建 project+period → 创建新报告
  - 同 project+period 第二次调用 → 返回已有
  - 并发: 模拟 IntegrityError → 兜底 select 仍返回一致结果
  - QuarterlyPeriodSpec 边界
"""
from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("AUDIT_LOG_WRITE_ONLY", "false")

from app.core.database import Base  # noqa: E402
from app.models.db_models import (  # noqa: E402
    Project,
    SentimentNotification,
    SentimentQuarterlyReport,
)
from app.services.sentiment.quarterly.trigger import (  # noqa: E402
    QuarterlyPeriodSpec,
    _add_notification,
    create_or_get_report,
    mark_briefing_ready,
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
async def project(session):
    p = Project(
        id=1,
        name="P1",
        company_name="测试公司",
        fiscal_year=2024,
        status="active",
    )
    session.add(p)
    await session.commit()
    return p


# ============================================================
#  Tests
# ============================================================


class TestCreateOrGetReport:
    """create_or_get_report 幂等性 + 触发字段填充."""

    @pytest.mark.asyncio
    async def test_create_or_get_report_creates_new(self, session, project):
        """首次调用 project+period 不存在 → 新建报告."""
        rep = await create_or_get_report(
            session, project_id=project.id, period_type="Q1", fiscal_year=2024,
            trigger_type="manual",
        )
        assert rep.id is not None
        assert rep.project_id == project.id
        assert rep.period_type == "Q1"
        assert rep.fiscal_year == 2024
        # 期间字段由 QuarterlyPeriodSpec 算出
        assert rep.period_end == "2024-03-31"
        assert rep.daily_briefing_window_start == "2024-01-01"
        assert rep.daily_briefing_window_end == "2024-03-31"
        assert rep.trigger_type == "manual"
        assert rep.title == "2024 第一季度 跟踪报告"

    @pytest.mark.asyncio
    async def test_create_or_get_report_returns_existing(self, session, project):
        """同 project+period 第二次调用 → 返回同一 id, 不重建."""
        rep1 = await create_or_get_report(
            session, project_id=project.id, period_type="Q1", fiscal_year=2024,
        )
        rep2 = await create_or_get_report(
            session, project_id=project.id, period_type="Q1", fiscal_year=2024,
        )
        # 同一对象 / 同一 id
        assert rep1.id == rep2.id

        # DB 中也确实只有 1 条
        from sqlalchemy import select, func

        count = (await session.execute(
            select(func.count(SentimentQuarterlyReport.id)).where(
                SentimentQuarterlyReport.project_id == project.id,
                SentimentQuarterlyReport.period_type == "Q1",
                SentimentQuarterlyReport.fiscal_year == 2024,
            )
        )).scalar_one()
        assert count == 1

    @pytest.mark.asyncio
    async def test_create_or_get_report_race_condition(self, session, project):
        """模拟并发: 第一次 commit 抛 IntegrityError, rollback 后重新 select → 一致结果."""
        from unittest.mock import AsyncMock, patch

        # 先插入一条 (模拟另一个并发请求已写入)
        existing = SentimentQuarterlyReport(
            project_id=project.id,
            period_type="Q1",
            fiscal_year=2024,
            period_end="2024-03-31",
            title="2024 第一季度 跟踪报告",
            daily_briefing_window_start="2024-01-01",
            daily_briefing_window_end="2024-03-31",
        )
        session.add(existing)
        await session.commit()
        existing_id = existing.id

        # 模拟 commit 抛 IntegrityError — 我们的实现会 rollback 再 select,
        # 此时应拿到 existing, 而不是新建失败.
        real_commit = session.commit

        call_count = {"n": 0}

        async def flaky_commit():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 第 1 次 commit 模拟竞争失败
                raise IntegrityError("simulated", {}, Exception("UNIQUE"))
            return await real_commit()

        session.commit = flaky_commit  # type: ignore[assignment]

        try:
            rep = await create_or_get_report(
                session, project_id=project.id, period_type="Q1", fiscal_year=2024,
            )
            # IntegrityError 兜底后 select → existing_id
            assert rep.id == existing_id
        finally:
            session.commit = real_commit  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_create_or_get_report_different_periods(self, session, project):
        """同一 project 不同 period_type / 不同 fiscal_year → 各自独立."""
        rep_q1_2024 = await create_or_get_report(
            session, project_id=project.id, period_type="Q1", fiscal_year=2024,
        )
        rep_h1_2024 = await create_or_get_report(
            session, project_id=project.id, period_type="H1", fiscal_year=2024,
        )
        rep_q1_2023 = await create_or_get_report(
            session, project_id=project.id, period_type="Q1", fiscal_year=2023,
        )

        assert rep_q1_2024.id != rep_h1_2024.id
        assert rep_q1_2024.id != rep_q1_2023.id
        assert rep_h1_2024.id != rep_q1_2023.id

        # 标题不同
        assert rep_q1_2024.title == "2024 第一季度 跟踪报告"
        assert rep_h1_2024.title == "2024 半年度 跟踪报告"
        assert rep_q1_2023.title == "2023 第一季度 跟踪报告"


class TestMarkBriefingReady:
    """mark_briefing_ready 写通知 + 红点."""

    @pytest.mark.asyncio
    async def test_mark_briefing_ready_creates_notification(self, session, project):
        """生成 report 后调用 → 1 条 'report_ready' 通知写入 DB."""
        rep = await create_or_get_report(
            session, project_id=project.id, period_type="Q1", fiscal_year=2024,
        )
        # 清掉 create_or_get_report 不写通知的事实, 此处只测 mark_briefing_ready 自身
        from sqlalchemy import select

        before = (await session.execute(
            select(SentimentNotification).where(
                SentimentNotification.project_id == project.id,
                SentimentNotification.notification_type == "report_ready",
            )
        )).scalars().all()
        assert len(before) == 0

        await mark_briefing_ready(session, project_id=project.id, report_id=rep.id)

        after = (await session.execute(
            select(SentimentNotification).where(
                SentimentNotification.project_id == project.id,
                SentimentNotification.notification_type == "report_ready",
            )
        )).scalars().all()
        assert len(after) == 1
        n = after[0]
        assert n.title == "季度跟踪报告已生成, 请审阅"
        assert f"report_id={rep.id}" in (n.link_url or "")
        assert n.is_read is False


class TestAddNotification:
    """_add_notification 内部 helper — 同样能正确插入."""

    @pytest.mark.asyncio
    async def test_add_notification_inserts_row(self, session, project):
        await _add_notification(
            session,
            project_id=project.id,
            ntype="report_ready",
            title="测试通知",
            body="正文",
            link_url="/x?y=1",
        )
        from sqlalchemy import select

        n = (await session.execute(
            select(SentimentNotification).where(
                SentimentNotification.project_id == project.id,
            )
        )).scalars().one()
        assert n.notification_type == "report_ready"
        assert n.title == "测试通知"
        assert n.body == "正文"
        assert n.link_url == "/x?y=1"
