"""前端 XSS 防护工具 + 日期校验.

P0 安全: 后端 / 抓取 / LLM 输出直接渲染到 st.markdown / st.markdown(f"[{...}]({url})")
会执行恶意链接 (javascript:alert(1) / data:text/html,...) 或被 LLM 注入脚本。
本模块提供:
  - safe_markdown(text)         — 转义用户可控文本后再交给 markdown 渲染
  - safe_link(label, url)       — 校验 url 协议后输出 markdown 链接
  - safe_text(text, max_len=200) — 转义 + 截断, 给 st.markdown / st.caption 用
  - validate_date_input(...)    — P1 日期校验 (round 32): 统一入口,
                                 旧 _date_input 各页散落, 现统一函数名
"""
from __future__ import annotations

import html
import re
from datetime import date as _date
from typing import Optional, Tuple

import streamlit as st

_SAFE_URL_PROTOCOLS = ("http://", "https://", "/", "mailto:", "#")

# P1 (round 32): YYYY-MM-DD 严格正则 — 拒绝 2025-1-1 / 2025/01/01 / 空串
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 月份正则 (YYYY-MM) — 严格 4 位年 + 2 位月 01-12, 不接受 2024-1 / 24-12
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


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


def is_valid_date_str(s: str) -> bool:
    """纯字符串校验 — 不写 streamlit, 给需要纯函数判断的调用方.

    例: API 路径校验 / URL 参数解析 / 比较器. 返回 True/False, 不抛.
    """
    if not s or not isinstance(s, str):
        return False
    if not _DATE_RE.match(s.strip()):
        return False
    try:
        y, m, d = s.split("-")
        _date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return False
    return True


def is_valid_month_str(s: str) -> bool:
    """YYYY-MM 月份校验 (规则见 _MONTH_RE)."""
    if not s or not isinstance(s, str):
        return False
    return bool(_MONTH_RE.match(s.strip()))


def validate_date_input(
    label: str,
    *,
    key: str = "",
    default: str = "",
    required: bool = True,
) -> Tuple[str, bool]:
    """统一日期输入 — P1 (round 32, 2026-06-20).

    统一命名 `validate_date_input` (替换各页散落的 _date_input):
      - 正则 ^\\d{4}-\\d{2}-\\d{2}$ 校验
      - 不通过: warning + return ("", False)
      - 通过: return (value, True)
      - required=False: 空串允许

    用法:
        period_end, ok = validate_date_input("期末日期 YYYY-MM-DD", default=str(date.today()))
        if not ok:
            return
    """
    default_val = default or str(_date.today())
    val = st.text_input(label, value=default_val, key=key or None)
    val = (val or "").strip()
    if not val:
        if required:
            st.warning(f"⚠️ {label} 不能为空 (格式 YYYY-MM-DD)")
            return "", False
        return "", True
    if not _DATE_RE.match(val):
        st.warning(f"⚠️ {label} 格式错误: '{val}', 应为 YYYY-MM-DD (例如 2025-12-31)")
        return "", False
    # 进一步校验月份/日期合法性 (例如 2025-02-30)
    try:
        y, m, d = val.split("-")
        _date(int(y), int(m), int(d))
    except ValueError:
        st.warning(f"⚠️ {label} 不是合法日期: '{val}'")
        return "", False
    return val, True
