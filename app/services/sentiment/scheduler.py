"""APScheduler 集成 — 每日扫描 + 启停钩子.

启动: 在 app/main.py 的 lifespan startup 调 start_scheduler()
停止: 在 lifespan shutdown 调 stop_scheduler()
幂等: 多次 start 不会重复添加 job; 多次 stop 不抛

调度任务: daily_scan_job
- 触发: cron 表达式从 settings.SENTIMENT_SCAN_CRON 读
- 默认: "0 30 8 * * mon-sat" (周一至周六 8:30)
- 防重入: max_instances=1, coalesce=True, misfire_grace_time=3600
- 任务内新建 AsyncSessionLocal(), 不复用 request-scoped session
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


_scheduler: Optional[AsyncIOScheduler] = None


JOB_ID_DAILY_SCAN = "sentiment_daily_scan"


def get_scheduler() -> Optional[AsyncIOScheduler]:
    """返回当前调度器实例 (测试用)."""
    return _scheduler


async def start_scheduler() -> None:
    """启动调度器 (幂等)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.info("Scheduler 已在运行, 跳过启动")
        return

    _scheduler = AsyncIOScheduler(timezone=settings.SENTIMENT_SCAN_TIMEZONE)

    # 解析 cron 表达式
    try:
        trigger = _parse_cron(settings.SENTIMENT_SCAN_CRON)
    except Exception as exc:
        logger.error("SENTIMENT_SCAN_CRON 解析失败 (%s), 使用默认 '30 8 * * 1-6'", exc)
        trigger = CronTrigger.from_crontab(
            "30 8 * * 1-6", timezone=settings.SENTIMENT_SCAN_TIMEZONE
        )

    _scheduler.add_job(
        daily_scan_job,
        trigger=trigger,
        id=JOB_ID_DAILY_SCAN,
        name="舆情每日扫描",
        replace_existing=True,
        max_instances=1,  # 防重入
        coalesce=True,  # 多次错过合并
        misfire_grace_time=3600,
    )

    _scheduler.start()
    logger.info(
        "Scheduler 启动: cron='%s' tz='%s'",
        settings.SENTIMENT_SCAN_CRON,
        settings.SENTIMENT_SCAN_TIMEZONE,
    )

    # 首次启动时注册默认信源 (幂等)
    try:
        from app.services.sentiment.scraper_service import SentimentScraperService

        async with AsyncSessionLocal() as db:
            svc = SentimentScraperService()
            added = await svc.bootstrap_default_sources(db)
            if added:
                logger.info("Scheduler: bootstrap 新增 %d 个信源", added)
    except Exception as exc:
        logger.warning("Scheduler: bootstrap 信源失败: %s", exc)


async def stop_scheduler() -> None:
    """停止调度器 (幂等)."""
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler 停止")
    _scheduler = None


async def daily_scan_job() -> None:
    """调度器任务入口: 全项目扫描."""
    from app.services.sentiment.scraper_service import SentimentScraperService

    logger.info("daily_scan_job: 开始")
    try:
        svc = SentimentScraperService()
        summary = await svc.run_daily_scan()
        logger.info("daily_scan_job: 完成 %s", summary)
    except Exception as exc:
        logger.exception("daily_scan_job: 异常: %s", exc)
        # 写一条红色通知
        try:
            async with AsyncSessionLocal() as db:
                from app.services.sentiment.notifier import create_notification

                await create_notification(
                    db,
                    notification_type="scan_failed",
                    title="舆情每日扫描任务异常",
                    body=str(exc)[:500],
                )
                await db.commit()
        except Exception:
            logger.exception("daily_scan_job: 写失败通知也失败")


# ---- 手动触发 (供 API 调用) --------------------------------------------


async def scan_now(project_id: Optional[int] = None) -> dict:
    """立即触发扫描. project_id 为 None 时扫全部."""
    from app.services.sentiment.scraper_service import SentimentScraperService
    from sqlalchemy import select
    from app.models.db_models import Project, SentimentSubject

    svc = SentimentScraperService()
    if project_id is not None:
        async with AsyncSessionLocal() as db:
            proj = await db.get(Project, project_id)
            if not proj:
                return {"error": f"project_id={project_id} 不存在"}
            sub_res = await db.execute(
                select(SentimentSubject).where(SentimentSubject.project_id == project_id)
            )
            subjects = list(sub_res.scalars().all()) or svc._synthesize_subjects(proj)
            added, status = await svc.scrape_project(db, proj, subjects)
            return {"project_id": project_id, "events_added": added, "source_status": status}
    else:
        return await svc.run_daily_scan()


# ---- 内部工具 ----------------------------------------------------------


def _parse_cron(expr: str) -> CronTrigger:
    """解析 cron 字符串为 CronTrigger. 失败抛 ValueError."""
    return CronTrigger.from_crontab(expr, timezone=settings.SENTIMENT_SCAN_TIMEZONE)
