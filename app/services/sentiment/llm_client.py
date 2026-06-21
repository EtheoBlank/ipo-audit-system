"""舆情 LLM 客户端工厂 — 优先 DeepSeek, 兜底 MiniMax.

理由:
- DeepSeek 在项目内已经验证过 (JSON mode + 围栏容错), 温度 0.1 适合结构化抽取
- MiniMax 作为兜底 (与现有 ai_analysis / ai_analysis_engine 复用)
- 都没有时抛 NoLlmConfigured (导入自 app.models.db_models)

接口 (LlmClientProtocol) — 所有实现必须暴露:
    async chat_json(system, user, *, temperature, max_tokens, timeout) -> dict
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Protocol

import httpx

from app.core.config import settings
from app.models.db_models import NoLlmConfigured

logger = logging.getLogger(__name__)


class LlmClientProtocol(Protocol):
    """统一 LLM 客户端接口. 所有实现只暴露 chat_json()."""

    async def chat_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]: ...


# ============================================================
#  工具: 区分"未配置" vs "已配置 (但 key 是占位符)"
# ============================================================

# 常见的 .env.example 占位符 / 假 key — 这些不能算"已配置", 否则会浪费网络往返
_PLACEHOLDER_KEY_PATTERNS: tuple[str, ...] = (
    "your_api_key_here",
    "your-key",
    "your-key-here",
    "sk-xxx",
    "sk-XXXX",
    "placeholder",
    "<your-key>",
    "change-me",
    "todo-replace",
)


def _is_real_key(value: str) -> bool:
    """判定 key 是否是真实可用的 (非空 + 非占位符)."""
    v = (value or "").strip()
    if not v:
        return False
    low = v.lower()
    for p in _PLACEHOLDER_KEY_PATTERNS:
        if p.lower() in low:
            return False
    # 长度过短 (绝大多数 API key 至少 32 字符)
    if len(v) < 32:
        return False
    return True


# ============================================================
#  DeepSeek 实现 (直接复用 DeepSeekClient — 不重新发明轮子)
# ============================================================


def _build_deepseek_client() -> LlmClientProtocol:
    """从 settings 实例化一个 DeepSeek 客户端 (项目已有)."""
    from app.services.sales_ledger.deepseek_client import DeepSeekClient

    return DeepSeekClient(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_API_BASE,
        model=settings.DEEPSEEK_MODEL,
    )


# ============================================================
#  MiniMax 实现 (薄封装, 沿用现有 ai_analysis._call_minimax 风格)
# ============================================================


class MiniMaxChatJsonClient:
    """MiniMax 的 chatcompletion_pro 端点封装, 返回 dict (JSON mode)."""

    DEFAULT_TIMEOUT = 60.0
    DEFAULT_MAX_TOKENS = 4000
    DEFAULT_TEMPERATURE = 0.1
    DEFAULT_MODEL = "abab6.5s-chat"

    def __init__(self, api_key: str, base_url: str, model: Optional[str] = None) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model or self.DEFAULT_MODEL

    @property
    def is_configured(self) -> bool:
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
        if not self.is_configured:
            raise RuntimeError("MINIMAX_API_KEY 未配置")

        url = f"{self.base_url}/text/chatcompletion_pro"
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
            # MiniMax 通过 prompt 引导 JSON, 不一定支持 response_format
            "response_format": {"type": "json_object"},
        }
        if extra_body:
            payload.update(extra_body)

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"调用 MiniMax 失败: {exc}") from exc

        if resp.status_code >= 400:
            raise RuntimeError(f"MiniMax 返回 {resp.status_code}: {resp.text[:500]}")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"MiniMax 响应非 JSON: {resp.text[:200]}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"MiniMax 响应结构异常: {data}") from exc

        # 容错: ```json 围栏 / 截取首个 { 到末个 }
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if "\n" in cleaned:
                cleaned = cleaned.split("\n", 1)[1]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 退化策略: 截取 { ... } 范围
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                return json.loads(cleaned[start : end + 1])
            raise


def _build_minimax_client() -> LlmClientProtocol:
    return MiniMaxChatJsonClient(
        api_key=settings.MINIMAX_API_KEY,
        base_url=settings.MINIMAX_API_BASE,
    )


# ============================================================
#  工厂
# ============================================================


class LlmClientFactory:
    """舆情 LLM 客户端工厂.

    优先级 (preferred):
        1) DEEPSEEK_API_KEY 非空 → DeepSeek
        2) MINIMAX_API_KEY 非空 → MiniMax
        3) 都没有 → NoLlmConfigured

    fallback() 是反过来的优先级.
    """

    _preferred: Optional[LlmClientProtocol] = None
    _fallback: Optional[LlmClientProtocol] = None

    @classmethod
    def preferred(cls) -> LlmClientProtocol:
        if cls._preferred is None:
            cls._preferred = cls._build_preferred()
        return cls._preferred

    @classmethod
    def fallback(cls) -> LlmClientProtocol:
        if cls._fallback is None:
            cls._fallback = cls._build_fallback()
        return cls._fallback

    @classmethod
    def reset_cache(cls) -> None:
        """测试用: 清除单例缓存."""
        cls._preferred = None
        cls._fallback = None

    @classmethod
    def _build_preferred(cls) -> LlmClientProtocol:
        if _is_real_key(settings.DEEPSEEK_API_KEY):
            logger.info("LlmClientFactory: 使用 DeepSeek")
            return _build_deepseek_client()
        if _is_real_key(settings.MINIMAX_API_KEY):
            logger.info("LlmClientFactory: 使用 MiniMax (DeepSeek 未配置)")
            return _build_minimax_client()
        logger.error("LlmClientFactory: 没有可用的 LLM (DEEPSEEK 与 MINIMAX 均未配置或为占位符)")
        raise NoLlmConfigured(
            "舆情模块需要至少配置 DEEPSEEK_API_KEY 或 MINIMAX_API_KEY。请在 .env 中设置(非占位符)后重启服务。"
        )

    @classmethod
    def _build_fallback(cls) -> LlmClientProtocol:
        # 兜底语义: 返回另一家; 如果只配了一家, 仍然返回它 (有总比没有好)
        if _is_real_key(settings.DEEPSEEK_API_KEY):
            return _build_deepseek_client()
        if _is_real_key(settings.MINIMAX_API_KEY):
            return _build_minimax_client()
        raise NoLlmConfigured("没有可用的兜底 LLM")
