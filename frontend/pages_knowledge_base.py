"""Streamlit 页面 — 自助知识库.

- 上传书籍 (PDF/EPUB/DOCX/TXT/MD)
- 书籍列表 + 索引状态
- 重建索引 / 删除
- 案例检索 / 与底稿联动 (按科目找相似案例 + 一键生成审计说明)
"""

from __future__ import annotations


import streamlit as st

# P0 安全修复: 使用共享 api_request (带 Authorization header + 401 处理)
from frontend._components import apply_feishu_theme, page_header
from frontend._http import api_request as _api, API_BASE_URL

KB_CATEGORIES = [
    "审计实务",
    "会计准则",
    "税务实务",
    "内控",
    "案例集",
    "行业研究",
    "其他",
]


def show_knowledge_base():
    apply_feishu_theme()
    page_header('📚', '自助知识库', 'PDF / EPUB / DOCX / TXT / MD 实务书籍, 三种嵌入 provider 检索')

    # [飞书化] st.markdown('<p class="sub-header">📚 自助知识库</p>', unsafe_allow_html=True)  # 已被 page_header() 替代

    st.caption(
        "上传你喜欢的实务书籍 / 案例集 / 准则解读 → 系统切块向量化 → "
        "生成审计说明时自动调用相似案例。"
    )

    tab_dash, tab_upload, tab_list, tab_search, tab_assist = st.tabs(
        ["📊 概览", "📥 上传书籍", "📚 我的书架", "🔎 案例检索", "🪄 审计说明助手"]
    )

    # —————————————————————————————
    # 概览
    # —————————————————————————————
    with tab_dash:
        stats = _api("GET", "/api/knowledge-base/stats") or {}
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("书籍总数", stats.get("total_books", 0))
        with c2:
            st.metric("已索引", stats.get("ready_books", 0))
        with c3:
            st.metric("文本片段", stats.get("total_chunks", 0))
        with c4:
            st.metric("总字数 (万)", round((stats.get("total_chars", 0) or 0) / 10000, 1))

        cats = _api("GET", "/api/knowledge-base/categories") or []
        if cats:
            st.markdown("#### 分类")
            cols = st.columns(min(len(cats), 4) or 1)
            for col, c in zip(cols, cats):
                with col:
                    st.metric(c["category"] or "未分类", c["count"])

    # —————————————————————————————
    # 上传
    # —————————————————————————————
    with tab_upload:
        st.markdown("#### 上传新书 / 文档")
        st.info("支持 PDF / EPUB / DOCX / TXT / MD —— 上传后会自动后台解析并建立向量索引。")

        with st.form("upload_book", clear_on_submit=True):
            uploaded = st.file_uploader(
                "选择文件",
                type=["pdf", "epub", "docx", "txt", "md", "markdown"],
            )
            c1, c2, c3 = st.columns(3)
            with c1:
                title = st.text_input("书名 (留空使用文件名)")
                author = st.text_input("作者")
            with c2:
                publisher = st.text_input("出版社")
                isbn = st.text_input("ISBN")
            with c3:
                category = st.selectbox("分类", [""] + KB_CATEGORIES)
                tags = st.text_input("标签 (逗号分隔)")
            description = st.text_area("简介 (可选)")

            submit = st.form_submit_button("📤 上传并索引", type="primary")
            if submit:
                if not uploaded:
                    st.error("请先选择文件")
                else:
                    files = {"file": (uploaded.name, uploaded.read(), uploaded.type)}
                    data = {
                        "title": title or "",
                        "author": author or "",
                        "publisher": publisher or "",
                        "isbn": isbn or "",
                        "category": category or "",
                        "tags": tags or "",
                        "description": description or "",
                    }
                    data = {k: v for k, v in data.items() if v}
                    r = _api(
                        "POST",
                        "/api/knowledge-base/books/upload",
                        files=files,
                        data=data,
                    )
                    if r:
                        st.success(f"✅ 已上传：{r.get('title')} — 索引状态：{r.get('status')}")
                        st.info("后台正在解析与切块，刷新「我的书架」查看进度。")

    # —————————————————————————————
    # 列表
    # —————————————————————————————
    with tab_list:
        st.markdown("#### 我的书架")
        cat_filter = st.selectbox("按分类过滤", [""] + KB_CATEGORIES, key="bookcat")
        params = {}
        if cat_filter:
            params["category"] = cat_filter

        books = _api("GET", "/api/knowledge-base/books", params=params) or []
        st.write(f"共 {len(books)} 本")
        for b in books:
            status_icon = {
                "ready": "✅",
                "pending": "⏳",
                "parsing": "🔍",
                "indexing": "📦",
                "failed": "❌",
            }.get(b.get("status"), "❓")
            with st.expander(
                f"{status_icon} 《{b.get('title', '')}》 - {b.get('author') or '佚名'} "
                f"({b.get('chunk_count', 0)} 片段)"
            ):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write(f"**类型**：{b.get('file_type', '').upper()}")
                    st.write(f"**大小**：{round(b.get('file_size', 0) / 1024 / 1024, 2)} MB")
                with c2:
                    st.write(f"**分类**：{b.get('category') or '未分类'}")
                    st.write(f"**标签**：{b.get('tags') or '—'}")
                with c3:
                    st.write(f"**状态**：{b.get('status')}")
                    st.write(f"**嵌入模型**：{b.get('embedding_model') or '—'}")
                if b.get("description"):
                    st.caption(b["description"])
                if b.get("error_msg"):
                    st.error(f"索引错误：{b['error_msg']}")

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("🔄 重建索引", key=f"reidx_{b['id']}"):
                        _api("POST", f"/api/knowledge-base/books/{b['id']}/reindex")
                        st.success("已加入队列")
                with c2:
                    # P0: 二次确认防误删
                    confirm_del_key = f"confirm_del_book_{b['id']}"
                    if st.session_state.get(confirm_del_key):
                        if st.button("确认删除", key=f"del_{b['id']}_confirm", type="primary"):
                            _api("DELETE", f"/api/knowledge-base/books/{b['id']}")
                            st.session_state.pop(confirm_del_key, None)
                            st.rerun()
                    else:
                        if st.button("🗑️ 删除", key=f"del_{b['id']}"):
                            st.session_state[confirm_del_key] = True
                            st.warning("⚠️ 再点一次确认删除")

    # —————————————————————————————
    # 直接检索
    # —————————————————————————————
    with tab_search:
        st.markdown("#### 案例 / 实务问题检索")
        query = st.text_area(
            "查询内容",
            placeholder="例如：制造业期末存货跌价测试的处理方式；客户函证差异的替代程序",
            height=80,
            key="kb_search_query",  # round 31 widget key
        )
        c1, c2 = st.columns(2)
        with c1:
            top_k = st.slider("返回条数", 1, 15, 5)
        with c2:
            category = st.selectbox("限定分类", [""] + KB_CATEGORIES, key="search_cat")

        if query and st.button("🔍 检索", key="kb_search_btn"):  # round 31 widget key
            payload = {"query": query, "top_k": int(top_k)}
            if category:
                payload["category"] = category
            results = _api("POST", "/api/knowledge-base/search", json=payload) or []
            if not results:
                st.warning("未命中 — 试试缩短关键词，或先上传相关书籍。")
            for r in results:
                with st.expander(
                    f"📖 《{r.get('book_title')}》"
                    + (f" — {r.get('chapter')}" if r.get("chapter") else "")
                    + f"  (相似度 {r.get('score'):.2f})"
                ):
                    st.caption(
                        " / ".join(
                            x
                            for x in [
                                r.get("chapter"),
                                r.get("section"),
                                f"第{r['page']}页" if r.get("page") else None,
                            ]
                            if x
                        )
                    )
                    st.write(r.get("content", ""))
                    st.caption(
                        f"语义得分 {r.get('semantic_score'):.2f}，"
                        f"关键词得分 {r.get('keyword_score'):.2f}"
                    )

    # —————————————————————————————
    # 审计说明助手 — 与底稿联动
    # —————————————————————————————
    with tab_assist:
        st.markdown("#### 一键生成审计说明 (调用知识库 + 法规库 + AI)")
        projects = _api("GET", "/api/projects/") or []
        if not projects:
            st.warning("请先在「项目管理」创建项目")
            return

        proj_map = {f"{p['id']} - {p['name']}": p for p in projects}
        sel = st.selectbox("选择项目", list(proj_map.keys()), key="kb_proj_sel")  # round 31 widget key
        project = proj_map[sel]

        tab_single, tab_batch = st.tabs(["🎯 单科目", "📦 批量写回底稿"])

        with tab_single:
            c1, c2 = st.columns(2)
            with c1:
                ac_code = st.text_input("科目编码", placeholder="例如 1122", key="kb_ac_code")  # round 31 widget key
                ac_name = st.text_input("科目名称", placeholder="例如 应收账款", key="kb_ac_name")  # round 31 widget key
            with c2:
                obj = st.selectbox(
                    "审计目标",
                    [
                        "",
                        "余额完整性",
                        "收入截止性",
                        "可回收性 / 坏账",
                        "存货跌价",
                        "成本结转准确性",
                        "关联交易",
                        "在建工程转固",
                        "公允价值计量",
                    ],
                    key="kb_audit_obj",  # round 31 widget key
                )
                kb_cat = st.selectbox("限定知识库分类", [""] + KB_CATEGORIES, key="ann_cat")
            risk = st.text_area("风险点描述 (可选)", key="kb_risk")  # round 31 widget key

            if st.button("🪄 生成审计说明", type="primary", key="kb_gen_note"):  # round 31 widget key
                payload = {
                    "project_id": project["id"],
                    "account_code": ac_code or None,
                    "account_name": ac_name or None,
                    "industry": project.get("industry"),
                    "audit_objective": obj or None,
                    "risk_description": risk or None,
                    "kb_category": kb_cat or None,
                    "include_regulations": True,
                }
                with st.spinner("调用知识库 / 法规库 / AI 中..."):
                    r = _api("POST", "/api/workbooks/audit-note", json=payload)
                if r:
                    if not r.get("ai_enabled"):
                        st.info("AI 未启用 — 当前返回的是基于检索结果的结构化骨架。")
                    st.markdown("### 生成的审计说明")
                    # P0 安全: LLM 输出经 safe_inline_text 转义后再 markdown
                    from frontend._components.safe_render import safe_inline_text
                    st.markdown(safe_inline_text(r.get("note", ""), max_len=8000))

                    with st.expander("引用 — 知识库"):
                        for k in r.get("references_kb", []):
                            st.write(
                                f"- 《{k.get('book_title')}》"
                                + (f" / {k['chapter']}" if k.get("chapter") else "")
                                + (f" / 第{k['page']}页" if k.get("page") else "")
                                + f"  相似度 {k.get('score', 0):.2f}"
                            )
                    with st.expander("引用 — 法规"):
                        for g in r.get("references_regulations", []):
                            st.write(
                                f"- 《{g.get('title')}》"
                                + (f" ({g['document_no']})" if g.get("document_no") else "")
                                + (f"  {g['publish_date']}" if g.get("publish_date") else "")
                            )

        with tab_batch:
            st.write(
                "在已生成的底稿 Excel 末尾追加「审计说明」sheet — "
                "每个重要科目自动调用知识库 / 法规库 / AI 生成一段说明。"
            )
            workbook_file = st.text_input(
                "底稿文件名",
                placeholder="例如 科目明细表_2024.xlsx",
                help="从「底稿生成」页拿到的 file_name",
                key="kb_wb_fname",  # round 31 widget key
            )
            c1, c2 = st.columns(2)
            with c1:
                top_n = st.slider("按余额取前 N 大科目", 5, 60, 20)
                kb_cat = st.selectbox("限定知识库分类", [""] + KB_CATEGORIES, key="batch_cat")
            with c2:
                audit_obj = st.text_input("通用审计目标 (可选)")
                include_reg = st.checkbox("引用法规库", value=True)

            if workbook_file and st.button("📦 批量生成并写回底稿", type="primary"):
                payload = {
                    "project_id": project["id"],
                    "workbook_file": workbook_file,
                    "top_n_by_balance": int(top_n),
                    "kb_category": kb_cat or None,
                    "audit_objective": audit_obj or None,
                    "include_regulations": include_reg,
                }
                with st.spinner("生成中...每个科目都会调用一次 AI，预计较慢"):
                    r = _api("POST", "/api/workbooks/audit-note/batch", json=payload)
                if r:
                    st.success(f"✅ 已生成 {r.get('notes_count')} 条审计说明并写回底稿")
                    st.markdown(f"📥 [下载更新后的底稿]({API_BASE_URL}{r.get('download_url')})")
