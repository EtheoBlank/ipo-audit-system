"""监管案例库服务 - 第三阶段."""

import httpx
import logging
from bs4 import BeautifulSoup
from typing import List, Dict
import hashlib
import asyncio
from app.core.config import settings

logger = logging.getLogger(__name__)


class RegulatoryCaseScraper:
    """抓取证监会、交易所监管案例.

    用作 async context manager:
        async with RegulatoryCaseScraper() as s:
            ...
    自动确保 httpx 连接池关闭,避免连接泄露.
    """

    def __init__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def __aenter__(self) -> "RegulatoryCaseScraper":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.session.aclose()

    async def close(self):
        await self.session.aclose()

    async def scrape_csrc_inquiry(self, page: int = 1, keyword: str = "") -> List[Dict]:
        """抓取证监会问询函."""
        url = f"{settings.CSRC_URL}/cortacts/ses/search"
        params = {"page": page, "keyword": keyword} if keyword else {"page": page}
        cases = []
        try:
            response = await self.session.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            for row in soup.select("table.list tr"):
                cols = row.select("td")
                if len(cols) >= 4:
                    cases.append(
                        {
                            "case_no": cols[0].get_text(strip=True),
                            "case_type": "问询函",
                            "source": "证监会",
                            "publish_date": cols[1].get_text(strip=True),
                            "title": cols[2].get_text(strip=True),
                            "content": cols[3].get_text(strip=True)[:500],
                        }
                    )
        except Exception as e:
            logger.exception("CSRC scrape error")
        return cases

    async def scrape_sse_inquiry(self, page: int = 1) -> List[Dict]:
        """抓取上交所问询函."""
        url = f"{settings.SseUrl}/markets/stock/list/inquiry"
        params = {"page": page}
        cases = []
        try:
            response = await self.session.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            for item in soup.select(".inquiry-item"):
                cases.append(
                    {
                        "case_no": item.select_one(".case-no").get_text(strip=True)
                        if item.select_one(".case-no")
                        else "",
                        "case_type": "问询函",
                        "source": "上交所",
                        "publish_date": item.select_one(".date").get_text(strip=True)
                        if item.select_one(".date")
                        else "",
                        "title": item.select_one(".title").get_text(strip=True)
                        if item.select_one(".title")
                        else "",
                        "content": item.select_one(".content").get_text(strip=True)[:500]
                        if item.select_one(".content")
                        else "",
                    }
                )
        except Exception as e:
            logger.exception("SSE scrape error")
        return cases

    async def scrape_szse_inquiry(self, page: int = 1) -> List[Dict]:
        """抓取深交所问询函."""
        url = f"{settings.SzseUrl}/market/inquiry/index"
        params = {"page": page}
        cases = []
        try:
            response = await self.session.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            for item in soup.select(".inquiry-list li"):
                cases.append(
                    {
                        "case_no": item.select_one(".code").get_text(strip=True)
                        if item.select_one(".code")
                        else "",
                        "case_type": "问询函",
                        "source": "深交所",
                        "publish_date": item.select_one(".date").get_text(strip=True)
                        if item.select_one(".date")
                        else "",
                        "title": item.select_one(".title").get_text(strip=True)
                        if item.select_one(".title")
                        else "",
                        "content": item.select_one(".summary").get_text(strip=True)[:500]
                        if item.select_one(".summary")
                        else "",
                    }
                )
        except Exception as e:
            logger.exception("SZSE scrape error")
        return cases

    async def scrape_csrc_penalty(self, page: int = 1) -> List[Dict]:
        """抓取证监会处罚决定."""
        url = f"{settings.CSRC_URL}/cortacts/ses/search/penalty"
        params = {"page": page}
        cases = []
        try:
            response = await self.session.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            for row in soup.select("table.list tr"):
                cols = row.select("td")
                if len(cols) >= 4:
                    cases.append(
                        {
                            "case_no": cols[0].get_text(strip=True),
                            "case_type": "处罚决定",
                            "source": "证监会",
                            "publish_date": cols[1].get_text(strip=True),
                            "title": cols[2].get_text(strip=True),
                            "content": cols[3].get_text(strip=True)[:500],
                        }
                    )
        except Exception as e:
            logger.exception("CSRC penalty scrape error")
        return cases

    async def scrape_all(self, max_pages: int = 5) -> List[Dict]:
        """抓取所有来源的案例."""
        all_cases = []
        tasks = []
        for page in range(1, max_pages + 1):
            tasks.append(self.scrape_csrc_inquiry(page))
            tasks.append(self.scrape_sse_inquiry(page))
            tasks.append(self.scrape_szse_inquiry(page))
            tasks.append(self.scrape_csrc_penalty(page))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_cases.extend(result)
        return all_cases

    def generate_case_id(self, case: Dict) -> str:
        """生成案例唯一ID."""
        content = f"{case.get('source', '')}{case.get('case_no', '')}{case.get('publish_date', '')}"
        return hashlib.md5(content.encode()).hexdigest()[:12].upper()


class CaseMatcher:
    """监管案例匹配器."""

    def __init__(self):
        self.industry_keywords = {
            "制造业": ["毛利率", "应收账款", "存货", "关联交易", "收入确认"],
            "信息技术": ["研发费用", "无形资产", "收入确认", "客户依赖", "商誉"],
            "医药生物": ["销售费用", "学术推广", "两票制", "经销商", "研发费用"],
            "金融服务": ["不良贷款", "拨备覆盖率", "资本充足率", "关联交易"],
            "房地产": ["土地储备", "存货周转", "预收账款", "融资成本"],
            "零售": ["现金流量", "门店扩张", "存货周转", "会员卡"],
        }

    def match_by_industry(self, cases: List[Dict], industry: str) -> List[Dict]:
        """按行业匹配案例."""
        if not industry:
            return cases
        keywords = self.industry_keywords.get(industry, [])
        matched = []
        for case in cases:
            title = case.get("title", "")
            content = case.get("content", "")
            for kw in keywords:
                if kw in title or kw in content:
                    case["matched_keywords"] = case.get("matched_keywords", []) + [kw]
                    matched.append(case)
                    break
        return matched

    def match_by_keywords(self, cases: List[Dict], keywords: List[str]) -> List[Dict]:
        """按关键词匹配案例."""
        matched = []
        for case in cases:
            title = case.get("title", "")
            content = case.get("content", "")
            score = 0
            matched_kws = []
            for kw in keywords:
                if kw in title:
                    score += 3
                    matched_kws.append(kw)
                elif kw in content:
                    score += 1
                    matched_kws.append(kw)
            if matched_kws:
                case["match_score"] = score
                case["matched_keywords"] = matched_kws
                matched.append(case)
        return sorted(matched, key=lambda x: x["match_score"], reverse=True)

    def calculate_relevance(self, case: Dict, company_info: Dict) -> float:
        """计算案例与企业相关性得分."""
        score = 0.0
        title = case.get("title", "")
        content = case.get("content", "")

        # 行业匹配
        industry = company_info.get("industry", "")
        if industry in title or industry in content:
            score += 30

        # 关键词匹配
        keywords = company_info.get("keywords", [])
        for kw in keywords:
            if kw in title:
                score += 10
            elif kw in content:
                score += 3

        # 规模匹配
        revenue = company_info.get("revenue", 0)
        if revenue > 10000000000 and "大额" in content:
            score += 10
        elif revenue < 1000000000 and "小规模" in content:
            score += 5

        return min(score, 100)

    def get_risk_categories(self, case: Dict) -> List[str]:
        """识别案例涉及的风险类别."""
        categories = []
        content = case.get("content", "")
        title = case.get("title", "")

        if any(kw in content or kw in title for kw in ["收入确认", "虚增收入", "提前确认"]):
            categories.append("收入确认")
        if any(kw in content or kw in title for kw in ["应收账款", "坏账", "回款"]):
            categories.append("应收账款")
        if any(kw in content or kw in title for kw in ["存货", "跌价", "积压"]):
            categories.append("存货")
        if any(kw in content or kw in title for kw in ["关联交易", "关联方", "利益输送"]):
            categories.append("关联交易")
        if any(kw in content or kw in title for kw in ["商誉", "减值", "并购"]):
            categories.append("商誉减值")
        if any(kw in content or kw in title for kw in ["毛利率", "成本", "调节"]):
            categories.append("毛利率异常")
        if any(kw in content or kw in title for kw in ["现金", "资金占用", "违规担保"]):
            categories.append("资金安全")
        if any(kw in content or kw in title for kw in ["研发费用", "资本化", "无形资产管理"]):
            categories.append("研发费用")

        return categories if categories else ["其他"]
