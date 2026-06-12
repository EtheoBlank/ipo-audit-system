"""监管/交易所披露页适配器 — 复用 regulation_scraper 思想.

实现原则:
- 不重复造轮子, 直接复用 app.services.regulation_scraper 已有的 CSRC/SSE/SZSE Adapter
- 增量逻辑: 解析 RegulationItem, 按 SentimentSubject 关键词过滤后转 RawSentimentItem
- 失败时静默降级 (返回空列表), 不影响主流程
"""
from __future__ import annotations

import logging
from typing import Optional

from app.models.db_models import Project, SentimentSubject
from app.services.sentiment.dedup import RawSentimentItem
from app.services.sentiment.http_client import SentimentHttpClient
from app.services.sentiment.sources.base import BaseSentimentSourceAdapter

logger = logging.getLogger(__name__)


class RegulatorAdapter(BaseSentimentSourceAdapter):
    source_code = "regulator"
    display_name = "监管/交易所披露"

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
        queries = self._subjects_to_queries(subjects)
        if not queries:
            return []

        # 复用 regulation_scraper 抓到的内容
        from app.services.regulation_scraper import RegulationScraperService

        out: list[RawSentimentItem] = []
        try:
            async with RegulationScraperService() as scraper:
                items = await scraper.scrape(max_pages=1)
        except Exception as exc:
            logger.warning("RegulatorAdapter: 复用 regulation_scraper 失败 %s", exc)
            return []

        for item in items:
            title = self.clean_text(item.title)
            content = self.clean_text(getattr(item, "summary", "") or "")
            if not title:
                continue
            matched = next(
                (q for q in queries if q in title or q in content),
                None,
            )
            if not matched:
                continue
            # 严重度: 处罚/问询 → critical/warn
            sev = "critical" if "处罚" in title or "罚" in title else ("warn" if "问询" in title or "关注" in title else "notice")
            out.append(
                RawSentimentItem(
                    project_id=project.id,
                    source_code=self.source_code,
                    event_kind="regulator",
                    severity=sev,
                    title=title,
                    url=item.source_url,
                    publisher=item.issuing_authority or item.source,
                    publish_date=self.norm_date(item.publish_date),
                    content_text=content,
                    matched_alias=matched,
                )
            )
        logger.info("RegulatorAdapter: project=%s 命中 %d 条", project.id, len(out))
        return out
