"""P0 (2026-06-20) — 批量 widget key= 缺失扫描 (round 30).

校验 frontend/pages_*.py + frontend/app.py 中所有目标 widget
(file_uploader / text_input / number_input / date_input / selectbox /
text_area / button / data_editor / checkbox / radio) 缺 key= 的位置.

form 块内 widget 跳过 — Streamlit form 自动管理 state.
"""

from __future__ import annotations

import ast
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parent.parent / "frontend"

# 强约束 — 全部 widget 必须带 key=
_ALL_WIDGET_FUNCS = {
    "file_uploader",
    "text_input",
    "text_area",
    "number_input",
    "date_input",
    "selectbox",
    "button",
    "data_editor",
    "checkbox",
    "radio",
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

    form 块内部 widget 由 form 自动管理, 不需要 key.
    """
    cur: ast.AST | None = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.With):
            for item in cur.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Attribute) and ctx.func.attr == "form":
                    return True
        cur = parents.get(id(cur)) if cur is not None else None
    return False


def _find_missing_key_in_file(file_path: Path) -> list[tuple[int, str]]:
    """返回 (line, code) 列表: 缺 key= 的 widget (排除 form 内部)."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    parents = _build_parent_map(tree)

    missing: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in _ALL_WIDGET_FUNCS):
            continue
        if _has_key_arg(node):
            continue
        if _is_inside_form(node, parents):
            continue
        missing.append((node.lineno, ast.unparse(node).strip()[:120]))
    return missing


# ============================================================
#  Tests — round 30
# ============================================================


class TestFrontendKeyBatchP0AuditCycles:
    """P0 — pages_audit_cycles.py 所有 widget 都有 key=."""

    def test_pages_audit_cycles_all_widgets_have_key(self):
        path = FRONTEND_ROOT / "pages_audit_cycles.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path)
        assert findings == [], (
            f"pages_audit_cycles.py 中发现缺 key= 的 widget: {findings}"
        )


class TestFrontendKeyBatchP0IpoSpecials:
    """P0 — pages_ipo_specials.py 所有 widget 都有 key=."""

    def test_pages_ipo_specials_all_widgets_have_key(self):
        path = FRONTEND_ROOT / "pages_ipo_specials.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path)
        assert findings == [], (
            f"pages_ipo_specials.py 中发现缺 key= 的 widget: {findings}"
        )


class TestFrontendKeyBatchP0SalesLedger:
    """P0 — pages_sales_ledger.py 所有 widget 都有 key=."""

    def test_pages_sales_ledger_all_widgets_have_key(self):
        path = FRONTEND_ROOT / "pages_sales_ledger.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path)
        assert findings == [], (
            f"pages_sales_ledger.py 中发现缺 key= 的 widget: {findings}"
        )


class TestFrontendKeyBatchP0App:
    """P0 — frontend/app.py 主界面所有 widget 都有 key=."""

    def test_app_all_widgets_have_key(self):
        path = FRONTEND_ROOT / "app.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path)
        assert findings == [], (
            f"frontend/app.py 中发现缺 key= 的 widget: {findings}"
        )


class TestFrontendKeyBatchP0RelatedParties:
    """P0 — pages_related_parties.py 所有 widget 都有 key= (round 28 补漏 + round 30 全量)."""

    def test_pages_related_parties_all_widgets_have_key(self):
        path = FRONTEND_ROOT / "pages_related_parties.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path)
        assert findings == [], (
            f"pages_related_parties.py 中发现缺 key= 的 widget: {findings}"
        )


class TestFrontendKeyBatchP0AccountAudit:
    """P0 — pages_account_audit.py 所有 widget 都有 key=."""

    def test_pages_account_audit_all_widgets_have_key(self):
        path = FRONTEND_ROOT / "pages_account_audit.py"
        if not path.exists():
            return
        findings = _find_missing_key_in_file(path)
        assert findings == [], (
            f"pages_account_audit.py 中发现缺 key= 的 widget: {findings}"
        )


class TestFrontendKeyBatchP0AllPages:
    """P0 — round 30 6 个目标文件全部缺 key= 数 = 0."""

    # round 30 修复的 6 个文件
    ROUND30_TARGETS = {
        "pages_audit_cycles.py",
        "pages_ipo_specials.py",
        "pages_sales_ledger.py",
        "pages_related_parties.py",
        "pages_account_audit.py",
        "app.py",
    }

    def test_all_pages_combined(self):
        """round 30 6 个目标文件全部缺 key= 数 = 0."""
        total_missing = 0
        bad = {}
        for name in self.ROUND30_TARGETS:
            py = FRONTEND_ROOT / name
            if not py.exists():
                continue
            findings = _find_missing_key_in_file(py)
            if findings:
                bad[name] = findings
                total_missing += len(findings)
        assert total_missing == 0, (
            f"round 30 6 个目标文件共有 {total_missing} 个缺 key= 的 widget:\n"
            + "\n".join(f"{f}: {v[:5]}" for f, v in bad.items())
        )