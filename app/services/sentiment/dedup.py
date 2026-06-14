"""舆情事件去重 — SHA-256 content_hash.

照搬 app.services.regulation_scraper.RegulationItem.__post_init__ 模式 (compute_hash):

    RegulationItem.__post_init__ 用 source|title|document_no|publish_date 算 SHA-256.
    SentimentEvent.content_hash 用 source_code|title|url|publish_date 算 SHA-256.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional


def compute_content_hash(
    source_code: Optional[str],
    title: Optional[str],
    url: Optional[str],
    publish_date: Optional[str],
) -> str:
    """计算舆情事件的内容指纹 (SHA-256 hex, 64 字符).

    同一事件被不同抓取时间重复抓到时 hash 一致, 用作唯一索引去重.
    """
    parts = [
        (source_code or "").strip().lower(),
        (title or "").strip(),
        (url or "").strip(),
        (publish_date or "").strip(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class RawSentimentItem:
    """抓取层 (adapters) 返回的"原始事件" — 不入库前.

    与 ORM SentimentEvent 字段几乎一致, 但用 dataclass 轻便. 入库时算 hash.
    """

    project_id: int
    source_code: str
    source_id: Optional[int] = None
    event_kind: Optional[str] = None
    severity: str = "info"
    title: str = ""
    url: Optional[str] = None
    publisher: Optional[str] = None
    publish_date: Optional[str] = None  # YYYY-MM-DD
    content_text: str = ""
    matched_alias: Optional[str] = None
    raw_payload: Optional[str] = None

    @property
    def content_hash(self) -> str:
        return compute_content_hash(
            self.source_code,
            self.title,
            self.url,
            self.publish_date,
        )
