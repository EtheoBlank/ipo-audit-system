"""Regulatory case scraping service for IPO Audit System.

P0 重构 (2026-06-17):
  - 原版 CSRC / SSE / SZSE 三个官方 URL 全部失效 (拼音路径乱码 / 参数拼错 / JSON 被当 HTML 解析)
  - 新版: 统一跳巨潮资讯 (cninfo) `POST /new/hisAnnouncement/query` 聚合搜索接口
  - 按 `column=sse|szse|bj` 参数分别取上交所 / 深交所 / 北交所 + CSRC (via bj)
  - SZSE 原 URL 当 fallback (修 parser: response.json() 而非 BS4)
  - 保留原方法签名 (scrape_csrc_inquiries / scrape_sse_inquiries / scrape_szse_inquiries /
    scrape_csrc_penalties / scrape_all / match_cases_by_*), 不破坏 API router 调用方

巨潮接口字段:
  - announcementId, announcementTitle, announcementTime(ms epoch),
    adjunctUrl, secCode, secName, plate
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _ms_epoch_to_date(ms: Any) -> str:
    """巨潮 announcementTime 是 ms epoch, 转换为 YYYY-MM-DD."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return ""


class RegulatoryCaseScraper:
    """Scrape regulatory cases via 巨潮资讯 (cninfo) unified aggregator."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": getattr(settings, "REGULATION_USER_AGENT", None) or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "http://www.cninfo.com.cn/new/disclosure/stock",
        }
        self.session = httpx.AsyncClient(
            timeout=timeout,
            headers=self.headers,
            follow_redirects=True,
        )

    async def close(self):
        """Close the HTTP session."""
        await self.session.aclose()

    # ---------------------------------------------------------------
    # 内部: 巨潮资讯统一搜索
    # ---------------------------------------------------------------

    async def _scrape_cninfo(
        self,
        column: str,
        searchkey: str = "问询函",
        page: int = 1,
        page_size: int = 30,
        source_label: str = "",
    ) -> List[Dict]:
        """POST 巨资讯 /new/hisAnnouncement/query.

        Args:
            column: 'sse' | 'szse' | 'bj' (北交所覆盖 CSRC 部分)
            searchkey: 搜索关键词, 默认"问询函"
            page: 页码
            page_size: 每页条数
            source_label: 写到 case['source'] 的人类可读名

        Returns:
            case dict 列表 (case_no/case_type/source/publish_date/title/content/pdf_url)
        """
        url = f"{settings.CNINFO_URL}{settings.CNINFO_QUERY_PATH}"
        body = {
            "pageNum": str(page),
            "pageSize": str(page_size),
            "column": column,
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": searchkey,
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        cases: List[Dict] = []
        try:
            resp = await self.session.post(url, data=body)
            resp.raise_for_status()
            payload = resp.json()
            announcements = payload.get("announcements") or []
            for ann in announcements:
                title = ann.get("announcementTitle") or ""
                # 巨潮标题含 HTML 标签 (如 <em>问询函</em>), 去掉
                import re as _re

                title = _re.sub(r"<[^>]+>", "", title).strip()
                adjunct = ann.get("adjunctUrl") or ""
                pdf_url = (
                    f"http://static.cninfo.com.cn/{adjunct}" if adjunct and not adjunct.startswith("http") else adjunct
                )
                cases.append(
                    {
                        "case_no": str(ann.get("announcementId") or ""),
                        "case_type": "问询函" if searchkey == "问询函" else "监管公告",
                        "source": source_label,
                        "publish_date": _ms_epoch_to_date(ann.get("announcementTime")),
                        "title": title,
                        "content": "",  # 巨潮只给标题, 详情需点 PDF, 此处留空
                        "pdf_url": pdf_url,
                        "sec_code": ann.get("secCode") or "",
                        "sec_name": ann.get("secName") or "",
                    }
                )
        except httpx.HTTPStatusError as e:
            logger.warning("巨潮 %s 返回 %s — 跳过: %s", column, e.response.status_code, url)
        except httpx.HTTPError as e:
            logger.warning("抓取巨潮 %s 失败: %s", column, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("巨潮 %s 解析失败: %s", column, e)
        return cases

    # ---------------------------------------------------------------
    # 内部: SZSE 原 URL fallback (修 parser)
    # ---------------------------------------------------------------

    async def _scrape_szse_native(self, page: int = 1) -> List[Dict]:
        """P0 修复: SZSE 原 URL 已返回 JSON, 旧版用 BS4 解析永远 0 条.

        SZSE `/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1839` 返回
        ``{"data": [[公司代码, 公司简称, ?, 标题, 日期, pdf_url], ...]}``
        位置索引按 CATALOGID 不同而不同, 这里用尝试性策略: 至少解析 ≥4 列的行.
        """
        url = (
            f"{settings.SZSE_URL}/api/report/ShowReport/data"
            f"?SHOWTYPE=JSON&CATALOGID=1839&CATEGORY=xxpl/tzzscx&page={page}"
            f"&random=0.{int(datetime.now(timezone.utc).timestamp() * 1000) % 10000}"
        )
        cases: List[Dict] = []
        try:
            resp = await self.session.get(url)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("data") or []
            for row in rows:
                if not isinstance(row, list) or len(row) < 4:
                    continue
                # 列序可能是 [code, short, ?, title, date, pdf] 也可能 [code, short, date, title, pdf]
                # 启发式: 含'.'或'.PDF'的是 PDF URL, 含'-'且长度>=8的是日期
                pdf_url = ""
                date_str = ""
                title = ""
                sec_code = str(row[0]) if row else ""
                sec_name = str(row[1]) if len(row) > 1 else ""
                for cell in row[2:]:
                    s = str(cell).strip()
                    if not s:
                        continue
                    if s.lower().endswith((".pdf", ".htm", ".html")) and not pdf_url:
                        pdf_url = s if s.startswith("http") else f"http://disc.static.szse.cn{s}"
                    elif len(s) >= 8 and s[:4].isdigit() and ("-" in s or "/" in s) and not date_str:
                        date_str = s
                    elif not title:
                        title = s
                cases.append(
                    {
                        "case_no": sec_code,
                        "case_type": "问询函",
                        "source": "深交所 (原生)",
                        "publish_date": date_str,
                        "title": title,
                        "content": "",
                        "pdf_url": pdf_url,
                        "sec_code": sec_code,
                        "sec_name": sec_name,
                    }
                )
        except httpx.HTTPStatusError as e:
            logger.warning("SZSE 原生返回 %s — 跳过: %s", e.response.status_code, url)
        except httpx.HTTPError as e:
            logger.warning("抓取 SZSE 原生失败: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.warning("SZSE 原生 JSON 解析失败: %s", e)
        return cases

    # ---------------------------------------------------------------
    # 公共方法 (签名不变 — 保留 API 兼容)
    # ---------------------------------------------------------------

    async def scrape_csrc_inquiries(self, page: int = 1) -> List[Dict]:
        """CSRC 问询函 — 走巨潮 column=bj + 关键词'问询函'."""
        return await self._scrape_cninfo(
            column="bj",
            searchkey="问询函",
            page=page,
            source_label="证监会 (via 巨潮)",
        )

    async def scrape_sse_inquiries(self, page: int = 1) -> List[Dict]:
        """上交所问询函 — 走巨潮 column=sse."""
        return await self._scrape_cninfo(
            column="sse",
            searchkey="问询函",
            page=page,
            source_label="上交所 (via 巨潮)",
        )

    async def scrape_szse_inquiries(self, page: int = 1) -> List[Dict]:
        """深交所问询函 — 优先巨潮 column=szse, fallback 原生 URL."""
        cases = await self._scrape_cninfo(
            column="szse",
            searchkey="问询函",
            page=page,
            source_label="深交所 (via 巨潮)",
        )
        if not cases:
            logger.info("巨潮未返回 SZSE 问询函, fallback 原生 URL")
            cases = await self._scrape_szse_native(page)
        return cases

    async def scrape_csrc_penalties(self, page: int = 1) -> List[Dict]:
        """证监会处罚决定 — 走巨潮 column=bj + 关键词'行政处罚'."""
        return await self._scrape_cninfo(
            column="bj",
            searchkey="行政处罚",
            page=page,
            source_label="证监会 (via 巨潮)",
        )

    async def scrape_all(self) -> List[Dict]:
        """Scrape all regulatory cases from all sources."""
        all_cases: List[Dict] = []
        # 单源失败不连坐: 每个调用独立 try
        for fn in (
            self.scrape_csrc_inquiries,
            self.scrape_sse_inquiries,
            self.scrape_szse_inquiries,
            self.scrape_csrc_penalties,
        ):
            try:
                res = await fn()
            except Exception as e:  # noqa: BLE001
                logger.warning("%s 失败: %s", fn.__name__, e)
                res = []
            all_cases.extend(res)
        logger.info(
            "共抓取 %d 条监管案例 (CSRC问询=%d, SSE=%d, SZSE=%d, CSRC处罚=%d)",
            len(all_cases),
            sum(1 for c in all_cases if "证监会" in c.get("source", "") and c.get("case_type") == "问询函"),
            sum(1 for c in all_cases if "上交所" in c.get("source", "")),
            sum(1 for c in all_cases if "深交所" in c.get("source", "")),
            sum(1 for c in all_cases if c.get("case_type") == "处罚决定"),
        )
        return all_cases

    # ---------------------------------------------------------------
    # 匹配 (签名/行为不变)
    # ---------------------------------------------------------------

    def match_cases_by_industry(self, cases: List[Dict], industry: str) -> List[Dict]:
        if not industry:
            return cases
        return [
            case
            for case in cases
            if industry in case.get("title", "") or industry in case.get("content", "")
        ]

    def match_cases_by_keywords(self, cases: List[Dict], keywords: List[str]) -> List[Dict]:
        matched: List[Dict] = []
        for case in cases:
            content = f"{case.get('title', '')} {case.get('content', '')}"
            matched_keywords = [kw for kw in keywords if kw in content]
            if matched_keywords:
                case["matched_keywords"] = matched_keywords
                matched.append(case)
        return matched