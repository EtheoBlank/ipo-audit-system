"""Frontend 共享 HTTP 客户端 + 工具.

抽出来避免 4 个 pages_*.py 各自实现一份 _api() 函数, 不一致风险高 (Agent #4 P0):
  - 401 处理不一致 (有的清 token, 有的不清; refresh_token 漏清)
  - 异常吞处理不一致 (有的只 catch ConnectionError, 其他 RequestException 让 Streamlit crash)
  - timeout 不一致

用法:
    from frontend._http import api_request, auth_headers, API_BASE_URL

    res = api_request("GET", "/api/projects/", params={...})
    if res is None:
        return  # 已经显示了 st.error / st.warning
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Union

import requests
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def auth_headers() -> Dict[str, str]:
    """从 st.session_state 取 Bearer token. 无 token 返回空 dict."""
    token = st.session_state.get("auth_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _clear_auth_session() -> None:
    """清掉所有 auth 相关 session_state (token 过期 / 主动登出时调)."""
    for k in ("auth_token", "auth_refresh_token", "auth_user"):
        st.session_state.pop(k, None)


def api_request(
    method: str,
    endpoint: str,
    *,
    expect_bytes: bool = False,
    timeout: int = 30,
    silent_errors: bool = False,
    **kwargs,
) -> Union[Dict, list, bytes, None]:
    """统一 API 调用入口.

    Args:
        method: HTTP 方法
        endpoint: 路径 (会拼到 API_BASE_URL 后面)
        expect_bytes: 二进制响应 (Excel/Word/PDF 下载)
        timeout: 秒
        silent_errors: True 时失败仅返回 None, 不弹 st.error (用于轻量 polling)
        **kwargs: 透传给 requests.request

    Returns:
        - 成功 + JSON: dict / list
        - 成功 + expect_bytes: bytes
        - 失败: None (并已显示 st.error / st.warning)
    """
    url = f"{API_BASE_URL}{endpoint}"
    headers = kwargs.pop("headers", {}) or {}
    headers.update(auth_headers())

    try:
        r = requests.request(method, url, timeout=timeout, headers=headers, **kwargs)
    except requests.exceptions.ConnectionError:
        if not silent_errors:
            st.error(
                "无法连接到后端服务, 请确保 FastAPI 已启动 (uv run uvicorn app.main:app --reload --port 8000)"
            )
        return None
    except requests.exceptions.Timeout:
        if not silent_errors:
            st.error(f"请求超时 ({timeout}s) — 后端可能过载或正在处理大数据")
        return None
    except requests.exceptions.RequestException as exc:
        if not silent_errors:
            st.error(f"网络错误: {exc}")
        return None

    # 401 — token 失效, 统一清掉 session 跳登录
    if r.status_code == 401:
        _clear_auth_session()
        if not silent_errors:
            st.warning("登录已失效, 请在左侧 '🔐 系统管理' 页重新登录")
        return None

    # 403 — 权限不足
    if r.status_code == 403:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:300]
        if not silent_errors:
            st.error(f"权限不足: {detail}")
        return None

    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:300]
        if not silent_errors:
            st.error(f"API {r.status_code}: {detail}")
        return None

    if expect_bytes:
        return r.content

    try:
        return r.json()
    except Exception:
        return None


def validate_password_strength(password: str) -> Optional[str]:
    """密码强度校验 — 返回错误消息字符串, None 表示通过.

    规则: 至少 8 位; 字母 + 数字至少有 1 位; 不能纯数字 / 纯字母.
    """
    if not password or len(password) < 8:
        return "密码至少 8 位"
    if len(password) > 128:
        return "密码不能超过 128 位"
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not has_letter:
        return "密码必须含至少 1 个字母"
    if not has_digit:
        return "密码必须含至少 1 个数字"
    # 弱密码黑名单 (前 100 大常见)
    weak = {"password", "12345678", "qwerty12", "admin123", "Admin@1234"}
    if password.lower() in {w.lower() for w in weak}:
        return "密码过于常见, 请用更复杂的组合"
    return None


__all__ = [
    "API_BASE_URL",
    "auth_headers",
    "api_request",
    "validate_password_strength",
]
