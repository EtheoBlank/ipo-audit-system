"""统一文件下载按钮 — 4+ 个 pages_*.py 写过同款.

用法:
    content = api_request("GET", "/api/.../export", expect_bytes=True)
    if isinstance(content, bytes) and content:
        download_excel(content, file_name="foo.xlsx")
"""
from __future__ import annotations

import streamlit as st


def download_excel(
    data: bytes,
    *,
    file_name: str,
    label: str = "⬇️ 下载 Excel",
) -> None:
    """xlsx 后缀默认用 xlsx mime; 其它后缀用 octet-stream."""
    if file_name.lower().endswith(".xlsx"):
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        mime = "application/octet-stream"
    st.download_button(label, data=data, file_name=file_name, mime=mime)


def download_word(
    data: bytes,
    *,
    file_name: str,
    label: str = "📥 下载 Word 文档",
) -> None:
    """Word 文档下载 — sentiment 简报 / 季度报告 用."""
    st.download_button(
        label,
        data=data,
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


__all__ = ["download_excel", "download_word"]
