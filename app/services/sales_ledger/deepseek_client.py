"""DeepSeek API client.

Uses DeepSeek's OpenAI-compatible chat completions endpoint with JSON mode.
The API key is read from the application settings (which load it from .env) and
is never embedded in source — see app.core.config.Settings.DEEPSEEK_API_KEY.

Quick sanity test (no key required to import):
    from app.services.sales_ledger.deepseek_client import DeepSeekClient
    c = DeepSeekClient(api_key="", base_url="https://api.deepseek.com/v1")
    assert c.model == "deepseek-chat"
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class DeepSeekError(RuntimeError):
    """Raised when the DeepSeek API returns an error or unparseable JSON."""


class DeepSeekClient:
    """Thin async wrapper around DeepSeek's chat/completions endpoint.

    DeepSeek's API mirrors OpenAI's; we pass response_format={"type": "json_object"}
    to force the model to return strict JSON. The same trick also works for the
    `deepseek-reasoner` model.
    """

    DEFAULT_TIMEOUT = 60.0
    DEFAULT_MAX_TOKENS = 4000
    DEFAULT_TEMPERATURE = 0.1

    def __init__(self, api_key: str, base_url: str, model: str = "deepseek-chat"):
        if not api_key:
            # We don't raise here — calling code is responsible for checking.
            logger.warning("DeepSeekClient initialised without an API key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    @property
    def is_configured(self) -> bool:
        """Return True if a non-empty API key has been provided."""
        return bool(self.api_key)

    async def chat_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call chat/completions and return a parsed JSON object.

        Raises DeepSeekError on transport failure, non-2xx responses, or if the
        model returns content that is not valid JSON.
        """
        if not self.is_configured:
            raise DeepSeekError("DEEPSEEK_API_KEY 未配置。请在 .env 中填入密钥后重启服务。")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if extra_body:
            payload.update(extra_body)

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise DeepSeekError(f"调用 DeepSeek 失败: {exc}") from exc

        if resp.status_code >= 400:
            # Surface a helpful diagnostic without leaking the key.
            raise DeepSeekError(f"DeepSeek 返回 {resp.status_code}: {resp.text[:500]}")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise DeepSeekError(f"DeepSeek 响应非 JSON: {resp.text[:200]}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError(f"DeepSeek 响应结构异常: {data}") from exc

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Some models occasionally wrap JSON in ``` fences; try to strip.
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if "\n" in cleaned:
                    cleaned = cleaned.split("\n", 1)[1]
            return json.loads(cleaned)
