"""AI分析引擎 - 第四阶段."""

import httpx
import json
import logging
from typing import Any, List, Dict, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class AIAnalysisEngine:
    """AI驱动的风险识别与分析引擎."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.MINIMAX_API_KEY
        self.api_base = settings.MINIMAX_API_BASE
        self.enabled = bool(self.api_key)

    async def _call_ai(self, prompt: str, system_prompt: str = "") -> str:
        """调用AI接口."""
        if not self.enabled:
            return json.dumps({"error": "AI功能未启用，请配置MINIMAX_API_KEY"})

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
                    or "你是一位专业的IPO审计专家，擅长识别财务风险和合规问题。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.api_base}/text/chatcompletion_pro", headers=headers, json=payload
                )
                response.raise_for_status()
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _ai_json_call(
        self,
        prompt: str,
        default: Any,
        *,
        method: str,
        context: str,
        coerce_list: bool = False,
    ) -> Any:
        """调 AI + 解析 JSON 响应 + 失败兜底。

        4 个公开方法 (analyze_risk_level / detect_anomalies /
        generate_audit_program / analyze_regulatory_compliance) 共用这个 helper,
        行为完全等价: 解析失败 logger.exception + 返回 default。

        Args:
            prompt: 拼接好的用户 prompt。
            default: 解析失败时返回的兜底 (dict / list / 任意)。
            method: 公开方法名, 用于日志前缀 (例 ``"analyze_risk_level"``)。
            context: 上下文信息 (行业/条数等), 附加到日志便于排查。
            coerce_list: True 时若 AI 返回 dict (单对象) 强制包成 [dict];
                False 时若 AI 返回非 list 直接返回 default。
        """
        response = await self._call_ai(prompt)
        try:
            result = json.loads(response)
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI %s 响应解析失败 (%s): %s", method, context, exc)
            return default
        if coerce_list:
            return result if isinstance(result, list) else [result]
        if isinstance(default, list) and not isinstance(result, list):
            return default
        return result

    async def analyze_risk_level(self, financial_data: Dict, industry: str) -> Dict:
        """分析风险等级."""
        prompt = f"""
作为IPO审计专家，请分析以下财务数据的风险等级：

行业: {industry}
总资产: {financial_data.get("total_assets") or 0:,.2f}
营业收入: {financial_data.get("revenue") or 0:,.2f}
净利润: {financial_data.get("net_profit") or 0:,.2f}
毛利率: {financial_data.get("gross_margin") or 0:.2f}%
应收账款周转天数: {financial_data.get("receivable_turnover_days", 0)}
存货周转天数: {financial_data.get("inventory_turnover_days", 0)}

请分析：
1. 风险等级（高/中/低）
2. 主要风险点（最多5条）
3. 监管关注建议

返回JSON格式：
{{"risk_level": "高/中/低", "risk_points": [...], "recommendations": [...]}}
"""
        return await self._ai_json_call(
            prompt,
            {
                "risk_level": "中",
                "risk_points": ["AI响应解析失败"],
                "recommendations": ["请检查API配置"],
            },
            method="analyze_risk_level",
            context=f"industry={industry}",
        )

    async def detect_anomalies(
        self, account_balances: List[Dict], chronological_accounts: List[Dict]
    ) -> List[Dict]:
        """检测财务异常."""
        prompt = f"""
作为IPO审计专家，请分析以下财务数据中的异常情况：

科目余额数据（前30个）：
{json.dumps(account_balances[:30], ensure_ascii=False, indent=2)}

序时账（前100条）：
{json.dumps(chronological_accounts[:100], ensure_ascii=False, indent=2)}

请识别：
1. 异常波动科目（与行业平均差异超过30%）
2. 期末突击调账迹象
3. 关联交易异常
4. 可疑交易模式

返回JSON数组格式：
[{{"account_code": "...", "anomaly_type": "...", "description": "...", "severity": "高/中/低"}}]
"""
        return await self._ai_json_call(
            prompt,
            [],
            method="detect_anomalies",
            context=f"account_count={len(account_balances)}",
            coerce_list=True,
        )

    async def generate_audit_program(
        self, risk_points: List[Dict], regulatory_cases: List[Dict]
    ) -> List[Dict]:
        """生成审计程序建议."""
        prompt = f"""
作为IPO审计专家，请根据风险点和监管案例生成详细审计程序：

风险点：
{json.dumps(risk_points, ensure_ascii=False, indent=2)}

相关监管案例：
{json.dumps(regulatory_cases[:5], ensure_ascii=False, indent=2)}

请生成5-10条具体审计程序，每条包含：
1. 程序名称
2. 具体执行步骤
3. 所需审计证据
4. 预期发现

返回JSON数组格式：
[{{"program_name": "...", "steps": [...], "evidence": [...], "expected_findings": [...]}}]
"""
        return await self._ai_json_call(
            prompt,
            [],
            method="generate_audit_program",
            context=f"risk_points={len(risk_points)}",
        )

    async def analyze_regulatory_compliance(self, company_info: Dict, industry: str) -> Dict:
        """分析监管合规性."""
        prompt = f"""
请分析{industry}行业公司IPO过程中的常见监管关注点：

公司信息：
- 名称：{company_info.get("name", "")}
- 行业：{industry}
- 主营业务：{company_info.get("main_business", "")}
- 营收规模：{company_info.get("revenue") or 0:,.2f}

请识别：
1. 该行业IPO最常见被质疑的问题
2. 监管问询重点关注领域
3. 合规建议

返回JSON格式：
{{"common_issues": [...], "focus_areas": [...], "compliance_suggestions": [...]}}
"""
        return await self._ai_json_call(
            prompt,
            {},
            method="analyze_regulatory_compliance",
            context=f"company={company_info.get('name', '')} industry={industry}",
        )


class RiskIdentifier:
    """风险识别器（规则引擎）."""

    @staticmethod
    def identify_revenue_recognition_risk(account_balances: List[Dict]) -> List[Dict]:
        """识别收入确认风险."""
        risks = []
        # P0 正确性: 只把主营业务收入 (5001) / 其他业务收入 (5051) 算收入, 排除 5401/5501 等费用类
        _revenue_prefixes = ("5001", "5002", "5051", "5301")
        revenue_accounts = [
            ab for ab in account_balances
            if any(str(ab.get("account_code", "")).startswith(p) for p in _revenue_prefixes)
        ]

        for ab in revenue_accounts:
            ending = ab.get("ending_balance") or 0
            credit = ab.get("credit_amount") or 0

            # 期末突然大量确认收入
            if credit > 5000000 and ending > 0:
                risks.append(
                    {
                        "risk_type": "收入确认",
                        "account_code": ab.get("account_code"),
                        "account_name": ab.get("account_name"),
                        "risk_level": "高",
                        "description": f"期末大额确认收入{credit:,.2f}，可能存在提前确认",
                        "indicator": "期末突击确认收入",
                    }
                )

            # 收入与应收账款不匹配
            if ending > credit * 2:
                risks.append(
                    {
                        "risk_type": "收入确认",
                        "account_code": ab.get("account_code"),
                        "account_name": ab.get("account_name"),
                        "risk_level": "中",
                        "description": f"应收账款({ending:,.2f})远超收入({credit:,.2f})，需关注收入确认时点",
                        "indicator": "应收账款异常偏高",
                    }
                )

        return risks

    @staticmethod
    def identify_related_party_risk(chronological_accounts: List[Dict]) -> List[Dict]:
        """识别关联交易风险."""
        risks = []
        keywords = ["关联方", "关联公司", "实际控制人", "一致行动", "兄弟公司"]

        for idx, ca in enumerate(chronological_accounts):
            summary = ca.get("summary", "")
            for kw in keywords:
                if kw in summary:
                    risks.append(
                        {
                            "risk_type": "关联交易",
                            "voucher_no": ca.get("voucher_no"),
                            "account_name": ca.get("account_name"),
                            "risk_level": "高",
                            "description": f"凭证{ca.get('voucher_no')}包含关联关键词'{kw}'",
                            "indicator": kw,
                        }
                    )
                    break

        return risks[:10]  # 最多返回10条

    @staticmethod
    def identify_goodwill_impairment_risk(account_balances: List[Dict]) -> List[Dict]:
        """识别商誉减值风险."""
        risks = []
        goodwill_accounts = [
            ab for ab in account_balances if "商誉" in str(ab.get("account_name", ""))
        ]

        for ab in goodwill_accounts:
            ending = ab.get("ending_balance") or 0
            if ending > 0:
                # 商誉占资产比例过高
                risks.append(
                    {
                        "risk_type": "商誉减值",
                        "account_code": ab.get("account_code"),
                        "account_name": ab.get("account_name"),
                        "risk_level": "中",
                        "description": f"商誉余额{ending:,.2f}，需关注减值测试",
                        "indicator": "大额商誉",
                    }
                )

        return risks

    @staticmethod
    def identify_inventory_turnover_risk(account_balances: List[Dict], industry: str) -> Dict:
        """识别存货周转风险."""
        inventory_accounts = [
            ab
            for ab in account_balances
            if any(kw in str(ab.get("account_name", "")) for kw in ["存货", "库存商品", "原材料"])
        ]
        total_inventory = sum(ab.get("ending_balance") or 0 for ab in inventory_accounts)

        if total_inventory > 0:
            # 简化计算，实际应用中需要结合销售成本
            risk_level = "中" if total_inventory > 5000000 else "低"
            return {
                "risk_type": "存货周转",
                "total_inventory": total_inventory,
                "risk_level": risk_level,
                "description": f"存货余额{total_inventory:,.2f}，需关注周转情况",
            }
        return {}

    @staticmethod
    def identify_cash_flow_risk(account_balances: List[Dict]) -> Dict:
        """识别现金流风险."""
        cash_accounts = [
            ab
            for ab in account_balances
            if any(kw in str(ab.get("account_name", "")) for kw in ["银行存款", "货币资金"])
        ]
        total_cash = sum(ab.get("ending_balance", 0) for ab in cash_accounts)

        # 查找短期借款
        short_borrowing = sum(
            ab.get("ending_balance", 0)
            for ab in account_balances
            if "短期借款" in str(ab.get("account_name", ""))
        )

        if total_cash < short_borrowing:
            return {
                "risk_type": "现金流",
                "cash_balance": total_cash,
                "short_borrowing": short_borrowing,
                "risk_level": "高",
                "description": "货币资金不足以覆盖短期借款，存在偿债风险",
            }
        elif total_cash < 1000000:
            return {
                "risk_type": "现金流",
                "cash_balance": total_cash,
                "risk_level": "中",
                "description": "货币资金余额较低，需关注流动性",
            }
        return {"risk_type": "现金流", "risk_level": "低", "description": "现金流状况正常"}


class AnomalyDetector:
    """数据异常检测器."""

    @staticmethod
    def detect_round_number_anomalies(account_balances: List[Dict]) -> List[Dict]:
        """检测整数金额异常."""
        anomalies = []
        for ab in account_balances:
            ending = ab.get("ending_balance") or 0
            if ending != 0 and ending % 10000 == 0 and ending > 100000:
                anomalies.append(
                    {
                        "account_code": ab.get("account_code"),
                        "account_name": ab.get("account_name"),
                        "amount": ending,
                        "anomaly_type": "整数金额",
                        "description": f"期末余额{ending:,.2f}为整万，可能存在估计或调节",
                    }
                )
        return anomalies

    @staticmethod
    def detect_balance_direction_anomalies(account_balances: List[Dict]) -> List[Dict]:
        """检测余额方向异常."""
        anomalies = []
        for ab in account_balances:
            direction = ab.get("balance_direction", "")
            ending = ab.get("ending_balance", 0)

            if direction == "借" and ending < 0:
                anomalies.append(
                    {
                        "account_code": ab.get("account_code"),
                        "account_name": ab.get("account_name"),
                        "amount": ending,
                        "anomaly_type": "余额方向异常",
                        "description": "借方科目出现贷方余额",
                    }
                )
            elif direction == "贷" and ending > 0:
                anomalies.append(
                    {
                        "account_code": ab.get("account_code"),
                        "account_name": ab.get("account_name"),
                        "amount": ending,
                        "anomaly_type": "余额方向异常",
                        "description": "贷方科目出现借方余额",
                    }
                )
        return anomalies

    @staticmethod
    def detect_zero_activity_anomalies(account_balances: List[Dict]) -> List[Dict]:
        """检测期末有余额但无发生额."""
        anomalies = []
        for ab in account_balances:
            ending = ab.get("ending_balance", 0)
            debit = ab.get("debit_amount", 0)
            credit = ab.get("credit_amount", 0)

            if ending != 0 and debit == 0 and credit == 0:
                anomalies.append(
                    {
                        "account_code": ab.get("account_code"),
                        "account_name": ab.get("account_name"),
                        "amount": ending,
                        "anomaly_type": "无发生额但有余额",
                        "description": "本期无发生额但期末有余额，可能存在账龄问题",
                    }
                )
        return anomalies

    @staticmethod
    def detect_concentration_risk(account_balances: List[Dict], top_n: int = 5) -> Dict:
        """检测客户/供应商集中度风险."""
        # 按余额排序，检测集中度
        sorted_accounts = sorted(
            account_balances, key=lambda x: abs(x.get("ending_balance", 0)), reverse=True
        )
        top_accounts = sorted_accounts[:top_n]
        total_balance = sum(ab.get("ending_balance", 0) for ab in account_balances)

        if top_accounts and total_balance > 0:
            top_ratio = sum(ab.get("ending_balance", 0) for ab in top_accounts) / abs(total_balance)
            if top_ratio > 0.8:
                return {
                    "risk_type": "集中度风险",
                    "top_accounts": [
                        {
                            "code": a.get("account_code"),
                            "name": a.get("account_name"),
                            "balance": a.get("ending_balance"),
                        }
                        for a in top_accounts
                    ],
                    "concentration_ratio": top_ratio,
                    "risk_level": "高" if top_ratio > 0.9 else "中",
                    "description": f"前{top_n}名合计占总余额{top_ratio:.1%}，集中度较高",
                }
        return {}
