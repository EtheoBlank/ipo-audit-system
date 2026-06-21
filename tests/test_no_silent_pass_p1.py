"""Round 29 P1 — 静态扫描 app/ 下裸 `except ...: pass` 模式.

本测试不做单元功能, 改用 AST 静态分析:
  - test_no_bare_pass_in_except_app: 扫描 app/**/*.py, 任何 except_clause
    体内首条 stmt 是 Pass 就报失败.
  - test_no_silent_pass_in_critical_paths: 关键文件 (confirmations / sentiment
    / audit_log / inventory) 严格不允许 silent pass (哪怕有注释也不行).

历史背景:
  - round 23 修了 9 处异常吞没 (audit_log/confirmations/notification)
  - round 29 修剩余 4 处: api/inventory.py unlink cleanup +
    services/confirmation/response_processor.py 日期 +
    services/inventory/count_plan.py _try_parse_date ISO 兜底 +
    services/inventory/photo_processor.py counted_at 兜底
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 关键路径 (相对 app/) — 这些模块一旦吞异常会掩盖业务问题
CRITICAL_PATHS = [
    "api/inventory.py",
    "api/confirmations.py",
    "api/sentiment.py",
    "services/audit_log",
    "services/notification",
    "services/inventory",
    "services/confirmation",
]


def _iter_app_py_files() -> list[Path]:
    """遍历 app/ 下所有 .py 文件 (排除 __pycache__)."""
    return [p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _find_bare_pass_in_except(tree: ast.AST) -> list[tuple[int, str]]:
    """AST 找 except_clause 体内**首条** stmt 是 Pass 的位置.

    只抓 bare `except ...: pass` (不抓 return None — 那是有意 fallback,
    例如日期解析器 ISO 失败 → return None 让上层走下一段解析).

    Returns:
        list[(line_no, "msg")]: 命中点 (line, 简单描述).
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not handler.body:
                continue
            first = handler.body[0]
            # 只抓 `except ...: pass` (Pass 是无操作, 才是真"无声吞掉")
            if isinstance(first, ast.Pass):
                hits.append(
                    (
                        handler.lineno,
                        f"bare `except ...: pass` at line {handler.lineno} "
                        f"(type={ast.unparse(handler.type) if handler.type else 'bare'})",
                    )
                )
    return hits


def test_no_bare_pass_in_except_app():
    """扫描整个 app/ 禁止 `except ...: pass` / `except ...: return` / `except ...: continue` 静默吞掉."""
    offenders: list[str] = []
    for py in _iter_app_py_files():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # 静态扫描忽略语法错
        for line, msg in _find_bare_pass_in_except(tree):
            offenders.append(f"{py.relative_to(PROJECT_ROOT)}:{line}  {msg}")
    assert not offenders, (
        "发现裸 except: pass / return / continue (无声吞掉异常), "
        "必须改为 logger.warning/debug 或 re-raise:\n  - " + "\n  - ".join(offenders)
    )


def test_no_silent_pass_in_critical_paths():
    """关键模块 (confirmations / sentiment / audit_log / inventory / notification) 严格无 silent pass."""
    offenders: list[str] = []
    for rel in CRITICAL_PATHS:
        # rel 既可能是文件也可能是目录
        target = APP_ROOT / rel
        if not target.exists():
            continue
        py_files: list[Path]
        if target.is_file():
            py_files = [target]
        else:
            py_files = [p for p in target.rglob("*.py") if "__pycache__" not in p.parts]
        for py in py_files:
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for line, msg in _find_bare_pass_in_except(tree):
                offenders.append(f"{py.relative_to(PROJECT_ROOT)}:{line}  {msg}")
    assert not offenders, (
        "关键模块存在 silent except 静默吞掉异常:\n  - " + "\n  - ".join(offenders)
    )


def test_specific_known_fixes_in_place():
    """round 29 修过的 4 处 silent pass 确认都加了 logger 留痕.

    如果未来重构把这 4 处的 logger 删了, 本测试会红.
    """
    expectations = [
        # (relative_path, 要包含的子串)
        ("app/api/inventory.py", "清理 OCR 失败照片 orphan 失败"),
        ("app/services/confirmation/response_processor.py", "received_date 解析失败"),
        ("app/services/inventory/count_plan.py", "ISO date 解析失败"),
        ("app/services/inventory/photo_processor.py", "counted_at 解析失败"),
    ]
    missing: list[str] = []
    for rel, needle in expectations:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        if needle not in text:
            missing.append(f"{rel} 缺少子串: {needle!r}")
    assert not missing, (
        "round 29 修过的 silent pass 留痕被破坏, 需要补回 logger.warning/debug:\n  - "
        + "\n  - ".join(missing)
    )
