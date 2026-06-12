"""免费 RSS 信源适配器 — 解析 RSS/Atom 源.

依赖 feedparser (pyproject.toml 已有). 实际部署时审计师需要配置 RSS URL 列表.

默认行为: 试解析一条示例 RSS (财联社 / 雪球公开频道), 失败时返回空列表 (优雅降级).
生产部署: 在 SentimentSource 表中注册带 base_url 的 RSS 信源.
"""
from __future__ import annotations

import logging
from typing import Optional

import feedparser

from app.models.db_models import Project, SentimentSubject
from app.services.sentiment.dedup import RawSentimentItem
from app.services.sentiment.http_client import SentimentHttpClient
from app.services.sentiment.sources.base import BaseSentimentSourceAdapter

logger = logging.getLogger(__name__)


class RssAdapter(BaseSentimentSourceAdapter):
    source_code = "rss"
    display_name = "RSS 订阅"

    DEFAULT_FEEDS: list[str] = [
        # 公开财经 RSS, 真实部署时可让审计师在 SentimentSource 表里覆盖
        "https://rsshub.app/caixin/latest",
        "https://rsshub.app/cls/telegraph",
    ]

    def __init__(
        self,
        http: SentimentHttpClient,
        api_key: Optional[str] = None,
        feeds: Optional[list[str]] = None,
    ) -> None:
        super().__init__(http, api_key)
        self.feeds = feeds or self.DEFAULT_FEEDS

    async def fetch(
        self,
        project: Project,
        subjects: list[SentimentSubject],
        *,
        date_from: str,
        date_to: str,
    ) -> list[RawSentimentItem]:
        queries = self._subjects_to_queries(subjects)
        if not queries:
            return []

        out: list[RawSentimentItem] = []
        for feed_url in self.feeds:
            try:
                # feedparser 同步, 直接在事件循环里跑 (轻量, 不会卡)
                parsed = feedparser.parse(feed_url)
            except Exception as exc:  # feedparser 极少抛, 但兜住
                logger.warning("RSS 解析失败 %s: %s", feed_url, exc)
                continue

            for entry in parsed.entries[:50]:  # 每个源限前 50 条
                title = self.clean_text(entry.get("title", ""))
                if not title:
                    continue
                # 命中任何一个搜索别名
                matched = next(
                    (q for q in queries if q in title or q in self.clean_text(entry.get("summary", ""))),
                    None,
                )
                if not matched:
                    continue
                url = entry.get("link")
                publish = self.norm_date(entry.get("published") or entry.get("updated"))
                summary = self.clean_text(entry.get("summary", ""))
                out.append(
                    RawSentimentItem(
                        project_id=project.id,
                        source_code=self.source_code,
                        event_kind="news",
                        severity="info",
                        title=title,
                        url=url,
                        publisher=parsed.feed.get("title", feed_url) if hasattr(parsed, "feed") else feed_url,
                        publish_date=publish,
                        content_text=summary,
                        matched_alias=matched,
                    )
                )
        logger.info("RssAdapter: project=%s 命中 %d 条", project.id, len(out))
        return out
