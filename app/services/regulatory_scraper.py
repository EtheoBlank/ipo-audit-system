"""Regulatory case scraping service for IPO Audit System.

NOTE: The official CSRC / SSE / SZSE websites are heavily JS-rendered and
frequently change their HTML structure. The URL templates below are
representative; in production these would need to be updated to match the
current site layout, or replaced with a proper API / RSS feed if available.
"""

import logging

import httpx
from bs4 import BeautifulSoup
from typing import List, Dict

from app.core.config import settings

logger = logging.getLogger(__name__)


class RegulatoryCaseScraper:
    """Scrape regulatory cases from CSRC and stock exchanges."""

    def __init__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    async def close(self):
        """Close the HTTP session."""
        await self.session.aclose()

    async def scrape_csrc_inquiries(self, page: int = 1) -> List[Dict]:
        """Scrape inquiry letters from CSRC website.

        Args:
            page: Page number to scrape

        Returns:
            List of inquiry letter records
        """
        url = f"{settings.CSRC_URL}/pub/newsite/sycyjgshjscxgshj/shjshjwxshjwxindex_{page}.shtml"
        cases = []

        try:
            response = await self.session.get(url, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            # Parse table rows (adjust selector based on actual page structure)
            rows = soup.select("table tr")
            for row in rows:
                cols = row.select("td")
                if len(cols) >= 4:
                    case = {
                        "case_no": cols[0].get_text(strip=True),
                        "case_type": "问询函",
                        "source": "证监会",
                        "publish_date": cols[1].get_text(strip=True),
                        "title": cols[2].get_text(strip=True),
                        "content": cols[3].get_text(strip=True),
                    }
                    cases.append(case)

        except httpx.HTTPStatusError as e:
            logger.warning("CSRC 返回 %s — 跳过: %s", e.response.status_code, url)
        except httpx.HTTPError as e:
            logger.warning("抓取 CSRC 失败: %s", e)

        return cases

    async def scrape_sse_inquiries(self, page: int = 1) -> List[Dict]:
        """Scrape inquiry letters from Shanghai Stock Exchange.

        Args:
            page: Page number to scrape

        Returns:
            List of inquiry letter records
        """
        url = (
            f"{settings.SSE_URL}/lawandregulation/query/regulativerecord/"
            f"qaTypeNO%20IN%20(%27401%27)?page={page}"
        )
        cases = []

        try:
            response = await self.session.get(url, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            rows = soup.select("table tr")
            for row in rows:
                cols = row.select("td")
                if len(cols) >= 4:
                    case = {
                        "case_no": cols[0].get_text(strip=True),
                        "case_type": "问询函",
                        "source": "上交所",
                        "publish_date": cols[1].get_text(strip=True),
                        "title": cols[2].get_text(strip=True),
                        "content": cols[3].get_text(strip=True),
                    }
                    cases.append(case)

        except httpx.HTTPStatusError as e:
            logger.warning("SSE 返回 %s — 跳过: %s", e.response.status_code, url)
        except httpx.HTTPError as e:
            logger.warning("抓取 SSE 失败: %s", e)

        return cases

    async def scrape_szse_inquiries(self, page: int = 1) -> List[Dict]:
        """Scrape inquiry letters from Shenzhen Stock Exchange.

        Args:
            page: Page number to scrape

        Returns:
            List of inquiry letter records
        """
        url = (
            f"{settings.SZSE_URL}/api/report/ShowReport/data"
            f"?SHOWTYPE=JSON&CATALOGID=1839&CATEGORY=xxpl/tzzscx&page={page}"
        )
        cases = []

        try:
            response = await self.session.get(url, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            rows = soup.select("table tr")
            for row in rows:
                cols = row.select("td")
                if len(cols) >= 4:
                    case = {
                        "case_no": cols[0].get_text(strip=True),
                        "case_type": "问询函",
                        "source": "深交所",
                        "publish_date": cols[1].get_text(strip=True),
                        "title": cols[2].get_text(strip=True),
                        "content": cols[3].get_text(strip=True),
                    }
                    cases.append(case)

        except httpx.HTTPStatusError as e:
            logger.warning("SZSE 返回 %s — 跳过: %s", e.response.status_code, url)
        except httpx.HTTPError as e:
            logger.warning("抓取 SZSE 失败: %s", e)

        return cases

    async def scrape_csrc_penalties(self, page: int = 1) -> List[Dict]:
        """Scrape penalty decisions from CSRC website.

        Args:
            page: Page number to scrape

        Returns:
            List of penalty records
        """
        url = f"{settings.CSRC_URL}/pub/newsite/sycyjgshjscxgshj/cfjdindex_{page}.shtml"
        cases = []

        try:
            response = await self.session.get(url, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            rows = soup.select("table tr")
            for row in rows:
                cols = row.select("td")
                if len(cols) >= 4:
                    case = {
                        "case_no": cols[0].get_text(strip=True),
                        "case_type": "处罚决定",
                        "source": "证监会",
                        "publish_date": cols[1].get_text(strip=True),
                        "title": cols[2].get_text(strip=True),
                        "content": cols[3].get_text(strip=True),
                    }
                    cases.append(case)

        except httpx.HTTPStatusError as e:
            logger.warning("CSRC 处罚 返回 %s — 跳过: %s", e.response.status_code, url)
        except httpx.HTTPError as e:
            logger.warning("抓取 CSRC 处罚失败: %s", e)

        return cases

    async def scrape_all(self) -> List[Dict]:
        """Scrape all regulatory cases from all sources.

        Returns:
            Combined list of all regulatory cases
        """
        all_cases = []

        # Scrape from all sources
        csrc_inquiries = await self.scrape_csrc_inquiries()
        sse_inquiries = await self.scrape_sse_inquiries()
        szse_inquiries = await self.scrape_szse_inquiries()
        csrc_penalties = await self.scrape_csrc_penalties()

        all_cases.extend(csrc_inquiries)
        all_cases.extend(sse_inquiries)
        all_cases.extend(szse_inquiries)
        all_cases.extend(csrc_penalties)

        logger.info(
            "共抓取 %d 条监管案例 (CSRC问询=%d, SSE=%d, SZSE=%d, CSRC处罚=%d)",
            len(all_cases),
            len(csrc_inquiries),
            len(sse_inquiries),
            len(szse_inquiries),
            len(csrc_penalties),
        )

        return all_cases

    def match_cases_by_industry(self, cases: List[Dict], industry: str) -> List[Dict]:
        """Match regulatory cases by industry.

        Args:
            cases: List of regulatory cases
            industry: Target industry

        Returns:
            Filtered list of matching cases
        """
        if not industry:
            return cases

        return [
            case
            for case in cases
            if industry in case.get("title", "") or industry in case.get("content", "")
        ]

    def match_cases_by_keywords(self, cases: List[Dict], keywords: List[str]) -> List[Dict]:
        """Match regulatory cases by keywords.

        Args:
            cases: List of regulatory cases
            keywords: List of keywords to search

        Returns:
            Filtered list of matching cases
        """
        matched = []

        for case in cases:
            content = f"{case.get('title', '')} {case.get('content', '')}"
            matched_keywords = [kw for kw in keywords if kw in content]
            if matched_keywords:
                case["matched_keywords"] = matched_keywords
                matched.append(case)

        return matched
