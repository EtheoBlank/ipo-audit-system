"""AI-powered risk analysis service for IPO Audit System."""

import httpx
import json
from typing import List, Dict, Optional
from app.core.config import settings


class AIAnalysisService:
    """Service for AI-powered risk analysis using MiniMax API."""

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
        except Exception as e:
            return f"AI分析调用失败: {str(e)}"

    async def analyze_risk_level(self, account_balances: List[Dict], industry: str) -> Dict:
        """Analyze risk level based on account balances.

        Args:
            account_balances: List of account balance records
            industry: Company industry

        Returns:
            Risk analysis result
        """
        if not self.enabled:
            return {
                "risk_level": "中",
                "summary": "AI分析功能未启用",
                "key_concerns": ["请配置 MINIMAX_API_KEY 以启用AI分析"],
            }

        # Prepare data summary
        total_revenue = sum(
            ab.get("debit_amount", 0)
            for ab in account_balances
            if "5" in ab.get("account_code", "")[:1]
        )

        prompt = f"""
作为IPO审计专家，请分析以下财务数据的风险等级：

公司行业: {industry}
总收入: {total_revenue}

科目余额数据:
{json.dumps(account_balances[:50], ensure_ascii=False, indent=2)}

请分析：
1. 风险等级（高/中/低）
2. 主要风险点
3. 监管关注建议

请用JSON格式返回，包含risk_level、summary、key_concerns字段。
"""

        response = await self._call_minimax(prompt)
        return self._parse_json_response(response)

    async def generate_audit_recommendations(
        self,
        risk_points: List[Dict],
        regulatory_cases: List[Dict],
    ) -> List[str]:
        """Generate audit recommendations based on risk points and regulatory cases.

        Args:
            risk_points: Identified risk points
            regulatory_cases: Related regulatory cases

        Returns:
            List of audit recommendations
        """
        if not self.enabled:
            return [
                "请配置 MINIMAX_API_KEY 以启用AI推荐功能",
            ]

        prompt = f"""
作为IPO审计专家，请根据以下风险点和监管案例生成审计程序建议：

风险点:
{json.dumps(risk_points, ensure_ascii=False, indent=2)}

相关监管案例:
{json.dumps(regulatory_cases[:5], ensure_ascii=False, indent=2)}

请生成5-10条具体的审计程序建议，每条建议应包含：
1. 审计程序名称
2. 具体执行步骤
3. 预期发现

请用JSON数组格式返回。
"""

        response = await self._call_minimax(prompt)
        return self._parse_list_response(response)

    async def match_regulatory_cases(
        self,
        company_info: Dict,
        industry: str,
        keywords: List[str],
    ) -> List[Dict]:
        """Match relevant regulatory cases for the company.

        Args:
            company_info: Company information
            industry: Company industry
            keywords: Business keywords

        Returns:
            List of matched regulatory cases
        """
        if not self.enabled:
            return []

        prompt = f"""
作为IPO审计专家，请为以下公司匹配合适的监管案例：

公司信息:
-名称: {company_info.get("name", "")}
- 行业: {industry}
- 关键词: {", ".join(keywords)}

请分析该公司可能面临的监管关注点，并推荐相关的监管案例。

请用JSON数组格式返回，每条包含case_no、title、relevance_score、reason字段。
"""

        response = await self._call_minimax(prompt)
        return self._parse_list_of_dicts(response)

    async def analyze_financial_anomalies(
        self,
        account_balances: List[Dict],
        chronological_accounts: List[Dict],
    ) -> List[Dict]:
        """Analyze financial data for anomalies.

        Args:
            account_balances: Account balance data
            chronological_accounts: Chronological account data

        Returns:
            List of detected anomalies
        """
        if not self.enabled:
            return []

        prompt = f"""
作为IPO审计专家，请分析以下财务数据中的异常情况：

科目余额:
{json.dumps(account_balances[:30], ensure_ascii=False, indent=2)}

序时账（前100条）:
{json.dumps(chronological_accounts[:100], ensure_ascii=False, indent=2)}

请识别：
1. 异常波动科目
2. 关联交易迹象
3. 期末突击调账迹象
4. 其他可疑交易模式

请用JSON数组格式返回，每条包含account_code、anomaly_type、description、severity字段。
"""

        response = await self._call_minimax(prompt)
        return self._parse_list_of_dicts(response)

    def _parse_json_response(self, response: str) -> Dict:
        """Parse JSON response from AI."""
        try:
            # Try to extract JSON from response
            start_idx = response.find("{")
            end_idx = response.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                return json.loads(json_str)
        except Exception:
            pass
        return {"error": "无法解析AI响应", "raw_response": response[:500]}

    def _parse_list_response(self, response: str) -> List[str]:
        """Parse list response from AI."""
        try:
            start_idx = response.find("[")
            end_idx = response.rfind("]") + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                items = json.loads(json_str)
                if isinstance(items, list):
                    return items if isinstance(items[0], str) else [str(item) for item in items]
        except Exception:
            pass
        return [response[:500]]

    def _parse_list_of_dicts(self, response: str) -> List[Dict]:
        """Parse list of dicts from AI response."""
        try:
            start_idx = response.find("[")
            end_idx = response.rfind("]") + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                items = json.loads(json_str)
                if isinstance(items, list) and len(items) > 0:
                    if isinstance(items[0], dict):
                        return items
                    elif isinstance(items[0], str):
                        return [{"recommendation": item} for item in items]
        except Exception:
            pass
        return []
