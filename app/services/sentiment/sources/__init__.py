"""信源适配器子包.

每个适配器实现 BaseSentimentSourceAdapter 接口 — 由 SentimentScraperService
统一调度 (asyncio.gather).

免费源 (默认启用):
    - rss:       feedparser 解析 RSS/Atom
    - announce:  巨潮 cninfo 公告
    - regulator: 监管/交易所披露页 (复用 regulation_scraper 思想, 但简化)

付费源 (用户配置 API Key 才启用):
    - tavily / bocha / serpapi

辅助:
    - manual:    审计师手工录入入口, 不抓取
"""

from __future__ import annotations

__all__: list[str] = []
