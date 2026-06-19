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
"""
from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
import streamlit as st

from frontend._components.feishu_theme import FEISHU_C, FEISHU_R


# ──────────────────────────────────────────────────────────────
# 页头
# ──────────────────────────────────────────────────────────────


def page_header(
    icon: str,
    title: str,
    subtitle: Optional[str] = None,
) -> None:
    """飞书页头 — 大标题 + 副标题 + 蓝紫左边框装饰."""
    sub_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="feishu-page-header feishu-fade-in">
            <h1><span>{icon}</span><span>{title}</span></h1>
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

    head = "".join(f"<th>{c}</th>" for c in df.columns)
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(f"<td>{v}</td>" for v in row.values)
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
]
