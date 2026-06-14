"""Streamlit page: 收发存盘点 & 减值 (Inventory module)."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import requests
import streamlit as st

API_BASE_URL = "http://localhost:8000"


def _api(method: str, endpoint: str, **kwargs):
    try:
        url = f"{API_BASE_URL}{endpoint}"
        r = requests.request(method, url, timeout=120, **kwargs)
        if r.status_code >= 400:
            try:
                msg = r.json().get("detail", r.text)
            except Exception:
                msg = r.text
            st.error(f"API {r.status_code}: {msg}")
            return None
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.content
    except requests.exceptions.ConnectionError:
        st.error("无法连接到后端服务，请确保 FastAPI 已启动")
        return None


@st.cache_data(ttl=30)
def _projects():
    return _api("GET", "/api/projects/") or []


def _pick_project():
    projs = _projects()
    if not projs:
        st.warning("请先在『项目管理』创建项目")
        return None
    options = {f"{p['id']} - {p['name']} ({p.get('industry') or '未填行业'})": p for p in projs}
    label = st.selectbox("选择项目", list(options.keys()), key="inv_project_select")
    return options[label]


def show_inventory():
    st.markdown('<p style="font-size:1.5rem;font-weight:bold;color:#2E4057;">📦 收发存盘点 &amp; 减值（成本相关）</p>', unsafe_allow_html=True)

    proj = _pick_project()
    if not proj:
        return
    project_id = proj["id"]
    default_pe = date(int(proj["fiscal_year"]), 12, 31)

    tabs = st.tabs([
        "1️⃣ 导入收发存",
        "2️⃣ 盘点计划",
        "3️⃣ 盘点用表(金额优先)",
        "4️⃣ 拍照回填实盘",
        "5️⃣ 盘点率统计",
        "6️⃣ 库龄&跌价&转回",
        "7️⃣ 编码映射(跨年)",
        "📥 一键导出",
    ])

    with tabs[0]:
        _tab_import(project_id, default_pe)
    with tabs[1]:
        _tab_plan(project_id, default_pe, proj.get("industry") or "")
    with tabs[2]:
        _tab_count_sheet(project_id, default_pe)
    with tabs[3]:
        _tab_photo(project_id)
    with tabs[4]:
        _tab_completion(project_id, default_pe)
    with tabs[5]:
        _tab_impairment(project_id, default_pe)
    with tabs[6]:
        _tab_code_mapping(project_id)
    with tabs[7]:
        _tab_export(project_id, default_pe)


# ---- 1. 导入收发存 ----------------------------------------------------

def _tab_import(project_id: int, default_pe: date):
    st.markdown("#### 上传收发存明细表")
    st.caption("支持 .xlsx/.xls/.csv，自动识别金蝶/用友/SAP/手工模板。"
               "必含列：物料编码、物料名称、期末数量、期末金额（或单价）。")
    col1, col2 = st.columns(2)
    with col1:
        period_end = st.date_input("报告期截止日", value=default_pe, key="imp_pe")
    with col2:
        is_prior = st.checkbox("作为上年同期数据导入（用于跌价转回）", value=False, key="imp_prior")
    replace = st.checkbox("导入前清空相同期间数据", value=True, key="imp_replace")

    f = st.file_uploader("选择收发存 Excel", type=["xlsx", "xls", "csv"], key="inv_upl_mov")
    if f and st.button("导入收发存", type="primary", key="imp_btn"):
        files = {"file": (f.name, f.read(), f.type or "application/octet-stream")}
        params = {"period_end": period_end.isoformat(), "is_prior_year": is_prior, "replace": replace}
        res = _api("POST", f"/api/inventory/projects/{project_id}/movements", files=files, params=params)
        if res:
            st.success(f"✅ 已导入 {res['imported_count']} 条，期末账面合计 ¥{res['total_ending_amount']:,.2f}")

    # 列表
    if st.checkbox("📋 查看已导入的收发存", key="imp_list_show"):
        params = {"period_end": st.session_state.get("imp_pe", default_pe).isoformat()}
        rows = _api("GET", f"/api/inventory/projects/{project_id}/movements", params=params)
        if rows:
            df = pd.DataFrame(rows)
            keep = ["material_code", "material_name", "category", "warehouse", "batch_no",
                    "opening_qty", "opening_amount", "inbound_qty", "inbound_amount",
                    "outbound_qty", "outbound_amount", "ending_qty", "ending_amount",
                    "unit_cost", "is_prior_year"]
            keep = [c for c in keep if c in df.columns]
            st.dataframe(df[keep], use_container_width=True)


# ---- 2. 盘点计划 ------------------------------------------------------

def _tab_plan(project_id: int, default_pe: date, industry: str):
    st.markdown("#### 行业化盘点计划（可与 AI 对话修改）")
    col1, col2 = st.columns(2)
    with col1:
        pe = st.date_input("盘点基准日", value=default_pe, key="plan_pe")
    with col2:
        ind = st.text_input("行业（不填则用项目行业）", value=industry, key="plan_ind")
    days_b = st.number_input("基准日前几天开始监盘", value=0, min_value=0, max_value=10)
    days_a = st.number_input("基准日后几天结束监盘", value=2, min_value=0, max_value=10)

    if st.button("生成/刷新盘点计划骨架", type="primary", key="plan_gen_btn"):
        body = {
            "period_end": pe.isoformat(),
            "industry": ind or None,
            "count_days_before": int(days_b),
            "count_days_after": int(days_a),
            "team": [],
        }
        res = _api("POST", f"/api/inventory/projects/{project_id}/count-plan", json=body)
        if res:
            st.success("✅ 计划已生成 / 更新")
            st.session_state["_plan_cache"] = res

    plan = st.session_state.get("_plan_cache") or _api(
        "GET", f"/api/inventory/projects/{project_id}/count-plan",
        params={"period_end": pe.isoformat()},
    )
    if not plan:
        st.info("当前基准日还没有盘点计划。请先点击「生成/刷新」。")
        return

    st.markdown("---")
    st.markdown(f"**📋 标题**：{plan.get('title','')}")
    st.markdown(f"**📅 监盘窗口**：{plan.get('count_date_start','')} → {plan.get('count_date_end','')}")
    st.markdown("**🎯 监盘目标**")
    st.text(plan.get("objectives", "") or "")
    st.markdown("**📦 监盘范围**")
    st.text(plan.get("scope", "") or "")
    try:
        team = json.loads(plan.get("team") or "[]")
        if team:
            st.markdown("**👥 监盘小组**")
            st.dataframe(pd.DataFrame(team), use_container_width=True)
    except json.JSONDecodeError:
        pass
    st.markdown("**🔍 监盘程序**")
    st.text(plan.get("procedures", "") or "")
    st.markdown("**⚠️ 特殊事项**")
    st.text(plan.get("special_notes", "") or "")
    st.markdown("**🚨 重大风险**")
    st.text(plan.get("risks", "") or "")

    # 修改历史
    try:
        rev = json.loads(plan.get("revision_log") or "[]")
        if rev:
            with st.expander(f"🧾 修改历史 ({len(rev)} 次)"):
                for i, r in enumerate(rev, 1):
                    st.markdown(f"**#{i}** 指令：{r.get('instruction','')}")
                    st.caption(r.get("applied", ""))
    except json.JSONDecodeError:
        pass

    st.markdown("---")
    st.markdown("##### 💬 与 AI 对话修改本计划")
    instruction = st.text_area(
        "举例：把监盘窗口改成 12月28-30日；增加冷链温度记录核对；删除『委外材料发函』；把团队中『审计员 B』换成『陈某』。",
        height=80,
        key="plan_instr",
    )
    if st.button("提交修改", key="plan_revise_btn") and instruction.strip():
        res = _api("PUT", f"/api/inventory/count-plans/{plan['id']}/revise", json={"instruction": instruction})
        if res:
            st.success("✅ 已根据指令更新计划")
            st.session_state["_plan_cache"] = res
            st.rerun()


# ---- 3. 盘点用表 ------------------------------------------------------

def _tab_count_sheet(project_id: int, default_pe: date):
    st.markdown("#### 生成盘点用表（金额优先 + 阈值覆盖）")
    pe = st.date_input("盘点基准日", value=default_pe, key="cs_pe")

    # 阈值模拟
    with st.expander("🔍 先模拟不同覆盖率，看金额覆盖 vs 行数权衡"):
        thresholds = st.multiselect(
            "对比覆盖率档位",
            options=[0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95],
            default=[0.70, 0.80, 0.90],
            key="cs_thr_sim",
        )
        if st.button("跑模拟", key="cs_sim_btn") and thresholds:
            res = _api("POST", f"/api/inventory/projects/{project_id}/count-sheets/simulate",
                       json={"period_end": pe.isoformat(), "thresholds": thresholds})
            if res:
                rows = []
                for s in res["scenarios"]:
                    rows.append({
                        "策略": s["strategy"],
                        "选中物料": s["selected_items"],
                        "总物料": s["total_items"],
                        "覆盖金额": s["covered_amount"],
                        "总金额": s["total_amount"],
                        "金额覆盖率": f"{s['coverage_ratio']:.2%}",
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True)

    st.markdown("---")
    st.markdown("##### 抽样策略参数")
    col1, col2, col3 = st.columns(3)
    with col1:
        cov = st.slider("A类金额累计覆盖率", 0.5, 0.99, 0.80, step=0.01, key="cs_cov")
    with col2:
        bp = st.slider("B类抽样比例", 0.0, 0.5, 0.20, step=0.05, key="cs_b")
    with col3:
        cp = st.slider("C类覆盖性抽样比例", 0.0, 0.3, 0.05, step=0.01, key="cs_c")

    col4, col5 = st.columns(2)
    with col4:
        whs = st.text_input("必盘仓库（逗号分隔）", "", key="cs_whs",
                            help="这些仓库的所有物料强制并入 A 类")
    with col5:
        cats = st.text_input("必盘类别（逗号分隔）", "", key="cs_cats",
                             help="如：在产品、委外、长龄物料")
    codes_raw = st.text_area("必盘物料编码（每行一个）", "", height=60, key="cs_codes")
    min_amt = st.number_input("忽略单行金额低于（元）", value=0.0, min_value=0.0, key="cs_min")

    col6, col7, col8 = st.columns(3)
    with col6:
        materiality = st.number_input(
            "重要性水平金额（元）", value=0.0, min_value=0.0, key="cs_mat",
            help="单条金额 ≥ 该值的物料强制入 A；常按税前利润 5% 估算。0 表示不启用",
        )
    with col7:
        b_method = st.selectbox(
            "B 类抽样方式", ["mus", "random"], index=0, key="cs_bm",
            help="mus = 按金额加权（金额大的更易被抽中，推荐）；random = 简单随机",
        )
    with col8:
        rev_ratio = st.slider(
            "反向抽盘比例（物→账）", 0.0, 0.30, 0.05, step=0.01, key="cs_rev",
            help="额外随机抽 N% 物料，从仓库现场反查账簿，验证是否有账外存货",
        )

    persist = st.checkbox("保存到数据库（覆盖旧用表）", value=True, key="cs_persist")
    force_ow = st.checkbox(
        "⚠️ 同时清空已回填的实盘数（不建议）", value=False, key="cs_force_ow",
        help="默认保留已经被现场盘点回填的行；仅在重新规划盘点策略时勾选",
    )

    if st.button("生成盘点用表", type="primary", key="cs_gen_btn"):
        body = {
            "period_end": pe.isoformat(),
            "coverage_threshold": cov,
            "b_sample_ratio": bp,
            "c_sample_ratio": cp,
            "high_value_warehouses": [s.strip() for s in whs.split(",") if s.strip()],
            "must_include_categories": [s.strip() for s in cats.split(",") if s.strip()],
            "must_include_codes": [s.strip() for s in codes_raw.splitlines() if s.strip()],
            "min_unit_amount": float(min_amt),
            "persist": persist,
            "force_overwrite_counted": force_ow,
            "materiality": float(materiality),
            "b_sample_method": b_method,
            "reverse_sample_ratio": float(rev_ratio),
        }
        res = _api("POST", f"/api/inventory/projects/{project_id}/count-sheets/generate", json=body)
        if res:
            c1, c2, c3, c4 = st.columns(4)
            with c1: st.metric("选中物料", f"{res['selected_items']} / {res['total_items']}")
            with c2: st.metric("覆盖金额", f"¥ {res['covered_amount']:,.0f}")
            with c3: st.metric("总金额", f"¥ {res['total_amount']:,.0f}")
            with c4: st.metric("金额覆盖率", f"{res['coverage_ratio']:.2%}")
            st.caption(res["strategy_desc"])

            ts = res.get("tier_summary") or {}
            tier_df = pd.DataFrame([
                {"层级": k, "物料数": v.get("items", 0), "金额": v.get("amount", 0),
                 "金额占比": f"{v.get('amount_pct', 0):.2%}"}
                for k, v in ts.items()
            ])
            st.dataframe(tier_df, use_container_width=True)

            if res.get("rows"):
                df = pd.DataFrame(res["rows"])
                st.dataframe(df, use_container_width=True, height=400)


# ---- 4. 照片回填 ------------------------------------------------------

def _tab_photo(project_id: int):
    st.markdown("#### 上传现场盘点用表照片 → OCR 自动回填")
    st.caption("支持 .jpg/.png/.pdf；图片越清晰、字越正、列清晰，回填准确率越高。"
               "AI 会自动识别物料编码与实盘数量并写回到对应盘点行；未匹配的会在下方列出。")
    counted_by = st.text_input("盘点人（可选；OCR 通常能自动识别）", "", key="ph_by")
    note = st.text_input("备注（可选）", "", key="ph_note")
    f = st.file_uploader("选择照片/扫描件", type=["jpg", "jpeg", "png", "pdf", "bmp", "tiff", "webp"], key="ph_upl")
    if f and st.button("上传并回填", type="primary", key="ph_btn"):
        files = {"file": (f.name, f.read(), f.type or "application/octet-stream")}
        params = {}
        if counted_by:
            params["counted_by"] = counted_by
        if note:
            params["note"] = note
        res = _api("POST", f"/api/inventory/projects/{project_id}/count-photos",
                   files=files, params=params)
        if res:
            c1, c2, c3 = st.columns(3)
            with c1: st.metric("OCR 引擎", res["ocr_engine"])
            with c2: st.metric("识别行数", res["parsed_row_count"])
            with c3: st.metric("成功回填", f"{res['matched_count']} (未匹配 {res['unmatched_count']})")
            if res.get("counted_by"):
                st.caption(f"识别到盘点人：{res['counted_by']}；盘点时间：{res.get('counted_at','')}")
            if res.get("unmatched_rows"):
                st.warning("⚠️ 以下行未能匹配到盘点用表，请人工核对编码/名称：")
                st.dataframe(pd.DataFrame(res["unmatched_rows"]), use_container_width=True)


# ---- 5. 盘点率统计 ---------------------------------------------------

def _tab_completion(project_id: int, default_pe: date):
    st.markdown("#### 盘点率 & 盘盈盘亏统计")
    col1, col2 = st.columns(2)
    with col1:
        pe = st.date_input("报告期截止日", value=default_pe, key="comp_pe")
    with col2:
        mat = st.number_input(
            "重要性水平金额（元）", value=0.0, min_value=0.0, key="comp_mat",
            help="≥ 该值的差异归入『重大差异』组，需重点关注；常按税前利润 5% 估算",
        )
    if st.button("🔄 刷新统计", key="comp_refresh"):
        st.cache_data.clear()
    res = _api(
        "GET", f"/api/inventory/projects/{project_id}/count-completion",
        params={"materiality": mat, "period_end": pe.isoformat()},
    )
    if not res:
        return
    o = res.get("overall") or {}
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("盘点率（数量）", f"{o.get('items_rate', 0):.2%}", f"{o.get('counted_items',0)}/{o.get('total_items',0)} 项")
    with c2: st.metric("盘点率（金额）", f"{o.get('amount_rate', 0):.2%}",
                       f"¥{o.get('counted_amount',0):,.0f} / ¥{o.get('total_amount',0):,.0f}")
    with c3:
        ds = res.get("difference_summary") or {}
        st.metric("差异笔数", ds.get("total_count", 0),
                  f"重大{ds.get('major_count',0)} / 小额{ds.get('minor_count',0)}")

    # 应盘未盘
    uc_items = o.get("uncovered_items", 0)
    uc_amount = o.get("uncovered_amount", 0)
    if uc_items > 0:
        st.warning(
            f"⚠️ 应盘未盘：{uc_items} 个物料 / ¥{uc_amount:,.2f} 未被盘点用表覆盖。"
            "可能是被'最小金额过滤'剔除，或新增物料未生成新盘点表 — 请追加程序。"
        )
        uc = res.get("uncovered") or []
        if uc:
            with st.expander("查看应盘未盘明细"):
                st.dataframe(pd.DataFrame(uc), use_container_width=True)

    by_wh = res.get("by_warehouse") or []
    if by_wh:
        st.markdown("##### 按仓库统计")
        df = pd.DataFrame(by_wh)
        df["盘点率(数量)"] = df["items_rate"].apply(lambda x: f"{x:.2%}")
        df["盘点率(金额)"] = df["amount_rate"].apply(lambda x: f"{x:.2%}")
        st.dataframe(df[["warehouse", "total_items", "counted_items", "盘点率(数量)",
                         "total_amount", "counted_amount", "盘点率(金额)"]],
                     use_container_width=True)

    diffs_major = res.get("differences_major") or []
    diffs_minor = res.get("differences_minor") or []
    if diffs_major:
        st.markdown("##### 🚨 重大差异（≥ 重要性水平）— 必须查清")
        st.dataframe(pd.DataFrame(diffs_major), use_container_width=True)
    if diffs_minor:
        st.markdown("##### 小额差异（< 重要性水平）— 汇总监控")
        st.dataframe(pd.DataFrame(diffs_minor), use_container_width=True)
    if not diffs_major and not diffs_minor:
        st.info("暂未发现盘点差异。")


# ---- 6. 库龄 / 跌价 / 转回 -------------------------------------------

def _tab_impairment(project_id: int, default_pe: date):
    st.markdown("#### 库龄分析 + 跌价计提 + 跌价转回")
    pe = st.date_input("报告期截止日", value=default_pe, key="imp_pe2")

    col1, col2 = st.columns(2)
    with col1:
        use_sales = st.checkbox("用销售清单测算 NRV（推荐）", value=True, key="imp_sales")
        include_reversal = st.checkbox("自动结合上年跌价做转回测算", value=True, key="imp_rev")
    with col2:
        sell_cost_rate = st.slider("估计销售费用率", 0.0, 0.30, 0.05, step=0.01, key="imp_sc")

    completion_rate = st.slider(
        "加工成本率（原材料/在产品用，占售价的%）", 0.0, 0.80, 0.0, step=0.05, key="imp_cc",
        help="0 = 不启用完工口径；> 0 时，原材料/在产品 NRV 还会扣这个比例。"
             "例：电子加工 30% 表示售价的 30% 还要花在后续加工上",
    )

    with st.expander("📝 上年期初跌价（可选）— 用于精确计算转回"):
        st.caption("如果上年期末已计提的跌价表没有自动结转过来，可在此手工录入。"
                   "格式：每行 `物料编码,金额`。")
        prior_text = st.text_area("上年期末跌价（CSV 两列）", "", height=100, key="imp_prior_csv")
        prior_pe = st.date_input(
            "上年期末日", value=date(pe.year - 1, 12, 31), key="imp_prior_pe"
        )
        if st.button("上传上年跌价", key="imp_prior_btn") and prior_text.strip():
            items = {}
            for line in prior_text.splitlines():
                parts = [s.strip() for s in line.split(",")]
                if len(parts) >= 2:
                    try:
                        items[parts[0]] = float(parts[1])
                    except ValueError:
                        continue
            if items:
                res = _api("POST", f"/api/inventory/projects/{project_id}/impairments/prior",
                           json={"items": items},
                           params={"period_end": prior_pe.isoformat()})
                if res:
                    st.success(f"✅ 已保存 {res['saved']} 条上年跌价记录")

    with st.expander("🛠 手工 NRV 单价覆盖（可选）"):
        st.caption("无销售记录的原材料可以手工录入近期市价。格式：每行 `物料编码,单价`。")
        manual_text = st.text_area("手工 NRV", "", height=80, key="imp_manual_csv")

    if st.button("开始计算", type="primary", key="imp_btn2"):
        manual_nrv = {}
        for line in (manual_text or "").splitlines():
            parts = [s.strip() for s in line.split(",")]
            if len(parts) >= 2:
                try:
                    manual_nrv[parts[0]] = float(parts[1])
                except ValueError:
                    continue
        body = {
            "period_end": pe.isoformat(),
            "use_sales_for_nrv": use_sales,
            "sell_cost_rate": sell_cost_rate,
            "completion_cost_rate": float(completion_rate),
            "manual_nrv": manual_nrv,
            "persist": True,
            "include_reversal": include_reversal,
        }
        res = _api("POST", f"/api/inventory/projects/{project_id}/impairments/compute", json=body)
        if res:
            s = res.get("summary") or {}
            c1, c2, c3, c4 = st.columns(4)
            with c1: st.metric("期末账面", f"¥ {s.get('book_amount',0):,.0f}")
            with c2: st.metric("期末应保留跌价", f"¥ {s.get('ending_impairment',0):,.0f}")
            with c3: st.metric("本期新增计提", f"¥ {s.get('current_provision',0):,.0f}")
            with c4: st.metric("本期跌价转回", f"¥ {s.get('current_reversal',0):,.0f}")

            rows = res.get("rows") or []
            if rows:
                df = pd.DataFrame(rows)
                st.markdown("##### 跌价测试明细")
                show_cols = [
                    "material_code", "material_name", "ending_qty", "book_unit_cost",
                    "book_amount", "weighted_avg_age", "nrv_unit_price", "nrv_source",
                    "impairment_current", "impairment_opening",
                    "impairment_provision", "impairment_reversal",
                    "method", "note",
                ]
                show_cols = [c for c in show_cols if c in df.columns]
                st.dataframe(df[show_cols], use_container_width=True, height=400)

                st.markdown("##### 库龄分布（金额）")
                aging_cols = ["age_le_90", "age_91_180", "age_181_365", "age_366_730", "age_gt_730"]
                if all(c in df.columns for c in aging_cols):
                    # df 中 age_* 是数量，乘以 book_unit_cost 得金额
                    amt = pd.DataFrame({
                        "≤90 天": df["age_le_90"] * df["book_unit_cost"],
                        "91-180 天": df["age_91_180"] * df["book_unit_cost"],
                        "181-365 天": df["age_181_365"] * df["book_unit_cost"],
                        "366-730 天": df["age_366_730"] * df["book_unit_cost"],
                        ">730 天": df["age_gt_730"] * df["book_unit_cost"],
                    })
                    st.bar_chart(amt.sum())


# ---- 7. 编码映射 -----------------------------------------------------

def _tab_code_mapping(project_id: int):
    st.markdown("#### 物料编码跨年映射（旧编码 → 新编码）")
    st.caption(
        "ERP 升级或编码改造后，上年的物料编码可能换名。本映射用于跌价转回时，"
        "把上年 InventoryImpairment 里的旧编码翻译为本年的新编码，避免转回数据丢失。"
    )

    res = _api("GET", f"/api/inventory/projects/{project_id}/code-mappings")
    if res:
        st.markdown(f"已配置 **{len(res)}** 条映射")
        if res:
            st.dataframe(pd.DataFrame(res)[["old_code", "new_code", "note"]],
                         use_container_width=True)

    st.markdown("##### 上传 / 追加映射")
    text = st.text_area(
        "每行一条，格式：`旧编码,新编码[,备注]`",
        "",
        height=150,
        key="cm_text",
        help="例：\nOLD-001,NEW-A-001,2024 年 ERP 升级\nOLD-002,NEW-A-002",
    )
    replace = st.checkbox("覆盖现有全部映射（不勾选则增量追加）", value=False, key="cm_replace")

    if st.button("保存映射", type="primary", key="cm_save_btn") and text.strip():
        items = []
        for line in text.splitlines():
            parts = [s.strip() for s in line.split(",")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                continue
            items.append({
                "old_code": parts[0],
                "new_code": parts[1],
                "note": parts[2] if len(parts) > 2 else None,
            })
        if not items:
            st.warning("没有可保存的行；请检查格式")
        else:
            saved = _api(
                "POST", f"/api/inventory/projects/{project_id}/code-mappings",
                json={"items": items, "replace": replace},
            )
            if saved is not None:
                st.success(f"✅ 已保存 {len(saved)} 条映射")
                st.rerun()

    if st.button("🗑 清空全部映射", key="cm_clear_btn"):
        r = _api("DELETE", f"/api/inventory/projects/{project_id}/code-mappings")
        if r:
            st.success(f"已删除 {r.get('deleted', 0)} 条")
            st.rerun()


# ---- 8. 一键导出 -----------------------------------------------------

def _tab_export(project_id: int, default_pe: date):
    st.markdown("#### 一键导出整套底稿")
    st.caption("生成的工作簿含：收发存明细 / 盘点计划 / 盘点用表 / 已盘点情况 / 盘点率统计 / 库龄分析 / 跌价测试 / 跌价汇总")
    pe = st.date_input("报告期截止日", value=default_pe, key="exp_pe")
    if st.button("📥 生成并下载", type="primary", key="exp_btn"):
        # 走统一的 _api (auth 头 + 统一错误处理), 不再直连 requests
        content = _api(
            "GET",
            f"/api/inventory/projects/{project_id}/export?period_end={pe.isoformat()}",
        )
        if isinstance(content, bytes) and content:
            st.download_button(
                "⬇️ 下载 Excel",
                data=content,
                file_name=f"inventory_project_{project_id}_{pe.isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
