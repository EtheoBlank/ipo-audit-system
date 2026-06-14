"""Confirmation response processor.

流程:
  1) 上传回函照片 (image / pdf) → 保存到 OUTPUT_DIR
  2) OCR 提取文本 (复用 contract_analysis.ocr.ContractOCR)
  3) DeepSeek AI 解析回函内容
       - 是否相符 (match/partial/mismatch/reject)
       - 确认金额
       - 各函证项的明细
       - 差异原因
  4) 回填到 ConfirmationResponse
  5) 更新 ConfirmationItem.status

Notes:
  - 银行回函结构化强，AI 解析更准
  - 客户 / 供应商回函常手写, OCR 难度大；尽量容错（正则兜底）
  - 拒函 (信息不符) 必须人工确认后才能关单
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.services.contract_analysis.ocr import ContractOCR, OCRError
from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)


class ResponseParseError(RuntimeError):
    """Raised when the response cannot be parsed."""


# ---- Prompt -----------------------------------------------------------


SYS_PROMPT_RESPONSE = (
    "你是一名审计助手,负责从回函扫描件/照片的 OCR 文本中提取结构化信息。"
    "回函可能为银行 / 客户 / 供应商,函证项包括余额/交易额/票据/合同条款等。"
    "请提取下列字段并**只返回 JSON**,结构如下:\n"
    "{\n"
    '  "response_status": "match | partial | mismatch | reject | unclear",\n'
    '  "amount_confirmed": <对方确认金额数值,若未填或无法识别填 0>,\n'
    '  "amount_difference": <差异 = 对方确认 - 我方账面,若无差异填 0>,\n'
    '  "difference_reason": "差异原因(若有),无填 null",\n'
    '  "received_date": "YYYY-MM-DD(回函上的日期,若有,无填 null)",\n'
    '  "response_method": "纸质原件/扫描件/电邮/传真(根据上下文判断,默认扫描件)",\n'
    '  "subjects_detail": {\n'
    '    "存款余额": {"confirmed": <数值|null>, "difference": <数值|null>, "note": "..."},\n'
    '    "贷款余额": {"confirmed": ..., "difference": ..., "note": "..."},\n'
    '    "本期销售/采购额": {"confirmed": ..., "difference": ..., "note": "..."},\n'
    '    "已背书票据": {"confirmed": ..., "difference": ..., "note": "..."}\n'
    "  },\n"
    '  "response_summary": "回函关键结论(一句话)",\n'
    '  "signer": "签章人(若有)"\n'
    "}\n"
    "若 OCR 文本质量极差无法识别,response_status 填 'unclear' 并尽可能提取金额。"
)


# ---- dataclass -------------------------------------------------------


@dataclass
class ParsedResponse:
    response_status: str = "unclear"  # match / partial / mismatch / reject / unclear
    amount_confirmed: float = 0.0
    amount_difference: float = 0.0
    difference_reason: Optional[str] = None
    received_date: Optional[datetime] = None
    response_method: str = "扫描件"
    subjects_detail: dict[str, Any] = field(default_factory=dict)
    response_summary: str = ""
    signer: str = ""
    ai_extracted: dict[str, Any] = field(default_factory=dict)


# ---- processor -------------------------------------------------------


class ConfirmationResponseProcessor:
    """回函照片 OCR + AI 解析处理器。"""

    def __init__(
        self,
        output_dir: Path,
        client: Optional[DeepSeekClient] = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = client

    # ---- 保存上传 -----------------------------------------------------

    def save_upload(self, file_bytes: bytes, filename: str) -> Path:
        safe = re.sub(r"[^\w\-_.]", "_", filename)[:120]
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        path = self.output_dir / f"resp_{ts}_{safe}"
        path.write_bytes(file_bytes)
        return path

    # ---- OCR ----------------------------------------------------------

    def ocr(self, file_path: Path, filename: str) -> tuple[str, str]:
        try:
            engine, text = ContractOCR.run(file_path, filename)
            return engine, text
        except OCRError as exc:
            raise ResponseParseError(f"回函 OCR 失败: {exc}") from exc

    # ---- AI parse -----------------------------------------------------

    async def parse(self, ocr_text: str) -> ParsedResponse:
        if not ocr_text or not ocr_text.strip():
            return ParsedResponse(response_status="unclear", response_summary="OCR 文本为空")

        result = ParsedResponse()
        if not (self.client and self.client.is_configured):
            # 退化：正则提取金额
            result = _heuristic_parse(ocr_text)
            result.ai_extracted = {"mode": "heuristic", "raw": ocr_text[:2000]}
            return result

        try:
            data = await self.client.chat_json(
                system=SYS_PROMPT_RESPONSE,
                user=ocr_text[:8000],
                temperature=0.0,
            )
        except DeepSeekError as exc:
            logger.warning("回函 AI parse failed: %s, fallback to heuristic", exc)
            return _heuristic_parse(ocr_text)

        result.response_status = str(data.get("response_status") or "unclear").strip()
        result.amount_confirmed = _to_float(data.get("amount_confirmed")) or 0.0
        result.amount_difference = _to_float(data.get("amount_difference")) or 0.0
        result.difference_reason = (data.get("difference_reason") or "") or None
        rd = str(data.get("received_date") or "").strip()
        if rd:
            try:
                result.received_date = datetime.fromisoformat(rd[:10])
            except ValueError:
                pass
        result.response_method = str(data.get("response_method") or "扫描件")
        result.subjects_detail = data.get("subjects_detail") or {}
        result.response_summary = str(data.get("response_summary") or "")
        result.signer = str(data.get("signer") or "")
        result.ai_extracted = data
        return result

    # ---- 一站式 -------------------------------------------------------

    async def process_upload(
        self,
        file_bytes: bytes,
        filename: str,
        expected_book_amount: float = 0.0,
    ) -> tuple[Path, str, str, ParsedResponse]:
        """返回 (saved_path, ocr_engine, ocr_text, parsed)."""
        path = self.save_upload(file_bytes, filename)
        engine, text = self.ocr(path, filename)
        parsed = await self.parse(text)
        if not parsed.amount_difference and expected_book_amount:
            parsed.amount_difference = round(parsed.amount_confirmed - expected_book_amount, 2)
        return path, engine, text, parsed


# ---- helpers ---------------------------------------------------------


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _heuristic_parse(ocr_text: str) -> ParsedResponse:
    """Heuristic fallback for回函解析: P0 修复后只返回 unclear, 不再自动 amount_confirmed.

    原因: 取 max(amounts) 几乎一定错 — 银行回函含活期/定期/贷款/承兑/保函等多个数,
    最大的通常是合计,不是『存款确认金额』。审计可信度受损。
    """
    result = ParsedResponse()
    # 始终返回 unclear, 强制人工核对
    result.response_status = "unclear"
    result.amount_confirmed = 0.0
    result.amount_difference = 0.0

    # 仅做关键字提取, 让前端展示
    if re.search(r"(信息证明无误|相符|核对无误|数据正确|无误)", ocr_text):
        result.response_summary = "[启发式] 检测到『相符』关键字, 请人工核对并填写确认金额"
    elif re.search(r"(信息不符|不符|有误|与.*不一致|差异)", ocr_text):
        result.response_summary = "[启发式] 检测到『不符』关键字, 请人工核对并填写确认金额"
    elif re.search(r"(拒函|拒绝回函|不予回复)", ocr_text):
        result.response_summary = "[启发式] 检测到『拒函』关键字, 请人工核对"
    else:
        result.response_summary = "[启发式] OCR 已完成, 请人工核对并填写确认金额"
    return result
