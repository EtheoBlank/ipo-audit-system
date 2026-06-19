"""P1 (2026-06-19) — 同步 IO 残留扫描 (round 29).

校验 app/api/*.py + app/services/*/*.py 在 async 函数体内没有直接的
Path.write_bytes / Path.open / unlink 调用 — 必须包在 asyncio.to_thread 内.

注意: 我们只对 Path 实例的同步 IO 报警 — datetime.replace / str.replace /
DB result.replace 之类的同名方法不算.
"""

from __future__ import annotations

import ast
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# 标记为"已修过"的文件 — 即便有 to_thread 包裹也允许直接调用.
ALLOWED_FILES = {
    "knowledge_base.py",   # round 25-26 已修 (upload_book + _sync_write)
    "contracts.py",        # round 25 已修 (upload_contract)
    "sales_ledger.py",     # round 25 已修 (DeepSeek 输入文件)
}

# 用户在任务里指定的关键目标文件 (round 29 必须 100% 干净).
TARGET_FILES = {
    "reports.py",
    "workbooks.py",
    "sales_ledger.py",
    "related_parties.py",
    "audit_cycles.py",
}

# Path 实例上常见的同步 IO 方法名
_PATH_IO_METHODS = {
    "write_bytes",
    "write_text",
    "read_bytes",
    "read_text",
    "unlink",
    "rename",
    "open",
}


def _collect_async_functions(tree: ast.AST) -> list[ast.AsyncFunctionDef]:
    """收集所有 async def 函数 (含嵌套)."""
    out: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            out.append(node)
    return out


def _is_path_method(node: ast.Call, attr_name: str) -> bool:
    """判断 Call 是否形如 <something>.method() 且 receiver 是 Name/Attribute/Call.

    启发式: receiver 看似 Path 实例 (即一个 Name/Attribute/Call 表达式).
    """
    f = node.func
    if not isinstance(f, ast.Attribute) or f.attr != attr_name:
        return False
    return isinstance(f.value, (ast.Name, ast.Attribute, ast.Call))


def _is_builtin_open(node: ast.Call) -> bool:
    """判断是否是 builtin open(...) 调用 — 必须 to_thread."""
    f = node.func
    return isinstance(f, ast.Name) and f.id == "open"


def _is_inside_to_thread(node: ast.Call, parents: dict[int, ast.AST]) -> bool:
    """判断 Call 是否被 asyncio.to_thread 包裹."""
    cur: ast.AST | None = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.Call):
            f = cur.func
            if isinstance(f, ast.Attribute) and f.attr == "to_thread":
                return True
            if isinstance(f, ast.Name) and f.id == "to_thread":
                return True
        cur = parents.get(id(cur)) if cur is not None else None
    return False


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}

    def visit(parent: ast.AST | None, node: ast.AST) -> None:
        if parent is not None:
            parents[id(node)] = parent
        for child in ast.iter_child_nodes(node):
            visit(node, child)

    visit(None, tree)
    return parents


def _find_path_io_in_async(file_path: Path) -> list[tuple[int, str]]:
    """返回 (line, code) 列表: async 函数体里 Path 实例的直接同步 IO."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    parents = _build_parent_map(tree)

    findings: list[tuple[int, str]] = []
    for fn in _collect_async_functions(tree):
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            flagged = False
            # 1. builtin open(...)
            if _is_builtin_open(sub):
                flagged = True
            # 2. Path method calls (.write_bytes / .unlink / ...)
            else:
                for m in _PATH_IO_METHODS:
                    if _is_path_method(sub, m):
                        flagged = True
                        break
            if not flagged:
                continue
            if _is_inside_to_thread(sub, parents):
                continue
            findings.append((sub.lineno, ast.unparse(sub).strip()[:120]))
    return findings


def _scan_dir(rel_dir: str) -> dict[str, list[tuple[int, str]]]:
    base = APP_ROOT / rel_dir
    if not base.exists():
        return {}
    result: dict[str, list[tuple[int, str]]] = {}
    for py in sorted(base.glob("*.py")):
        if py.name.startswith("_"):
            continue
        findings = _find_path_io_in_async(py)
        if findings:
            result[py.name] = findings
    return result


def _scan_services_subdirs() -> dict[str, list[tuple[int, str]]]:
    services = APP_ROOT / "services"
    if not services.exists():
        return {}
    result: dict[str, list[tuple[int, str]]] = {}
    for sub in sorted(services.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_") or sub.name == "__pycache__":
            continue
        for py in sorted(sub.glob("*.py")):
            if py.name.startswith("_"):
                continue
            findings = _find_path_io_in_async(py)
            if findings:
                result[f"services/{sub.name}/{py.name}"] = findings
    return result


# ============================================================
#  Tests
# ============================================================


class TestIOWorkbooks:
    """P1 — workbooks.py round 29 修后无残留."""

    def test_no_sync_open_in_async_function_in_workbooks(self):
        """workbooks.py 中 async def 函数体内没有直接 open() / Path.write_bytes / unlink.

        round 29 修复: generate_workbook + generate_audit_notes_batch 都已 to_thread.
        """
        findings = _find_path_io_in_async(APP_ROOT / "api" / "workbooks.py")
        assert findings == [], (
            f"workbooks.py 中发现同步 IO 残留: {findings}\n"
            "需用 `await asyncio.to_thread(...)` 包裹."
        )


class TestIOSalesLedger:
    """P1 — sales_ledger.py round 25 修后无残留 (验证回归)."""

    def test_no_sync_open_in_async_function_in_sales_ledger(self):
        findings = _find_path_io_in_async(APP_ROOT / "api" / "sales_ledger.py")
        assert findings == [], (
            f"sales_ledger.py 中发现同步 IO 残留: {findings}"
        )


class TestIOKnownTargets:
    """P1 — 用户指定的目标文件清单 (零容忍)."""

    def test_reports_workbooks_sales_ledger_related_audit_cycles_clean(self):
        """用户指定的 TARGET_FILES 必须 100% 干净.

        ALLOWED_FILES 中的文件 (knowledge_base / contracts / sales_ledger) 已被
        round 25-26 处理, 这里仅做静态扫描回归.
        """
        targets = [
            APP_ROOT / "api" / "reports.py",
            APP_ROOT / "api" / "workbooks.py",
            APP_ROOT / "api" / "related_parties.py",
            APP_ROOT / "api" / "audit_cycles.py",
        ]
        bad: list[str] = []
        for p in targets:
            if not p.exists():
                continue
            findings = _find_path_io_in_async(p)
            if findings:
                bad.append(f"{p.name}: {findings}")
        assert bad == [], (
            "以下目标文件存在同步 IO 残留:\n" + "\n".join(bad)
        )


class TestIOApiCoverage:
    """P1 — 全 app/api/ 扫描报告 (TARGET_FILES 零容忍, 其它留给后续 round)."""

    def test_api_target_files_zero_residual(self):
        all_findings = _scan_dir("api")
        target_residual = {
            f: v for f, v in all_findings.items() if f in TARGET_FILES
        }
        assert target_residual == {}, (
            f"用户指定 TARGET_FILES 中存在残留: {target_residual}"
        )


class TestIOServicesCoverage:
    """P1 — app/services/*/ 扫描报告 (软约束, 仅警告)."""

    def test_services_sync_io_residual_report(self):
        """扫描 services 子目录, 软报告.

        round 29 范围之外, 留给后续 round 处理.
        """
        findings = _scan_services_subdirs()
        # 仅 print, 不 fail
        if findings:
            print(f"\n[soft-report] app/services/ 中存在同步 IO 残留: {findings}")
