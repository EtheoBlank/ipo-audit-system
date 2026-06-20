"""Streamlit 页面 — 法律法规库.

# P1 widget keys (round 32): reg_scrape_selected, reg_scrape_max_pages, reg_scrape_async, reg_scrape_go, reg_search_q, reg_search_mode, reg_search_src, reg_search_limit, reg_search_go
- 抓取触发 (按来源 / 全部)
- 来源统计
- 列表查询 + 全文搜索
- 详情查看 + 收藏
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# P0 安全修复: 使用共享 api_request (带 Authorization header + 401 处理)
from frontend._components import apply_feishu_theme, page_header
from frontend._http import api_request as _api

SOURCE_CODES = ["CSRC", "MOF", "STA", "SAFE", "PBOC"]
SOURCE_LABELS = {
    "CSRC": "证监会",
    "MOF": "财政部",
    "STA": "国家税务总局",
    "SAFE": "国家外汇管理局",
    "PBOC": "中国人民银行",
    "LOCAL": "地方财税",
    "OTHER": "其他",
}


def show_regulations():
    apply_feishu_theme()
    page_header('⚖️', '法律法规库', '证监会 / 财政部 / 税务总局 / 外管局 / 人民银行 政策与准则')

    # [飞书化] st.markdown('<p class="sub-header">⚖️ 法律法规库</p>', unsafe_allow_html=True)  # 已被 page_header() 替代

    st.caption(
        "自动抓取证监会 / 财政部 / 国家税务总局 / 外管局 / 人民银行的政策文件、准则、规章、问答口径，"
        "支持来源/日期/关键词多维过滤、全文搜索、按项目收藏。"
    )

    tab_dash, tab_scrape, tab_search, tab_list, tab_fav = st.tabs(
        ["📊 概览", "📥 抓取更新", "🔎 全文搜索", "📋 浏览", "⭐ 我的收藏"]
    )

    # —————————————————————————————
    # 概览
    # —————————————————————————————
    with tab_dash:
        sources = _api("GET", "/api/regulations/sources") or []
        if not sources:
            st.info("法规库为空 — 请到「抓取更新」拉取数据。")
        else:
            cols = st.columns(len(sources))
            for col, s in zip(cols, sources):
                with col:
                    st.metric(
                        f"{s.get('name', s['code'])}",
                        s.get("count", 0),
                        help=f"最新发布日期：{s.get('latest_publish_date') or '—'}",
                    )
        cats = _api("GET", "/api/regulations/categories") or []
        if cats:
            st.markdown("#### 分类分布")
            st.dataframe(pd.DataFrame(cats), use_container_width=True, hide_index=True)

    # —————————————————————————————
    # 抓取
    # —————————————————————————————
    with tab_scrape:
        st.markdown("#### 触发抓取")
        col1, col2 = st.columns([2, 1])
        with col1:
            selected = st.multiselect(
                "选择来源",
                options=SOURCE_CODES,
                default=SOURCE_CODES,
                format_func=lambda c: f"{c} - {SOURCE_LABELS.get(c, c)}",
            )
        with col2:
            max_pages = st.number_input("每栏目最大页数", 1, 20, 3)

        run_async = st.checkbox("后台执行 (大批量推荐)", value=False)
        if st.button("🚀 开始抓取", type="primary"):
            payload = {"sources": selected, "max_pages": int(max_pages)}
            path = "/api/regulations/scrape/async" if run_async else "/api/regulations/scrape"
            with st.spinner("抓取中，请稍候 (官方站点限速较严)..."):
                resp = _api("POST", path, json=payload)
            if resp:
                st.success("✅ 已完成" if not run_async else "✅ 已加入后台队列")
                st.json(resp)

    # —————————————————————————————
    # 搜索
    # —————————————————————————————
    with tab_search:
        st.markdown("#### 全文 / 关键词搜索")
        q = st.text_input("关键词 (空格分隔多个词)", placeholder="例如：收入确认 时点 风险报酬")
        col1, col2, col3 = st.columns(3)
        with col1:
            mode = st.radio("匹配方式", ["and", "or"], horizontal=True)
        with col2:
            source = st.selectbox("限定来源", [""] + SOURCE_CODES)
        with col3:
            limit = st.number_input("返回条数", 5, 100, 30)

        if q and st.button("🔍 搜索"):
            params = {"q": q, "mode": mode, "limit": int(limit)}
            if source:
                params["source"] = source
            r = _api("GET", "/api/regulations/search", params=params)
            if r:
                st.success(f"找到 {r.get('count', 0)} 条")
                for item in r.get("results", []):
                    _render_regulation(item)

    # —————————————————————————————
    # 浏览
    # —————————————————————————————
    with tab_list:
        st.markdown("#### 按条件浏览")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            source = st.selectbox("来源", [""] + SOURCE_CODES, key="list_src")
        with c2:
            category = st.text_input("分类 (留空则全部)", key="list_cat")
        with c3:
            after = st.text_input("发布日期 >=", placeholder="2024-01-01", key="list_after")
        with c4:
            keyword = st.text_input("标题包含", key="list_kw")

        params: dict = {"limit": 60}
        if source:
            params["source"] = source
        if category:
            params["category"] = category
        if after:
            params["publish_after"] = after
        if keyword:
            params["keyword"] = keyword

        items = _api("GET", "/api/regulations/", params=params) or []
        st.write(f"共 {len(items)} 条")
        for item in items:
            _render_regulation(item)

    # —————————————————————————————
    # 收藏
    # —————————————————————————————
    with tab_fav:
        st.markdown("#### 我的收藏")
        favs = _api("GET", "/api/regulations/favorites/list") or []
        if not favs:
            st.info("暂无收藏。")
        for f in favs:
            with st.expander(
                f"⭐ {f.get('regulation', {}).get('title', '')[:80]} ({f.get('tag') or '无标签'})"
            ):
                st.write(f"备注: {f.get('note') or '—'}")
                _render_regulation(f.get("regulation") or {}, key_prefix=f"fav_{f['id']}")
                if st.button("取消收藏", key=f"unfav_{f['id']}"):
                    _api("DELETE", f"/api/regulations/favorites/{f['id']}")
                    st.rerun()


def _render_regulation(item: dict, key_prefix: str = ""):
    if not item:
        return
    title = item.get("title", "")
    meta = " | ".join(
        x
        for x in [
            SOURCE_LABELS.get(item.get("source") or "", item.get("source") or ""),
            item.get("category"),
            item.get("document_no"),
            item.get("publish_date"),
        ]
        if x
    )
    with st.expander(f"📄 {title[:120]}"):
        st.caption(meta)
        if item.get("summary"):
            # P0 安全: 抓取内容转义后再渲染, 防 javascript: 链接注入
            from frontend._components.safe_render import safe_inline_text, validate_date_input
            st.markdown(f"**摘要**：{safe_inline_text(item.get('summary', ''), max_len=300)}")
        if item.get("full_text"):
            st.markdown("**正文摘录**")
            st.write(item["full_text"][:1500])
        if item.get("source_url"):
            # P0 安全: 校验 URL 协议, 防 javascript: 注入
            from frontend._components.safe_render import safe_link
            st.markdown(safe_link("在官方网站打开 ↗", item["source_url"]))
        with st.form(
            f"fav_form_{key_prefix or item.get('id')}_{item.get('id')}", clear_on_submit=True
        ):
            tag = st.text_input("收藏标签", key=f"tag_{key_prefix}_{item.get('id')}")
            note = st.text_area("备注", key=f"note_{key_prefix}_{item.get('id')}")
            if st.form_submit_button("⭐ 收藏"):
                _api(
                    "POST",
                    f"/api/regulations/{item.get('id')}/favorite",
                    json={"tag": tag or None, "note": note or None},
                )
                st.success("已收藏")
