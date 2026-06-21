"""AI-powered risk analysis service for IPO Audit System.

保留 ``AIAnalysisService`` 薄封装 (audit_note_generator 依赖 ``_call_minimax`` + ``enabled``).
四个公开业务方法 (``analyze_risk_level`` / ``generate_audit_recommendations`` /
``match_regulatory_cases`` / ``analyze_financial_anomalies``) 与三个 ``_parse_*`` 解析器
已迁出至 ``app/services/ai_analysis_engine.py`` (RiskIdentifier / AnomalyDetector /
AIAnalysisEngine), 此处不再保留.
"""

import logging

import httpx
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class AIAnalysisService:
    """Service for AI-powered risk analysis using MiniMax API.

    保留 ``_call_minimax`` 供 ``AuditNoteGenerator`` 调用, 保留 ``enabled`` 供外部检测.
    业务级方法 (analyze_risk_level / 其它) 已迁移到 ai_analysis_engine.py.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.MINIMAX_API_KEY
        self.api_base = settings.MINIMAX_API_BASE
        self.enabled = bool(self.api_key)

    async def _call_minimax(self, prompt: str, system_prompt: str = "") -> str:
        """Call MiniMax API for text generation.

        Args:
            prompt: User prompt
            system_prompt: System prompt for context

        Returns:
            Generated text response
        """
        if not self.enabled:
            return "AI分析功能未启用，请配置 MINIMAX_API_KEY"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": "abab6.5s-chat",
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                    or "你是一位专业的IPO审计专家，帮助识别财务风险和监管关注点。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.api_base}/text/chatcompletion_pro",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            # round37 P1: 原 silent return 现留 logger.exception 留 traceback 便于排查 prompt/网络问题
            logger.exception("AIAnalysisService._call_minimax 调用失败: %s", exc)
            return f"AI分析调用失败: {str(exc)}"
