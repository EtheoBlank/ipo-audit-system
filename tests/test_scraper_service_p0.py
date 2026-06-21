"""Round 26 P0 (#4) — 付费信源无 key → 推红点通知测试.

覆盖:
  - 付费信源缺 key → run_daily_scan 后 NotificationService 收到 source_missing_key
  - 首次发现: severity=warn + body 含"请配置"提示
  - 30 天内重复发现 → 不重复发
  - 免费信源缺 key → 不通知
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
from app.models.db.notification import (  # noqa: E402
    Notification,
    NOTIF_MODULE_SENTIMENT,
    NOTIF_SEVERITY_WARN,
)
from app.models.db_models import Project, SentimentSource, SentimentSubject  # noqa: E402
from app.services.sentiment.scraper_service import SentimentScraperService  # noqa: E402


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
    """创建一个 active 项目, 用于 scrape_project."""
    p = Project(
        id=1,
        name="测试项目",
        company_name="测试公司",
        stock_short_name="测试",
        stock_code="600000",
        actual_controller="张三",
        legal_representative="李四",
        status="active",
        fiscal_year=2024,
    )
    session.add(p)
    await session.commit()
    return p


@pytest_asyncio.fixture
async def subject(session):
    s = SentimentSubject(
        id=1,
        project_id=1,
        alias_type="company",
        alias_value="测试公司",
        match_mode="contains",
        is_primary=True,
        weight=10,
        is_active=True,
    )
    session.add(s)
    await session.commit()
    return s


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
#  P0-4 — 付费信源缺 key → 推红点
# ============================================================


class TestPaidSourceMissingNotifies:
    """P0-4 (2026-06-19): 付费信源配置缺失 → 推 source_missing_key 红点."""

    @pytest.mark.asyncio
    async def test_paid_source_missing_notifies(
        self, session, project, subject
    ):
        """付费信源缺 key → NotificationService 收到 source_missing_key 通知."""
        from app.core.config import settings

        # 清掉所有付费源的 API key
        original_keys = {}
        for ref in ("TAVILY_API_KEY", "BOCHA_API_KEY", "SERPAPI_API_KEY"):
            original_keys[ref] = getattr(settings, ref, "")
            setattr(settings, ref, "")
        try:
            svc = SentimentScraperService()
            # 跑单个付费源 (tavily)
            await svc.scrape_project(session, project, [subject], source_codes=["tavily"])

            # 查通知
            from sqlalchemy import select

            stmt = select(Notification).where(
                Notification.type == "source_missing_key",
                Notification.module == NOTIF_MODULE_SENTIMENT,
                Notification.resource_id == "tavily",
            )
            notifs = list((await session.execute(stmt)).scalars().all())
            assert len(notifs) == 1
            assert notifs[0].severity == NOTIF_SEVERITY_WARN
            assert "tavily" in (notifs[0].body or "").lower() or "Tavily" in (notifs[0].body or "")
        finally:
            for ref, val in original_keys.items():
                setattr(settings, ref, val)

    @pytest.mark.asyncio
    async def test_first_time_detection(self, session, project, subject):
        """第一次缺失通知, severity=warn + 提示"请配置 X"."""
        from app.core.config import settings

        original = getattr(settings, "TAVILY_API_KEY", "")
        settings.TAVILY_API_KEY = ""
        try:
            svc = SentimentScraperService()
            await svc.scrape_project(session, project, [subject], source_codes=["tavily"])

            from sqlalchemy import select

            notif = (
                await session.execute(
                    select(Notification).where(
                        Notification.type == "source_missing_key",
                        Notification.resource_id == "tavily",
                    )
                )
            ).scalars().first()
            assert notif is not None
            assert notif.severity == "warn"
            assert "TAVILY_API_KEY" in (notif.body or "") or "请配置" in (notif.body or "")
        finally:
            settings.TAVILY_API_KEY = original

    @pytest.mark.asyncio
    async def test_repeat_missing_no_duplicate(self, session, project, subject):
        """30 天内已通知过 → 不重复发."""
        from app.core.config import settings

        original = getattr(settings, "BOCHA_API_KEY", "")
        settings.BOCHA_API_KEY = ""
        try:
            svc = SentimentScraperService()
            # 第一次: 应当推
            await svc.scrape_project(session, project, [subject], source_codes=["bocha"])
            # 第二次: 30 天内, 不再推 (但 scrape_project 会 commit, 所以走另一轮)
            await svc.scrape_project(session, project, [subject], source_codes=["bocha"])

            from sqlalchemy import select, func

            stmt = select(func.count(Notification.id)).where(
                Notification.type == "source_missing_key",
                Notification.resource_id == "bocha",
            )
            count = int((await session.execute(stmt)).scalar_one() or 0)
            assert count == 1, f"30 天内重复缺失应只发 1 次, 实际 {count}"
        finally:
            settings.BOCHA_API_KEY = original

    @pytest.mark.asyncio
    async def test_free_source_no_notification(self, session, project, subject):
        """免费信源 (CSRC RSS) 缺 key 不通知 (只有付费源才通知)."""
        # 免费信源 (rss/regulator/cninfo_announce) 不需要 key, 但走默认路径时不应
        # 触发 source_missing_key 通知 (即使被设为 unknown_source 也不通知).
        svc = SentimentScraperService()
        # 用一个 unknown 的免费 code 验证不推
        await svc.scrape_project(session, project, [subject], source_codes=["unknown_free_code"])

        from sqlalchemy import select

        notifs = list(
            (
                await session.execute(
                    select(Notification).where(Notification.type == "source_missing_key")
                )
            ).scalars().all()
        )
        assert notifs == [], "免费信源 / unknown code 不应触发 source_missing_key 通知"