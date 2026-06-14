"""舆情抓取服务 — 照搬 RegulationScraperService.scrape() 模式 (asyncio.gather + dedup)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    Project,
    SentimentEvent,
    SentimentSource,
    SentimentSubject,
    PaidSourceMissingKey,
)
from app.services.sentiment.dedup import RawSentimentItem
from app.services.sentiment.http_client import SentimentHttpClient
from app.services.sentiment.notifier import create_notification
from app.services.sentiment.sources.announce_adapter import CninfoAnnounceAdapter
from app.services.sentiment.sources.manual_adapter import ManualAdapter
from app.services.sentiment.sources.paid_adapters import (
    BochaAdapter,
    SerpAPIAdapter,
    TavilyAdapter,
)
from app.services.sentiment.sources.regulator_adapter import RegulatorAdapter
from app.services.sentiment.sources.rss_adapter import RssAdapter

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 信源注册表 — code → (provider_type, display_name, is_paid, api_key_ref, factory)
_PROVIDER_REGISTRY: dict[str, dict] = {
    "rss": {
        "provider_type": "free_rss",
        "display_name": "RSS 订阅",
        "is_paid": False,
        "api_key_ref": None,
        "factory": lambda: RssAdapter,
    },
    "cninfo_announce": {
        "provider_type": "free_scrape",
        "display_name": "巨潮公告",
        "is_paid": False,
        "api_key_ref": None,
        "factory": lambda: CninfoAnnounceAdapter,
    },
    "regulator": {
        "provider_type": "free_scrape",
        "display_name": "监管/交易所披露",
        "is_paid": False,
        "api_key_ref": None,
        "factory": lambda: RegulatorAdapter,
    },
    "tavily": {
        "provider_type": "paid_api",
        "display_name": "Tavily 搜索",
        "is_paid": True,
        "api_key_ref": "TAVILY_API_KEY",
        "factory": lambda: TavilyAdapter,
    },
    "bocha": {
        "provider_type": "paid_api",
        "display_name": "博查搜索",
        "is_paid": True,
        "api_key_ref": "BOCHA_API_KEY",
        "factory": lambda: BochaAdapter,
    },
    "serpapi": {
        "provider_type": "paid_api",
        "display_name": "SerpAPI",
        "is_paid": True,
        "api_key_ref": "SERPAPI_API_KEY",
        "factory": lambda: SerpAPIAdapter,
    },
    "manual": {
        "provider_type": "manual",
        "display_name": "手工录入",
        "is_paid": False,
        "api_key_ref": None,
        "factory": lambda: ManualAdapter,
    },
}


class SentimentScraperService:
    """舆情抓取主服务 — 调度所有信源 + 入库 + 写红点通知.

    复用模式 (与 RegulationScraperService.scrape() 一致):
        - asyncio.gather 并发抓多个信源
        - content_hash 在 RawSentimentItem 内已算好
        - 入库前 select where content_hash == ch 二次去重
        - 任何异常不阻断其他信源

    关键方法:
        - scrape_project(project_id, source_codes)  → (新增事件数, 各信源状态)
        - run_daily_scan()                          → 扫描所有 active 项目
        - bootstrap_default_sources()               → 首次启动注册 7 个默认信源
    """

    def __init__(self) -> None:
        pass

    # ---- 信源注册 -------------------------------------------------------

    async def bootstrap_default_sources(self, db: AsyncSession) -> int:
        """首次启动把 7 个默认信源写入 SentimentSource (若不存在)."""
        added = 0
        for code, meta in _PROVIDER_REGISTRY.items():
            res = await db.execute(select(SentimentSource).where(SentimentSource.code == code))
            if res.scalar_one_or_none():
                continue
            db.add(
                SentimentSource(
                    code=code,
                    provider_type=meta["provider_type"],
                    display_name=meta["display_name"],
                    is_paid=meta["is_paid"],
                    api_key_ref=meta["api_key_ref"],
                    is_enabled=True,
                )
            )
            added += 1
        if added:
            await db.commit()
            logger.info("bootstrap_default_sources: 新增 %d 个信源", added)
        return added

    # ---- 单项目扫描 -----------------------------------------------------

    async def scrape_project(
        self,
        db: AsyncSession,
        project: Project,
        subjects: list[SentimentSubject],
        *,
        source_codes: Optional[list[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        max_events: Optional[int] = None,
    ) -> tuple[int, dict[str, str]]:
        """扫描单个项目. 返回 (新增事件数, {source_code: last_run_status})."""
        max_events = max_events or settings.SENTIMENT_MAX_EVENTS_PER_PROJECT_PER_DAY

        # 默认窗口: 昨天 0:00 ~ 今天 23:59 (覆盖 scheduler 跑的时刻)
        if not date_to:
            date_to = _utcnow().strftime("%Y-%m-%d")
        if not date_from:
            # 默认 7 天前
            d = datetime.now() - timedelta(days=7)
            date_from = d.strftime("%Y-%m-%d")

        # 收集要跑的信源
        if source_codes is None:
            source_codes = list(_PROVIDER_REGISTRY.keys())

        # 信源状态收集
        source_status: dict[str, str] = {}

        # 抓取 + 入库
        async with SentimentHttpClient() as http:
            tasks = []
            for code in source_codes:
                meta = _PROVIDER_REGISTRY.get(code)
                if not meta:
                    source_status[code] = "unknown_source"
                    continue
                # 付费源无 key → skip
                if meta["is_paid"]:
                    key = getattr(settings, meta["api_key_ref"], "") if meta["api_key_ref"] else ""
                    if not key:
                        source_status[code] = "skipped"
                        logger.debug("信源 %s 无 API key, 跳过", code)
                        continue
                    adapter_cls = meta["factory"]()
                    adapter = adapter_cls(http, api_key=key)
                else:
                    adapter_cls = meta["factory"]()
                    adapter = adapter_cls(http)
                tasks.append(self._run_one_source(adapter, project, subjects, date_from, date_to))

            # 限频: 单项目最多 max_events 条入库
            results: list[list[RawSentimentItem]] = []
            if tasks:
                # 单个信源超时用 gather, 单个失败不阻断
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                for code, result in zip(source_codes, gathered):
                    if isinstance(result, Exception):
                        source_status[code] = "failed"
                        logger.warning("信源 %s 抓取失败: %s", code, result)
                    else:
                        source_status[code] = "success"
                        results.append(result)
            else:
                results = []

        # 合并 + 截断
        all_items: list[RawSentimentItem] = []
        for items in results:
            all_items.extend(items)
        # 按发布时间倒序 (近的优先), 截断
        all_items.sort(key=lambda x: x.publish_date or "", reverse=True)
        all_items = all_items[:max_events]

        # 入库
        added = 0
        for item in all_items:
            try:
                ev = await self._persist_event(db, item)
                if ev is not None:
                    added += 1
            except IntegrityError:
                # content_hash 冲突 (并发), 跳过
                await db.rollback()
                continue
            except Exception as exc:
                logger.warning("入库失败 %s: %s", item.title, exc)
                await db.rollback()

        if added > 0:
            await create_notification(
                db,
                notification_type="new_event",
                title=f"新增 {added} 条舆情事件: {project.company_name}",
                body=f"扫描时间 {_utcnow().strftime('%Y-%m-%d %H:%M')}",
                project_id=project.id,
                link_url=f"/sentiment?project_id={project.id}",
            )

        await db.commit()
        logger.info(
            "scrape_project: project=%s 命中 %d 条 (新增 %d) 各源状态=%s",
            project.id,
            len(all_items),
            added,
            source_status,
        )
        return added, source_status

    async def _run_one_source(
        self,
        adapter,
        project: Project,
        subjects: list[SentimentSubject],
        date_from: str,
        date_to: str,
    ) -> list[RawSentimentItem]:
        try:
            return await adapter.fetch(project, subjects, date_from=date_from, date_to=date_to)
        except PaidSourceMissingKey:
            logger.info("信源 %s 缺 key, 跳过 (内部检查已应挡)", adapter.source_code)
            return []
        except Exception as exc:
            logger.exception("信源 %s 异常: %s", adapter.source_code, exc)
            return []

    async def _persist_event(
        self,
        db: AsyncSession,
        item: RawSentimentItem,
    ) -> Optional[SentimentEvent]:
        """入库一条事件. 已存在 (content_hash 冲突) 返回 None."""
        # 二次查重 (适配器层去重 + DB 唯一索引双保险)
        ch = item.content_hash
        res = await db.execute(select(SentimentEvent).where(SentimentEvent.content_hash == ch))
        if res.scalar_one_or_none():
            return None

        # 找 source_id
        source_id: Optional[int] = None
        if item.source_code:
            res = await db.execute(
                select(SentimentSource).where(SentimentSource.code == item.source_code)
            )
            src = res.scalar_one_or_none()
            if src:
                source_id = src.id

        ev = SentimentEvent(
            project_id=item.project_id,
            source_id=source_id,
            source_code=item.source_code,
            event_kind=item.event_kind,
            severity=item.severity,
            title=item.title,
            url=item.url,
            publisher=item.publisher,
            publish_date=item.publish_date,
            content_text=item.content_text,
            content_hash=ch,
            matched_alias=item.matched_alias,
            raw_payload=item.raw_payload,
        )
        db.add(ev)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return None
        return ev

    # ---- 全量扫描 -------------------------------------------------------

    async def run_daily_scan(self) -> dict:
        """调度入口: 扫描所有 active 项目. 返回汇总 dict.

        使用独立的 AsyncSessionLocal() — 不能复用 request-scoped session.
        """
        summary = {"projects_scanned": 0, "events_added": 0, "errors": []}
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Project).where(Project.status == "active"))
            projects = res.scalars().all()
            # 收集所有项目的 subjects (一次查完)
            for p in projects:
                try:
                    sub_res = await db.execute(
                        select(SentimentSubject).where(
                            SentimentSubject.project_id == p.id,
                            SentimentSubject.is_active == True,  # noqa: E712
                        )
                    )
                    subjects = sub_res.scalars().all()
                    # 若没有 subject, 用公司名 + 股票简称 + 实控人 + 股票代码合成一个
                    if not subjects:
                        subjects = self._synthesize_subjects(p)

                    added, _ = await self.scrape_project(db, p, subjects)
                    summary["projects_scanned"] += 1
                    summary["events_added"] += added
                except Exception as exc:
                    logger.exception("扫描项目 %s 失败: %s", p.id, exc)
                    summary["errors"].append({"project_id": p.id, "error": str(exc)})
        return summary

    def _synthesize_subjects(self, project: Project) -> list[SentimentSubject]:
        """项目未显式配 SentimentSubject 时, 从 Project 自身字段合成一组.

        返回的列表是 in-memory 对象, 不会写回 DB. 仅用于本轮扫描.
        """
        names: list[tuple[str, str]] = []  # (alias_type, alias_value)
        if project.company_name:
            names.append(("company", project.company_name))
        if project.stock_short_name and project.stock_short_name != project.company_name:
            names.append(("brand", project.stock_short_name))
        if project.stock_code:
            names.append(("code", project.stock_code))
        if project.actual_controller:
            names.append(("person", project.actual_controller))
        if project.legal_representative:
            names.append(("person", project.legal_representative))
        if project.keywords_extra:
            for kw in project.keywords_extra.splitlines():
                kw = kw.strip()
                if kw:
                    names.append(("extra", kw))

        out: list[SentimentSubject] = []
        for i, (typ, val) in enumerate(names):
            out.append(
                SentimentSubject(
                    id=0,  # 内存对象, 不入库
                    project_id=project.id,
                    alias_type=typ,
                    alias_value=val,
                    match_mode="contains",
                    is_primary=(i == 0),
                    weight=10,
                    is_active=True,
                )
            )
        return out
