"""P0 (2026-06-19) — contracts 上传同步 IO 改为 asyncio.to_thread.

验证 2 个行为:
  1. upload_contract 函数体内 temp_path.write_bytes + unlink 都用 to_thread 包裹
  2. _safe_unlink helper 存在并被 to_thread 包裹
"""
from __future__ import annotations

import ast
from pathlib import Path


def _get_upload_contract_body(source: str) -> str:
    """AST-based: 提取 upload_contract 函数体源码."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "upload_contract"
        ):
            lines = source.splitlines(keepends=True)
            # node.lineno 是函数签名行 (含装饰器可能跨多行), body[0].lineno 是第一行
            start = node.body[0].lineno - 1
            # 找 body 最后一行 (end_lineno 是 Python 3.8+ 属性)
            end = getattr(node, "end_lineno", None)
            if end is None:
                # 回退: body 最后一项的 lineno + 行内偏移
                last_stmt = node.body[-1]
                end = last_stmt.lineno
            return "".join(lines[start:end])
    raise AssertionError("找不到 upload_contract 函数")


def _get_helper_body(source: str, helper_name: str) -> str:
    """AST-based: 提取 helper 函数体源码."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == helper_name:
            lines = source.splitlines(keepends=True)
            start = node.body[0].lineno - 1
            end = getattr(node, "end_lineno", node.body[-1].lineno)
            return "".join(lines[start:end])
    raise AssertionError(f"找不到 {helper_name} 函数")


# ============================================================
#  Tests
# ============================================================


class TestContractsAsyncIO:
    """P0-9 — contracts.py upload_contract 改 asyncio.to_thread."""

    def test_contracts_upload_uses_async_to_thread(self):
        """upload_contract 函数体内 temp_path.write_bytes + unlink 都用 to_thread 包裹."""
        contracts_path = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "api"
            / "contracts.py"
        )
        source = contracts_path.read_text(encoding="utf-8")
        body = _get_upload_contract_body(source)

        # 必须包含 to_thread 包裹的 write_bytes
        write_pattern = "await asyncio.to_thread(temp_path.write_bytes, content)"
        assert write_pattern in body, (
            f"upload_contract 必须包含 '{write_pattern}'"
        )

        # 必须包含至少 2 处 to_thread 包裹的 _safe_unlink (except + finally)
        unlink_count = body.count("await asyncio.to_thread(_safe_unlink, temp_path)")
        assert unlink_count >= 2, (
            f"upload_contract 必须至少 2 处 to_thread(_safe_unlink, temp_path) "
            f"(except 分支 + finally 分支), 实际找到 {unlink_count}"
        )

        # 不应再有顶层 temp_path.write_bytes / unlink (同步)
        assert "temp_path.write_bytes(content)" not in body, (
            "upload_contract 内不应再有顶层 'temp_path.write_bytes(content)' 同步写"
        )
        assert "temp_path.unlink(missing_ok=True)" not in body, (
            "upload_contract 内不应再有顶层 'temp_path.unlink(missing_ok=True)' 同步删"
        )

    def test_contracts_large_file_no_block(self):
        """静态分析: _safe_unlink 内部走 unlink(missing_ok=True) + try/except."""
        contracts_path = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "api"
            / "contracts.py"
        )
        source = contracts_path.read_text(encoding="utf-8")

        # _safe_unlink 必须存在
        helper_body = _get_helper_body(source, "_safe_unlink")
        assert "unlink" in helper_body, f"_safe_unlink 必须包含 'unlink'; 实际: {helper_body}"
        assert "missing_ok=True" in helper_body, (
            f"_safe_unlink 必须包含 'missing_ok=True'; 实际: {helper_body}"
        )

        # asyncio 必须被 import
        assert "import asyncio" in source, "asyncio 必须被 import"

        # 全文不应再有顶层同步 write_bytes / unlink
        assert "temp_path.write_bytes(content)" not in source, (
            "不应再有顶层 'temp_path.write_bytes(content)' 同步写 (P0 阻塞 event loop)"
        )
        assert "temp_path.unlink(missing_ok=True)" not in source, (
            "不应再有顶层 'temp_path.unlink(missing_ok=True)' 同步删 (P0 阻塞 event loop)"
        )
