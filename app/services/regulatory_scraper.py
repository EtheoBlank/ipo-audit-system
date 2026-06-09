"""Regulatory case scraping service for IPO Audit System."""
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from datetime import datetime
from app.core.config import settings


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
        url = f"{settings.CSRC_URL}/cortacts/ses/search公告？page={page}"
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

        except Exception as e:
            print(f"Error scraping CSRC: {e}")

        return cases

    async def scrape_sse_inquiries(self, page: int = 1) -> List[Dict]:
        """Scrape inquiry letters from Shanghai Stock Exchange.

        Args:
            page: Page number to scrape

        Returns:
            List of inquiry letter records
        """
        url = f"{settings.SseUrl}/_DISPATCH/ses/search/？page={page}"
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

        except Exception as e:
            print(f"Error scraping SSE: {e}")

        return cases

    async def scrape_szse_inquiries(self, page: int = 1) -> List[Dict]:
        """Scrape inquiry letters from Shenzhen Stock Exchange.

        Args:
            page: Page number to scrape

        Returns:
            List of inquiry letter records
        """
        url = f"{settings.SzseUrl}/cortacts/ses/search/？page={page}"
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

        except Exception as e:
            print(f"Error scraping SZSE: {e}")

        return cases

    async def scrape_csrc_penalties(self, page: int = 1) -> List[Dict]:
        """Scrape penalty decisions from CSRC website.

        Args:
            page: Page number to scrape

        Returns:
            List of penalty records
        """
        url = f"{settings.CSRC_URL}/cortacts/ses/search/处罚？page={page}"
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

        except Exception as e:
            print(f"Error scraping CSRC penalties: {e}")

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
            case for case in cases
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
            matched_keywords = [
                kw for kw in keywords
                if kw in content
            ]
            if matched_keywords:
                case["matched_keywords"] = matched_keywords
                matched.append(case)

        return matched