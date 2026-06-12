"""Shared pytest fixtures and bootstrap for the IPO Audit System test suite.

Adds the repository root to ``sys.path`` so test modules can simply do
``from app.xxx import ...`` regardless of where pytest was invoked from.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _reset_streamlit_session_state():
    """前端层一些测试会赋值 ``st.session_state``,跑全量时会污染后续测试。

    本 fixture 在每个 test 结束后把 ``st.session_state`` 还原为初始可用对象,
    避免 Pydantic/Streamlit 在多 test 串跑时出现假阳性。"""
    yield
    try:
        import streamlit as st  # noqa: WPS433 — defer import; streamlit 是可选依赖
        # 用空字典替代,既兼容 ``.get(...)`` 又兼容 ``[k] = v`` 语义。
        try:
            st.session_state = {}  # type: ignore[assignment]
        except Exception:
            pass
    except ImportError:
        pass

