"""法律法规抓取服务 (Regulation Scraper).

按机构封装多个 Adapter，统一返回 ``RegulationItem`` 字典 → 由调用方写入 DB。

注意：官方站点的 HTML / JSON 结构会变 — 这里给出的解析路径是 2025 年常见结构，
若日后变化，只需要改对应 Adapter 的 ``parse_*`` 方法即可，其他流程不需要改。

各 Adapter 都做到了 **失败安全**：网络异常 / 解析失败时返回空列表 + 日志告警，
不会让上层的并发抓取整体崩溃。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------


@dataclass
class RegulationItem:
    """统一的法规条目结构 — 直接 ``**asdict(item)`` 入 ``Regulation`` 表。"""

    source: str  # CSRC / MOF / STA / SAFE / PBOC / LOCAL / OTHER
    title: str
    source_url: Optional[str] = None
    issuing_authority: Optional[str] = None
    category: Optional[str] = None  # 公告 / 通知 / 规章 / 准则 / 问答 ...
    document_no: Optional[str] = None
    publish_date: Optional[str] = None
    effective_date: Optional[str] = None
    expire_date: Optional[str] = None
    is_effective: bool = True
    summary: Optional[str] = None
    full_text: str = ""
    keywords: Optional[str] = None
    attachments: Optional[str] = None
    content_hash: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = self.compute_hash()

    def compute_hash(self) -> str:
        """以 source+title+document_no+publish_date 计算唯一指纹。

        正文易因网页改版变动；用元信息计算指纹更稳定，避免抓两次入两条。
        """
        raw = f"{self.source}|{self.title}|{self.document_no or ''}|{self.publish_date or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# HTTP 工具
# ----------------------------------------------------------------------


class _HttpClient:
    """共用的 httpx.AsyncClient 封装 (带重试 + UA)。"""

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=settings.REGULATION_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": settings.REGULATION_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    async def get(self, url: str, **kwargs: Any) -> Optional[httpx.Response]:
        for attempt in range(settings.REGULATION_FETCH_RETRY + 1):
            try:
                resp = await self.client.get(url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "HTTP %s — %s (attempt %d)", e.response.status_code, url, attempt + 1
                )
                if e.response.status_code in (404, 403, 401):
                    return None
            except httpx.HTTPError as e:
                logger.warning("HTTP 抓取失败 %s — %s (attempt %d)", url, e, attempt + 1)
            await asyncio.sleep(0.5 * (attempt + 1))
        return None

    async def close(self) -> None:
        await self.client.aclose()


# ----------------------------------------------------------------------
# Adapter 基类
# ----------------------------------------------------------------------


class BaseRegulationAdapter:
    """法规抓取 Adapter 基类。每个机构一个子类。"""

    source_code: str = "OTHER"
    issuing_authority: str = ""

    def __init__(self, http: _HttpClient) -> None:
        self.http = http

    async def fetch(self, max_pages: int = 0) -> List[RegulationItem]:
        """主入口 — 抓取并返回标准化后的法规列表。"""
        raise NotImplementedError

    # —— 解析工具 ——

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    @staticmethod
    def _norm_date(text: str) -> Optional[str]:
        """把各种中文日期格式归一为 YYYY-MM-DD。"""
        if not text:
            return None
        text = text.strip()
        # 2024-01-01 / 2024/01/01 / 2024.01.01
        m = re.search(r"(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})", text)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        # 20240101
        m = re.search(r"(\d{4})(\d{2})(\d{2})", text)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{mo}-{d}"
        return None

    @staticmethod
    def _extract_doc_no(text: str) -> Optional[str]:
        """识别"财会〔2024〕12号""国家税务总局公告 2024 年第 1 号"等文号。"""
        if not text:
            return None
        patterns = [
            r"[^\s]{0,8}〔\d{4}〕\d+号",
            r"[^\s]{0,8}\[\d{4}\]\d+号",
            r"[^\s]{0,12}公告\s*\d{4}\s*年\s*第\s*\d+\s*号",
            r"[^\s]{0,12}第\s*\d+\s*号令",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(0).strip()
        return None


# ----------------------------------------------------------------------
# 证监会 Adapter
# ----------------------------------------------------------------------


class CSRCAdapter(BaseRegulationAdapter):
    """证监会法规 / 规范性文件抓取。

    主要栏目：
      - 法律法规：/zjhpublic/zjh/
      - 规范性文件：/zjhpublic/zjh/
      - 部门规章：/zjhpublic/zjh/
      - 证监会令：/zjhpublic/zjh/
    """

    source_code = "CSRC"
    issuing_authority = "中国证券监督管理委员会"

    # 法律法规库主入口（含规章 / 规范性文件 / 法律 / 行政法规）
    LIST_URLS = [
        (
            "法律法规",
            "/searchList/00000000fa0000fmifn/?_isAgg=true&_isJson=true&_pageSize=20&_template=index&_keyword=&_rangeTimeGte=&_channelName=%E6%B3%95%E5%BE%8B%E6%B3%95%E8%A7%84",
        ),
        (
            "部门规章",
            "/searchList/00000000fa0000fmifn/?_isAgg=true&_isJson=true&_pageSize=20&_template=index&_keyword=&_rangeTimeGte=&_channelName=%E9%83%A8%E9%97%A8%E8%A7%84%E7%AB%A0",
        ),
        (
            "规范性文件",
            "/searchList/00000000fa0000fmifn/?_isAgg=true&_isJson=true&_pageSize=20&_template=index&_keyword=&_rangeTimeGte=&_channelName=%E8%A7%84%E8%8C%83%E6%80%A7%E6%96%87%E4%BB%B6",
        ),
    ]

    async def fetch(self, max_pages: int = 0) -> List[RegulationItem]:
        max_pages = max_pages or settings.REGULATION_MAX_PAGES
        items: List[RegulationItem] = []
        for category, path in self.LIST_URLS:
            for page in range(1, max_pages + 1):
                url = f"{settings.CSRC_URL}{path}&_pageNum={page}"
                resp = await self.http.get(url)
                if not resp:
                    break
                page_items = self._parse_csrc_json(resp.text, category)
                if not page_items:
                    break
                items.extend(page_items)
        logger.info("CSRC: 抓取 %d 条法规", len(items))
        return items

    def _parse_csrc_json(self, body: str, category: str) -> List[RegulationItem]:
        """解析 CSRC 搜索接口 (JSONP / JSON)。失败回退到 HTML 解析。"""
        items: List[RegulationItem] = []
        try:
            data = json.loads(body)
            rows = data.get("data", {}).get("results") or data.get("results") or []
            for r in rows:
                title = self._clean(r.get("title") or r.get("name") or "")
                if not title:
                    continue
                items.append(
                    RegulationItem(
                        source=self.source_code,
                        issuing_authority=self.issuing_authority,
                        category=category,
                        title=title,
                        document_no=self._extract_doc_no(title) or r.get("articleNumber"),
                        publish_date=self._norm_date(
                            r.get("publishedTimeStr") or r.get("publishDate") or ""
                        ),
                        source_url=r.get("url"),
                        summary=self._clean(r.get("description") or ""),
                        full_text=self._clean(r.get("description") or "")[:4000],
                    )
                )
        except (json.JSONDecodeError, AttributeError):
            # 回退到 HTML（极少数情况下接口返回 HTML 错误页）
            soup = BeautifulSoup(body, "lxml")
            for a in soup.select("a[href*='.htm']")[:30]:
                title = self._clean(a.get_text())
                href = a.get("href", "")
                if len(title) < 6:
                    continue
                items.append(
                    RegulationItem(
                        source=self.source_code,
                        issuing_authority=self.issuing_authority,
                        category=category,
                        title=title,
                        document_no=self._extract_doc_no(title),
                        source_url=urljoin(settings.CSRC_URL, href),
                    )
                )
        return items


# ----------------------------------------------------------------------
# 财政部 Adapter
# ----------------------------------------------------------------------


class MOFAdapter(BaseRegulationAdapter):
    """财政部 / 会计司：CAS 准则、应用指南、问答、法规库公告。"""

    source_code = "MOF"
    issuing_authority = "中华人民共和国财政部"

    LIST_URLS = [
        (
            "会计司公告",
            "{base}/zhengwuxinxi/zhengcefabu/",
            "{accounting}/zhengwuxinxi/gongzuotongzhi/",
        ),
        ("会计准则", "{accounting}/zhengwuxinxi/zhengcefabu/kuaijizhuze/"),
        ("准则解释", "{accounting}/zhengwuxinxi/zhengcefabu/zhunzejieshi/"),
        ("准则问答", "{accounting}/zhengwuxinxi/zhengcefabu/zhunzewenda/"),
    ]

    async def fetch(self, max_pages: int = 0) -> List[RegulationItem]:
        max_pages = max_pages or settings.REGULATION_MAX_PAGES
        items: List[RegulationItem] = []
        urls = [
            ("会计司公告", f"{settings.MOF_ACCOUNTING_URL}/zhengwuxinxi/zhengcefabu/"),
            ("会计准则", f"{settings.MOF_ACCOUNTING_URL}/zhengwuxinxi/zhengcefabu/kuaijizhuze/"),
            ("准则解释", f"{settings.MOF_ACCOUNTING_URL}/zhengwuxinxi/zhengcefabu/zhunzejieshi/"),
            ("准则问答", f"{settings.MOF_ACCOUNTING_URL}/zhengwuxinxi/zhengcefabu/zhunzewenda/"),
        ]
        for category, base_url in urls:
            for page in range(1, max_pages + 1):
                page_url = base_url if page == 1 else f"{base_url}index_{page}.htm"
                resp = await self.http.get(page_url)
                if not resp:
                    break
                page_items = self._parse_mof_list(resp.text, base_url, category)
                if not page_items:
                    break
                items.extend(page_items)
        logger.info("MOF: 抓取 %d 条法规", len(items))
        return items

    def _parse_mof_list(self, body: str, base_url: str, category: str) -> List[RegulationItem]:
        soup = BeautifulSoup(body, "lxml")
        items: List[RegulationItem] = []
        # 列表通常在 ul.liBox / div.xxgkBox / table 中
        for li in soup.select("ul li, .xxgkBox li, table tr"):
            a = li.find("a")
            if not a:
                continue
            title = self._clean(a.get_text())
            href = a.get("href", "")
            if not title or len(title) < 6 or not href:
                continue
            date_text = ""
            for span in li.find_all(["span", "td"]):
                t = self._clean(span.get_text())
                if re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", t):
                    date_text = t
                    break
            items.append(
                RegulationItem(
                    source=self.source_code,
                    issuing_authority=self.issuing_authority,
                    category=category,
                    title=title,
                    document_no=self._extract_doc_no(title),
                    publish_date=self._norm_date(date_text),
                    source_url=urljoin(base_url, href),
                )
            )
        return items[:50]


# ----------------------------------------------------------------------
# 国家税务总局 Adapter
# ----------------------------------------------------------------------


class STAAdapter(BaseRegulationAdapter):
    """国家税务总局：增值税 / 企业所得税 / 个人所得税 / 印花税公告与问答。"""

    source_code = "STA"
    issuing_authority = "国家税务总局"

    LIST_URLS = [
        ("税务总局公告", "/n810341/n810755/"),
        ("税收规范性文件", "/n810341/n810765/"),
        ("税收法律法规", "/n810341/n810770/"),
        ("税务总局令", "/n810341/n810771/"),
    ]

    async def fetch(self, max_pages: int = 0) -> List[RegulationItem]:
        max_pages = max_pages or settings.REGULATION_MAX_PAGES
        items: List[RegulationItem] = []
        for category, path in self.LIST_URLS:
            base = f"{settings.STA_URL}{path}"
            for page in range(1, max_pages + 1):
                page_url = base if page == 1 else f"{base}index_{page}.html"
                resp = await self.http.get(page_url)
                if not resp:
                    break
                page_items = self._parse_sta_list(resp.text, base, category)
                if not page_items:
                    break
                items.extend(page_items)
        logger.info("STA: 抓取 %d 条法规", len(items))
        return items

    def _parse_sta_list(self, body: str, base_url: str, category: str) -> List[RegulationItem]:
        soup = BeautifulSoup(body, "lxml")
        items: List[RegulationItem] = []
        for li in soup.select("ul li, .list_box li, .news_list li"):
            a = li.find("a")
            if not a:
                continue
            title = self._clean(a.get("title") or a.get_text())
            href = a.get("href", "")
            if not title or len(title) < 6 or not href:
                continue
            date_text = ""
            span = li.find("span")
            if span:
                date_text = self._clean(span.get_text())
            items.append(
                RegulationItem(
                    source=self.source_code,
                    issuing_authority=self.issuing_authority,
                    category=category,
                    title=title,
                    document_no=self._extract_doc_no(title),
                    publish_date=self._norm_date(date_text),
                    source_url=urljoin(base_url, href),
                )
            )
        return items[:50]


# ----------------------------------------------------------------------
# 外管局 / 人民银行 (简版)
# ----------------------------------------------------------------------


class SAFEAdapter(BaseRegulationAdapter):
    source_code = "SAFE"
    issuing_authority = "国家外汇管理局"

    async def fetch(self, max_pages: int = 0) -> List[RegulationItem]:
        max_pages = max_pages or settings.REGULATION_MAX_PAGES
        items: List[RegulationItem] = []
        base = f"{settings.SAFE_URL}/safe/zcfg/index.html"
        for page in range(1, max_pages + 1):
            url = base if page == 1 else f"{settings.SAFE_URL}/safe/zcfg/index_{page}.html"
            resp = await self.http.get(url)
            if not resp:
                break
            soup = BeautifulSoup(resp.text, "lxml")
            page_items: List[RegulationItem] = []
            for li in soup.select("ul li, .list li"):
                a = li.find("a")
                if not a:
                    continue
                title = self._clean(a.get("title") or a.get_text())
                href = a.get("href", "")
                if not title or len(title) < 6:
                    continue
                date_text = ""
                span = li.find("span")
                if span:
                    date_text = self._clean(span.get_text())
                page_items.append(
                    RegulationItem(
                        source=self.source_code,
                        issuing_authority=self.issuing_authority,
                        category="外汇政策",
                        title=title,
                        document_no=self._extract_doc_no(title),
                        publish_date=self._norm_date(date_text),
                        source_url=urljoin(settings.SAFE_URL, href),
                    )
                )
            if not page_items:
                break
            items.extend(page_items[:50])
        logger.info("SAFE: 抓取 %d 条法规", len(items))
        return items


class PBOCAdapter(BaseRegulationAdapter):
    source_code = "PBOC"
    issuing_authority = "中国人民银行"

    async def fetch(self, max_pages: int = 0) -> List[RegulationItem]:
        max_pages = max_pages or settings.REGULATION_MAX_PAGES
        items: List[RegulationItem] = []
        base = f"{settings.PBOC_URL}/zhengcehuobisi/125207/125213/index.html"
        for page in range(1, max_pages + 1):
            url = (
                base
                if page == 1
                else f"{settings.PBOC_URL}/zhengcehuobisi/125207/125213/index_{page}.html"
            )
            resp = await self.http.get(url)
            if not resp:
                break
            soup = BeautifulSoup(resp.text, "lxml")
            page_items: List[RegulationItem] = []
            for tr in soup.select("table tr"):
                a = tr.find("a")
                if not a:
                    continue
                title = self._clean(a.get_text())
                href = a.get("href", "")
                if not title or len(title) < 6:
                    continue
                tds = tr.find_all("td")
                date_text = self._clean(tds[-1].get_text()) if tds else ""
                page_items.append(
                    RegulationItem(
                        source=self.source_code,
                        issuing_authority=self.issuing_authority,
                        category="货币政策",
                        title=title,
                        document_no=self._extract_doc_no(title),
                        publish_date=self._norm_date(date_text),
                        source_url=urljoin(settings.PBOC_URL, href),
                    )
                )
            if not page_items:
                break
            items.extend(page_items[:50])
        logger.info("PBOC: 抓取 %d 条法规", len(items))
        return items


# ----------------------------------------------------------------------
# 入口服务
# ----------------------------------------------------------------------


_ADAPTERS: Dict[str, Callable[[_HttpClient], BaseRegulationAdapter]] = {
    "CSRC": CSRCAdapter,
    "MOF": MOFAdapter,
    "STA": STAAdapter,
    "SAFE": SAFEAdapter,
    "PBOC": PBOCAdapter,
}


class RegulationScraperService:
    """对外的统一抓取入口 — 支持并发拉取多个机构。"""

    SUPPORTED_SOURCES = list(_ADAPTERS.keys())

    def __init__(self) -> None:
        self.http = _HttpClient()

    async def __aenter__(self) -> "RegulationScraperService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        await self.http.close()

    async def scrape(
        self,
        sources: Optional[List[str]] = None,
        max_pages: int = 0,
    ) -> List[RegulationItem]:
        """抓取指定来源（不传则全部）。

        Args:
            sources: 来源代码列表，例如 ["CSRC", "MOF"]；None 表示全抓
            max_pages: 每个栏目的最大页数（0 = 使用 settings 默认值）

        Returns:
            标准化后的 RegulationItem 列表，已按 content_hash 去重
        """
        codes = [s.upper() for s in sources] if sources else self.SUPPORTED_SOURCES
        codes = [c for c in codes if c in _ADAPTERS]
        if not codes:
            return []

        tasks = []
        for code in codes:
            adapter = _ADAPTERS[code](self.http)
            tasks.append(self._safe_fetch(adapter, max_pages))

        # return_exceptions=True: 单个信源失败不应拖垮整次抓取 (一个 adapter 抛错不应取消其余)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_items: List[RegulationItem] = []
        for code, batch in zip(codes, results):
            if isinstance(batch, Exception):
                logger.warning("法规源 %s 抓取失败 (跳过): %s", code, batch)
                continue
            all_items.extend(batch)

        # 按 content_hash 去重
        seen: set[str] = set()
        unique: List[RegulationItem] = []
        for item in all_items:
            h = item.content_hash or ""
            if h and h in seen:
                continue
            seen.add(h)
            unique.append(item)

        logger.info(
            "Regulation 抓取完成：%d 条 (去重前 %d 条)，来源 %s",
            len(unique),
            len(all_items),
            codes,
        )
        return unique

    async def _safe_fetch(
        self, adapter: BaseRegulationAdapter, max_pages: int
    ) -> List[RegulationItem]:
        """单 Adapter 出错时不影响其他来源。"""
        try:
            return await adapter.fetch(max_pages=max_pages)
        except Exception as e:  # noqa: BLE001
            logger.exception("%s 抓取失败: %s", adapter.source_code, e)
            return []


def item_to_dict(item: RegulationItem) -> Dict[str, Any]:
    """把 RegulationItem 转为可直接 **kw 入 ORM 的字典。"""
    return {k: v for k, v in asdict(item).items() if v is not None}
