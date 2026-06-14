"""Contract analyzer — basic 7-field extraction + CAS 14 five-step analysis.

Both prompts emit strict JSON; we lean on DeepSeek's `response_format=json_object`
to keep the parser simple. The same `DeepSeekClient` from the sales-ledger
subpackage is reused — API key stays in settings, never embedded.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)


KEY_POINTS_SYSTEM = """你是 IPO 审计师助理。请从给定的『收入合同』文本中抽取 7 项基础要点，
严格按 JSON 输出（不要任何额外文字、不要 markdown 围栏）：

{
  "contract_no":    "合同编号（找不到填 null）",
  "party_a":        "甲方 / 销售方（公司全称）",
  "party_b":        "乙方 / 采购方（公司全称）",
  "total_amount":   "合同总金额（数字，币种用 currency 字段；找不到填 0）",
  "currency":       "币种（CNY / USD / EUR 等，找不到填 CNY）",
  "effective_period": "合同有效期，如 '2024-01-01 至 2024-12-31'（找不到填 null）",
  "breach_dispute": "违约责任 / 争议解决条款（原文摘录 1-2 句，找不到填 null）",
  "side_letter":    "补充协议 / 附件 / 例外条款（原文摘录，找不到填 null）"
}

只输出 JSON 对象，不要解释。
"""


FIVE_STEP_SYSTEM = """你是按《企业会计准则第 14 号——收入》（CAS 14，2017 修订）进行五步法分析的审计师助理。
给定一份『收入合同』文本，请严格按下面 5 步结构输出 JSON（不要任何额外文字）：

{
  "step1_contract_identification": {
    "exists":            true/false,
    "approval_status":   "是否已取得必要审批（已审批/未审批/未明示）",
    "commercial_substance": "是否具有商业实质（是/否/未明示）",
    "parties":           "合同双方",
    "effective_date":    "合同生效日 YYYY-MM-DD",
    "expiration_date":   "合同到期日 YYYY-MM-DD",
    "notes":             "其他识别要点"
  },
  "step2_contract_modification": {
    "has_modification":   true/false,
    "details":            "是否存在补充协议、变更条款；如何会计处理（单独合同 / 视为终止 + 新合同 / 作为原合同组成部分）",
    "notes":              "原文摘录"
  },
  "step3_performance_obligations": [
    {
      "id":                "PO-1",
      "description":       "履约义务描述",
      "type":              "时点 / 时段",
      "recognition_basis": "确认依据（控制权转移标志，如：客户验收 / 完工进度 / 服务期间）"
    }
  ],
  "step4_transaction_price": {
    "fixed_amount":        0.0,
    "currency":            "CNY",
    "variable_consideration": {"has": true/false, "details": "返利 / 折扣 / 退换货 / 业绩奖罚 等"},
    "significant_financing_component": {"has": true/false, "details": "是否存在重大融资成分（账期 > 1 年等）"},
    "non_cash_consideration": {"has": true/false, "details": "非现金对价"},
    "payable_to_customer": {"has": true/false, "details": "应支付客户对价"},
    "notes":               "其他价格相关说明"
  },
  "step5_recognition": [
    {
      "po_id":              "PO-1",
      "timing":             "时点 / 时段",
      "method":             "如：产出法 / 投入法 / 客户验收法 / 里程碑法 / 到货法 / 完工时点",
      "evidence_required":  "收入确认所需证据（如：客户签收单、验收报告、里程碑确认函）",
      "amount_or_progress": "金额或履约进度说明"
    }
  ],
  "audit_warnings": [
    "审计师需特别关注的条款（如：回购权、保证最低收益、装机容量保证、超长账期、寄售、附条件条款）"
  ]
}

只输出 JSON 对象，不要解释。
"""


_RISK_PATTERNS = [
    ("回购条款", ["回购", "回售", "赎回", "buyback"]),
    ("保证最低收益", ["保底", "最低收益", "保证收益率"]),
    ("寄售/代销", ["寄售", "代销", "consignment"]),
    ("超长账期", None),  # detected by length comparison in caller
    ("可变对价", ["返利", "销售返利", "业绩奖罚", "考核"]),
    ("重大融资成分", ["分期", "融资", "远期", "承兑"]),
    ("争议/仲裁", ["仲裁", "诉讼", "争议"]),
]


class ContractAnalyzer:
    """Run basic 7-field extraction and/or CAS 14 five-step analysis."""

    MAX_CHARS = 28_000

    def __init__(self, client: DeepSeekClient):
        self.client = client

    async def key_points(self, ocr_text: str) -> dict[str, Any]:
        return await self._ask(KEY_POINTS_SYSTEM, ocr_text)

    async def five_step(self, ocr_text: str) -> dict[str, Any]:
        return await self._ask(FIVE_STEP_SYSTEM, ocr_text)

    async def _ask(self, system: str, ocr_text: str) -> dict[str, Any]:
        text = self._truncate(ocr_text)
        user_msg = f"以下是一份收入合同的 OCR 文本，请按要求输出 JSON：\n\n{text}"
        try:
            return await self.client.chat_json(system=system, user=user_msg, max_tokens=4000)
        except DeepSeekError as exc:
            logger.warning("DeepSeek contract analysis failed: %s", exc)
            return {"error": str(exc)}
        except json.JSONDecodeError as exc:
            logger.warning("DeepSeek returned non-JSON: %s", exc)
            return {"error": f"AI 返回非 JSON: {exc}"}

    @staticmethod
    def scan_risks(
        key_points: Optional[dict[str, Any]],
        five_step: Optional[dict[str, Any]],
        ocr_text: str,
    ) -> list[str]:
        """Lightweight local scan for audit risk keywords. Cheap, no LLM call."""
        text = ocr_text or ""
        hits: list[str] = []
        for label, kws in _RISK_PATTERNS:
            if kws is None:
                continue
            for kw in kws:
                if kw.lower() in text.lower():
                    hits.append(label)
                    break
        if five_step:
            tp = five_step.get("step4_transaction_price") or {}
            if (tp.get("variable_consideration") or {}).get("has"):
                hits.append("可变对价（CAS 14 §16-19）")
            if (tp.get("significant_financing_component") or {}).get("has"):
                hits.append("重大融资成分（CAS 14 §17）")
        if key_points:
            side = (key_points.get("side_letter") or "").strip()
            if side and side.lower() != "null":
                hits.append("存在补充协议 / Side Letter")
        # De-dupe preserving order
        seen = set()
        out = []
        for h in hits:
            if h not in seen:
                seen.add(h)
                out.append(h)
        return out

    def _truncate(self, text: str) -> str:
        if len(text) <= self.MAX_CHARS:
            return text
        return text[: self.MAX_CHARS] + "\n\n…(以下内容因超长被截断)…"
