"""信源适配器抽象基类 — 照搬 app.services.regulation_scraper.BaseRegulationAdapter 风格."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable, Optional

from app.models.db_models import Project, SentimentSubject
from app.services.sentiment.dedup import RawSentimentItem
from app.services.sentiment.http_client import SentimentHttpClient

logger = logging.getLogger(__name__)


class BaseSentimentSourceAdapter(ABC):
    """舆情信源适配器抽象基类.

    子类必须实现:
        - source_code: str        # 唯一信源编码
        - requires_api_key: bool   # 是否需要 API Key
        - async fetch(...)         # 拉取并返回 RawSentimentItem 列表

    通用工具:
        - norm_date(text)         # 各种日期格式归一化到 YYYY-MM-DD
        - clean_text(text)        # 去除空白 / 控制字符
    """

    source_code: str = "base"
    display_name: str = "基础适配器"
    requires_api_key: bool = False

    def __init__(self, http: SentimentHttpClient, api_key: Optional[str] = None) -> None:
        self.http = http
        self.api_key = api_key

    @abstractmethod
    async def fetch(
        self,
        project: Project,
        subjects: list[SentimentSubject],
        *,
        date_from: str,
        date_to: str,
    ) -> list[RawSentimentItem]:
        """拉取窗口 [date_from, date_to] 内与 project/subjects 相关的事件.

        返回的 RawSentimentItem 由调用方入库 + 去重.
        """
        raise NotImplementedError

    # ---- 通用工具 --------------------------------------------------------

    @staticmethod
    def norm_date(value: Optional[str]) -> Optional[str]:
        """把各种日期字符串归一化到 YYYY-MM-DD. 失败返回 None.

        支持:
            - 2025-06-12 / 2025/06/12
            - 2025-06-12T10:30:00 / 2025-06-12T10:30:00+08:00
            - 2025年06月12日 / 2025年6月12日
            - "今天" / "today"
        """
        if not value:
            return None
        s = str(value).strip()
        if not s:
            return None
        # 已经是 ISO 格式
        m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        # 中文格式
        m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        # 相对日期
        if s in ("今天", "today", "Today"):
            return datetime.now().strftime("%Y-%m-%d")
        if s in ("昨天", "yesterday"):
            from datetime import timedelta

            return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return None

    @staticmethod
    def clean_text(value: Optional[str], max_len: int = 8000) -> str:
        """去除控制字符 + 折叠空白 + 截断."""
        if not value:
            return ""
        # 去除控制字符 (保留中文 / 标点)
        s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value))
        s = re.sub(r"\s+", " ", s).strip()
        return s[:max_len]

    def _subjects_to_queries(self, subjects: Iterable[SentimentSubject]) -> list[str]:
        """把 SentimentSubject 列表压成可搜索的关键词列表."""
        seen: set[str] = set()
        out: list[str] = []
        for s in subjects:
            if not s.is_active:
                continue
            v = (s.alias_value or "").strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out
