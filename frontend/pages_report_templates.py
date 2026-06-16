"""报告模板管理页面 (Pack A — Phase 20)."""

from __future__ import annotations

import json
from typing import Any, Dict

import pandas as pd
import streamlit as st

from frontend._http import api_request


_REPORT_TYPE_LABELS = {
    "audit_report": "审计报告",
    "management_letter": "管理建议书",
    "walkthrough_report": "内控穿行报告",
    "sentiment_briefing": "舆情简报",
    "related_party_report": "关联方专项报告",
    "comprehensive_report": "综合报告",
    "custom": "自定义",
}


def _api(method: str, endpoint: str, *, expect_bytes: bool = False, **kwargs):
    """薄封装 — 复用共享 _http.api_request."""
    return api_request(method, endpoint, expect_bytes=expect_bytes, timeout=60, **kwargs)


def _tab_list() -> None:
    st.markdown("### 📋 模板清单")
    cols = st.columns(3)
    report_type = cols[0].selectbox(
        "类型",
        ["全部"] + list(_REPORT_TYPE_LABELS.keys()),
        format_func=lambda k: "全部" if k == "全部" else _REPORT_TYPE_LABELS[k],
    )
    active_filter = cols[1].selectbox("状态", ["全部", "active", "inactive"])
    firm_id = cols[2].number_input("事务所 ID (0=全部)", min_value=0, step=1, value=0)

    params: Dict[str, Any] = {"limit": 200}
    if report_type != "全部":
        params["report_type"] = report_type
    if active_filter != "全部":
        params["is_active"] = active_filter == "active"
    if firm_id:
        params["firm_id"] = int(firm_id)

    res = _api("GET", "/api/report-templates", params=params) or {"total": 0, "items": []}
    items = res.get("items", [])
    if items:
        df = pd.DataFrame(
            [
                {
                    "ID": t["id"],
                    "代码": t["template_code"],
                    "名称": t["template_name"],
                    "类型": _REPORT_TYPE_LABELS.get(t["report_type"], t["report_type"]),
                    "版本": t["version"],
                    "格式": t["output_format"],
                    "事务所": t.get("firm_id") or "-",
                    "状态": "✅" if t["is_active"] else "🚫",
                    "大小(KB)": round((t["template_size"] or 0) / 1024, 1),
                    "创建人": t.get("created_by_display") or "-",
                    "更新时间": t.get("updated_at"),
                }
                for t in items
            ]
        )
        st.dataframe(df, width="stretch", height=400)
    else:
        st.info("无模板, 请上传")


def _tab_upload() -> None:
    st.markdown("### ⬆️ 上传新模板")
    st.caption(
        "模板里用 ``${name}`` 占位符, 渲染时按 context 字段替换。支持点号 ``${section.field}`` "
        "嵌套字段。常见 placeholder: company_name / fiscal_year / report_date / firm_name / signing_partner。"
    )
    with st.form("upload_tpl"):
        c1, c2, c3 = st.columns(3)
        template_code = c1.text_input("模板代码 (英文/数字/_)*", value="")
        template_name = c2.text_input("模板名称*", value="")
        report_type = c3.selectbox(
            "类型*",
            list(_REPORT_TYPE_LABELS.keys()),
            format_func=lambda k: _REPORT_TYPE_LABELS[k],
        )

        c4, c5, c6 = st.columns(3)
        output_format = c4.selectbox("输出格式", ["docx", "xlsx"])
        version = c5.text_input("版本", value="v1")
        firm_id = c6.number_input("事务所 ID (0=不指定)", min_value=0, step=1, value=0)

        description = st.text_area("说明", height=68)
        file = st.file_uploader("模板文件", type=["docx", "xlsx", "dotx", "xltx"])

        ok = st.form_submit_button("上传", type="primary")

    if ok:
        if not (template_code and template_name and file):
            st.error("模板代码/名称/文件必填")
            return
        files = {"file": (file.name, file.read(), file.type or "application/octet-stream")}
        data = {
            "template_code": template_code,
            "template_name": template_name,
            "report_type": report_type,
            "output_format": output_format,
            "version": version,
            "description": description or "",
        }
        if firm_id:
            data["firm_id"] = str(int(firm_id))
        res = _api("POST", "/api/report-templates", files=files, data=data)
        if res:
            st.success(f"已上传 {res['template_code']} v{res['version']} (id={res['id']})")
            st.rerun()


def _tab_preview_render() -> None:
    st.markdown("### 👁️ 预览 / 渲染")
    template_id = st.number_input("模板 ID", min_value=0, step=1, value=0)
    if not template_id:
        st.info("请输入有效的模板 ID")
        return

    tpl = _api("GET", f"/api/report-templates/{template_id}")
    if not tpl:
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("代码", tpl["template_code"])
    c2.metric("类型", _REPORT_TYPE_LABELS.get(tpl["report_type"], tpl["report_type"]))
    c3.metric("版本", tpl["version"])

    st.markdown("**Placeholder 解析**")
    analysis = _api("GET", f"/api/report-templates/{template_id}/analyze") or {}
    placeholders = analysis.get("placeholders", [])
    if placeholders:
        st.write(f"📌 共 {len(placeholders)} 个 placeholder: {', '.join(placeholders)}")
        suggestions = analysis.get("suggested_context_keys") or {}
        if suggestions:
            st.json(suggestions)
    else:
        st.info("此模板没有 placeholder, 渲染后内容不变")

    with st.expander("🛠️ 渲染 (传入 context JSON)", expanded=True):
        default_ctx = {ph: f"<{ph}>" for ph in placeholders}
        ctx_text = st.text_area(
            "Context JSON",
            value=json.dumps(default_ctx, ensure_ascii=False, indent=2),
            height=200,
        )
        cc1, cc2 = st.columns(2)
        project_id = cc1.number_input("关联项目 ID (可选)", min_value=0, step=1, value=0)
        output_name = cc2.text_input("输出文件名 (可选)")

        if st.button("🚀 渲染", type="primary"):
            try:
                ctx = json.loads(ctx_text or "{}")
            except Exception as exc:
                st.error(f"Context JSON 解析失败: {exc}")
                return
            payload = {
                "template_id": template_id,
                "context": ctx,
                "project_id": int(project_id) or None,
                "output_filename": output_name or None,
            }
            content = _api("POST", "/api/report-templates/render", json=payload, expect_bytes=True)
            if content:
                ext = ".docx" if tpl["output_format"] == "docx" else ".xlsx"
                mime = (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    if ext == ".docx"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                fname = (output_name or f"{tpl['template_code']}_{tpl['version']}") + ext
                if not fname.endswith(ext):
                    fname += ext
                st.download_button(
                    "💾 下载渲染结果",
                    data=content,
                    file_name=fname,
                    mime=mime,
                )


def _tab_manage() -> None:
    st.markdown("### 🛠️ 修改 / 停用 / 删除")
    template_id = st.number_input("模板 ID", min_value=0, step=1, value=0, key="mng_id")
    if not template_id:
        st.info("请输入模板 ID")
        return

    tpl = _api("GET", f"/api/report-templates/{template_id}")
    if not tpl:
        return

    with st.form("mng_form"):
        name = st.text_input("名称", value=tpl["template_name"])
        desc = st.text_area("说明", value=tpl.get("description") or "")
        is_active = st.checkbox("启用", value=bool(tpl["is_active"]))
        ok = st.form_submit_button("保存", type="primary")
    if ok:
        payload = {
            "template_name": name,
            "description": desc,
            "is_active": is_active,
        }
        res = _api("PUT", f"/api/report-templates/{template_id}", json=payload)
        if res:
            st.success("已更新")
            st.rerun()

    st.markdown("---")
    if st.button(f"🗑️ 删除模板 {template_id}", type="secondary"):
        res = _api("DELETE", f"/api/report-templates/{template_id}")
        if res:
            st.success("已删除")
            st.rerun()

    content = _api("GET", f"/api/report-templates/{template_id}/download", expect_bytes=True)
    if content:
        st.download_button(
            "📥 下载原模板",
            data=content,
            file_name=tpl["template_filename"],
            mime="application/octet-stream",
        )


def show_report_templates() -> None:
    st.markdown(
        '<p style="font-size:1.8rem;font-weight:bold;color:#4472C4;">🎨 报告模板自定义化</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "事务所品牌定制: 上传 Word/Excel 模板, 用 ``${placeholder}`` 嵌入字段, 系统按 context 渲染。"
    )

    tabs = st.tabs(["📋 清单", "⬆️ 上传", "👁️ 预览/渲染", "🛠️ 管理"])
    with tabs[0]:
        _tab_list()
    with tabs[1]:
        _tab_upload()
    with tabs[2]:
        _tab_preview_render()
    with tabs[3]:
        _tab_manage()
