"""IDOR / 权限断言 helper.

把项目里重复出现的多租户 / RBAC 校验模式抽出来. 用法::

    from tests._helpers.idor import (
        assert_cross_firm_404, assert_role_required, assert_anonymous_401,
    )
"""
from __future__ import annotations

from typing import Optional

import pytest
from starlette.testclient import TestClient


def assert_cross_firm_404(
    client: TestClient,
    method: str,
    path: str,
    *,
    own_token: str,
    other_token: str,
    json: Optional[dict] = None,
    msg: str = "跨所访问应 404",
) -> None:
    """跨所访问应 404 (信息隐藏, 防止 IDOR 枚举).

    测试同一端点: own_token 200, other_token 404.

    用法::

        assert_cross_firm_404(
            client, "GET", f"/api/projects/{project_b.id}",
            own_token=token_a, other_token=token_b,
        )
    """
    resp_own = client.request(method, path, headers={"Authorization": f"Bearer {own_token}"}, json=json)
    resp_other = client.request(method, path, headers={"Authorization": f"Bearer {other_token}"}, json=json)

    assert resp_own.status_code == 200, (
        f"own_token 应 200, 实得 {resp_own.status_code}: {resp_own.text[:200]}\n{msg}"
    )
    assert resp_other.status_code == 404, (
        f"other_token 应 404 (防枚举), 实得 {resp_other.status_code}: "
        f"{resp_other.text[:200]}\n{msg}"
    )


def assert_role_required(
    client: TestClient,
    method: str,
    path: str,
    *,
    allowed_token: str,
    denied_token: str,
    json: Optional[dict] = None,
    expected_denied: int = 403,
    msg: str = "",
) -> None:
    """校验 RBAC: allowed_token 200, denied_token 403 (或 expected_denied).

    用法::

        assert_role_required(
            client, "POST", "/api/auth/users",
            allowed_token=qc_token, denied_token=assistant_token,
        )
    """
    resp_ok = client.request(method, path, headers={"Authorization": f"Bearer {allowed_token}"}, json=json)
    resp_no = client.request(method, path, headers={"Authorization": f"Bearer {denied_token}"}, json=json)

    assert resp_ok.status_code in (200, 201, 204), (
        f"allowed_token 应 2xx, 实得 {resp_ok.status_code}: {resp_ok.text[:200]}\n{msg}"
    )
    assert resp_no.status_code == expected_denied, (
        f"denied_token 应 {expected_denied}, 实得 {resp_no.status_code}: "
        f"{resp_no.text[:200]}\n{msg}"
    )


def assert_anonymous_401(
    client: TestClient,
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    msg: str = "未认证应 401",
) -> None:
    """无 Authorization header 应 401 (若 AUTH_ENABLED=True).

    用法::

        assert_anonymous_401(client, "GET", "/api/auth/users/1")
    """
    resp = client.request(method, path, json=json)
    assert resp.status_code == 401, (
        f"无 token 应 401, 实得 {resp.status_code}: {resp.text[:200]}\n{msg}"
    )
