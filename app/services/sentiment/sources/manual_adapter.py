"""手工录入信源 — 不抓取, 仅作为"事件库"录入入口的逻辑标签."""

from __future__ import annotations

import logging
from typing import Optional

from app.models.db_models import Project, SentimentSubject
from app.services.sentiment.dedup import RawSentimentItem
from app.services.sentiment.http_client import SentimentHttpClient
from app.services.sentiment.sources.base import BaseSentimentSourceAdapter

logger = logging.getLogger(__name__)


class ManualAdapter(BaseSentimentSourceAdapter):
    """手工录入入口 — fetch 永远返回空列表, 事件由审计师在『事件库』页面手动录入.

    保留这个类是为了让 ScraperService 把它当"信源"统一处理, 在 last_run 时记录
    状态为 "skipped" 或 "n/a", 表明此信源不需要自动抓取.
    """

    source_code = "manual"
    display_name = "手工录入"
    requires_api_key = False

    def __init__(self, http: SentimentHttpClient, api_key: Optional[str] = None) -> None:
        super().__init__(http, api_key)

    async def fetch(
        self,
        project: Project,
        subjects: list[SentimentSubject],
        *,
        date_from: str,
        date_to: str,
    ) -> list[RawSentimentItem]:
        return []
