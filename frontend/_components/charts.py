"""统一图表辅助 — st.bar_chart / st.line_chart 包装.

5+ 个 pages_*.py 写过同款 'pd.DataFrame.from_dict(...) + st.bar_chart' 模式.
抽出来后: bar_from_dict / line_from_dict 一行调用.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import streamlit as st


def bar_from_dict(
    data: Dict[str, Any],
    *,
    x_key: str = "类别",
    y_keys: Dict[str, int] = None,
) -> None:
    """把 {label: count} dict 转 DataFrame 并 st.bar_chart — 用于 `by_status` / `by_module` 等.

    行为等价于:
        df = pd.DataFrame([{x_key: k, "数量": v} for k, v in data.items()])
        st.bar_chart(df.set_index(x_key))
    """
    if not data:
        st.info("暂无数据")
        return
    rows = [{x_key: k, "数量": v} for k, v in data.items()]
    df = pd.DataFrame(rows)
    st.bar_chart(df.set_index(x_key))


def line_from_dict(
    data: Dict[str, Any],
    *,
    x_key: str = "类别",
    y_key: str = "数量",
) -> None:
    """把 {label: count} dict 转 DataFrame 并 st.line_chart."""
    if not data:
        st.info("暂无数据")
        return
    rows = [{x_key: k, y_key: v} for k, v in data.items()]
    df = pd.DataFrame(rows)
    st.line_chart(df.set_index(x_key))


__all__ = ["bar_from_dict", "line_from_dict"]
