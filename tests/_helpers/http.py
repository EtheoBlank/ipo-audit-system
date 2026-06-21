"""HTTP client / auth header helper."""
from __future__ import annotations

from typing import Optional

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="function")
def client():
    """FastAPI TestClient. 默认走项目 ``app.main:app``."""
    from app.main import app

    with TestClient(app) as c:
        yield c


def auth_headers(token: str) -> dict:
    """Bearer token 助手. 用法::

        r = client.get("/api/x", headers=auth_headers(token))
    """
    return {"Authorization": f"Bearer {token}"}
