"""P1 (2026-06-19) — 前端 widget key= 缺失扫描 (round 29).

校验 frontend/pages_*.py + frontend/app.py 中 file_uploader / data_editor
必须有 key= 参数 (tab 切换防重置).

text_input / selectbox / number_input 等虽然建议加 key, 但允许 form 块内省略.
"""

from __future__ import annotations

import ast
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parent.parent / "frontend"

# 强约束 — 必须在所有文件中带 key=
_STRICT_WIDGET_FUNCS = {
    "file_uploader",
    "data_editor",
}

# 软约束 — 建议带 key, 但不强制
_LOOSE_WIDGET_FUNCS = {
    "text_input",
    "text_area",
    "number_input",
    "date_input",
    "time_input",
    "selectbox",
    "multiselect",
    "slider",
}


def _has_key_arg(node: ast.Call) -> bool:
    """检查 Call 节点是否带非 None 的关键字参数 key=."""
    for kw in node.keywords:
        if kw.arg == "key":
            if kw.value is None:
                continue
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                continue
            return True
    return False


def _is_form_call(node: ast.Call) -> bool:
    """判断是否是 `st.form(...)` 调用 — 用于识别 form 上下文."""
    f = node.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "form"
    )


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}

    def visit(parent: ast.AST | None, node: ast.AST) -> None:
        if parent is not None:
            parents[id(node)] = parent
        for child in ast.iter_child_nodes(node):
            visit(node, child)

    visit(None, tree)
    return parents


def _is_inside_form(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """判断 node 是否在 `with st.form(...)` 块内.

    Streamlit form 块内部 widget 自动管理 state, 不需要 key.
    形式: `with st.form("xxx"): <widget call>`
    即 widget Call 的某个祖先是 ast.With, 且 with.items[*].context_expr 是 st.form(...) 调用.
    """
    cur: ast.AST | None = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.With):
            for item in cur.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and _is_form_call(ctx):
                    return True
        cur = parents.get(id(cur)) if cur is not None else None
    return False


def _find_missing_key_in_file(
    file_path: Path,
    strict_only: bool = False,
) -> list[tuple[int, str]]:
    """返回 (line, code) 列表: 缺 key= 的 widget.

    strict_only=True: 只检查 file_uploader + data_editor (强约束).
    strict_only=False: 检查所有 widget (软约束, 用于报告).
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    parents = _build_parent_map(tree)

    target_funcs = _STRICT_WIDGET_FUNCS if strict_only else (
        _STRICT_WIDGET_FUNCS | _LOOSE_WIDGET_FUNCS
    )

    missing: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in target_funcs):
            continue
        if _has_key_arg(node):
            continue
        # form 块内部允许省略 key (Streamlit 自动管理)
        if _is_inside_form(node, parents):
            continue
        missing.append((node.lineno, ast.unparse(node).strip()[:120]))
    return missing


def _scan_frontend_files(strict_only: bool = True) -> dict[str, list[tuple[int, str]]]:
    """扫描 frontend/ 下所有 pages_*.py + app.py."""
    result: dict[str, list[tuple[int, str]]] = {}
    if not FRONTEND_ROOT.exists():
        return result
    targets = sorted(FRONTEND_ROOT.glob("pages_*.py")) + [
        FRONTEND_ROOT / "app.py"
    ]
    for py in targets:
        if not py.exists():
            continue
        findings = _find_missing_key_in_file(py, strict_only=strict_only)
        if findings:
            result[py.name] = findings
    return result


# ============================================================
#  Tests
# ============================================================


class TestFrontendKeyPagesComprehensive:
    """P1 — pages_comprehensive.py 所有 file_uploader / data_editor 都有 key=."""

    def test_pages_comprehensive_file_uploader_has_keys(self):
        path = FRONTEND_ROOT / "pages_comprehensive.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path, strict_only=True)
        assert findings == [], (
            f"pages_comprehensive.py 中发现缺 key= 的 file_uploader/data_editor: {findings}"
        )


class TestFrontendKeyPagesAuditCycles:
    """P1 — pages_audit_cycles.py 所有 file_uploader / data_editor 都有 key=."""

    def test_pages_audit_cycles_file_uploader_has_keys(self):
        path = FRONTEND_ROOT / "pages_audit_cycles.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path, strict_only=True)
        assert findings == [], (
            f"pages_audit_cycles.py 中发现缺 key= 的 file_uploader/data_editor: {findings}"
        )


class TestFrontendKeyApp:
    """P1 — app.py 主界面 file_uploader / data_editor 都有 key=."""

    def test_app_file_uploader_has_keys(self):
        path = FRONTEND_ROOT / "app.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path, strict_only=True)
        assert findings == [], (
            f"frontend/app.py 中发现缺 key= 的 file_uploader/data_editor: {findings}"
        )


class TestFrontendKeyPagesAccountAuditRegression:
    """回归 — pages_account_audit.py round 14 已修."""

    def test_pages_account_audit_file_uploader_has_keys(self):
        path = FRONTEND_ROOT / "pages_account_audit.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path, strict_only=True)
        assert findings == [], (
            f"pages_account_audit.py 出现缺 key= (回归): {findings}"
        )


class TestFrontendKeyFullScan:
    """P1 — 全 frontend/ 强约束扫描 (file_uploader / data_editor)."""

    REQUIRED_KEY_FILES = {
        "pages_comprehensive.py",
        "pages_audit_cycles.py",
        "app.py",
    }

    def test_required_key_files_clean(self):
        all_findings = _scan_frontend_files(strict_only=True)
        bad = {
            f: v for f, v in all_findings.items()
            if f in self.REQUIRED_KEY_FILES
        }
        assert bad == {}, (
            f"以下关键前端文件存在缺 key= 的 widget:\n"
            + "\n".join(f"{f}: {v}" for f, v in bad.items())
        )

    def test_all_pages_file_uploader_have_keys(self):
        """全部 pages_*.py 中 file_uploader / data_editor 都必须有 key."""
        all_findings = _scan_frontend_files(strict_only=True)
        # 排除不在 REQUIRED_KEY_FILES 中的 (其它文件留待后续 round)
        bad = {
            f: v for f, v in all_findings.items()
            if f.startswith("pages_") or f == "app.py"
        }
        assert bad == {}, (
            f"以下文件存在缺 key= 的 file_uploader/data_editor:\n"
            + "\n".join(f"{f}: {v}" for f, v in bad.items())
        )
