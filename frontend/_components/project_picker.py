"""项目选择器 (project picker) — 5+ 个 pages_*.py 各自实现一份.

提供:
  - pick_project()         — 返回 project_id (int), 或 None (用户取消)
  - pick_project_dict()    — 返回 project dict (含 id, name, company_name, fiscal_year, industry)

两者都走 @st.cache_data(ttl=60) 的 _get_projects(), 缓存项目列表.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from frontend._http import api_request


# P0: 取消缓存, 每次都拉新 (firm_id 已写入 token claims, 后端 scope 隔离)
def _get_projects() -> List[Dict[str, Any]]:
    """拉取项目列表 — 走共享 api_request, 自动带 auth 头 + 统一错误处理."""
    return api_request("GET", "/api/projects/") or []


def _label_project(p: Dict[str, Any], *, fmt: str = "with_company") -> str:
    """统一 label 格式 — 几个 pages_*.py 各自写过的拼字符串.

    fmt 选项:
      - "with_company"   : "id - name / company_name" (Pack B / Pack A)
      - "name_only"      : "id - name"                (Pack C / Pack D)
      - "with_industry"  : "id - name (industry)"     (industry 默认行为)
      - "with_industry_fallback": "id - name (未填行业)"  (inventory 旧行为)
      - "team_mgmt"      : "#id name (company_name)"  (team_management / sales_ledger)
      - "sentiment"      : "name (#id · company_name)" (sentiment)
    """
    pid = p.get("id", "?")
    name = p.get("name", "")
    company = p.get("company_name", "")
    if fmt == "name_only":
        return f"{pid} - {name}"
    if fmt == "with_industry":
        ind = p.get("industry")
        suffix = f" ({ind})" if ind else ""
        return f"{pid} - {name}{suffix}"
    if fmt == "with_industry_fallback":
        ind = p.get("industry") or "未填行业"
        return f"{pid} - {name} ({ind})"
    if fmt == "team_mgmt":
        return f"#{pid} {name} ({company})"
    if fmt == "sentiment":
        return f"{name} (#{pid} · {company})"
    return f"{pid} - {name} / {company}"


def pick_project(
    label: str = "选择项目",
    *,
    key: Optional[str] = None,
    fmt: str = "with_company",
    no_projects_warning: str = "尚未创建项目, 请先在 '📁 项目管理' 创建",
) -> Optional[int]:
    """统一项目选择器 (返回 project_id).

    行为等价于历史 pages_*.py 里的 _pick_project().
    fmt 见 _label_project.
    no_projects_warning 可定制警告文本 (Pack D 原本用更短版).
    """
    projects = _get_projects()
    if not projects:
        st.warning(no_projects_warning)
        return None
    options = {_label_project(p, fmt=fmt): p["id"] for p in projects}
    chosen = st.selectbox(label, list(options.keys()), key=key)
    return options.get(chosen)


def pick_project_dict(
    label: str = "选择项目",
    *,
    key: Optional[str] = None,
    fmt: str = "team_mgmt",
    no_projects_warning: str = "⚠️ 请先在『项目管理』中创建一个项目。",
) -> Optional[Dict[str, Any]]:
    """统一项目选择器 (返回完整 project dict).

    team_management / sales_ledger 需要完整 dict (name / industry / fiscal_year 等).
    """
    projects = _get_projects()
    if not projects:
        st.warning(no_projects_warning)
        return None
    options = {_label_project(p, fmt=fmt): p for p in projects}
    chosen = st.selectbox(label, list(options.keys()), key=key)
    return options.get(chosen)


__all__ = ["pick_project", "pick_project_dict", "_get_projects"]
