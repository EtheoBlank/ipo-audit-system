"""付费信源适配器 — Tavily / 博查 / SerpAPI.

设计原则:
- 用户填了 API Key 才启用; 没填时, scheduler 抓取前会跳过 (last_run_status=skipped)
- 每个适配器都实现同一种简单接口: 关键词搜索 → 返回 RawSentimentItem 列表
- 实际请求参数与各家略有不同, 但调用方 (scraper_service) 不感知

注意: 本项目不推荐任何特定服务, 也不对第三方服务的可用性 / 价格 / 数据质量
做任何承诺. 用户自行决定是否启用.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.models.db_models import (
    PaidSourceMissingKey,
    Project,
    SentimentSubject,
)
from app.services.sentiment.dedup import RawSentimentItem
from app.services.sentiment.http_client import SentimentHttpClient
from app.services.sentiment.sources.base import BaseSentimentSourceAdapter

logger = logging.getLogger(__name__)


# ============================================================
#  Tavily (https://tavily.com)
# ============================================================


class TavilyAdapter(BaseSentimentSourceAdapter):
    source_code = "tavily"
    display_name = "Tavily 搜索"
    requires_api_key = True
    API_URL = "https://api.tavily.com/search"

    async def fetch(
        self,
        project: Project,
        subjects: list[SentimentSubject],
        *,
        date_from: str,
        date_to: str,
    ) -> list[RawSentimentItem]:
        if not self.api_key:
            raise PaidSourceMissingKey("TAVILY_API_KEY 未配置")
        queries = self._subjects_to_queries(subjects)
        if not queries:
            return []

        out: list[RawSentimentItem] = []
        for q in queries[:5]:  # 限前 5 个别名
            payload = {
                "api_key": self.api_key,
                "query": f"{q} 公告 OR 处罚 OR 问询 OR 诉讼",
                "search_depth": "basic",
                "max_results": 10,
                "include_raw_content": False,
            }
            try:
                r = await self.http.post(self.API_URL, json=payload)
                if r.status_code != 200:
                    logger.warning("Tavily HTTP %s for %s", r.status_code, q)
                    continue
                data = r.json()
            except Exception as exc:
                logger.warning("Tavily 请求失败 %s: %s", q, exc)
                continue

            for item in (data.get("results") or []):
                title = self.clean_text(item.get("title", ""))
                url = item.get("url")
                content = self.clean_text(item.get("content", ""))
                if not title:
                    continue
                out.append(
                    RawSentimentItem(
                        project_id=project.id,
                        source_code=self.source_code,
                        event_kind="news",
                        severity="info",
                        title=title,
                        url=url,
                        publish_date=self.norm_date(item.get("published_date")),
                        content_text=content,
                        matched_alias=q,
                    )
                )
        return out


# ============================================================
#  博查 (Bocha) — 通用网络搜索
# ============================================================


class BochaAdapter(BaseSentimentSourceAdapter):
    source_code = "bocha"
    display_name = "博查搜索"
    requires_api_key = True
    API_URL = "https://api.bochaai.com/v1/web-search"

    async def fetch(
        self,
        project: Project,
        subjects: list[SentimentSubject],
        *,
        date_from: str,
        date_to: str,
    ) -> list[RawSentimentItem]:
        if not self.api_key:
            raise PaidSourceMissingKey("BOCHA_API_KEY 未配置")
        queries = self._subjects_to_queries(subjects)
        if not queries:
            return []

        out: list[RawSentimentItem] = []
        for q in queries[:5]:
            payload = {
                "query": f"{q} 公告 OR 处罚 OR 问询",
                "summary": True,
                "count": 10,
                "freshness": "oneWeek",
            }
            try:
                r = await self.http.post(
                    self.API_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if r.status_code != 200:
                    logger.warning("Bocha HTTP %s for %s", r.status_code, q)
                    continue
                data = r.json()
            except Exception as exc:
                logger.warning("Bocha 请求失败 %s: %s", q, exc)
                continue

            for item in (data.get("data", {}).get("webPages", {}).get("value") or []):
                title = self.clean_text(item.get("name", ""))
                url = item.get("url")
                content = self.clean_text(item.get("snippet", ""))
                if not title:
                    continue
                out.append(
                    RawSentimentItem(
                        project_id=project.id,
                        source_code=self.source_code,
                        event_kind="news",
                        severity="info",
                        title=title,
                        url=url,
                        publish_date=self.norm_date(item.get("datePublished")),
                        content_text=content,
                        matched_alias=q,
                    )
                )
        return out


# ============================================================
#  SerpAPI (https://serpapi.com)
# ============================================================


class SerpAPIAdapter(BaseSentimentSourceAdapter):
    source_code = "serpapi"
    display_name = "SerpAPI"
    requires_api_key = True
    API_URL = "https://serpapi.com/search"

    async def fetch(
        self,
        project: Project,
        subjects: list[SentimentSubject],
        *,
        date_from: str,
        date_to: str,
    ) -> list[RawSentimentItem]:
        if not self.api_key:
            raise PaidSourceMissingKey("SERPAPI_API_KEY 未配置")
        queries = self._subjects_to_queries(subjects)
        if not queries:
            return []

        out: list[RawSentimentItem] = []
        for q in queries[:5]:
            params = {
                "q": f"{q} 公告 OR 处罚 OR 问询",
                "api_key": self.api_key,
                "engine": "google",
                "num": 10,
                "tbs": "qdr:w",  # past week
            }
            try:
                r = await self.http.get(self.API_URL, params=params)
                if r.status_code != 200:
                    logger.warning("SerpAPI HTTP %s for %s", r.status_code, q)
                    continue
                data = r.json()
            except Exception as exc:
                logger.warning("SerpAPI 请求失败 %s: %s", q, exc)
                continue

            for item in (data.get("organic_results") or []):
                title = self.clean_text(item.get("title", ""))
                url = item.get("link")
                content = self.clean_text(item.get("snippet", ""))
                if not title:
                    continue
                out.append(
                    RawSentimentItem(
                        project_id=project.id,
                        source_code=self.source_code,
                        event_kind="news",
                        severity="info",
                        title=title,
                        url=url,
                        publish_date=self.norm_date(item.get("date")),
                        content_text=content,
                        matched_alias=q,
                    )
                )
        return out
