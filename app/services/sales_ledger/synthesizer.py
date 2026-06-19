"""Sales-ledger synthesizer.

Turns the raw text extracted from user documents (contracts, invoices,
shipments, customs declarations, etc.) into structured sales records using
DeepSeek. The system prompt enforces a strict JSON schema so the output can be
parsed deterministically.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)


# ============================================================
#  Pydantic schema for AI-synthesized sales rows (P0-13)
# ============================================================
#
# 背景: 老实现把 DeepSeek 返回的 dict 直接塞进 SalesRecord (Float / Date 列),
#       一旦 AI 返回 "abc" / 字符串日期 等异常值, 就在 db.add(new) 之后才
#       触发 IntegrityError, 整批回滚, 审计师拿不到数据.
# 修复: 在落入 DB 前用 Pydantic v2 schema (model_validate) 校验, 失败行
#       收集到 errors 列表, API 层只对 valid_rows 落库 + 返 errors 给前端.
class SynthesizedRow(BaseModel):
    """DeepSeek 抽取出来的单行销售明细 — 进入 DB 前的强类型闸门.

    字段名兼容 SYNTHESIS_SYSTEM prompt 输出, 同时容忍原 synthesizer 的别名.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    contract_no: Optional[str] = None
    customer_name: Optional[str] = None
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    invoice_no: Optional[str] = None
    currency: Optional[str] = "CNY"
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    gross_amount: Optional[float] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    revenue_amount: float = 0.0  # 必填 (后续 coerce 会兜底再算一次)
    cost_amount: Optional[float] = None
    shipping_fee: Optional[float] = None
    customs_fee: Optional[float] = None
    other_direct_fee: Optional[float] = None
    return_amount: Optional[float] = None
    discount_amount: Optional[float] = None
    rebate_amount: Optional[float] = None
    ship_date: Optional[date] = None
    receipt_date: Optional[date] = None
    revenue_confirm_date: Optional[date] = None
    source_doc: Optional[str] = None
    source: Optional[str] = None


@dataclass
class SynthesizeError:
    """单行校验失败的明细 — 送给前端 '待复核' 列表."""

    idx: int
    row_summary: str
    error: str


@dataclass
class SynthesizeResult:
    """synthesize() 完整返回值 — API 层据此分流: valid 入 DB, errors 仅记录."""

    records: list[dict[str, Any]] = field(default_factory=list)
    errors: list[SynthesizeError] = field(default_factory=list)

    @property
    def valid_count(self) -> int:
        return len(self.records)

    @property
    def error_count(self) -> int:
        return len(self.errors)


SYNTHESIS_SYSTEM = """你是 IPO 审计项目的资深审计师助理，擅长从销售合同/发票/发货单/报关单/对账单等
散乱文档中抽取结构化的销售明细。请严格按 JSON 输出（不要任何额外文字、不要 markdown 围栏）：

{
  "records": [
    {
      "contract_no":   "合同号（找不到填 null）",
      "customer_name": "客户名称（必填）",
      "product_code":  "销售产品编号（必填，能与收发存对账）",
      "product_name":  "产品名称（找不到填 null）",
      "invoice_no":    "销售发票号（找不到填 null）",
      "currency":      "币种（默认 CNY，找不到填 CNY）",
      "tax_rate":      税率（数字，如 0.13，找不到填 0）,
      "tax_amount":    税额（数字，找不到填 0）,
      "gross_amount":  价税合计（数字，找不到填 0）,
      "quantity":      数量（数字，找不到填 0）,
      "unit_price":    不含税单价（数字，找不到填 0）,
      "revenue_amount": 不含税收入金额（数字，找不到填 0）,
      "cost_amount":   对应成本金额（数字，找不到填 0）,
      "shipping_fee":  运费（数字，找不到填 0）,
      "customs_fee":   报关费（数字，找不到填 0）,
      "other_direct_fee": 其他直接销售费用（数字，找不到填 0）,
      "return_amount": 退货冲减金额（数字，找不到填 0）,
      "discount_amount": 折扣折让金额（数字，找不到填 0）,
      "rebate_amount": 销售返利金额（数字，找不到填 0）,
      "ship_date":     "YYYY-MM-DD",        // 发货时间
      "receipt_date":  "YYYY-MM-DD",        // 客户签收/验收日期（收入确认时点的关键证据）
      "revenue_confirm_date": "YYYY-MM-DD", // 收入确认时间
      "source_doc":    "源文档名或关键引用片段"
    }
  ]
}

注意：
1. 一份文档里可能有多个销售明细行，必须逐行列出，不要汇总。
2. 同一份文档中重复出现的行请去重。
3. 如果文档完全没有销售信息，返回 {"records": []}。
4. 日期必须使用 YYYY-MM-DD 格式；金额保留两位小数（如 1234.50）。
5. 价税合计 = 不含税收入 + 税额；如果文档只给了"价税合计"而没有单独的不含税金额，
   请把 revenue_amount 设为价税合计 / (1 + tax_rate)，并填上税额。
"""


class SalesLedgerSynthesizer:
    """Calls DeepSeek per document and de-duplicates across documents."""

    # Safety net to prevent oversized prompts.
    MAX_CHARS_PER_DOC = 24_000

    def __init__(self, client: DeepSeekClient):
        self.client = client

    async def synthesize(
        self,
        documents: Iterable[Any],
        *,
        extra_user_hint: str = "",
    ) -> SynthesizeResult:
        """Run synthesis across an iterable of (id, filename, raw_text).

        P0-13: 用 Pydantic ``SynthesizedRow`` schema 校验每一行 — 失败行收
        集到 ``result.errors``, valid 行收 ``result.records``. 整批不再因
        单行异常值 (如 revenue_amount="abc") 触发 IntegrityError.
        """
        records: list[dict[str, Any]] = []
        errors: list[SynthesizeError] = []
        global_idx = 0  # 用于错误回传 (前端可定位 '第 N 行失败')
        for doc in documents:
            doc_id = getattr(doc, "id", None)
            filename = getattr(doc, "filename", "unknown")
            raw_text = (getattr(doc, "raw_text", "") or "").strip()
            if not raw_text:
                logger.info("Skip empty doc id=%s filename=%s", doc_id, filename)
                continue
            chunk = self._truncate(raw_text)
            user_msg = (
                f"以下是一份销售相关文档的内容（文件名：{filename}）。"
                f"请从中抽取销售明细并按指定 JSON 格式输出。\n\n"
                f"{chunk}"
            )
            if extra_user_hint:
                user_msg += f"\n\n额外提示：{extra_user_hint}"

            try:
                result = await self.client.chat_json(
                    system=SYNTHESIS_SYSTEM,
                    user=user_msg,
                )
            except DeepSeekError as exc:
                logger.warning("DeepSeek failed on %s: %s", filename, exc)
                # We continue with other documents so a single failure doesn't
                # abort the whole synthesis.
                continue
            except json.JSONDecodeError as exc:
                logger.warning("DeepSeek returned non-JSON for %s: %s", filename, exc)
                continue

            batch = self._extract_records(result)
            for rec in batch:
                rec.setdefault("document_id", doc_id)
                rec.setdefault("source", filename)
                # P0-13: 强类型闸门 — 用 Pydantic v2 model_validate 校验
                try:
                    SynthesizedRow.model_validate(rec)
                except ValidationError as exc:
                    logger.warning(
                        "synthesized row %d 校验失败, 跳过 (file=%s): %s",
                        global_idx, filename, exc,
                    )
                    errors.append(
                        SynthesizeError(
                            idx=global_idx,
                            row_summary=str(rec)[:200],
                            error=str(exc),
                        )
                    )
                    global_idx += 1
                    continue
                records.append(rec)
                global_idx += 1

        unique = self._dedupe(records)
        # dedupe 后, 错误索引仍按 global_idx 保留 — 让前端知道哪一行被丢弃
        return SynthesizeResult(records=unique, errors=errors)

    # --- helpers --------------------------------------------------------

    @staticmethod
    def _extract_records(payload: Any) -> list[dict[str, Any]]:
        """Forgive slightly-off JSON shapes: a list, or {"records": [...]},
        or a single record dict."""
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            if "records" in payload and isinstance(payload["records"], list):
                return [r for r in payload["records"] if isinstance(r, dict)]
            # Sometimes the model returns a single record
            if {"customer_name", "product_code"} <= payload.keys():
                return [payload]
        return []

    @classmethod
    def _dedupe(cls, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate on (contract_no, product_code) keeping the first hit."""
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        for rec in records:
            key = (
                str(rec.get("contract_no") or "").strip(),
                str(rec.get("product_code") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(rec)
        return unique

    def _truncate(self, text: str) -> str:
        if len(text) <= self.MAX_CHARS_PER_DOC:
            return text
        head = text[: self.MAX_CHARS_PER_DOC]
        return head + "\n\n…(以下内容因超长被截断)…"

    # --- utilities exposed for tests / API -----------------------------

    @staticmethod
    def coerce_numbers(rec: dict[str, Any]) -> dict[str, float]:
        revenue = _parse_float(rec.get("revenue_amount"))
        tax_rate = _parse_float(rec.get("tax_rate"))
        tax_amount = _parse_float(rec.get("tax_amount"))
        gross = _parse_float(rec.get("gross_amount"))
        # Heuristic: if revenue is 0 but gross is given, back-solve revenue
        if revenue == 0 and gross > 0 and tax_rate > 0:
            revenue = round(gross / (1 + tax_rate), 2)
            if tax_amount == 0:
                tax_amount = round(gross - revenue, 2)
        # And vice versa: if gross is 0 but revenue + tax_rate are given
        if gross == 0 and revenue > 0 and tax_rate > 0:
            tax_amount = tax_amount or round(revenue * tax_rate, 2)
            gross = round(revenue + tax_amount, 2)
        return {
            "quantity": _parse_float(rec.get("quantity")),
            "unit_price": _parse_float(rec.get("unit_price")),
            "revenue_amount": revenue,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "gross_amount": gross,
            "cost_amount": _parse_float(rec.get("cost_amount")),
            "shipping_fee": _parse_float(rec.get("shipping_fee")),
            "customs_fee": _parse_float(rec.get("customs_fee")),
            "other_direct_fee": _parse_float(rec.get("other_direct_fee")),
            "return_amount": _parse_float(rec.get("return_amount")),
            "discount_amount": _parse_float(rec.get("discount_amount")),
            "rebate_amount": _parse_float(rec.get("rebate_amount")),
            "confirmation_diff": _parse_float(rec.get("confirmation_diff")),
        }

    @staticmethod
    def coerce_dates(
        rec: dict[str, Any],
    ) -> tuple[Optional[datetime], Optional[datetime], Optional[datetime]]:
        ship = _parse_date(rec.get("ship_date"))
        receipt = _parse_date(rec.get("receipt_date"))
        confirm = _parse_date(rec.get("revenue_confirm_date"))
        return ship, receipt, confirm


_DATE_RX = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})")


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    m = _DATE_RX.search(s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d)
    except ValueError:
        return None


def _parse_float(value: Any) -> float:
    # P0 正确性修复: 先尝试直接 float 解析, 失败再用兜底
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "").replace("元", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0
