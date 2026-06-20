"""Streamlit page for the contract analysis (收入合同五步法) module.

Workflow:
  1) 合同上传 — 图片/PDF 走 OCR，纯文本直传
  2) 列表 — 显示项目下所有合同
  3) 五步法 — 选中一份合同跑 CAS 14 五步法
  4) 要点抽取 — 7 字段基础要点
  5) 风险扫描 — 本地关键词扫描结果
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import streamlit as st

from frontend._components import apply_feishu_theme, page_header
from frontend.app import api_request, get_projects


# ---------- helpers ------------------------------------------------------


def _projects_selectbox(label: str = "选择项目") -> Optional[dict[str, Any]]:
    projects = get_projects() or []
    if not projects:
        st.warning("⚠️ 请先在『项目管理』中创建一个项目。")
        return None
    options = {f"#{p['id']} {p['name']} ({p.get('company_name', '')})": p for p in projects}
    chosen = st.selectbox(label, list(options.keys()), key="contracts_proj_chosen")  # round 31 widget key
    return options.get(chosen)


def _render_key_points(data: dict[str, Any]) -> None:
    if not data:
        st.caption("(无要点数据)")
        return
    if "error" in data:
        st.error(data["error"])
        return
    rows = [
        ("合同号", data.get("contract_no")),
        ("甲方", data.get("party_a")),
        ("乙方", data.get("party_b")),
        ("总金额", f"{data.get('total_amount', 0):,.2f} {data.get('currency', 'CNY')}"),
        ("有效期", data.get("effective_period")),
        ("违约/争议", data.get("breach_dispute")),
        ("补充协议", data.get("side_letter")),
    ]
    st.dataframe(
        pd.DataFrame(rows, columns=["项目", "内容"]),
        use_container_width=True,
        hide_index=True,
    )


def _render_five_step(data: dict[str, Any]) -> None:
    if not data:
        st.caption("(无五步法结果)")
        return
    if "error" in data:
        st.error(data["error"])
        return
    step1 = data.get("step1_contract_identification") or {}
    step2 = data.get("step2_contract_modification") or {}
    pos = data.get("step3_performance_obligations") or []
    step4 = data.get("step4_transaction_price") or {}
    step5 = data.get("step5_recognition") or []

    st.markdown("##### ① 合同识别")
    st.json(step1)
    st.markdown("##### ② 合同变更")
    st.json(step2)
    st.markdown(f"##### ③ 履约义务分拆（{len(pos)} 项）")
    st.dataframe(pd.DataFrame(pos), use_container_width=True, hide_index=True)
    st.markdown("##### ④ 交易价格")
    st.json(step4)
    st.markdown(f"##### ⑤ 收入确认（{len(step5)} 项）")
    st.dataframe(pd.DataFrame(step5), use_container_width=True, hide_index=True)
    warns = data.get("audit_warnings") or []
    if warns:
        st.markdown("##### ⚠️ 审计关注点")
        for w in warns:
            st.markdown(f"- {w}")


# ---------- main entry point --------------------------------------------


def show_contracts() -> None:
    apply_feishu_theme()
    page_header('📄', '收入合同分析', 'OCR 识别 + CAS 14 五步法分析 + 风险扫描')

    # [飞书化]     st.markdown("## 📄 收入合同分析")  # 已被 page_header() 替代

    st.caption("上传合同图片 / 扫描件 → OCR → CAS 14 五步法分析 + 7 字段要点提取 + 风险扫描")

    project = _projects_selectbox()
    if not project:
        return
    project_id = project["id"]
    st.info(f"当前项目：**{project['name']}** | 公司：{project.get('company_name', '')}")

    tab_upload, tab_list, tab_analyse = st.tabs(["📤 上传合同", "📋 合同列表", "🤖 五步法分析"])

    # --- Tab 1: 上传 --------------------------------------------------
    with tab_upload:
        st.subheader("上传合同")
        mode = st.radio(
            "输入方式",
            ["📷 图片 / 扫描 PDF (OCR)", "📝 纯文本 (跳过 OCR)"],
            horizontal=True,
            key="contracts_input_mode",  # round 31 widget key
        )

        if mode.startswith("📷"):
            files = st.file_uploader(
                "选择合同图片 / PDF（可多份）",
                type=["png", "jpg", "jpeg", "bmp", "tiff", "pdf"],
                accept_multiple_files=True,
                key="contracts_image_upload",
            )
            note = st.text_input("备注", "", key="contracts_image_note")  # round 31 widget key
            if st.button("📤 上传并 OCR", type="primary", key="contracts_upload_ocr"):  # round 31 widget key
                if not files:
                    st.warning("请先选择文件。")
                else:
                    ok = 0
                    for f in files:
                        files_param = {
                            "file": (f.name, f.read(), f.type or "application/octet-stream")
                        }
                        data = {"note": note} if note else None
                        r = api_request(
                            "POST",
                            f"/api/contracts/projects/{project_id}/contracts",
                            files=files_param,
                            data=data,
                        )
                        if r is not None:
                            ok += 1
                    if ok:
                        st.success(f"✅ 成功上传 {ok} 份合同（OCR 完成）")
                        st.rerun()
        else:
            st.write(
                "适用于：① 已经用本地 OCR 工具跑过；② 不想装 paddleocr 等依赖；"
                "③ 文本量较小、复制粘贴方便。"
            )
            filename = st.text_input("文件名（自定义）", value=f"contract_{project_id}.txt", key="contracts_text_fname")  # round 31 widget key
            text = st.text_area("合同文本", height=240, placeholder="把合同正文粘到这里…", key="contracts_text_body")  # round 31 widget key
            note = st.text_input("备注", "", key="text_note")
            if st.button("📤 上传文本", type="primary", key="contracts_upload_text"):  # round 31 widget key
                if not text.strip():
                    st.warning("请先粘贴合同文本。")
                else:
                    payload = {"filename": filename, "text": text, "note": note or None}
                    r = api_request(
                        "POST",
                        f"/api/contracts/projects/{project_id}/contracts/text",
                        json=payload,
                    )
                    if r is not None:
                        st.success("✅ 文本已保存")
                        st.rerun()

    # --- Tab 2: 列表 --------------------------------------------------
    with tab_list:
        st.subheader("已上传的合同")
        rows = api_request("GET", f"/api/contracts/projects/{project_id}/contracts") or []
        if not rows:
            st.caption("(尚无合同，请先在『上传合同』中提交)")
        else:
            df = pd.DataFrame(
                [
                    {
                        "ID": c["id"],
                        "文件名": c["filename"],
                        "媒体类型": c["media_type"],
                        "OCR 引擎": c.get("ocr_engine") or "-",
                        "已分析": "✅" if c.get("analyzed_at") else "❌",
                        "上传时间": c.get("uploaded_at", ""),
                        "风险点": len(c.get("risk_flags") or []),
                    }
                    for c in rows
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

            for c in rows:
                with st.expander(f"#{c['id']} {c['filename']}", expanded=False):
                    st.markdown("**OCR 文本（前 2000 字）**")
                    st.code((c.get("ocr_text") or "")[:2000], language=None)
                    if c.get("analyzed_at"):
                        st.markdown("**风险点**")
                        flags = c.get("risk_flags") or []
                        if flags:
                            for f in flags:
                                st.markdown(f"- ⚠️ {f}")
                        else:
                            st.caption("(未发现明显风险点)")

    # --- Tab 3: 五步法 ------------------------------------------------
    with tab_analyse:
        with st.expander("📐 CAS 14 五步法分析", expanded=False):
            st.subheader("CAS 14 五步法分析")
            rows = api_request("GET", f"/api/contracts/projects/{project_id}/contracts") or []
            if not rows:
                st.info("请先上传合同。")
            else:
                options = {f"#{c['id']} {c['filename']}": c for c in rows}
            chosen_label = st.selectbox("选择合同", list(options.keys()), key="contracts_chosen_contract")  # round 31 widget key
            chosen = options[chosen_label]
            run_kp = st.checkbox("运行 7 字段要点提取", value=True, key="contracts_run_kp")  # round 31 widget key
            run_fs = st.checkbox("运行 CAS 14 五步法分析", value=True, key="contracts_run_fs")  # round 31 widget key

            if st.button("🤖 开始分析", type="primary", key="contracts_start_analysis"):  # round 31 widget key
                payload = {
                    "project_id": project_id,
                    "contract_id": chosen["id"],
                    "run_key_points": run_kp,
                    "run_five_step": run_fs,
                }
                with st.spinner("DeepSeek 分析中…"):
                    r = api_request(
                        "POST",
                        f"/api/contracts/contracts/{chosen['id']}/analyze",
                        json=payload,
                    )
                if r is None:
                    st.error("分析失败")
                else:
                    st.success("✅ 分析完成")
                    # P0: 用 contract_id 作 key 前缀防跨合同污染
                    st.session_state[f"contract_result_{chosen['id']}"] = r
                    st.rerun()

            result = st.session_state.get(f"contract_result_{chosen['id']}")
            if result and result.get("contract_id") == chosen["id"]:
                flags = result.get("risk_flags") or []
                if flags:
                    st.error("⚠️ 风险点：" + "、".join(flags))
                else:
                    st.success("未发现明显风险点")
                with st.expander("🔑 7 字段要点提取", expanded=False):
                    _render_key_points(result.get("key_points") or {})
                with st.expander("📐 CAS 14 五步法分析", expanded=True):
                    _render_five_step(result.get("five_step_analysis") or {})
