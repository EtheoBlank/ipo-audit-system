"""舆情模块共用的 HTTP 客户端.

照搬 app.services.regulation_scraper._HttpClient 模式:
    - httpx.AsyncClient
    - 简单线性退避重试
    - UA 来自 settings
    - 超时 / 重试次数可由 settings 覆盖

这个客户端与 regulation_scraper 的不同之处:
    - 超时 / 重试从 SENTIMENT_FETCH_TIMEOUT / SENTIMENT_FETCH_RETRY 读
    - 不强制 Accept-Language (信源杂, 默认即可)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class SentimentHttpClient:
    """舆情抓取共用的 httpx 客户端.

    用法::

        async with SentimentHttpClient() as http:
            r = await http.get("https://example.com/feed.rss")
            r.raise_for_status()
            text = r.text
    """

    def __init__(
        self,
        timeout: Optional[int] = None,
        retry: Optional[int] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        self.timeout = timeout or settings.SENTIMENT_FETCH_TIMEOUT
        self.retry = retry if retry is not None else settings.SENTIMENT_FETCH_RETRY
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "SentimentHttpClient":
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET with linear-backoff retry on transient failure."""
        assert self._client is not None, "SentimentHttpClient must be used as async context manager"
        last_exc: Optional[Exception] = None
        for attempt in range(self.retry + 1):
            try:
                return await self._client.get(url, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < self.retry:
                    wait = 0.5 * (attempt + 1)
                    logger.warning(
                        "GET %s failed (attempt %d/%d): %s; retry in %.1fs",
                        url,
                        attempt + 1,
                        self.retry + 1,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
        # 最后一次仍失败 — 抛最后一次的异常
        assert last_exc is not None
        raise last_exc

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """POST with the same retry semantics as get()."""
        assert self._client is not None, "SentimentHttpClient must be used as async context manager"
        last_exc: Optional[Exception] = None
        for attempt in range(self.retry + 1):
            try:
                return await self._client.post(url, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < self.retry:
                    wait = 0.5 * (attempt + 1)
                    logger.warning(
                        "POST %s failed (attempt %d/%d): %s; retry in %.1fs",
                        url,
                        attempt + 1,
                        self.retry + 1,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
        assert last_exc is not None
        raise last_exc
