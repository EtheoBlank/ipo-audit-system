"""Round 28 P0-3: deepseek_client 嵌套 JSON 解析失败的 fallback 兜底.

P0-3 bug: 第二次 json.loads 没 try/except, 直接 500 透传.
修复: 失败抛 DeepSeekError 结构化错误.

通过 monkeypatch httpx.AsyncClient.post 模拟各种 DeepSeek 返回.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError


def _make_response(content: str, status_code: int = 200):
    """构造一个 httpx.Response-like 对象."""

    class FakeResp:
        def __init__(self, content_str: str, status: int):
            self._content_str = content_str
            self.status_code = status
            self.text = content_str

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": self._content_str},
                    }
                ]
            }

    return FakeResp(content, status_code)


class _FakeHttpxClient:
    """模拟 httpx.AsyncClient, 让 .post 返回预设响应."""

    def __init__(self, response):
        self._response = response
        self.last_payload: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.last_payload = json or {}
        return self._response


@pytest.fixture
def client():
    """构造一个 is_configured=True 的 DeepSeekClient."""
    return DeepSeekClient(api_key="test-key", base_url="https://api.deepseek.com/v1")


# ============================================================
# P0-3 修复: 正常/围栏/垃圾 三种场景
# ============================================================
class TestDeepSeekChatJsonP0Fix:
    """P0-3: 嵌套 JSON 解析失败时抛 DeepSeekError (结构化), 不让 500 透传."""

    @pytest.mark.asyncio
    async def test_normal_json_parses(self, client, monkeypatch):
        """正常 JSON content → 直接解析返回 dict."""
        import httpx

        payload = json.dumps({"answer": "ok", "score": 0.9})
        fake = _FakeHttpxClient(_make_response(payload))

        # 替换 httpx.AsyncClient
        original = httpx.AsyncClient

        def _factory(*args, **kwargs):
            return fake

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

        result = await client.chat_json(system="s", user="u")
        assert result == {"answer": "ok", "score": 0.9}

    @pytest.mark.asyncio
    async def test_fenced_json_parses(self, client, monkeypatch):
        """```json\\n{...}\\n``` 围栏格式 → 剥离后解析成功."""
        import httpx

        payload_str = json.dumps({"x": 1, "y": [1, 2, 3]})
        fenced = f"```json\n{payload_str}\n```"
        fake = _FakeHttpxClient(_make_response(fenced))

        def _factory(*args, **kwargs):
            return fake

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

        result = await client.chat_json(system="s", user="u")
        assert result == {"x": 1, "y": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_fenced_but_not_json_raises_deepseek_error(self, client, monkeypatch):
        """围栏里仍不是 JSON → 抛 DeepSeekError (不抛 JSONDecodeError)."""
        import httpx

        # 围栏里是 "not json"
        fenced = "```\nthis is not json at all\n```"
        fake = _FakeHttpxClient(_make_response(fenced))

        def _factory(*args, **kwargs):
            return fake

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

        with pytest.raises(DeepSeekError) as exc_info:
            await client.chat_json(system="s", user="u")
        # 错误信息应包含原 content 摘要, 便于排查
        assert "JSON 仍解析失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_completely_garbage_raises_deepseek_error(self, client, monkeypatch):
        """完全没有 JSON 格式 → 抛 DeepSeekError."""
        import httpx

        fake = _FakeHttpxClient(_make_response("完全不是 JSON"))

        def _factory(*args, **kwargs):
            return fake

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

        with pytest.raises(DeepSeekError) as exc_info:
            await client.chat_json(system="s", user="u")
        assert "JSON 仍解析失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_truncated_json_raises_deepseek_error(self, client, monkeypatch):
        """JSON 被截断 → 抛 DeepSeekError 而非裸 JSONDecodeError."""
        import httpx

        # 模拟模型中断输出
        truncated = '{"answer": "hel'
        fake = _FakeHttpxClient(_make_response(truncated))

        def _factory(*args, **kwargs):
            return fake

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

        with pytest.raises(DeepSeekError):
            await client.chat_json(system="s", user="u")

    @pytest.mark.asyncio
    async def test_unconfigured_raises_deepseek_error(self):
        """未配置 API key → DeepSeekError, 不打网络."""
        client_unconfigured = DeepSeekClient(
            api_key="", base_url="https://api.deepseek.com/v1"
        )
        with pytest.raises(DeepSeekError) as exc_info:
            await client_unconfigured.chat_json(system="s", user="u")
        assert "DEEPSEEK_API_KEY" in str(exc_info.value)