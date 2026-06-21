"""Shared pytest fixtures and bootstrap for the IPO Audit System test suite.

Adds the repository root to ``sys.path`` so test modules can simply do
``from app.xxx import ...`` regardless of where pytest was invoked from.

Round 32+: 自动加载 ``tests/_helpers/`` 下的 fixture. 测试可以直接::

    from tests._helpers.auth import make_user, ROLE_ADMIN
    from tests._helpers.idor import assert_cross_firm_404

或在 test 函数签名里使用 fixture::

    async def test_x(async_session, client):
        user = await make_user(async_session, role=ROLE_ADMIN)
        ...
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


# ============================================================
#  Round 32+: tests/_helpers/ 自动暴露
# ============================================================
# 把 helpers 里的 fixture 提升到 conftest 命名空间, 让所有 test 文件无须
# import 就能直接用 ``async def test_x(async_session, client)``.
# 注意: helper 自己的 fixture (async_engine / client) 已带 scope 注解.

from tests._helpers.db import (  # noqa: E402, F401 — re-export fixtures
    async_engine,
    async_session_factory,
    async_session,
)
from tests._helpers.http import client  # noqa: E402, F401 — re-export fixture as 'client'


def pytest_configure(config: pytest.Config) -> None:
    """pytest 启动时注册 helper marker / 文档."""
    config.addinivalue_line(
        "markers",
        "skill_smoke: tests/_helpers/ 自身可用性冒烟测试 (round 32+)",
    )

