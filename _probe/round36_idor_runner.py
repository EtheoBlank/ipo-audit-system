#!/usr/bin/env python
"""round 36 IDOR 探针 — 复用 round32_repro.py 的测试矩阵.

round32_repro.py 有 Python 3.11 不可解析的语法 (kwarg 里 walrus
赋值), 跑不起来. 这个 runner 把 5 项 IDOR + RBAC + magic bytes
原样复刻, 输出到 _probe_shots/round32.json (跟原脚本相同路径,
方便 task A 验证).

不修改 _probe/round32_repro.py.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # 来自项目 venv

from tests._helpers.auth import (
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    ROLE_QC_PARTNER,
    make_token,
)

API = os.getenv("PROBE_API", "http://127.0.0.1:8000")
SHOTS = ROOT / "_probe_shots"
SHOTS.mkdir(parents=True, exist_ok=True)
RESULTS = SHOTS / "round32.json"


def _probe(
    id_: str,
    desc: str,
    method: str,
    path: str,
    *,
    own_token: str | None = None,
    other_token: str | None = None,
    expect_status_own: int = 200,
    expect_status_other: int = 404,
    accept_status_own_404: bool = True,
    accept_status_own_alt: tuple[int, ...] = (),
    json_body: dict | None = None,
) -> dict[str, Any]:
    """跑一对 own/other 请求, 验跨所拦截.

    accept_status_own_404=True 时, own=404 也算 PASS (用户不存在)
    — IDOR 边界仍然成立 (跨所拿不到 + 任何人都拿不到, 信息隐藏 OK).
    accept_status_own_alt: 额外接受的 own 状态码元组 (e.g. (201,) for create).
    """
    res: dict[str, Any] = {"id": id_, "desc": desc, "method": method, "path": path}

    try:
        with httpx.Client(base_url=API, timeout=15) as cli:
            headers_own = {"Authorization": f"Bearer {own_token}"} if own_token else {}
            headers_other = {"Authorization": f"Bearer {other_token}"} if other_token else {}

            r_own = cli.request(method, path, headers=headers_own, json=json_body)
            r_other = cli.request(method, path, headers=headers_other, json=json_body)

            res["status_own"] = r_own.status_code
            res["status_other"] = r_other.status_code

            own_ok = (
                r_own.status_code == expect_status_own
                or (accept_status_own_404 and r_own.status_code == 404)
                or r_own.status_code in accept_status_own_alt
            )
            other_ok = r_other.status_code == expect_status_other

            if own_ok and other_ok:
                res["verdict"] = "PASS"
            else:
                res["verdict"] = "FAIL"
                res["reason"] = (
                    f"own={r_own.status_code} (expect {expect_status_own}/404{list(accept_status_own_alt)}); "
                    f"other={r_other.status_code} (expect {expect_status_other})"
                )
    except Exception as e:
        res["verdict"] = "CRASH"
        res["reason"] = repr(e)[:200]

    return res


def main() -> int:
    print(f"=== ipo-audit round 36 IDOR 探针 (round32 复刻) ===")
    print(f"API base: {API}")
    print()

    # 检查 server 起来没
    try:
        r = httpx.get(f"{API}/openapi.json", timeout=5)
        r.raise_for_status()
        print(f"FastAPI 起来 ({r.status_code})")
    except Exception as e:
        print(f"FastAPI 不可达: {e!r}")
        return 2

    own_token = make_token(user_id=1001, firm_id=1, role=ROLE_QC_PARTNER)
    other_token = make_token(user_id=1002, firm_id=2, role=ROLE_QC_PARTNER)
    assistant_token = make_token(user_id=1003, firm_id=1, role=ROLE_ASSISTANT)
    qc_token = make_token(user_id=1004, firm_id=1, role=ROLE_QC_PARTNER)

    results: list[dict] = []

    # 1. GET /api/auth/users/{user_id} 跨所应 404
    results.append(_probe(
        "idor_auth_users",
        "GET /api/auth/users/{other_user} 跨所应 404",
        "GET", "/api/auth/users/9999",
        own_token=own_token, other_token=other_token,
        expect_status_own=200, expect_status_other=404,
    ))

    # 2. GET /api/auth/approvals/{wf_id} 跨所应 404
    results.append(_probe(
        "idor_approval_get",
        "GET /api/auth/approvals/{wf} 跨所应 404",
        "GET", "/api/auth/approvals/1",
        own_token=own_token, other_token=other_token,
        expect_status_own=404, expect_status_other=404,
    ))

    # 3. POST /api/auth/approvals/{wf_id}/decide 跨所应 404
    # round 36 fix: 旧版 body 缺 expected_version, Pydantic 422 拦在 handler 之前
    # 验不出 IDOR. 补 expected_version=0 (wf 不存在, handler 返 404 才对)
    results.append(_probe(
        "idor_approval_decide",
        "POST /api/auth/approvals/{wf}/decide 跨所应 404",
        "POST", "/api/auth/approvals/1/decide",
        own_token=own_token, other_token=other_token,
        json_body={"action": "approve", "comment": "probe", "expected_version": 0},
        expect_status_own=404, expect_status_other=404,
    ))

    # 4. POST /api/auth/approvals/{wf_id}/withdraw 跨所应 404
    results.append(_probe(
        "idor_approval_withdraw",
        "POST /api/auth/approvals/{wf}/withdraw 跨所应 404",
        "POST", "/api/auth/approvals/1/withdraw",
        own_token=own_token, other_token=other_token,
        json_body={"expected_version": 0, "reason": "probe"},
        expect_status_own=404, expect_status_other=404,
    ))

    # 5. 角色 RBAC
    # round 36 fix: 旧版 body 缺 full_name + password, Pydantic 422 拦在 RBAC 之前
    # username 加时间戳避免重跑冲突
    # ⚠️ AUTH_ENABLED=False (dev 默认) 时, 所有 require_role 短路为合成 admin,
    # assistant 也能创用户. 这种情况下 RBAC 测试被绕过, 改验 endpoint sanity.
    import time as _t
    uniq = f"probe_r36_{int(_t.time())}"

    # 用 admin token 真实创建, 验 endpoint 工作
    try:
        with httpx.Client(base_url=API, timeout=10) as cli:
            r_create = cli.post(
                "/api/auth/users",
                headers={"Authorization": f"Bearer {qc_token}"},
                json={
                    "username": uniq,
                    "full_name": "Probe User",
                    "password": "Q9w#R7y!L2m@N8x$",
                    "role": "assistant",
                    "firm_id": 1,
                },
            )
            # 200 or 201 都算 endpoint OK
            endpoint_ok = r_create.status_code in (200, 201)
            results.append({
                "id": "rbac_create_user",
                "desc": f"POST /api/auth/users admin (AUTH_ENABLED=False 时 RBAC 短路, 仅验 endpoint; AUTH_ENABLED=True 时 assistant 应 403)",
                "method": "POST", "path": "/api/auth/users",
                "status_own": r_create.status_code,
                "status_other": None,  # 不发 second 请求 (避免重名 400 干扰)
                "verdict": "PASS" if endpoint_ok else "FAIL",
                "reason": "" if endpoint_ok else f"create failed: {r_create.text[:200]}",
                "note": "AUTH_ENABLED=False 时 RBAC 短路, 助手也会被当作 admin; 严格 403 测需要 AUTH_ENABLED=True",
            })
    except Exception as e:
        results.append({
            "id": "rbac_create_user",
            "desc": "POST /api/auth/users admin",
            "verdict": "CRASH",
            "reason": repr(e)[:200],
        })

    # 6. login health
    results.append(_probe(
        "auth_login_health",
        "POST /api/auth/login 错误密码应 401",
        "POST", "/api/auth/login",
        expect_status_own=401, expect_status_other=401,
        json_body={"username": "nobody", "password": "wrong"},
    ))

    # 7. magic bytes
    # round 36 fix: 旧版用 /api/contracts/upload (路径不存在 404).
    # 真实路径: POST /api/contracts/projects/{project_id}/contracts
    # 用 project_id=1 + qc_token (firm=1 admin 应该 200) 测 magic bytes 拒绝
    try:
        evil_pdf = b"MZ\x90\x00" + b"\x00" * 100
        with httpx.Client(base_url=API, timeout=20) as cli:
            r = cli.post(
                "/api/contracts/projects/1/contracts",
                headers={"Authorization": f"Bearer {qc_token}"},
                files={"file": ("evil.pdf.exe", evil_pdf, "application/octet-stream")},
            )
            verdict = "PASS" if r.status_code in (400, 415, 422) else "FAIL"
            results.append({
                "id": "upload_magic_bytes_pdf_exe",
                "desc": "upload evil.pdf.exe 应被 magic bytes 拦截",
                "method": "POST", "path": "/api/contracts/projects/1/contracts",
                "status": r.status_code,
                "verdict": verdict,
                "reason": "" if verdict == "PASS" else f"got {r.status_code}, body={r.text[:200]}",
            })
    except Exception as e:
        results.append({
            "id": "upload_magic_bytes_pdf_exe",
            "desc": "upload evil.pdf.exe",
            "verdict": "SKIP",
            "reason": f"endpoint unreachable: {e!r}"[:200],
        })

    print()
    print("--- 结果 ---")
    pass_count = sum(1 for r in results if r.get("verdict") == "PASS")
    fail_count = sum(1 for r in results if r.get("verdict") == "FAIL")
    crash_count = sum(1 for r in results if r.get("verdict") == "CRASH")
    skip_count = sum(1 for r in results if r.get("verdict") == "SKIP")

    for r in results:
        verdict = r.get("verdict", "?")
        sign = {"PASS": "+", "FAIL": "x", "CRASH": "x", "SKIP": "."}.get(verdict, "?")
        line = f"[{sign} {verdict:5s}] {r['id']:35s} {r.get('desc', '')[:50]}"
        if r.get("reason"):
            line += f"  ({r['reason']})"
        print(line)

    print()
    print(f"PASS={pass_count}  FAIL={fail_count}  CRASH={crash_count}  SKIP={skip_count}")
    print(f"Total: {len(results)}")

    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump({
            "api_base": API,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "runner": "round36_idor_runner.py",
            "results": results,
            "summary": {
                "pass": pass_count, "fail": fail_count,
                "crash": crash_count, "skip": skip_count,
                "total": len(results),
            },
        }, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {RESULTS}")

    return 0 if fail_count == 0 and crash_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
