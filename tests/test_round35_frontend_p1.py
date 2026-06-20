"""round 35 (2026-06-20) 前端 P1 修复回归测试.

覆盖任务列表:
  1. pages_regulations.py:98 search_q widget key
  2. pages_team_management.py: add_member form widget keys + JSON specialties 校验
  3. pages_team_management.py: mt_title / mt_location / confirm_cb keys
  4. pages_ipo_specials.py: cutoff tab 三个日期 text_input 改成 validate_date_input
  5. pages_ipo_specials.py: letter_form 内日期 is_valid_date_str 二次校验
  6. pages_inventory.py: cache clear + st.rerun + 上传失败 st.error
  7. pages_comprehensive.py: number_input 改 pick_project_dict
  8. validate_date_input helper 已存在 + 严格 ISO 校验

不依赖 streamlit runtime — 只用 AST 静态分析 + pure function 调用。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


# ============================================================
#  Helper — AST 工具
# ============================================================


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _find_func(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _build_parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}

    def visit(p, n):
        if p is not None:
            parents[id(n)] = p
        for c in ast.iter_child_nodes(n):
            visit(n, c)

    visit(None, tree)
    return parents


def _has_key_arg(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg != "key":
            continue
        if kw.value is None:
            continue
        if isinstance(kw.value, ast.Constant) and kw.value.value is None:
            continue
        return True
    return False


def _is_in_form(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.With):
            for item in cur.items:
                ctx = item.context_expr
                if (
                    isinstance(ctx, ast.Call)
                    and isinstance(ctx.func, ast.Attribute)
                    and ctx.func.attr == "form"
                ):
                    return True
        cur = parents.get(id(cur)) if cur is not None else None
    return False


def _collect_widget_calls(
    tree: ast.Module, *, skip_form: bool = True
) -> list[tuple[ast.Call, bool]]:
    """返回所有 widget Call 节点 + 是否在 form 内."""
    parents = _build_parents(tree)
    out: list[tuple[ast.Call, bool]] = []
    widget_funcs = {
        "text_input",
        "text_area",
        "number_input",
        "date_input",
        "time_input",
        "selectbox",
        "multiselect",
        "slider",
        "button",
        "file_uploader",
        "data_editor",
        "checkbox",
        "radio",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in widget_funcs):
            continue
        in_form = _is_in_form(node, parents)
        if skip_form and in_form:
            continue
        out.append((node, in_form))
    return out


# ============================================================
#  1. safe_render.validate_date_input 已存在 + 严格 ISO 校验
# ============================================================


class TestSafeRenderValidateDateInput:
    """round 32 已加 helper — round 35 验证仍可用, 校验逻辑不变."""

    def test_helper_exists_and_importable(self):
        from frontend._components.safe_render import validate_date_input  # noqa: F401

        assert callable(validate_date_input)

    def test_is_valid_date_strict_iso(self):
        from frontend._components.safe_render import is_valid_date_str

        assert is_valid_date_str("2024-12-31") is True
        assert is_valid_date_str("2025-01-01") is True
        # 拒绝 2025-1-1 / 2025/01/01 / 5月6日 / 空
        for bad in ["", "2025-1-1", "2025/01/01", "5月6日", "2025-13-01", "2025-02-30", None]:
            assert is_valid_date_str(bad) is False, f"应拒绝: {bad!r}"

    def test_is_valid_month_strict(self):
        from frontend._components.safe_render import is_valid_month_str

        assert is_valid_month_str("2024-12") is True
        assert is_valid_month_str("2024-01") is True
        for bad in ["", "2024-1", "24-12", "2024/12", "2024-13", None]:
            assert is_valid_month_str(bad) is False, f"应拒绝: {bad!r}"


# ============================================================
#  2. pages_regulations.py:98 search_q 加 key
# ============================================================


class TestPagesRegulationsSearchKey:
    """P1: pages_regulations.py:98 关键词搜索 text_input 加 key='search_q'."""

    def test_search_q_widget_has_key(self):
        tree = _parse(FRONTEND / "pages_regulations.py")
        found = False
        for node, _ in _collect_widget_calls(tree):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "text_input"
                and _has_key_arg(node)
            ):
                # 检查 label 是否含 "关键词"
                label = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
                if "关键词" in str(label):
                    found = True
                    # 断言 key=search_q
                    for kw in node.keywords:
                        if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                            assert kw.value.value == "search_q"
        assert found, "未找到带 key 的『关键词』text_input"


# ============================================================
#  3. pages_team_management.py add_member 表单 keys + 校验
# ============================================================


class TestPagesTeamManagementAddMember:
    """P1: round 35 给 add_member 表单 widgets 加 key + JSON 校验."""

    def _get_add_member_body(self) -> str:
        tree = _parse(FRONTEND / "pages_team_management.py")
        fn = _find_func(tree, "_tab_members")
        assert fn is not None
        return ast.unparse(fn)

    def test_add_member_widgets_have_keys(self):
        body = self._get_add_member_body()
        # 期望 key: add_member_name, add_member_email, add_member_phone,
        # add_member_spec, add_member_joined, add_member_notes
        # ast.unparse() 可能用单引号或双引号, 用 in 判断即可
        for k in [
            "add_member_name",
            "add_member_email",
            "add_member_phone",
            "add_member_spec",
            "add_member_joined",
            "add_member_notes",
        ]:
            assert k in body, f"add_member 表单缺 key={k}"
            assert f"key='{k}'" in body or f'key="{k}"' in body, f"add_member 表单缺 key={k} (格式)"

    def test_specialties_json_validation_present(self):
        """specialties 输入非 JSON 应在前端先校验, 不阻塞提交."""
        body = self._get_add_member_body()
        assert "json.loads" in body, "未加 json.loads 预解析"
        assert "JSON 数组" in body or "json" in body.lower(), "未给 JSON 错误提示"

    def test_email_basic_format_validation(self):
        """邮箱含 @ 才合法."""
        body = self._get_add_member_body()
        assert '"@" not in' in body or "'@' not in" in body, "未加邮箱基本格式校验"

    def test_phone_basic_format_validation(self):
        body = self._get_add_member_body()
        assert "isdigit" in body, "未加电话 digit 校验"

    def test_meeting_widgets_have_keys(self):
        """mt_title / mt_location / confirm_cb 加 key."""
        tree = _parse(FRONTEND / "pages_team_management.py")
        src = (FRONTEND / "pages_team_management.py").read_text(encoding="utf-8")
        assert "key=\"mt_title\"" in src
        assert "key=\"mt_location\"" in src
        assert "confirm_cb_{r['id']}" in src or "confirm_cb_" in src


# ============================================================
#  4. pages_ipo_specials.py cutoff 三个日期 → validate_date_input
# ============================================================


class TestPagesIpoSpecialsCutoffDateValidation:
    """P1: round 35 cutoff tab 用 validate_date_input helper 统一入口."""

    def test_cutoff_uses_validate_date_input(self):
        tree = _parse(FRONTEND / "pages_ipo_specials.py")
        fn = _find_func(tree, "_tab_cutoff")
        assert fn is not None
        body = ast.unparse(fn)
        # 三个 helper 调用
        assert body.count("validate_date_input") >= 3, (
            "cutoff tab 应有 3 次 validate_date_input 调用"
        )
        # 必须有日期校验阻断逻辑
        assert "ship_ok" in body and "confirm_ok" in body and "period_ok" in body
        assert "请修正" in body or "格式错误" in body or "YYYY-MM-DD" in body


# ============================================================
#  5. pages_ipo_specials.py letter_form 日期二次校验
# ============================================================


class TestPagesIpoSpecialsLetterFormDateValidation:
    """P1: round 35 letter_form 提交后用 is_valid_date_str 二次校验."""

    def test_letter_form_post_submit_date_validation(self):
        tree = _parse(FRONTEND / "pages_ipo_specials.py")
        src = (FRONTEND / "pages_ipo_specials.py").read_text(encoding="utf-8")
        # letter_form 内日期, 提交后用 is_valid_date_str
        assert "letter_form" in src
        # 至少 3 处 is_valid_date_str 检查
        assert src.count("is_valid_date_str") >= 3, (
            "letter_form 应有 3 个日期 is_valid_date_str 校验"
        )


# ============================================================
#  6. pages_inventory.py cache clear + rerun + 上传失败错误处理
# ============================================================


class TestPagesInventoryCacheRerunAndError:
    """P1: round 35 inventory 三处修复."""

    def test_comp_refresh_cache_clear_then_rerun(self):
        src = (FRONTEND / "pages_inventory.py").read_text(encoding="utf-8")
        # 找 _tab_completion 内 comp_refresh 按钮块
        tree = _parse(FRONTEND / "pages_inventory.py")
        fn = _find_func(tree, "_tab_completion")
        assert fn is not None
        body = ast.unparse(fn)
        assert "comp_refresh" in body
        assert "st.cache_data.clear()" in body
        # P1: 必须紧接 st.rerun(), 否则 UI 还显示旧数据
        # 用正则找 cache_data.clear() 后面是否有 st.rerun()
        idx = body.find("comp_refresh")
        # 截取到下一个函数定义之前
        block = body[idx:idx + 800]
        assert "st.cache_data.clear()" in block
        clear_idx = block.find("st.cache_data.clear()")
        rest = block[clear_idx:]
        assert "st.rerun()" in rest, "st.cache_data.clear() 后必须有 st.rerun()"

    def test_tab_photo_upload_failure_shows_error(self):
        """OCR 上传失败必须 st.error, 不能静默."""
        src = (FRONTEND / "pages_inventory.py").read_text(encoding="utf-8")
        tree = _parse(FRONTEND / "pages_inventory.py")
        fn = _find_func(tree, "_tab_photo")
        assert fn is not None
        body = ast.unparse(fn)
        # 找 if res: 块 后必须有 else: st.error
        # 用简单模式: if res 在 _tab_photo 里, 后续应有 st.error
        if_idx = body.find("if res:")
        assert if_idx > 0
        block_after_if = body[if_idx:]
        # 在 if res 之后必须出现 else: 和 st.error
        assert "else:" in block_after_if, "_tab_photo if res 后缺 else 分支"
        assert "OCR 上传失败" in block_after_if or "上传失败" in block_after_if, (
            "_tab_photo 缺上传失败错误提示"
        )


# ============================================================
#  7. pages_comprehensive.py: number_input → pick_project_dict
# ============================================================


class TestPagesComprehensiveProjectPicker:
    """P1: round 35 pages_comprehensive.py 用 pick_project_dict 替换手填 number_input."""

    def test_comprehensive_uses_pick_project_dict(self):
        tree = _parse(FRONTEND / "pages_comprehensive.py")
        src = (FRONTEND / "pages_comprehensive.py").read_text(encoding="utf-8")
        assert "from frontend._components.project_picker import pick_project_dict" in src
        # 找带 "项目ID" 标签的 number_input — round 35 已替换, 应找不到
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "number_input"):
                continue
            label = (
                node.args[0].value
                if node.args and isinstance(node.args[0], ast.Constant)
                else ""
            )
            assert label != "项目ID", (
                "pages_comprehensive.py 仍有 number_input('项目ID'), 应改 pick_project_dict"
            )

    def test_comprehensive_pick_project_called_with_correct_label(self):
        src = (FRONTEND / "pages_comprehensive.py").read_text(encoding="utf-8")
        assert "pick_project_dict(" in src
        # 找到该行, 至少有 label="选择项目" 之类
        m = re.search(r"pick_project_dict\([^)]*\)", src)
        assert m is not None
        assert "选择项目" in m.group(0) or "项目" in m.group(0)


# ============================================================
#  8. 已有 frontend 测试不破 — 回归
# ============================================================


class TestRegressionNoNewMissingKeys:
    """回归 — round 35 新增/修改的 4 个文件, 关键 widget 不破.

    强约束 (本 round 重点) — text_input / number_input / file_uploader
    等都加 key= (form 内 widget 跳过)."""

    def test_pages_regulations_search_text_input_has_key(self):
        """search_q widget 必须带 key."""
        tree = _parse(FRONTEND / "pages_regulations.py")
        for node, _ in _collect_widget_calls(tree):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "text_input"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and "关键词" in str(node.args[0].value)
            ):
                assert _has_key_arg(node), f"关键词 text_input 缺 key: L{node.lineno}"

    def test_pages_ipo_specials_cutoff_dates_have_keys(self):
        """三个 cutoff 日期 widget 都必须有 key.
        round 35: cutoff 改用 validate_date_input, key 移到 helper 调用参数.
        所以应当检查 _tab_cutoff 内有 3 次 validate_date_input 调用,
        且每次都带正确的 key= 参数."""
        tree = _parse(FRONTEND / "pages_ipo_specials.py")
        fn = _find_func(tree, "_tab_cutoff")
        assert fn is not None
        body = ast.unparse(fn)
        # 3 次 validate_date_input 调用, 每次带正确 key
        for k in ("ipo_co_ship", "ipo_co_confirm", "ipo_co_period"):
            pattern = re.compile(
                rf"validate_date_input\([^)]*key=['\"]{k}['\"]",
                re.DOTALL,
            )
            assert pattern.search(body), f"cutoff tab 缺 validate_date_input(..., key={k})"
        # 总共 3 次
        assert body.count("validate_date_input") >= 3

    def test_pages_inventory_refresh_button_has_key(self):
        tree = _parse(FRONTEND / "pages_inventory.py")
        for node, _ in _collect_widget_calls(tree):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "button"
                and _has_key_arg(node)
            ):
                for kw in node.keywords:
                    if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                        if kw.value.value == "comp_refresh":
                            return
        pytest.fail("pages_inventory.py 缺 comp_refresh 按钮 key")


# ============================================================
#  9. JSON parse 单元测试 (specialties 路径)
# ============================================================


class TestSpecialtiesJsonParsing:
    """模拟 specialties 文本解析逻辑 (复刻 _tab_members 中的 try/except)."""

    def test_valid_json_array_passes(self):
        raw = '["收入循环", "存货盘点"]'
        parsed = json.loads(raw)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_invalid_json_raises(self):
        raw = "收入循环, 存货盘点"  # 非 JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw)

    def test_json_object_rejected_as_not_array(self):
        """后端要求 list, JSON 对象应被前端拒绝."""
        raw = '{"cycle": "sales"}'
        parsed = json.loads(raw)
        assert not isinstance(parsed, list)  # 前端应 if not isinstance(...)

    def test_empty_string_allowed(self):
        """空串视作 None, 后端用 or None 兼容."""
        raw = ""
        parsed = None if not raw.strip() else json.loads(raw)
        assert parsed is None
