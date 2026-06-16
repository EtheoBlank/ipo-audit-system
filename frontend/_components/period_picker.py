"""期末日期 / 报告期选择器.

5+ 个 pages_*.py 写过:
    period_end = st.text_input("期末日期 YYYY-MM-DD", value=str(date.today()))
    period_end = st.date_input("报告期截止日", value=default_pe, key="...")
提供 2 种封装: text (string YYYY-MM-DD) + date (datetime.date).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import streamlit as st


def period_end_text(
    label: str = "期末日期 YYYY-MM-DD",
    *,
    default: Optional[str] = None,
    key: Optional[str] = None,
) -> Optional[str]:
    """文本式期末日期 (YYYY-MM-DD), 返回字符串或空 (用户未填)."""
    if default is None:
        default = str(date.today())
    return st.text_input(label, value=default, key=key) or None


def period_end_date(
    label: str = "报告期截止日",
    *,
    default: Optional[date] = None,
    key: Optional[str] = None,
) -> date:
    """日期式期末日期 (返回 date)."""
    if default is None:
        default = date.today()
    return st.date_input(label, value=default, key=key)


def is_valid_period(period: Optional[str]) -> bool:
    """返回 True 当 period 形如 YYYY-MM-DD 且长度 >= 8.

    account_audit 原来写过 `if not period_end or len(period_end) < 8: st.error(...)`."""
    return bool(period) and len(period) >= 8 and period[4:5] == "-"


__all__ = ["period_end_text", "period_end_date", "is_valid_period"]
