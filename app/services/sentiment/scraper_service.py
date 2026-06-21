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
from app.services.notification import NotificationService
from app.models.db.notification import (
    NOTIF_MODULE_SENTIMENT,
    NOTIF_SEVERITY_WARN,
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


# 信源注册表 — code → (provider_type, display_name, api_key_ref, adapter_cls)
# is_paid 由 api_key_ref 是否非空派生, 避免双源真相.
# adapter_cls 直接存类本身 (类本就 callable), 替代 lambda: AdapterClass 多余包装.
_PROVIDER_REGISTRY: dict[str, dict] = {
    "rss": {
        "provider_type": "free_rss",
        "display_name": "RSS 订阅",
        "api_key_ref": None,
        "adapter_cls": RssAdapter,
    },
    "cninfo_announce": {
        "provider_type": "free_scrape",
        "display_name": "巨潮公告",
        "api_key_ref": None,
        "adapter_cls": CninfoAnnounceAdapter,
    },
    "regulator": {
        "provider_type": "free_scrape",
        "display_name": "监管/交易所披露",
        "api_key_ref": None,
        "adapter_cls": RegulatorAdapter,
    },
    "tavily": {
        "provider_type": "paid_api",
        "display_name": "Tavily 搜索",
        "api_key_ref": "TAVILY_API_KEY",
        "adapter_cls": TavilyAdapter,
    },
    "bocha": {
        "provider_type": "paid_api",
        "display_name": "博查搜索",
        "api_key_ref": "BOCHA_API_KEY",
        "adapter_cls": BochaAdapter,
    },
    "serpapi": {
        "provider_type": "paid_api",
        "display_name": "SerpAPI",
        "api_key_ref": "SERPAPI_API_KEY",
        "adapter_cls": SerpAPIAdapter,
    },
    "manual": {
        "provider_type": "manual",
        "display_name": "手工录入",
        "api_key_ref": None,
        "adapter_cls": ManualAdapter,
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
                    is_paid=meta["api_key_ref"] is not None,
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
            d = _utcnow() - timedelta(days=7)
            date_from = d.strftime("%Y-%m-%d")

        # 收集要跑的信源
        if source_codes is None:
            source_codes = list(_PROVIDER_REGISTRY.keys())

        # 信源状态收集
        source_status: dict[str, str] = {}
        # P0-4 (2026-06-19): 收集被跳过的付费信源 (无 API key), run_daily_scan 末尾汇总时
        # 推红点 (NotificationService.push), 提醒管理员配置. 仅付费源, 免费源不通知.
        skipped_paid_codes: list[str] = []

        # 抓取 + 入库
        async with SentimentHttpClient() as http:
            tasks: list[asyncio.Task] = []
            # 记录任务与 source code 的对应关系, 避免 zip 错位 (跳过 unknown/skipped 源)
            task_code_pairs: list[tuple[str, asyncio.Task]] = []
            for code in source_codes:
                meta = _PROVIDER_REGISTRY.get(code)
                if not meta:
                    source_status[code] = "unknown_source"
                    continue
                # 付费源无 key → skip
                api_key_ref = meta["api_key_ref"]
                if api_key_ref:
                    key = getattr(settings, api_key_ref, "")
                    if not key:
                        source_status[code] = "skipped"
                        skipped_paid_codes.append(code)
                        logger.debug("信源 %s 无 API key, 跳过", code)
                        continue
                    adapter_cls = meta["adapter_cls"]
                    adapter = adapter_cls(http, api_key=key)
                else:
                    adapter_cls = meta["adapter_cls"]
                    adapter = adapter_cls(http)
                task = asyncio.create_task(
                    self._run_one_source(adapter, project, subjects, date_from, date_to)
                )
                task_code_pairs.append((code, task))

            # 限频: 单项目最多 max_events 条入库
            results: list[list[RawSentimentItem]] = []
            if task_code_pairs:
                # 单个信源超时用 gather, 单个失败不阻断
                codes = [c for c, _ in task_code_pairs]
                tasks = [t for _, t in task_code_pairs]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                for code, result in zip(codes, gathered):
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
        # P0 (round 32) 性能: 批量入库替代 N+1
        # 之前每条都跑 _persist_event (3 次 DB round-trip): content_hash 去重 +
        # source_code 查 source_id + INSERT. 200 条事件 = 600 次 round-trip.
        # 现在: 1) 预拉所有 SentimentSource → dict
        #      2) content_hash 一次性 WHERE IN (...) 查重
        #      3) 剩下的 add_all + flush 一次 INSERT
        # 200 条事件从 600 round-trips → 3 round-trips.
        added = 0
        if all_items:
            try:
                added = await self._bulk_persist_events(db, all_items)
            except IntegrityError:
                await db.rollback()
                logger.warning("scrape_project: 批量入库 IntegrityError, 项目=%s", project.id)
            except Exception as exc:
                logger.warning("scrape_project: 批量入库失败 项目=%s: %s", project.id, exc)
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

        # P0-4 (2026-06-19): 付费信源无 key → 推红点 (管理员可感知 "信源配置缺失")
        if skipped_paid_codes:
            await self._notify_missing_paid_sources(db, project, skipped_paid_codes)

        await db.commit()
        logger.info(
            "scrape_project: project=%s 命中 %d 条 (新增 %d) 各源状态=%s",
            project.id,
            len(all_items),
            added,
            source_status,
        )
        return added, source_status

    async def _first_time_missing(self, db: AsyncSession, source_code: str) -> bool:
        """返回 True 当 source_code 在最近 30 天没出现过 source_missing_key 通知.
        P0-4 (2026-06-19): 30 天内已通知过 → 不重复推, 避免噪声刷屏.
        """
        from sqlalchemy import and_, func, select

        from app.models.db.notification import Notification

        cutoff = _utcnow() - timedelta(days=30)
        stmt = (
            select(func.count(Notification.id))
            .where(
                and_(
                    Notification.module == NOTIF_MODULE_SENTIMENT,
                    Notification.type == "source_missing_key",
                    Notification.resource_type == "sentiment_source",
                    Notification.resource_id == source_code,
                    Notification.created_at >= cutoff,
                )
            )
        )
        n = int((await db.execute(stmt)).scalar_one() or 0)
        return n == 0

    async def _notify_missing_paid_sources(
        self,
        db: AsyncSession,
        project: Project,
        skipped_codes: list[str],
    ) -> None:
        """对每个跳过的付费信源, 30 天内首次发现就推一条 source_missing_key 通知."""
        for code in skipped_codes:
            meta = _PROVIDER_REGISTRY.get(code) or {}
            display = meta.get("display_name", code)
            api_key_ref = meta.get("api_key_ref") or ""
            try:
                first = await self._first_time_missing(db, code)
            except Exception as exc:
                logger.warning("查 source_missing_key 历史失败 %s: %s", code, exc)
                first = True  # 兜底: 失败就当首次, 至少推一条
            if not first:
                continue
            body = (
                f"项目 {project.company_name}: 付费信源 {display} ({code}) 未配置 API key. "
                f"请在环境变量 {api_key_ref} 设置密钥后重启扫描. "
                f"重复出现将不再提示 (30 天内)."
            )
            try:
                await NotificationService.push(
                    db,
                    module=NOTIF_MODULE_SENTIMENT,
                    type="source_missing_key",
                    title=f"舆情信源缺密钥: {display}",
                    body=body,
                    severity=NOTIF_SEVERITY_WARN,
                    resource_type="sentiment_source",
                    resource_id=code,
                    project_id=project.id,
                    commit=False,  # 留给 scrape_project 末尾统一 commit
                )
            except Exception as exc:
                logger.warning("推 source_missing_key 通知失败 %s: %s", code, exc)

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

    async def _bulk_persist_events(
        self,
        db: AsyncSession,
        items: list,
    ) -> int:
        """P0 (round 32): 批量入库 SentimentEvent — 替代 N+1.

        流程:
          1) 一次 SELECT 拉所有 SentimentSource → dict[code → id]
          2) 一次 SELECT WHERE content_hash IN (hashes) → 已有 hash 集合
          3) 构造 SentimentEvent 列表, 过滤已存在的, db.add_all + flush
          4) 兜底: flush 阶段若仍撞 IntegrityError (并发), 全 rollback + 退回逐条

        Returns: 实际新增条数.
        """
        # 1) 预拉 source 字典 (1 round-trip)
        src_res = await db.execute(select(SentimentSource))
        src_map: dict[str, int] = {s.code: s.id for s in src_res.scalars().all()}

        # 2) 一次性查重 (1 round-trip)
        hashes = [it.content_hash for it in items if it.content_hash]
        existing_hashes: set[str] = set()
        if hashes:
            # 避免 IN 子句过长, 分批
            CHUNK = 500
            for i in range(0, len(hashes), CHUNK):
                batch = hashes[i : i + CHUNK]
                r = await db.execute(
                    select(SentimentEvent.content_hash).where(
                        SentimentEvent.content_hash.in_(batch)
                    )
                )
                existing_hashes.update(r.scalars().all())

        # 3) 构造新事件
        new_events: list[SentimentEvent] = []
        for it in items:
            if it.content_hash and it.content_hash in existing_hashes:
                continue
            source_id = src_map.get(it.source_code or "") if it.source_code else None
            new_events.append(
                SentimentEvent(
                    project_id=it.project_id,
                    source_id=source_id,
                    source_code=it.source_code,
                    event_kind=it.event_kind,
                    severity=it.severity,
                    title=it.title,
                    url=it.url,
                    publisher=it.publisher,
                    publish_date=it.publish_date,
                    content_text=it.content_text,
                    content_hash=it.content_hash,
                    matched_alias=it.matched_alias,
                    raw_payload=it.raw_payload,
                )
            )
        if not new_events:
            return 0

        # 4) 批量 INSERT (1 round-trip)
        db.add_all(new_events)
        try:
            await db.flush()
        except IntegrityError:
            # 兜底: 并发场景下 hash 冲突无法在 SELECT 时发现
            # 全 rollback 后退回逐条 (保证数据不丢)
            await db.rollback()
            logger.warning(
                "_bulk_persist_events: flush 撞 IntegrityError, 退回逐条 fallback"
            )
            fallback_added = 0
            for it in items:
                try:
                    ev = await self._persist_event(db, it)
                    if ev is not None:
                        fallback_added += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("入库 fallback 失败 %s: %s", it.title, exc)
                    await db.rollback()
            return fallback_added
        return len(new_events)

    # ---- 全量扫描 -------------------------------------------------------

    async def run_daily_scan(self) -> dict:
        """调度入口: 扫描所有 active 项目. 返回汇总 dict.

        使用独立的 AsyncSessionLocal() — 不能复用 request-scoped session.

        P0 (round 32): 项目循环内用独立 session, 单项目失败不影响其他项目,
        同时避免 1 个慢项目拖长事务锁住所有数据.
        """
        summary = {"projects_scanned": 0, "events_added": 0, "errors": []}
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Project).where(Project.status == "active"))
            projects = res.scalars().all()
            project_ids = [p.id for p in projects]
        for project_id in project_ids:
            try:
                async with AsyncSessionLocal() as session:
                    p = (
                        await session.execute(
                            select(Project).where(Project.id == project_id)
                        )
                    ).scalar_one_or_none()
                    if p is None:
                        continue
                    sub_res = await session.execute(
                        select(SentimentSubject).where(
                            SentimentSubject.project_id == p.id,
                            SentimentSubject.is_active == True,  # noqa: E712
                        )
                    )
                    subjects = sub_res.scalars().all()
                    if not subjects:
                        subjects = self._synthesize_subjects(p)
                    added, _ = await self.scrape_project(session, p, subjects)
                    summary["projects_scanned"] += 1
                    summary["events_added"] += added
            except Exception as exc:
                logger.exception("扫描项目 %s 失败: %s", project_id, exc)
                summary["errors"].append({"project_id": project_id, "error": str(exc)})
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
            # 防 None: ORM 上可空, 即使 if Truthy 仍建议加防护
            for kw in (project.keywords_extra or "").splitlines():
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
