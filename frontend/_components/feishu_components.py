"""飞书化组件库 — 替换/包装 Streamlit 默认组件.

提供:
    page_header(icon, title, subtitle)
        — 飞书页头 (大标题 + 副标题 + 蓝紫左边框)

    metric_card(label, value, delta=None, status="default")
        — 飞书风格指标卡 (顶部蓝紫渐变条)

    status_badge(text, status="default")
        — 状态徽章 (default/primary/success/warning/error/info)

    status_dot(status)
        — 状态点 (success/warning/error/primary/muted)

    section_card(title, icon=None)
        — 装饰性 section 容器 (左侧蓝色 3px 装饰条)

    empty_state(icon, message, hint=None)
        — 空状态卡片 (虚线边框 + 居中图标)

    data_table(df, columns=None, hide_index=True)
        — 飞书化 HTML 表格 (流式输出, 配合 dataframe 使用)

    render_top_badges(sentiment_count, notification_count)
        — 右上角红点 (从 app.py 抽出来, 飞书化样式)

    feishu_divider()
        — 装饰性分隔线 (HR)

Pack A 美学扩展 (φ 黄金分割驱动):
    kbd(keys)              — 键盘按键徽章 (Ctrl+K 等)
    toast(text, kind)      — 操作反馈条 (success/error/info/warning)
    timeline(events)       — 审计时间线 (垂直步骤条)
    skeleton(lines)        — 加载骨架屏
    breadcrumb(items)      — 面包屑导航
    progress_ring(percent) — 圆形进度环 (SVG)
    stat_grid(items)       — φ 网格统计卡 (左大右小 / 等宽 / 递增)
    info_panel(title, body, kind)
                            — 信息面板 (默认/成功/警告/错误)
    link_card(icon, title, desc, action_label, key)
                            — 可点击卡片 (大尺寸入口)
    kv_list(items)         — 键值对列表 (左键右值)
    pill_label(text, kind) — 圆角标签 (可选可关闭)
"""
from __future__ import annotations

from typing import Optional, Sequence

import html

import pandas as pd
import streamlit as st

from frontend._components.feishu_theme import FEISHU_C, FEISHU_R
from frontend._components.golden import (
    PHI,
    PHI_INV,
    GOLDEN_FONT_SCALE,
    GOLDEN_SPACE_PX,
    GOLDEN_TIME_MS,
    golden_columns_st,
    golden_grid_columns,
)


# ──────────────────────────────────────────────────────────────
# 页头
# ──────────────────────────────────────────────────────────────


def page_header(
    icon: str,
    title: str,
    subtitle: Optional[str] = None,
) -> None:
    """飞书页头 — 大标题 + 副标题 + 蓝紫左边框装饰."""
    sub_html = f'<div class="subtitle">{html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="feishu-page-header feishu-fade-in">
            <h1><span>{html.escape(icon)}</span><span>{html.escape(title)}</span></h1>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────
# 指标卡 (replacement for st.metric, 飞书化)
# ──────────────────────────────────────────────────────────────


def metric_card(
    label: str,
    value: str,
    delta: Optional[str] = None,
    delta_direction: str = "neutral",  # up / down / neutral
    status: str = "default",           # default / primary / success / warning / error
) -> None:
    """飞书风格指标卡 — 顶部蓝紫渐变条 + 圆角卡片.

    典型用法:
        with col1:
            metric_card("项目总数", "12", delta="+2 较上周", delta_direction="up")
    """
    arrow = ""
    if delta_direction == "up":
        arrow = "↗ "
    elif delta_direction == "down":
        arrow = "↘ "

    delta_class = (
        f"delta {delta_direction}" if delta and delta_direction in ("up", "down") else "delta"
    )
    delta_html = f'<div class="{delta_class}">{arrow}{delta}</div>' if delta else ""

    st.markdown(
        f"""
        <div class="feishu-metric-card feishu-fade-in">
            <div class="label">{label}</div>
            <div class="value">{value}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────
# 状态徽章 / 状态点
# ──────────────────────────────────────────────────────────────


def status_badge(text: str, status: str = "default") -> str:
    """状态徽章 — 返回 HTML 字符串, 可在 markdown / 表格里直接嵌入."""
    return f'<span class="feishu-badge {status}">{text}</span>'


def status_dot(status: str = "muted") -> str:
    """状态点 — 返回 HTML 字符串."""
    return f'<span class="feishu-dot {status}"></span>'


def render_status_badge(text: str, status: str = "default") -> None:
    """便捷函数 — 直接 streamlit 渲染状态徽章."""
    st.markdown(status_badge(text, status), unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# Section 容器
# ──────────────────────────────────────────────────────────────


def section_card_start(title: Optional[str] = None, icon: Optional[str] = None) -> None:
    """开启 section 卡片. 配套 section_card_end() 使用.

    例:
        section_card_start("项目列表", "📋")
        st.dataframe(...)
        section_card_end()
    """
    if title:
        icon_html = f'<span>{icon}</span>' if icon else ""
        st.markdown(
            f"""
            <div class="feishu-card feishu-fade-in">
                <div class="feishu-card-title">{icon_html}<span>{title}</span></div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="feishu-card feishu-fade-in">',
            unsafe_allow_html=True,
        )


def section_card_end() -> None:
    """关闭 section 卡片."""
    st.markdown("</div>", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# 空状态
# ──────────────────────────────────────────────────────────────


def empty_state(
    icon: str = "📭",
    message: str = "暂无数据",
    hint: Optional[str] = None,
) -> None:
    """飞书风格空状态卡片."""
    hint_html = f'<div class="hint">{hint}</div>' if hint else ""
    st.markdown(
        f"""
        <div class="feishu-empty feishu-fade-in">
            <div class="icon">{icon}</div>
            <div class="title">{message}</div>
            {hint_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────
# 表格 (HTML 表格, 用于 dataframe 之外的纯展示场景)
# ──────────────────────────────────────────────────────────────


def data_table(
    df: pd.DataFrame,
    columns: Optional[Sequence[str]] = None,
    hide_index: bool = True,
    max_rows: Optional[int] = None,
) -> None:
    """飞书化 HTML 表格 (不依赖 st.dataframe).

    适用场景: 嵌入卡片, 与其他内容混排.
    """
    if df is None or df.empty:
        empty_state(icon="📋", message="暂无数据")
        return

    if columns:
        df = df[list(columns)]
    if hide_index:
        df = df.reset_index(drop=True)
    if max_rows is not None:
        df = df.head(max_rows)

    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(f"<td>{html.escape(str(v))}</td>" for v in row.values)
        body_rows.append(f"<tr>{cells}</tr>")
    body = "".join(body_rows)

    st.markdown(
        f"""
        <div style="overflow-x:auto;border:1px solid {FEISHU_C.border_light};
                    border-radius:{FEISHU_R.md};box-shadow:{FEISHU_C.shadow_sm};">
        <table class="feishu-table">
            <thead><tr>{head}</tr></thead>
            <tbody>{body}</tbody>
        </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────
# 顶栏红点
# ──────────────────────────────────────────────────────────────


def render_top_badges(
    sentiment_count: int = 0,
    notification_count: int = 0,
) -> None:
    """右上角红点 — 舆情 + 通用通知, 飞书化样式.

    从 app.py 抽出来, 让任意 sub-page 都能复用.
    """
    # 舆情
    if sentiment_count > 0:
        st.markdown(
            f'<div class="feishu-top-badge right">🔴 舆情 {sentiment_count}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="feishu-top-badge right-muted">⚪ 舆情 0</div>',
            unsafe_allow_html=True,
        )

    # 通用通知
    if notification_count > 0:
        st.markdown(
            f'<div class="feishu-top-badge left-right">🔔 通知 {notification_count}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="feishu-top-badge left-right-muted">🔕 通知 0</div>',
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────────────────────
# 装饰性分隔线
# ──────────────────────────────────────────────────────────────


def feishu_divider() -> None:
    """飞书风格分隔线 — 飞书细线 (1px)."""
    st.markdown("<hr/>", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# Pack A — 美学 + UX 扩展组件 (φ 黄金分割驱动)
# ──────────────────────────────────────────────────────────────


def kbd(keys):
    """键盘按键徽章 — 返回 HTML, 用于提示快捷键.

    用法:
        st.markdown(f"按 {kbd('Ctrl+K')} 打开搜索", unsafe_allow_html=True)
        st.markdown(f"按 {kbd(['Ctrl', 'Shift', 'P'])} 命令面板", unsafe_allow_html=True)
    """
    if isinstance(keys, str):
        keys = [keys]
    parts = "".join(
        f'<kbd class="feishu-kbd">{html.escape(k)}</kbd>' for k in keys
    )
    return f'<span class="feishu-kbd-group">{parts}</span>'


def toast(text: str, kind: str = "info", icon=None) -> None:
    """操作反馈条 — 一次性 toast 风格的横条 (success/error/info/warning)."""
    icons = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}
    if icon is None:
        icon = icons.get(kind, "ℹ️")
    st.markdown(
        f"""
        <div class="feishu-toast feishu-toast-{kind} feishu-fade-in">
            <span class="feishu-toast-icon">{icon}</span>
            <span class="feishu-toast-text">{html.escape(text)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def timeline(events: list) -> None:
    """审计时间线 — 垂直步骤条.

    events: [{"time": "2026-06-20 10:00", "title": "...", "desc": "...",
              "status": "success|warning|error|primary"}, ...]
    """
    if not events:
        empty_state(icon="📜", message="暂无时间线事件")
        return
    rows = []
    for ev in events:
        status = ev.get("status", "muted")
        time_str = html.escape(str(ev.get("time", "")))
        title = html.escape(str(ev.get("title", "")))
        desc = html.escape(str(ev.get("desc", "")))
        rows.append(
            f"""
            <div class="feishu-timeline-item">
                <div class="feishu-timeline-dot {status}"></div>
                <div class="feishu-timeline-content">
                    <div class="feishu-timeline-time">{time_str}</div>
                    <div class="feishu-timeline-title">{title}</div>
                    {f'<div class="feishu-timeline-desc">{desc}</div>' if desc else ''}
                </div>
            </div>
            """
        )
    st.markdown(
        f'<div class="feishu-timeline">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def skeleton(lines: int = 3, height: int = 16) -> None:
    """加载骨架屏 — 用 CSS 动画显示灰色横条 (流式加载占位)."""
    widths = [95, 80, 88, 65, 92][:lines]
    bars = "".join(
        f'<div class="feishu-skeleton-bar" '
        f'style="width:{w}%;height:{height}px;animation-delay:{i * 80}ms"></div>'
        for i, w in enumerate(widths)
    )
    st.markdown(
        f'<div class="feishu-skeleton">{bars}</div>',
        unsafe_allow_html=True,
    )


def breadcrumb(items) -> None:
    """面包屑导航 — 项目路径式. items: ["首页", "项目", "ACME 2025"]"""
    if not items:
        return
    parts = []
    for i, label in enumerate(items):
        is_last = i == len(items) - 1
        if is_last:
            parts.append(f'<span class="feishu-crumb current">{html.escape(label)}</span>')
        else:
            sep = '<span class="feishu-crumb-sep">/</span>'
            parts.append(
                f'<span class="feishu-crumb">{html.escape(label)}</span>{sep}'
            )
    st.markdown(
        f'<div class="feishu-breadcrumb">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )


def progress_ring(percent: float, size: int = 64, label=None) -> None:
    """圆形进度环 — SVG. percent: 0-100, label: 中心文字 (默认 "{percent}%")."""
    pct = max(0.0, min(100.0, float(percent)))
    r = (size - 8) / 2
    cx = cy = size / 2
    circumference = 2 * 3.141592653589793 * r
    offset = circumference * (1 - pct / 100.0)
    color = FEISHU_C.primary
    if pct >= 100:
        color = FEISHU_C.success
    elif pct < 30:
        color = FEISHU_C.warning
    text = html.escape(label or f"{pct:.0f}%")
    svg = f"""
    <div class="feishu-ring-wrap">
      <svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">
        <circle cx="{cx}" cy="{cy}" r="{r}" fill="none"
                stroke="{FEISHU_C.border_light}" stroke-width="4"/>
        <circle cx="{cx}" cy="{cy}" r="{r}" fill="none"
                stroke="{color}" stroke-width="4"
                stroke-dasharray="{circumference}"
                stroke-dashoffset="{offset}"
                stroke-linecap="round"
                transform="rotate(-90 {cx} {cy})"/>
        <text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central"
              font-size="{int(size * 0.28)}" font-weight="700"
              fill="{FEISHU_C.text_primary}">{text}</text>
      </svg>
    </div>
    """
    st.markdown(svg, unsafe_allow_html=True)


def stat_grid(items: list, aspect: str = "phi") -> None:
    """φ 网格统计卡 — 按 φ 比例分配列宽.

    items: [{"label","value","delta","delta_dir","status"}, ...]
    aspect: "phi" / "phi_inv" / "equal"
    """
    if not items:
        return
    widths = golden_grid_columns(len(items), aspect)
    cols = st.columns(widths)
    for col, item in zip(cols, items):
        with col:
            metric_card(
                label=item.get("label", ""),
                value=item.get("value", ""),
                delta=item.get("delta"),
                delta_direction=item.get("delta_dir", "neutral"),
                status=item.get("status", "default"),
            )


def info_panel(title: str, body: str, kind: str = "info", icon=None) -> None:
    """信息面板 — 大块说明区域 (操作引导 / 帮助 / 警告)."""
    icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
    if icon is None:
        icon = icons.get(kind, "ℹ️")
    st.markdown(
        f"""
        <div class="feishu-info-panel feishu-info-{kind} feishu-fade-in">
            <div class="feishu-info-head">
                <span class="feishu-info-icon">{icon}</span>
                <span class="feishu-info-title">{html.escape(title)}</span>
            </div>
            <div class="feishu-info-body">{html.escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def link_card(
    icon: str,
    title: str,
    desc: str,
    action_label: str = "进入",
    key: Optional[str] = None,
    button_kind: str = "secondary",
) -> bool:
    """可点击大卡片 — 图标 + 标题 + 描述 + 按钮.

    返回按钮是否被点击. 用法:
        clicked = link_card("📁", "项目管理", "创建 / 查询 IPO 项目", "新建项目", "home_new_project")
        if clicked:
            ...
    """
    st.markdown(
        f"""
        <div class="feishu-link-card feishu-fade-in">
            <div class="feishu-link-icon">{icon}</div>
            <div class="feishu-link-title">{html.escape(title)}</div>
            <div class="feishu-link-desc">{html.escape(desc)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return st.button(
        action_label,
        key=key,
        use_container_width=True,
        type="primary" if button_kind == "primary" else "secondary",
    )


def kv_list(items) -> None:
    """键值对列表 — 左键右值 (元数据展示). items: [(k, v), ...]"""
    if not items:
        return
    rows = "".join(
        f"""
        <div class="feishu-kv-row">
            <span class="feishu-kv-key">{html.escape(str(k))}</span>
            <span class="feishu-kv-val">{html.escape(str(v))}</span>
        </div>
        """
        for k, v in items
    )
    st.markdown(f'<div class="feishu-kv-list">{rows}</div>', unsafe_allow_html=True)


def pill_label(text: str, kind: str = "default", icon=None) -> str:
    """圆角标签 — 返回 HTML, 适合作为分类标签."""
    icon_html = f'<span>{icon}</span>' if icon else ""
    return (
        f'<span class="feishu-pill feishu-pill-{kind}">'
        f'{icon_html}<span>{html.escape(text)}</span></span>'
    )


def greeting_banner(
    user_name: str = "用户",
    role: str = "auditor",
    project_count: int = 0,
    pending_count: int = 0,
) -> None:
    """欢迎横幅 — 首页顶部, 个性化问候 + 状态速览."""
    from datetime import datetime

    hour = datetime.now().hour
    if hour < 6:
        greet = "夜深了, 注意休息"
    elif hour < 12:
        greet = "早上好"
    elif hour < 18:
        greet = "下午好"
    else:
        greet = "晚上好"

    role_zh = {
        "admin": "管理员",
        "partner": "合伙人",
        "manager": "项目经理",
        "assistant": "审计助理",
        "qc_partner": "质控合伙人",
        "signing_partner": "签字合伙人",
        "auditor": "审计师",
    }.get(role, role)

    body = (
        f"{greet}, <b>{html.escape(user_name)}</b> "
        f"<span class='feishu-greet-role'>({html.escape(role_zh)})</span>"
    )
    if project_count or pending_count:
        body += (
            "  ·  📁 " + str(project_count) + " 个项目"
            + ("  ·  ⏳ " + str(pending_count) + " 项待办" if pending_count else "")
        )

    st.markdown(
        f"""
        <div class="feishu-greeting feishu-fade-in">
            <div class="feishu-greeting-text">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def feature_card(icon: str, title: str, desc: str, badge=None) -> None:
    """功能介绍卡片 — 静态展示 (无按钮), 用于「功能矩阵 / 关于页」."""
    badge_html = (
        f'<span class="feishu-pill feishu-pill-primary">{html.escape(badge)}</span>'
        if badge else ""
    )
    st.markdown(
        f"""
        <div class="feishu-feature-card feishu-fade-in">
            <div class="feishu-feature-head">
                <span class="feishu-feature-icon">{icon}</span>
                <span class="feishu-feature-title">{html.escape(title)}</span>
                {badge_html}
            </div>
            <div class="feishu-feature-desc">{html.escape(desc)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


__all__ = [
    "page_header",
    "metric_card",
    "status_badge",
    "status_dot",
    "render_status_badge",
    "section_card_start",
    "section_card_end",
    "empty_state",
    "data_table",
    "render_top_badges",
    "feishu_divider",
    # Pack A 美学 + UX 扩展
    "kbd",
    "toast",
    "timeline",
    "skeleton",
    "breadcrumb",
    "progress_ring",
    "stat_grid",
    "info_panel",
    "link_card",
    "kv_list",
    "pill_label",
    "greeting_banner",
    "feature_card",
]
