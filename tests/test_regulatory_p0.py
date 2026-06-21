"""Regulatory Scraper P0 修复测试 (2026-06-17).

覆盖:
  - #8a/b/c CSRC/SSE/SZSE 三个方法跳巨潮 (cninfo) 统一接口
  - SZSE 原 URL fallback 用 response.json() 而非 BS4
  - 公共方法签名 + 返回结构不变 (match_cases_by_keywords 等保留)
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.regulatory_scraper import RegulatoryCaseScraper


# ============================================================
# 工具: httpx MockTransport
# ============================================================
def _mock_transport(handler):
    """包装一个 handler (url → json) 为 httpx MockTransport."""
    return httpx.MockTransport(handler)


def _mock_cninfo_handler(payload: dict):
    """默认 cninfo 响应 handler."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return handler


def _mock_szse_native_handler(payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return handler


def _mock_error_handler(status: int = 500):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="error")
    return handler


def _make_scraper(handler) -> RegulatoryCaseScraper:
    """构造 scraper 并替换其 session."""
    s = RegulatoryCaseScraper(timeout=5.0)
    # 替换内部 session 的 transport 为 mock
    s.session = httpx.AsyncClient(
        timeout=5.0,
        headers=s.headers,
        transport=_mock_transport(handler),
    )
    return s


CNINFO_PAYLOAD = {
    "totalAnnouncement": 2,
    "announcements": [
        {
            "announcementId": 9900012345,
            "announcementTitle": "关于对<em>XX股份</em>发出<em>问询函</em>的公告",
            "announcementTime": 1705276800000,  # 2024-01-15
            "adjunctUrl": "finalpage/2024-01-15/12345-PDF",
            "secCode": "600000",
            "secName": "XX股份",
            "plate": "sse",
        },
        {
            "announcementId": 9900012346,
            "announcementTitle": "对YY公司年报问询函",
            "announcementTime": 1705814400000,  # 2024-01-21
            "adjunctUrl": "http://static.cninfo.com.cn/finalpage/2024-01-21/12346.PDF",
            "secCode": "300750",
            "secName": "YY公司",
            "plate": "szse",
        },
    ],
}


# ============================================================
# #8 巨潮统一接口
# ============================================================
class TestScrapeCninfoAggregator:
    """三个公共方法都跳 _scrape_cninfo (column 区分)."""

    @pytest.mark.asyncio
    async def test_csrc_uses_cninfo_column_bj(self):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["body"] = dict(request.headers)  # headers only for test
            # 验证 POST + column=bj
            return httpx.Response(200, json=CNINFO_PAYLOAD)

        s = _make_scraper(handler)
        try:
            cases = await s.scrape_csrc_inquiries()
        finally:
            await s.close()

        assert captured["method"] == "POST"
        # 2 cases 都拿到
        assert len(cases) == 2
        # 字段映射正确
        c0 = cases[0]
        assert c0["case_no"] == "9900012345"
        assert c0["title"] == "关于对XX股份发出问询函的公告"  # HTML 标签被剥
        assert c0["publish_date"] == "2024-01-15"
        assert c0["source"] == "证监会 (via 巨潮)"
        assert c0["pdf_url"] == "http://static.cninfo.com.cn/finalpage/2024-01-15/12345-PDF"
        assert c0["sec_code"] == "600000"
        assert c0["sec_name"] == "XX股份"

        # 第二条 adjunctUrl 已是完整 URL
        assert cases[1]["pdf_url"] == "http://static.cninfo.com.cn/finalpage/2024-01-21/12346.PDF"

    @pytest.mark.asyncio
    async def test_sse_uses_cninfo_column_sse(self):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode() if request.content else ""
            return httpx.Response(200, json=CNINFO_PAYLOAD)

        s = _make_scraper(handler)
        try:
            cases = await s.scrape_sse_inquiries()
        finally:
            await s.close()

        assert "column=sse" in captured["body"]
        assert "searchkey=%E9%97%AE%E8%AF%A2%E5%87%BD" in captured["body"] or "searchkey=" in captured["body"]
        assert all("上交所" in c["source"] for c in cases)

    @pytest.mark.asyncio
    async def test_szse_uses_cninfo_column_szse(self):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode() if request.content else ""
            return httpx.Response(200, json=CNINFO_PAYLOAD)

        s = _make_scraper(handler)
        try:
            cases = await s.scrape_szse_inquiries()
        finally:
            await s.close()

        assert "column=szse" in captured["body"]
        # 来源标 "深交所 (via 巨潮)" 或 fallback 后是 "深交所 (原生)"
        assert any("深交所" in c["source"] for c in cases)

    @pytest.mark.asyncio
    async def test_csrc_penalties_uses_searchkey_administrative(self):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode() if request.content else ""
            return httpx.Response(200, json=CNINFO_PAYLOAD)

        s = _make_scraper(handler)
        try:
            cases = await s.scrape_csrc_penalties()
        finally:
            await s.close()

        # 处罚用 "行政处罚" 关键词
        assert "行政处罚" in captured["body"] or "searchkey=" in captured["body"]
        assert all(c["case_type"] == "监管公告" or c["case_type"] == "处罚决定" for c in cases)

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        s = _make_scraper(_mock_error_handler(503))
        try:
            cases = await s.scrape_csrc_inquiries()
            assert cases == []
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json {{")
        s = _make_scraper(handler)
        try:
            cases = await s.scrape_csrc_inquiries()
            assert cases == []
        finally:
            await s.close()

    @pytest.mark.asyncio
    async def test_empty_announcements_returns_empty(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"announcements": None})
        s = _make_scraper(handler)
        try:
            cases = await s.scrape_csrc_inquiries()
            assert cases == []
        finally:
            await s.close()


# ============================================================
# #8c SZSE 原生 URL fallback
# ============================================================
class TestScrapeSzseNativeFallback:
    """P0 修复: SZSE 原 URL 当巨潮空时 fallback, 用 response.json() 解析."""

    @pytest.mark.asyncio
    async def test_szse_fallback_when_cninfo_empty(self):
        """巨潮返空 → fallback 到原生 URL."""
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            url = str(request.url)
            if "cninfo.com.cn" in url:
                # 巨潮返空
                return httpx.Response(200, json={"announcements": []})
            elif "szse.cn" in url:
                # SZSE 原生 JSON 返
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            ["600000", "浦发银行", "2024-01-15", "对浦发银行问询函", "http://disc.static.szse.cn/x.pdf"],
                            ["300750", "宁德时代", "2024-02-01", "对宁德时代问询函", "http://disc.static.szse.cn/y.pdf"],
                        ]
                    },
                )
            return httpx.Response(404)

        s = _make_scraper(handler)
        try:
            cases = await s.scrape_szse_inquiries()
        finally:
            await s.close()

        # 调用了两次 (cninfo + fallback)
        assert call_count["n"] == 2
        assert len(cases) == 2
        assert cases[0]["source"] == "深交所 (原生)"
        assert cases[0]["case_no"] == "600000"
        assert cases[0]["title"] == "对浦发银行问询函"
        assert cases[0]["publish_date"] == "2024-01-15"
        assert cases[0]["pdf_url"] == "http://disc.static.szse.cn/x.pdf"

    @pytest.mark.asyncio
    async def test_szse_native_json_parsed_not_html(self):
        """P0 修复: 原版用 BS4 解析 JSON 永远 0 条, 新版用 .json() 正确解析."""
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        ["000001", "平安银行", "2024-03-01", "问询函标题", "http://x.pdf"],
                    ]
                },
            )
        s = _make_scraper(handler)
        try:
            cases = await s._scrape_szse_native()
        finally:
            await s.close()

        assert len(cases) == 1
        assert cases[0]["sec_code"] == "000001"


# ============================================================
# scrape_all 整合
# ============================================================
class TestScrapeAll:
    @pytest.mark.asyncio
    async def test_scrape_all_aggregates_four_sources(self):
        cninfo_calls = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            cninfo_calls["n"] += 1
            return httpx.Response(200, json=CNINFO_PAYLOAD)

        s = _make_scraper(handler)
        try:
            cases = await s.scrape_all()
        finally:
            await s.close()

        # 4 个方法都调用 cninfo (CSRC问询/SSE/SZSE/CSRC处罚)
        assert cninfo_calls["n"] == 4
        # CNINFO_PAYLOAD 有 2 条 × 4 = 8 条
        assert len(cases) == 8

    @pytest.mark.asyncio
    async def test_scrape_all_resilient_one_source_fails(self):
        """单源失败不连坐其他源."""
        call_log: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            call_log.append(url)
            # 失败注入: 第 3 次调用 (SZSE) 返回 500
            if len(call_log) == 3:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json=CNINFO_PAYLOAD)

        s = _make_scraper(handler)
        try:
            cases = await s.scrape_all()
        finally:
            await s.close()

        # 4 个调用, 1 个失败 → 3 个成功 × 2 条 = 6 条
        assert len(cases) == 6


# ============================================================
# 公共方法保留 (API 兼容)
# ============================================================
class TestPublicMethodsPreserved:
    """scrape_csrc_inquiries / scrape_sse_inquiries / scrape_szse_inquiries /
    scrape_csrc_penalties / scrape_all / match_cases_by_keywords / match_cases_by_industry
    必须存在且 signature 不变.
    """

    def test_method_signatures(self):
        s = RegulatoryCaseScraper()
        # 公共方法存在
        assert callable(getattr(s, "scrape_csrc_inquiries"))
        assert callable(getattr(s, "scrape_sse_inquiries"))
        assert callable(getattr(s, "scrape_szse_inquiries"))
        assert callable(getattr(s, "scrape_csrc_penalties"))
        assert callable(getattr(s, "scrape_all"))
        assert callable(getattr(s, "match_cases_by_keywords"))
        assert callable(getattr(s, "match_cases_by_industry"))
        assert callable(getattr(s, "close"))

    def test_match_cases_by_keywords(self):
        s = RegulatoryCaseScraper()
        cases = [
            {"title": "关于收入确认的问询函", "content": "涉及应收账款"},
            {"title": "关于存货的问询函", "content": ""},
            {"title": "其他公告", "content": ""},
        ]
        matched = s.match_cases_by_keywords(cases, ["收入确认", "存货"])
        assert len(matched) == 2
        assert matched[0]["matched_keywords"] == ["收入确认"]
        assert "存货" in matched[1]["matched_keywords"]

    def test_match_cases_by_industry(self):
        s = RegulatoryCaseScraper()
        cases = [
            {"title": "医药行业案例", "content": ""},
            {"title": "制造业案例", "content": ""},
        ]
        matched = s.match_cases_by_industry(cases, "医药")
        assert len(matched) == 1
        assert matched[0]["title"] == "医药行业案例"

    def test_match_empty_industry_returns_all(self):
        s = RegulatoryCaseScraper()
        cases = [{"title": "A"}, {"title": "B"}]
        assert s.match_cases_by_industry(cases, "") == cases