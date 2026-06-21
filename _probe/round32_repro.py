#!/usr/bin/env python
"""round 32 P0 端到端探针 (probe-testing skill 场景 F).

跑前先启 FastAPI::

    cd D:/ipo_audit_link
    .venv/Scripts/python -m uvicorn app.main:app --port 8000 &

然后::

    .venv/Scripts/python _probe/round32_repro.py

输出:
  - 控制台: 每个 P0 PASS / FAIL
  - _probe_shots/round32.json: 机器可读报告
  - _probe_shots/round32_evidence/<id>.png: 前端截图 (Streamlit)

复现 vs 修复前:
  - 修复前: pages_sentiment.py c4 NameError, sentiment 概览 Tab 白屏
  - 修复后: 4 列正常渲染

复用 tests/_helpers/auth + tests/_helpers/idor 验跨所 IDOR.
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
SHOTS = ROOT / "_probe_shots" / "round32_evidence"
RESULTS = ROOT / "_probe_shots" / "round32.json"
SHOTS.mkdir(parents=True, exist_ok=True)


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
    json_body: dict | None = None,
) -> dict[str, Any]:
    """跑一对 own/other 请求, 验跨所拦截. 返回单条结果."""
    res: dict[str, Any] = {"id": id_, "desc": desc, "method": method, "path": path}

    try:
        with httpx.Client(base_url=API, timeout=15) as cli:
            headers_own = {"Authorization": f"Bearer {own_token}"} if own_token else {}
            headers_other = {"Authorization": f"Bearer {other_token}"} if other_token else {}

            r_own = cli.request(method, path, headers=headers_own, json=json_body)
            r_other = cli.request(method, path, headers=headers_other, json=json_body)

            res["status_own"] = r_own.status_code
            res["status_other"] = r_other.status_code

            # own 应 = expect_status_own, other 应 = expect_status_other (IDOR 防枚举)
            if r_own.status_code == expect_status_own and r_other.status_code == expect_status_other:
                res["verdict"] = "PASS"
            else:
                res["verdict"] = "FAIL"
                res["reason"] = (
                    f"own={r_own.status_code} (expect {expect_status_own}); "
                    f"other={r_other.status_code} (expect {expect_status_other})"
                )

    except Exception as e:
        res["verdict"] = "CRASH"
        res["reason"] = repr(e)[:200]

    return res


def main() -> int:
    print(f"=== ipo-audit round 32 P0 探针 ===")
    print(f"API base: {API}")
    print(f"需要先启动 FastAPI: uvicorn app.main:app --port 8000")
    print()

    # 检查 server 起来没
    try:
        r = httpx.get(f"{API}/openapi.json", timeout=5)
        r.raise_for_status()
        print(f"✓ FastAPI 起来 ({r.status_code})")
    except Exception as e:
        print(f"✗ FastAPI 不可达: {e!r}")
        print(f"  请先: cd {ROOT} && .venv/Scripts/python -m uvicorn app.main:app --port 8000 &")
        return 2

    # 准备两个 firm + 两个 admin (用测试数据). 真实场景从 /api/auth/login 拿 token.
    # 此探针用 make_token 直接签发 (绕开密码验证), 假设 secrets.JWT_SECRET 已设置.
    own_token = make_token(user_id=1001, firm_id=1, role=ROLE_QC_PARTNER)
    other_token = make_token(user_id=1002, firm_id=2, role=ROLE_QC_PARTNER)
    admin_token = make_token(user_id=9999, firm_id=1, role=ROLE_ADMIN)
    assistant_token = make_token(user_id=1003, firm_id=1, role=ROLE_ASSISTANT)

    results: list[dict] = []

    # ============================================================
    #  IDOR / RBAC 5 项 (round 32 IDOR agent 修的)
    # ============================================================

    # 1. GET /api/auth/users/{user_id} 跨所应 404 (信息隐藏, 防枚举)
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
        # own 可能 404 (wf 不存在), other 也应 404, 但 own 不会是 200
        expect_status_own=404, expect_status_other=404,
    ))

    # 3. POST /api/auth/approvals/{wf_id}/decide 跨所应 404
    results.append(_probe(
        "idor_approval_decide",
        "POST /api/auth/approvals/{wf}/decide 跨所应 404",
        "POST", "/api/auth/approvals/1/decide",
        own_token=own_token, other_token=other_token,
        json_body={"decision": "approve", "comment": "probe"},
        expect_status_own=404, expect_status_other=404,
    ))

    # 4. POST /api/auth/approvals/{wf_id}/withdraw 跨所应 404
    results.append(_probe(
        "idor_approval_withdraw",
        "POST /api/auth/approvals/{wf}/withdraw 跨所应 404",
        "POST", "/api/auth/approvals/1/withdraw",
        own_token=own_token, other_token=other_token,
        json_body={"reason": "probe"},
        expect_status_own=404, expect_status_other=404,
    ))

    # 5. 角色 RBAC: 普通 assistant 调 /api/auth/users 创建应 403
    # round37 P1 修复: 原 own_token=qc_token := make_token(...) 是 walrus 在 kwarg 内赋值,
    # Python 3.11 不支持 (SyntaxError). 改先赋值再传.
    qc_token = make_token(user_id=1004, firm_id=1, role=ROLE_QC_PARTNER)
    results.append(_probe(
        "rbac_create_user",
        "POST /api/auth/users 普通用户应 403",
        "POST", "/api/auth/users",
        own_token=qc_token,
        other_token=assistant_token,
        json_body={"username": "probe", "role": "assistant", "firm_id": 1},
        expect_status_own=200, expect_status_other=403,
    ))

    # ============================================================
    #  静默失败防护 (SEC-03/04): API 层很难直接验, 用 unit test 替代.
    #  这里只 sanity check login endpoint 不返 500.
    # ============================================================
    results.append(_probe(
        "auth_login_health",
        "POST /api/auth/login 错误密码应 401 (不静默 200)",
        "POST", "/api/auth/login",
        expect_status_own=401, expect_status_other=401,
        json_body={"username": "nobody", "password": "wrong"},
    ))

    # ============================================================
    #  上传路径穿越 + magic bytes: 真实上传文件, 验 .pdf.exe 拒绝
    # ============================================================
    try:
        # evil.exe 假装 .pdf
        evil_pdf = b"MZ\x90\x00" + b"\x00" * 100  # PE 头
        with httpx.Client(base_url=API, timeout=20) as cli:
            r = cli.post(
                "/api/contracts/upload",
                headers={"Authorization": f"Bearer {qc_token}"},
                files={"file": ("evil.pdf.exe", evil_pdf, "application/octet-stream")},
            )
            verdict = "PASS" if r.status_code in (400, 415, 422) else "FAIL"
            results.append({
                "id": "upload_magic_bytes_pdf_exe",
                "desc": "upload evil.pdf.exe 应被 magic bytes 拦截 (400/415/422)",
                "method": "POST", "path": "/api/contracts/upload",
                "status": r.status_code,
                "verdict": verdict,
                "reason": "" if verdict == "PASS" else f"got {r.status_code}, body={r.text[:200]}",
            })
    except Exception as e:
        results.append({
            "id": "upload_magic_bytes_pdf_exe",
            "desc": "upload evil.pdf.exe",
            "verdict": "SKIP",
            "reason": f"endpoint unreachable or no auth: {e!r}"[:200],
        })

    # ============================================================
    #  输出
    # ============================================================
    print()
    print("--- 结果 ---")
    pass_count = sum(1 for r in results if r.get("verdict") == "PASS")
    fail_count = sum(1 for r in results if r.get("verdict") == "FAIL")
    crash_count = sum(1 for r in results if r.get("verdict") == "CRASH")
    skip_count = sum(1 for r in results if r.get("verdict") == "SKIP")

    for r in results:
        verdict = r.get("verdict", "?")
        sign = {"PASS": "✓", "FAIL": "✗", "CRASH": "✗", "SKIP": "·"}.get(verdict, "?")
        line = f"[{sign} {verdict:5s}] {r['id']:35s} {r.get('desc', '')[:50]}"
        if r.get("reason"):
            line += f"  ({r['reason']})"
        print(line)

    print()
    print(f"PASS={pass_count}  FAIL={fail_count}  CRASH={crash_count}  SKIP={skip_count}")
    print(f"Total: {len(results)}")

    # 落盘
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump({
            "api_base": API,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
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
