"""DataFrame 展示/编辑 统一封装 — 10+ 个 pages_*.py 各自写 st.dataframe(...) 一遍.

提供:
  - show_df:        只读展示 (统一 hide_index + use_container_width)
  - edit_df:        st.data_editor 入口
  - keep_columns:   过滤列 (只保留在 df.columns 里存在的)

抽出来后调用方 1 行替代 5 行; 用户行为 (渲染结果) 完全一致.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd
import streamlit as st


def show_df(
    df: pd.DataFrame,
    *,
    height: Optional[int] = None,
    hide_index: bool = True,
    column_config: Optional[dict] = None,
    use_container_width: bool = True,
    width: Optional[str] = None,  # "stretch" / "content" — 旧 page 用过
) -> None:
    """统一 st.dataframe 包装.

    Pages 历史上有 3 种调用法 (use_container_width / width="stretch" / 都不传),
    默认行为与 use_container_width=True 模式一致 — 那是最常见的 8+ 个页面.
    """
    kwargs: dict[str, Any] = {"hide_index": hide_index}
    if column_config:
        kwargs["column_config"] = column_config
    if height is not None:
        kwargs["height"] = height
    if width is not None:
        kwargs["width"] = width
    else:
        kwargs["use_container_width"] = use_container_width
    st.dataframe(df, **kwargs)


def edit_df(
    df: pd.DataFrame,
    *,
    key: str,
    column_config: Optional[dict] = None,
    height: Optional[int] = None,
    num_rows: str = "fixed",
    hide_index: bool = True,
    width: Optional[str] = None,
) -> pd.DataFrame:
    """统一 st.data_editor 包装 (默认 num_rows=fixed 与现有 2 个用法一致)."""
    kwargs: dict[str, Any] = {
        "key": key,
        "num_rows": num_rows,
        "hide_index": hide_index,
    }
    if column_config:
        kwargs["column_config"] = column_config
    if height is not None:
        kwargs["height"] = height
    if width is not None:
        kwargs["width"] = width
    else:
        kwargs["use_container_width"] = True
    return st.data_editor(df, **kwargs)


def keep_columns(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """只保留在 df 里存在的列 — 5+ 个页面写过 `keep = [c for c in keep if c in df.columns]`."""
    present = [c for c in cols if c in df.columns]
    return df[present] if present else df


__all__ = ["show_df", "edit_df", "keep_columns"]
