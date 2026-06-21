"""Confirmation Excel exporter.

Generates a multi-sheet workbook:
  - 函证统计表       (Confirmation items grouped by party_type)
  - 发函清单         (Letters with sent_date, status)
  - 回函情况         (Response status, confirmed amount, difference)
  - 回函差异分析     (Items with mismatched responses)
  - 函证汇总         (Summary by party_type)
  - 未回函催办       (Items past expected reply date with no response)
"""

from __future__ import annotations

import io
import json
import logging
from collections import defaultdict
from typing import Any, Iterable

import pandas as pd

from app.models.db_models import (
    ConfirmationItem,
    ConfirmationLetter,
    ConfirmationResponse,
    ITEM_STATUS_LABELS,
    ITEM_STATUS_NO_REPLY,
    ITEM_STATUS_SENT,
    PARTY_TYPE_LABELS,
    RESPONSE_STATUS_LABELS,
)

logger = logging.getLogger(__name__)


class ConfirmationExporter:
    @staticmethod
    def _items_df(items: Iterable[Any]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for it in items:
            try:
                subjects = json.loads(it.subject_matters or "[]")
                if not isinstance(subjects, list):
                    subjects = [str(subjects)]
            except Exception:
                # round 36 P1: 之前静默赋空, 函证项一栏空着, 审计师误以为确实没函证项
                logger.exception(
                    "confirmation excel_exporter: subject_matters 解析失败 item_id=%s, 退化为空",
                    getattr(it, "id", None),
                )
                subjects = []
            rows.append(
                {
                    "编号": it.id,
                    "函证方类型": PARTY_TYPE_LABELS.get(it.party_type, it.party_type),
                    "对方名称": it.party_name,
                    "对方编号": it.party_id or "",
                    "我方科目": it.account_name or "",
                    "我方科目编号": it.account_code or "",
                    "账面余额": it.book_balance or 0.0,
                    "函证金额": it.total_confirm_amount or 0.0,
                    "函证项": "；".join(subjects),
                    "选样方式": it.selection_method,
                    "选样原因": it.selection_reason or "",
                    "重要性": it.importance,
                    "状态": ITEM_STATUS_LABELS.get(it.status, it.status),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _letters_df(letters: Iterable[Any]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for lt in letters:
            rows.append(
                {
                    "函证编号": lt.letter_no,
                    "对方": getattr(lt, "item", None) and lt.item.party_name or "",
                    "类型": PARTY_TYPE_LABELS.get(lt.letter_type, lt.letter_type),
                    "发函日期": lt.sent_date.strftime("%Y-%m-%d") if lt.sent_date else "",
                    "发函方式": lt.sent_method,
                    "发函人": lt.sent_by or "",
                    "收件人": lt.recipient or "",
                    "快递单号": lt.courier_no or "",
                    "预计回函日": lt.expected_reply_date.strftime("%Y-%m-%d")
                    if lt.expected_reply_date
                    else "",
                    "催办次数": lt.reminder_count,
                    "状态": lt.letter_status,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _responses_df(responses: Iterable[Any]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for r in responses:
            letter = r.letter
            item = letter.item if letter else None
            rows.append(
                {
                    "函证编号": letter.letter_no if letter else "",
                    "对方": item.party_name if item else "",
                    "类型": PARTY_TYPE_LABELS.get(item.party_type, item.party_type) if item else "",
                    "账面余额": item.book_balance if item else 0.0,
                    "回函日期": r.received_date.strftime("%Y-%m-%d") if r.received_date else "",
                    "回函方式": r.response_method,
                    "回函状态": RESPONSE_STATUS_LABELS.get(r.response_status, r.response_status),
                    "对方确认金额": r.amount_confirmed or 0.0,
                    "差异金额": r.amount_difference or 0.0,
                    "差异原因": r.difference_reason or "",
                    "已人工核对": "是" if r.is_manually_confirmed else "否",
                    "核对人": r.confirmed_by or "",
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _differences_df(responses: Iterable[Any]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for r in responses:
            if abs(r.amount_difference or 0) < 0.01:
                continue
            letter = r.letter
            item = letter.item if letter else None
            rows.append(
                {
                    "函证编号": letter.letter_no if letter else "",
                    "对方": item.party_name if item else "",
                    "类型": PARTY_TYPE_LABELS.get(item.party_type, item.party_type) if item else "",
                    "账面余额": item.book_balance if item else 0.0,
                    "对方确认金额": r.amount_confirmed or 0.0,
                    "差异金额": r.amount_difference or 0.0,
                    "差异率(%)": round(
                        (r.amount_difference / item.book_balance * 100)
                        if item and item.book_balance
                        else 0,
                        2,
                    ),
                    "差异原因": r.difference_reason or "",
                    "回函状态": RESPONSE_STATUS_LABELS.get(r.response_status, r.response_status),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _summary_df(
        items: Iterable[Any], letters: Iterable[Any], responses: Iterable[Any]
    ) -> pd.DataFrame:
        items_list = list(items)
        letters_list = list(letters)
        responses_list = list(responses)

        by_type: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "函证对象数": 0,
                "账面金额合计": 0.0,
                "已发函数": 0,
                "已回函数": 0,
                "相符": 0,
                "部分相符": 0,
                "不符": 0,
                "拒函": 0,
                "差异金额合计": 0.0,
            }
        )
        for it in items_list:
            d = by_type[PARTY_TYPE_LABELS.get(it.party_type, it.party_type)]
            d["函证对象数"] += 1
            d["账面金额合计"] += it.book_balance or 0
        for lt in letters_list:
            item = lt.item
            if not item:
                continue
            d = by_type[PARTY_TYPE_LABELS.get(item.party_type, item.party_type)]
            d["已发函数"] += 1
        for r in responses_list:
            letter = r.letter
            item = letter.item if letter else None
            if not item:
                continue
            d = by_type[PARTY_TYPE_LABELS.get(item.party_type, item.party_type)]
            d["已回函数"] += 1
            d["差异金额合计"] += r.amount_difference or 0
            d[RESPONSE_STATUS_LABELS.get(r.response_status, r.response_status)] += 1

        rows = []
        for k, v in by_type.items():
            total = v["已发函数"]
            v["回函率(%)"] = round(v["已回函数"] / total * 100, 2) if total else 0
            v["账面金额合计"] = round(v["账面金额合计"], 2)
            v["差异金额合计"] = round(v["差异金额合计"], 2)
            v["类型"] = k
            rows.append(v)

        return pd.DataFrame(rows)[
            [
                "类型",
                "函证对象数",
                "账面金额合计",
                "已发函数",
                "已回函数",
                "回函率(%)",
                "相符",
                "部分相符",
                "不符",
                "拒函",
                "差异金额合计",
            ]
        ]

    @staticmethod
    def _pending_df(items: Iterable[Any], letters: Iterable[Any]) -> pd.DataFrame:
        letters_list = list(letters)
        items_list = list(items)
        by_item_id: dict[int, ConfirmationLetter] = {
            lt.item_id: lt for lt in letters_list if lt.item_id
        }

        rows = []
        for it in items_list:
            # P0 修复: 用常量代替硬编码字符串
            if it.status not in (ITEM_STATUS_SENT, ITEM_STATUS_NO_REPLY):
                continue
            lt = by_item_id.get(it.id)
            if lt and lt.letter_status != "sent":
                continue
            rows.append(
                {
                    "对方": it.party_name,
                    "类型": PARTY_TYPE_LABELS.get(it.party_type, it.party_type),
                    "账面余额": it.book_balance or 0.0,
                    "发函日期": lt.sent_date.strftime("%Y-%m-%d") if lt and lt.sent_date else "",
                    "预计回函日": lt.expected_reply_date.strftime("%Y-%m-%d")
                    if lt and lt.expected_reply_date
                    else "",
                    "催办次数": lt.reminder_count if lt else 0,
                    "状态": ITEM_STATUS_LABELS.get(it.status, it.status),
                }
            )
        return pd.DataFrame(rows)

    @classmethod
    def build(
        cls,
        items: list[ConfirmationItem],
        letters: list[ConfirmationLetter],
        responses: list[ConfirmationResponse],
    ) -> bytes:
        """Generate the multi-sheet confirmation workbook."""
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            cls._items_df(items).to_excel(writer, sheet_name="函证统计表", index=False)
            if letters:
                cls._letters_df(letters).to_excel(writer, sheet_name="发函清单", index=False)
            if responses:
                cls._responses_df(responses).to_excel(writer, sheet_name="回函情况", index=False)
                cls._differences_df(responses).to_excel(
                    writer, sheet_name="回函差异分析", index=False
                )
            cls._summary_df(items, letters, responses).to_excel(
                writer, sheet_name="函证汇总", index=False
            )
            pending = cls._pending_df(items, letters)
            if not pending.empty:
                pending.to_excel(writer, sheet_name="未回函催办", index=False)
        return buf.getvalue()
