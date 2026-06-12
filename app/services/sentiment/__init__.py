"""舆情跟踪 (Sentiment Tracking) 服务层.

子包结构 (与现有 app/services/inventory/ 风格一致):

    sentiment/
        __init__.py                # 本文件
        http_client.py             # httpx + 重试封装 (照搬 regulation_scraper 模式)
        llm_client.py              # LlmClientFactory: DeepSeek 优先 / MiniMax 兜底
        dedup.py                   # content_hash SHA-256 计算
        notifier.py                # SentimentNotification 写入
        scraper_service.py         # SentimentScraperService — gather + dedup
        scheduler.py               # APScheduler 集成
        sources/                   # 信源适配器
        briefing/                  # 每日简报
        quarterly/                 # 季度跟踪报告
"""
from __future__ import annotations

__all__: list[str] = []
