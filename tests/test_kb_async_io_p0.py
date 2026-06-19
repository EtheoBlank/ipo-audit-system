"""P0 (2026-06-19) — knowledge_base 上传同步 IO 改为 asyncio.to_thread.

验证 2 个行为:
  1. upload_book 函数体内没有同步 open( 调用 (除 asyncio.to_thread 内)
  2. _sync_write helper 存在并被 to_thread 包裹
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


# ============================================================
#  Tests
# ============================================================


class TestKnowledgeBaseAsyncIO:
    """P0-8 — knowledge_base.py 上传改为 asyncio.to_thread."""

    def test_kb_upload_uses_async_to_thread(self):
        """upload_book 函数体内没有顶层 open( 调用 — 都被 asyncio.to_thread 包裹."""
        kb_path = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "api"
            / "knowledge_base.py"
        )
        source = kb_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # 找 upload_book 函数
        upload_fn = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "upload_book"
            ):
                upload_fn = node
                break
        assert upload_fn is not None, "upload_book 函数必须存在"

        # 遍历函数体, 找顶层的 open( 调用 (不在 asyncio.to_thread 内)
        offending: list[tuple[int, str]] = []

        def visit(node, in_to_thread=False):
            if isinstance(node, ast.Call):
                # 检查 asyncio.to_thread(_sync_write, ...)
                is_to_thread = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_thread"
                )
                if is_to_thread:
                    # 进入 to_thread 内部标记
                    for arg in node.args:
                        visit(arg, in_to_thread=True)
                    return

                # 检查 open( 调用
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    if not in_to_thread:
                        offending.append((node.lineno, "open() 不应在 asyncio.to_thread 外"))
                # 嵌套检查
                for arg in node.args:
                    visit(arg, in_to_thread)
                for kw in node.keywords:
                    visit(kw.value, in_to_thread)

            elif isinstance(node, (ast.With, ast.AsyncWith)):
                # 进入 with 块
                for item in node.items:
                    visit(item.context_expr, in_to_thread)
                for stmt in node.body:
                    visit(stmt, in_to_thread)

            elif isinstance(node, ast.Assign):
                visit(node.value, in_to_thread)

            elif isinstance(node, ast.Await):
                visit(node.value, in_to_thread)

            elif isinstance(node, ast.Expr):
                visit(node.value, in_to_thread)

        # 顶层遍历函数体
        for stmt in upload_fn.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "size":
                        # size = 0 (顶层赋值, 不算)
                        continue
                # 检查 RHS
                visit(stmt.value)
            else:
                visit(stmt)

        assert not offending, (
            f"upload_book 体内发现顶层同步 open 调用 (P0 阻塞 event loop): {offending}"
        )

        # 同时验证 _sync_write helper 存在
        assert "_sync_write" in source, "_sync_write helper 必须存在"

        # 验证调用模式: await asyncio.to_thread(_sync_write, ...)
        pattern = re.compile(
            r"await\s+asyncio\.to_thread\(\s*_sync_write\s*,",
            re.MULTILINE,
        )
        assert pattern.search(source), (
            "upload_book 应使用 'await asyncio.to_thread(_sync_write, target, content)' 模式"
        )

    def test_kb_upload_large_file_no_block(self):
        """静态分析: 验证上传路径无同步 write — _sync_write 是同步但只在 to_thread 内被调."""
        kb_path = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "api"
            / "knowledge_base.py"
        )
        source = kb_path.read_text(encoding="utf-8")

        # _sync_write 定义必须存在
        assert "def _sync_write" in source, "_sync_write 定义必须存在"
        assert "asyncio.to_thread(_sync_write" in source, (
            "调用模式 'asyncio.to_thread(_sync_write, ...)' 必须存在"
        )

        # 检查是否还有未替换的同步 with target.open("wb") 模式
        old_pattern = re.compile(r"with\s+target\.open\(\s*[\"']wb[\"']\s*\)")
        assert not old_pattern.search(source), (
            "不应再有 'with target.open(\"wb\")' 同步写模式 (P0 阻塞 event loop)"
        )

        # 校验 asyncio 已被 import
        assert "import asyncio" in source, "asyncio 必须被 import"
