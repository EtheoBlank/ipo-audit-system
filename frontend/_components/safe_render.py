"""前端 XSS 防护工具.

P0 安全: 后端 / 抓取 / LLM 输出直接渲染到 st.markdown / st.markdown(f"[{...}]({url})")
会执行恶意链接 (javascript:alert(1) / data:text/html,...) 或被 LLM 注入脚本。
本模块提供:
  - safe_markdown(text)         — 转义用户可控文本后再交给 markdown 渲染
  - safe_link(label, url)       — 校验 url 协议后输出 markdown 链接
  - safe_text(text, max_len=200) — 转义 + 截断, 给 st.markdown / st.caption 用
"""
from __future__ import annotations

import html
from typing import Optional

_SAFE_URL_PROTOCOLS = ("http://", "https://", "/", "mailto:", "#")


def safe_url(url: Optional[str], fallback: str = "#") -> str:
    """只允许 http/https/mailto/相对路径/锚点 协议, 其他 (javascript:, data:, vbscript:) 走 fallback."""
    if not url:
        return fallback
    s = str(url).strip()
    if not s:
        return fallback
    if s.startswith(_SAFE_URL_PROTOCOLS):
        return s
    return fallback


def safe_link(label: str, url: Optional[str]) -> str:
    """构造安全的 markdown 链接."""
    return f"[{html.escape(label or '')}]({safe_url(url)})"


def safe_inline_text(text: Optional[str], max_len: int = 300) -> str:
    """转义 + 截断, 用于 st.markdown(...) 内的内联文本.

    注意: streamlit markdown 会把 [label](url) 渲染成链接, 把 `code` 渲染成代码块,
    把 *bold* 渲染成加粗。如果文本来自后端/用户/LLM, 必须 escape 避免被渲染成 HTML。
    """
    if not text:
        return ""
    s = str(text)
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return html.escape(s)