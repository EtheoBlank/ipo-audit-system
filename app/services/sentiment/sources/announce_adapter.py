"""巨潮资讯网公告信源 — cninfo.com.cn.

cninfo 提供公开的"上市公司公告"搜索 API 与 RSS 频道.
本适配器采用"公司简称搜索"方式 — 用户在 SentimentSubject 里配 alias_value 后,
按 stock_code 优先 / 简称次之 进行公告搜索.

实际生产: 该站点接口与反爬经常变; 适配器做的是"最简实现 + 优雅降级",
不命中时静默返回空列表, 不抛错.
"""
from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import urlencode

from app.models.db_models import Project, SentimentSubject
from app.services.sentiment.dedup import RawSentimentItem
from app.services.sentiment.http_client import SentimentHttpClient
from app.services.sentiment.sources.base import BaseSentimentSourceAdapter

logger = logging.getLogger(__name__)


class CninfoAnnounceAdapter(BaseSentimentSourceAdapter):
    source_code = "cninfo_announce"
    display_name = "巨潮公告"

    SEARCH_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"

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
        # 优先用 stock_code 查 (最准)
        if not project.stock_code:
            logger.debug("CninfoAnnounceAdapter: project=%s 无 stock_code, 跳过", project.id)
            return []

        queries = self._subjects_to_queries(subjects)
        if not queries:
            return []

        # 构造 POST body
        body = {
            "stock": project.stock_code,
            "tabName": "fulltext",
            "pageSize": 30,
            "pageNum": 1,
            "column": "sse" if project.exchange and "上" in project.exchange else "szse",
            "category": "category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh",
            "seDate": f"{date_from}~{date_to}",
            "searchkey": "",
            "secid": "",
            "sortName": "time",
            "sortType": "desc",
            "isHLtitle": "true",
        }

        try:
            r = await self.http.post(
                self.SEARCH_URL,
                params=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                logger.warning("CninfoAnnounceAdapter: HTTP %s", r.status_code)
                return []
            data = r.json()
        except Exception as exc:
            logger.warning("CninfoAnnounceAdapter: 请求失败 %s", exc)
            return []

        announcements = (data or {}).get("announcements") or []
        out: list[RawSentimentItem] = []
        for ann in announcements:
            title = self.clean_text(ann.get("announcementTitle", ""))
            # 去掉标题中的 <em> 标签 (cninfo 用它高亮)
            import re
            title = re.sub(r"</?em>", "", title)
            if not title:
                continue
            # 命中别名
            matched = next((q for q in queries if q in title), None)
            if not matched:
                continue
            ann_id = ann.get("announcementId", "")
            sec_code = ann.get("secCode", project.stock_code or "")
            url = f"http://static.cninfo.com.cn/finalpage/{ann.get('adjunctUrl', '')}" if ann.get("adjunctUrl") else None
            publish = self.norm_date(ann.get("announcementTime"))
            out.append(
                RawSentimentItem(
                    project_id=project.id,
                    source_code=self.source_code,
                    event_kind="announce",
                    severity="notice",
                    title=title,
                    url=url,
                    publisher="巨潮资讯网",
                    publish_date=publish,
                    content_text=title,  # 公告正文要二次抓, 暂用标题
                    matched_alias=matched,
                )
            )
        logger.info("CninfoAnnounceAdapter: project=%s 命中 %d 条公告", project.id, len(out))
        return out
