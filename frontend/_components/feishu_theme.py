"""飞书浅色主题 — 设计令牌 + 注入函数.

设计参考:
  - 飞书默认浅色 (Lark Light) — 白底 + 蓝紫主色 #3370FF
  - 卡片化布局 + 柔和阴影 + 6/8/12/16px 圆角
  - 思源黑体 / Inter / PingFang SC 字体栈
  - **黄金分割 (φ = 1.618) 字号 / 间距 / 时长梯度**:
      字号: 10 / 13 / 16 / 20 / 26 / 33 / 42 px (×φ 比例)
      间距: 4 / 6 / 10 / 16 / 26 / 42 / 68 / 110 px (×φ 比例)
      时长: 97 / 162 / 262 / 424 / 686 / 1110 ms (×φ 比例)
  - φ 布局: 主内容 61.8% / 侧栏 38.2%, 卡片宽 = 容器 / φ

用法:
    from frontend._components.feishu_theme import apply_feishu_theme
    apply_feishu_theme()  # 在每个 page 顶部调用一次, 幂等

提供:
    FEISHU_*             — 设计令牌 (色板/字号/圆角/阴影/间距)
    apply_feishu_theme() — 注入全局 CSS
    feishu_fade_in()     — 内容淡入动画 (φ 节奏)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import streamlit as st

from frontend._components.golden import (
    PHI,
    PHI_INV,
    GOLDEN_FONT_SCALE,
    GOLDEN_SPACE_PX,
    GOLDEN_TIME_MS,
    golden_font_size,
    golden_padding,
    golden_duration,
)


# ──────────────────────────────────────────────────────────────
# 设计令牌 (design tokens) — 整库统一, 不在 CSS / 组件里硬编码
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FeishuColors:
    """飞书浅色色板."""

    # 主色 — 飞书蓝紫
    primary: str = "#3370FF"
    primary_hover: str = "#2860E5"
    primary_active: str = "#1E50D9"
    primary_light: str = "#E8F0FF"
    primary_bg: str = "#F2F7FF"

    # 语义色
    success: str = "#00B96B"
    success_light: str = "#E8FAF0"
    warning: str = "#FF7D00"
    warning_light: str = "#FFF3E0"
    error: str = "#F53F3F"
    error_light: str = "#FFECE8"
    info: str = "#3370FF"
    info_light: str = "#E8F0FF"

    # 中性
    bg: str = "#FFFFFF"
    surface: str = "#F5F6F7"          # 次级背景
    surface_hover: str = "#F0F1F3"
    surface_alt: str = "#FAFAFA"

    border: str = "#E5E6EB"
    border_light: str = "#F2F3F5"
    border_strong: str = "#C9CDD4"

    # 文字
    text_primary: str = "#1F2329"    # 主文
    text_secondary: str = "#4E5969"  # 次文
    text_tertiary: str = "#8F959E"   # 弱文
    text_disabled: str = "#C9CDD4"
    text_inverse: str = "#FFFFFF"

    # 阴影
    shadow_sm: str = "0 1px 2px rgba(31, 35, 41, 0.04)"
    shadow_md: str = "0 2px 8px rgba(31, 35, 41, 0.06), 0 1px 2px rgba(31, 35, 41, 0.04)"
    shadow_lg: str = "0 4px 16px rgba(31, 35, 41, 0.08), 0 2px 4px rgba(31, 35, 41, 0.04)"
    shadow_xl: str = "0 8px 32px rgba(31, 35, 41, 0.10), 0 4px 8px rgba(31, 35, 41, 0.06)"


@dataclass(frozen=True)
class FeishuRadius:
    """圆角 — 与 φ 节奏协同: 6/10/16/26 px (≈ ×φ)."""
    sm: str = "6px"    # 按钮/输入
    md: str = "8px"    # 卡片/面板
    lg: str = "12px"   # 大卡片/页头
    xl: str = "16px"   # 浮层/弹窗
    xxl: str = "26px"  # 大型容器 (φ²×10)
    pill: str = "9999px"


@dataclass(frozen=True)
class FeishuSpace:
    """间距梯度 — φ 比例: 4/6/10/16/26/42/68 px."""
    xs: str = "4px"
    sm: str = "8px"
    md: str = "16px"
    lg: str = "26px"     # φ × 16 = 26
    xl: str = "42px"     # φ × 26 ≈ 42
    xxl: str = "68px"    # φ × 42 ≈ 68


FEISHU_C: Final = FeishuColors()
FEISHU_R: Final = FeishuRadius()
FEISHU_S: Final = FeishuSpace()

# 字体栈 — 思源黑体 / Inter / PingFang SC
FEISHU_FONT: Final = (
    '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", '
    '"Segoe UI", "Source Han Sans CN", "Inter", "Helvetica Neue", sans-serif'
)
FEISHU_MONO: Final = (
    '"JetBrains Mono", "SF Mono", "Cascadia Code", "Consolas", '
    '"Source Code Pro", Menlo, monospace'
)


# ──────────────────────────────────────────────────────────────
# 全局 CSS — 一次性注入, 覆盖 streamlit 默认 + 飞书化
# ──────────────────────────────────────────────────────────────


def _feishu_css() -> str:
    """生成飞书主题完整 CSS — 通过 st.markdown 注入, 覆盖 Streamlit 默认样式.

    所有间距 / 字号 / 时长均按 φ (1.618) 推导, 保证视觉节奏感.
    """
    # ── φ 推导的关键尺寸 (px) ──
    PAD_XS = golden_padding(0)   # 4
    PAD_SM = golden_padding(2)   # 10
    PAD_MD = golden_padding(3)   # 16
    PAD_LG = golden_padding(4)   # 26
    PAD_XL = golden_padding(5)   # 42

    # 时长 (ms → s)
    T_FAST = golden_duration(1) / 1000   # 162ms
    T_MED = golden_duration(2) / 1000    # 262ms
    T_SLOW = golden_duration(3) / 1000   # 424ms

    # 字号 (rem, 1rem=16px) — 标题按 φ 梯度
    F_HERO = golden_font_size(6)    # 2.618rem ~ 42px
    F_H1 = golden_font_size(5)      # 2.058rem ~ 33px
    F_H2 = golden_font_size(4)      # 1.618rem ~ 26px
    F_H3 = golden_font_size(3)      # 1.272rem ~ 20px
    F_BODY = 1.0                    # 1.0rem ~ 16px
    F_SM = golden_font_size(1)      # 0.786rem ~ 13px
    F_XS = golden_font_size(0)      # 0.618rem ~ 10px

    return f"""
<style>
/* ============================================================
 * 飞书浅色主题 — IPO 审计系统
 * 设计参考: Lark Light v3 + φ (1.618) 黄金分割
 * φ=1.618 / 1/φ=0.618 — 字号/间距/时长均按 φ 比例推导
 * ============================================================ */

/* ── 字体 & 全局 ───────────────────────────────────────── */
html, body, [class*="css"], .stApp {{
    font-family: {FEISHU_FONT} !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    color: {FEISHU_C.text_primary};
    font-size: {F_BODY}rem;
}}

.stApp {{
    background: {FEISHU_C.surface};
}}

/* ── 主区块留白 (φ 节奏) ───────────────────────────────── */
.main .block-container {{
    padding-top: {PAD_LG}px;       /* 26px — 1/φ 主区顶 */
    padding-bottom: {PAD_XL}px;    /* 42px — φ 主区底 */
    max-width: 1400px;             /* 容器宽 = 1400 → 主区 535 / 侧栏 865 */
}}

/* ── 顶部 toolbar / header 改成飞书蓝 ───────────────────── */
header[data-testid="stHeader"] {{
    background: linear-gradient(135deg, {FEISHU_C.primary} 0%, #5B8DEF 100%);
    color: white;
    box-shadow: {FEISHU_C.shadow_sm};
    height: {golden_duration(2) // 8}px;  /* φ 节奏: header 高度 ≈ 32px */
}}
header[data-testid="stHeader"] * {{
    color: white !important;
}}
header[data-testid="stHeader"] [data-testid="stToolbar"] button {{
    color: white !important;
}}

/* ── 侧栏 — 飞书化 (宽度按 φ = 38.2%) ──────────────────── */
section[data-testid="stSidebar"] {{
    background: {FEISHU_C.bg};
    border-right: 1px solid {FEISHU_C.border_light};
    box-shadow: 2px 0 8px rgba(31,35,41,0.02);
    width: {int(1400 * PHI_INV)}px;       /* 535px ≈ 38.2% × 1400 */
    min-width: {int(1400 * PHI_INV)}px;
}}
section[data-testid="stSidebar"] > div {{
    padding-top: {PAD_SM}px;              /* 10px */
    padding-left: {PAD_SM}px;
    padding-right: {PAD_SM}px;
}}
section[data-testid="stSidebar"] .stRadio label {{
    padding: {PAD_XS + 2}px {PAD_SM}px;  /* ~6 / 10 — φ 小间距 */
    border-radius: {FEISHU_R.md};
    transition: all {T_FAST}s ease;       /* 162ms — φ 快速节奏 */
    margin-bottom: 2px;
    color: {FEISHU_C.text_primary};
}}
section[data-testid="stSidebar"] .stRadio label:hover {{
    background: {FEISHU_C.primary_bg};
    color: {FEISHU_C.primary};
}}
section[data-testid="stSidebar"] .stRadio input:checked + div {{
    color: {FEISHU_C.primary} !important;
    font-weight: 600;
}}
section[data-testid="stSidebar"] h1 {{
    font-size: {golden_font_size(3):.3f}rem !important;  /* 1.272rem */
    font-weight: 600 !important;
    color: {FEISHU_C.text_primary} !important;
    padding-bottom: {PAD_XS + 2}px;
    border-bottom: 1px solid {FEISHU_C.border_light};
    margin-bottom: {PAD_SM + 2}px !important;
}}

/* ── 标题 (φ 梯度) ─────────────────────────────────────── */
h1, h2, h3, h4, h5, h6 {{
    color: {FEISHU_C.text_primary} !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
    line-height: 1.272;  /* φ 重设行高 */
}}
h1 {{ font-size: {F_H1:.3f}rem !important; }}  /* 2.058rem ~ 33px */
h2 {{ font-size: {F_H2:.3f}rem !important; }}  /* 1.618rem ~ 26px */
h3 {{ font-size: {F_H3:.3f}rem !important; }}  /* 1.272rem ~ 20px */
h4 {{ font-size: 1.05rem !important; }}
h5 {{ font-size: 0.95rem !important; }}
h6 {{ font-size: {F_SM:.3f}rem !important; }}  /* 0.786rem */

/* ── 页头 (page-header) ─────────────────────────────────── */
.feishu-page-header {{
    background: linear-gradient(135deg, {FEISHU_C.primary_bg} 0%, #ffffff 100%);
    border: 1px solid {FEISHU_C.border_light};
    border-left: 4px solid {FEISHU_C.primary};
    border-radius: {FEISHU_R.lg};
    padding: {PAD_LG - 5}px {PAD_LG + 2}px;  /* 21 / 28 — φ 内距 */
    margin-bottom: {PAD_LG}px;                /* 26 — φ 主间距 */
    box-shadow: {FEISHU_C.shadow_sm};
}}
.feishu-page-header h1 {{
    margin: 0 !important;
    font-size: {F_H2:.3f}rem !important;     /* 1.618rem ~ 26px */
    color: {FEISHU_C.text_primary} !important;
    display: flex;
    align-items: center;
    gap: {PAD_SM}px;                          /* 10px */
    font-weight: 700 !important;
}}
.feishu-page-header .subtitle {{
    margin-top: {PAD_XS + 2}px;
    color: {FEISHU_C.text_secondary};
    font-size: {F_SM:.3f}rem;                 /* 0.786rem */
    font-weight: 400;
}}

/* ── 卡片 ───────────────────────────────────────────────── */
.feishu-card {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.lg};
    padding: {PAD_LG - 5}px {PAD_LG + 2}px;   /* 21 / 28 — φ 内距 */
    margin-bottom: {PAD_MD}px;                 /* 16 */
    box-shadow: {FEISHU_C.shadow_sm};
    transition: box-shadow {T_MED}s ease, transform {T_MED}s ease;
}}
.feishu-card:hover {{
    box-shadow: {FEISHU_C.shadow_md};
    transform: translateY(-1px);
}}
.feishu-card-title {{
    font-size: 1rem;
    font-weight: 600;
    color: {FEISHU_C.text_primary};
    margin-bottom: {PAD_SM + 2}px;            /* ~12 */
    display: flex;
    align-items: center;
    gap: {PAD_XS + 2}px;
}}
.feishu-card-title::before {{
    content: "";
    display: inline-block;
    width: 3px;
    height: {PAD_MD}px;                        /* 16px ≈ φ × 10 */
    background: {FEISHU_C.primary};
    border-radius: 2px;
}}

/* ── 指标卡 (metric card) ───────────────────────────────── */
.feishu-metric-card {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.lg};
    padding: {PAD_LG - 5}px {PAD_LG + 2}px;
    box-shadow: {FEISHU_C.shadow_sm};
    transition: all {T_MED}s ease;
    position: relative;
    overflow: hidden;
}}
.feishu-metric-card:hover {{
    box-shadow: {FEISHU_C.shadow_md};
    transform: translateY(-1px);
}}
.feishu-metric-card::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, {FEISHU_C.primary}, #5B8DEF);
}}
.feishu-metric-card .label {{
    font-size: {F_SM:.3f}rem;                 /* 0.786rem */
    color: {FEISHU_C.text_secondary};
    margin-bottom: {PAD_XS + 2}px;
    font-weight: 500;
}}
.feishu-metric-card .value {{
    font-size: {F_H2:.3f}rem;                 /* 1.618rem */
    font-weight: 700;
    color: {FEISHU_C.text_primary};
    line-height: 1.2;
    font-feature-settings: "tnum";
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.02em;
}}
.feishu-metric-card .delta {{
    font-size: {F_XS:.3f}rem;                 /* 0.618rem */
    margin-top: {PAD_XS}px;
    color: {FEISHU_C.text_tertiary};
    font-weight: 500;
}}
.feishu-metric-card .delta.up {{ color: {FEISHU_C.success}; }}
.feishu-metric-card .delta.down {{ color: {FEISHU_C.error}; }}

/* ── 状态徽章 (status badge) ────────────────────────────── */
.feishu-badge {{
    display: inline-flex;
    align-items: center;
    gap: {PAD_XS}px;
    padding: 2px {PAD_SM - 2}px;               /* 2 / 8 */
    border-radius: {FEISHU_R.pill};
    font-size: {F_XS:.3f}rem;                 /* 0.618rem */
    font-weight: 500;
    line-height: 1.4;
    white-space: nowrap;
}}
.feishu-badge.default {{
    background: {FEISHU_C.surface};
    color: {FEISHU_C.text_secondary};
}}
.feishu-badge.primary {{
    background: {FEISHU_C.primary_light};
    color: {FEISHU_C.primary};
}}
.feishu-badge.success {{
    background: {FEISHU_C.success_light};
    color: {FEISHU_C.success};
}}
.feishu-badge.warning {{
    background: {FEISHU_C.warning_light};
    color: {FEISHU_C.warning};
}}
.feishu-badge.error {{
    background: {FEISHU_C.error_light};
    color: {FEISHU_C.error};
}}
.feishu-badge.info {{
    background: {FEISHU_C.info_light};
    color: {FEISHU_C.info};
}}

/* ── 状态点 (status dot) ────────────────────────────────── */
.feishu-dot {{
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    margin-right: {PAD_XS + 2}px;
    vertical-align: middle;
}}
.feishu-dot.success {{ background: {FEISHU_C.success}; box-shadow: 0 0 0 3px {FEISHU_C.success_light}; }}
.feishu-dot.warning {{ background: {FEISHU_C.warning}; box-shadow: 0 0 0 3px {FEISHU_C.warning_light}; }}
.feishu-dot.error {{ background: {FEISHU_C.error}; box-shadow: 0 0 0 3px {FEISHU_C.error_light}; }}
.feishu-dot.primary {{ background: {FEISHU_C.primary}; box-shadow: 0 0 0 3px {FEISHU_C.primary_light}; }}
.feishu-dot.muted {{ background: {FEISHU_C.border_strong}; }}

/* ── 空状态 (empty state) ───────────────────────────────── */
.feishu-empty {{
    background: {FEISHU_C.surface_alt};
    border: 1px dashed {FEISHU_C.border};
    border-radius: {FEISHU_R.lg};
    padding: {PAD_XL}px {PAD_LG}px;           /* 42 / 26 — φ 内边距 */
    text-align: center;
    color: {FEISHU_C.text_secondary};
    animation: feishuFadeIn {T_SLOW}s ease-out;
}}
.feishu-empty .icon {{
    font-size: {F_HERO:.3f}rem;               /* 2.618rem ~ 42px */
    margin-bottom: {PAD_SM}px;
    opacity: 0.55;
}}
.feishu-empty .title {{
    font-size: 1rem;
    color: {FEISHU_C.text_primary};
    font-weight: 500;
    margin-bottom: {PAD_XS + 2}px;
}}
.feishu-empty .hint {{
    font-size: {F_SM:.3f}rem;
    color: {FEISHU_C.text_tertiary};
}}

/* ── 按钮 (button) ──────────────────────────────────────── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    background: {FEISHU_C.bg} !important;
    color: {FEISHU_C.text_primary} !important;
    border: 1px solid {FEISHU_C.border} !important;
    border-radius: {FEISHU_R.md} !important;
    padding: {PAD_XS + 2}px {PAD_MD - 2}px !important;  /* 6 / 14 */
    font-weight: 500 !important;
    font-family: {FEISHU_FONT} !important;
    transition: all {T_FAST}s ease !important;
    box-shadow: {FEISHU_C.shadow_sm} !important;
    letter-spacing: 0.01em;
}}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
    background: {FEISHU_C.primary_bg} !important;
    color: {FEISHU_C.primary} !important;
    border-color: {FEISHU_C.primary} !important;
    transform: translateY(-1px) !important;
    box-shadow: {FEISHU_C.shadow_md} !important;
}}
.stButton > button:active, .stDownloadButton > button:active, .stFormSubmitButton > button:active {{
    transform: translateY(0) !important;
}}
/* primary 按钮 */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
    background: linear-gradient(135deg, {FEISHU_C.primary} 0%, #5B8DEF 100%) !important;
    color: white !important;
    border: none !important;
    box-shadow: 0 2px {PAD_XS}px rgba(51, 112, 255, 0.3) !important;
}}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {{
    background: linear-gradient(135deg, {FEISHU_C.primary_hover} 0%, #4A7AE5 100%) !important;
    color: white !important;
    box-shadow: 0 4px {PAD_SM + 2}px rgba(51, 112, 255, 0.4) !important;
    transform: translateY(-1px) !important;
}}

/* ── 输入框 ─────────────────────────────────────────────── */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stNumberInput > div > div > input,
.stDateInput > div > div > input {{
    border: 1px solid {FEISHU_C.border} !important;
    border-radius: {FEISHU_R.md} !important;
    background: {FEISHU_C.bg} !important;
    color: {FEISHU_C.text_primary} !important;
    transition: all {T_FAST}s ease !important;
    font-family: {FEISHU_FONT} !important;
    padding: {PAD_XS + 2}px {PAD_SM}px !important;  /* 6 / 10 */
}}
.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus,
.stNumberInput > div > div > input:focus,
.stDateInput > div > div > input:focus {{
    border-color: {FEISHU_C.primary} !important;
    box-shadow: 0 0 0 3px {FEISHU_C.primary_light} !important;
    outline: none !important;
}}
.stTextInput > label, .stTextArea > label, .stNumberInput > label, .stDateInput > label,
.stSelectbox > label, .stMultiselect > label, .stRadio > label, .stCheckbox > label,
.stSlider > label, .stFileUploader > label {{
    color: {FEISHU_C.text_primary} !important;
    font-weight: 500 !important;
    font-size: {F_SM:.3f}rem !important;
    margin-bottom: {PAD_XS}px !important;
}}

/* ── Selectbox ──────────────────────────────────────────── */
.stSelectbox > div > div, .stMultiSelect > div > div {{
    border: 1px solid {FEISHU_C.border} !important;
    border-radius: {FEISHU_R.md} !important;
    background: {FEISHU_C.bg} !important;
    transition: all {T_FAST}s ease !important;
}}
.stSelectbox > div > div:hover, .stMultiSelect > div > div:hover {{
    border-color: {FEISHU_C.primary} !important;
}}

/* ── Tabs ───────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {{
    gap: {PAD_XS + 2}px;
    background: transparent;
    border-bottom: 1px solid {FEISHU_C.border_light};
    padding-bottom: 0;
}}
.stTabs [data-baseweb="tab"] {{
    height: 2.75rem;
    padding: 0 {PAD_LG - 5}px;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {FEISHU_C.text_secondary};
    font-weight: 500;
    border-radius: 0;
    transition: all {T_FAST}s ease;
}}
.stTabs [data-baseweb="tab"]:hover {{
    color: {FEISHU_C.primary};
    background: {FEISHU_C.primary_bg};
}}
.stTabs [aria-selected="true"] {{
    color: {FEISHU_C.primary} !important;
    border-bottom-color: {FEISHU_C.primary} !important;
    background: transparent !important;
    font-weight: 600;
}}

/* ── Expander ───────────────────────────────────────────── */
.streamlit-expanderHeader, [data-testid="stExpander"] summary {{
    background: {FEISHU_C.surface} !important;
    border: 1px solid {FEISHU_C.border_light} !important;
    border-radius: {FEISHU_R.md} !important;
    color: {FEISHU_C.text_primary} !important;
    font-weight: 500 !important;
    padding: {PAD_XS + 2}px {PAD_MD - 2}px !important;
    transition: all {T_FAST}s ease !important;
}}
.streamlit-expanderHeader:hover, [data-testid="stExpander"] summary:hover {{
    background: {FEISHU_C.primary_bg} !important;
    border-color: {FEISHU_C.primary_light} !important;
}}

/* ── DataFrame 容器 ─────────────────────────────────────── */
.stDataFrame, [data-testid="stDataFrame"], [data-testid="stTable"] {{
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.md};
    overflow: hidden;
    box-shadow: {FEISHU_C.shadow_sm};
}}

/* ── Progress ───────────────────────────────────────────── */
.stProgress > div > div > div > div {{
    background: linear-gradient(90deg, {FEISHU_C.primary}, #5B8DEF) !important;
    border-radius: {FEISHU_R.pill};
}}

/* ── Alert / Info / Success / Error ─────────────────────── */
.stAlert, [data-testid="stAlert"] {{
    border-radius: {FEISHU_R.md} !important;
    border-left: 4px solid !important;
    padding: {PAD_SM}px {PAD_MD}px !important;
    font-size: {F_SM:.3f}rem !important;
}}
.stAlert[data-baseweb="notification"] {{
    background: {FEISHU_C.surface} !important;
}}

/* ── Spinner ────────────────────────────────────────────── */
.stSpinner > div {{
    border-top-color: {FEISHU_C.primary} !important;
}}

/* ── Code block ─────────────────────────────────────────── */
.stCodeBlock, code, pre {{
    font-family: {FEISHU_MONO} !important;
    background: {FEISHU_C.surface} !important;
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.md};
    color: {FEISHU_C.text_primary};
    padding: {PAD_SM}px {PAD_MD}px !important;
    font-size: {F_SM:.3f}rem !important;
}}

/* ── File uploader ──────────────────────────────────────── */
[data-testid="stFileUploaderDropzone"] {{
    background: {FEISHU_C.surface_alt} !important;
    border: 1.5px dashed {FEISHU_C.border} !important;
    border-radius: {FEISHU_R.lg} !important;
    transition: all {T_FAST}s ease;
    padding: {PAD_LG}px !important;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
    background: {FEISHU_C.primary_bg} !important;
    border-color: {FEISHU_C.primary} !important;
}}

/* ── 顶栏 Decoration (隐藏 MainMenu 冗余) ───────────────── */
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}

/* ── 全局红点 (沿用原逻辑, 飞书化) ─────────────────────── */
.feishu-top-badge {{
    position: fixed;
    top: {PAD_SM}px;                          /* 10 */
    z-index: 999;
    display: inline-flex;
    align-items: center;
    gap: {PAD_XS}px;
    padding: {PAD_XS - 2}px {PAD_SM}px;
    border-radius: {FEISHU_R.pill};
    font-size: {F_XS:.3f}rem;
    font-weight: 500;
    box-shadow: {FEISHU_C.shadow_md};
    backdrop-filter: blur(8px);
    animation: feishuPulse {T_SLOW}s ease-in-out infinite;
}}
.feishu-top-badge.right {{ right: {PAD_MD}px; background: rgba(245, 63, 63, 0.92); color: white; }}
.feishu-top-badge.right-muted {{ right: {PAD_MD}px; background: rgba(143, 149, 158, 0.85); color: white; }}
.feishu-top-badge.left-right {{ right: {int(PHI * PAD_MD)}px; background: rgba(255, 125, 0, 0.92); color: white; }}
.feishu-top-badge.left-right-muted {{ right: {int(PHI * PAD_MD)}px; background: rgba(143, 149, 158, 0.85); color: white; }}

/* ── Metric (Streamlit 内置) 飞书化 ────────────────────── */
[data-testid="stMetric"] {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.lg};
    padding: {PAD_LG - 5}px {PAD_LG + 2}px;
    box-shadow: {FEISHU_C.shadow_sm};
    transition: all {T_MED}s ease;
}}
[data-testid="stMetric"]:hover {{
    box-shadow: {FEISHU_C.shadow_md};
    transform: translateY(-1px);
}}
[data-testid="stMetric"] label {{
    color: {FEISHU_C.text_secondary} !important;
    font-size: {F_SM:.3f}rem !important;
    font-weight: 500 !important;
}}
[data-testid="stMetricValue"] {{
    color: {FEISHU_C.text_primary} !important;
    font-weight: 700 !important;
    font-size: {F_H2:.3f}rem !important;
    letter-spacing: -0.02em;
}}
[data-testid="stMetricDelta"] {{
    font-size: {F_XS:.3f}rem !important;
}}

/* ── 滚动条 (φ 比例: 8px 宽) ───────────────────────────── */
::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{
    background: {FEISHU_C.border_strong};
    border-radius: {FEISHU_R.pill};
}}
::-webkit-scrollbar-thumb:hover {{ background: {FEISHU_C.text_tertiary}; }}

/* ── 表格内文字 (df 渲染) ───────────────────────────────── */
.feishu-table {{
    width: 100%;
    border-collapse: collapse;
    background: {FEISHU_C.bg};
    font-size: {F_SM:.3f}rem;
}}
.feishu-table th {{
    background: {FEISHU_C.surface};
    color: {FEISHU_C.text_secondary};
    font-weight: 600;
    text-align: left;
    padding: {PAD_SM}px {PAD_SM + 2}px;
    border-bottom: 1px solid {FEISHU_C.border_light};
    font-size: {F_SM:.3f}rem;
    letter-spacing: 0.02em;
}}
.feishu-table td {{
    padding: {PAD_SM + 1}px {PAD_SM + 2}px;
    border-bottom: 1px solid {FEISHU_C.border_light};
    color: {FEISHU_C.text_primary};
}}
.feishu-table tr:hover td {{ background: {FEISHU_C.surface_alt}; }}
.feishu-table tr:last-child td {{ border-bottom: none; }}

/* ── 飞书动画: 渐入 (φ 节奏 262ms) ─────────────────────── */
@keyframes feishuFadeIn {{
    from {{ opacity: 0; transform: translateY({PAD_XS}px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}
.feishu-fade-in {{
    animation: feishuFadeIn {T_MED}s ease-out;
}}

/* ── 飞书动画: 红点呼吸 (φ 倍时长 686ms) ───────────────── */
@keyframes feishuPulse {{
    0%, 100% {{ transform: scale(1); opacity: 1; }}
    50% {{ transform: scale(1.05); opacity: 0.85; }}
}}

/* ── 飞书动画: 滑入 (从右侧) ───────────────────────────── */
@keyframes feishuSlideIn {{
    from {{ opacity: 0; transform: translateX({PAD_MD}px); }}
    to {{ opacity: 1; transform: translateX(0); }}
}}
.feishu-slide-in {{
    animation: feishuSlideIn {T_MED}s ease-out;
}}

/* ── 全局分隔线 (φ 边距 16/26) ─────────────────────────── */
hr {{
    border: none;
    border-top: 1px solid {FEISHU_C.border_light};
    margin: {PAD_MD}px 0;
}}

/* ── 减少动效 (无障碍) ─────────────────────────────────── */
@media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }}
}}

/* ── 焦点环 (键盘可达性) ───────────────────────────────── */
*:focus-visible {{
    outline: 2px solid {FEISHU_C.primary} !important;
    outline-offset: 2px;
    border-radius: {FEISHU_R.sm};
}}

/* ── 键盘按键 (kbd) ─────────────────────────────────────── */
.feishu-kbd-group {{ display: inline-flex; gap: {PAD_XS}px; align-items: center; }}
.feishu-kbd {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 22px;
    height: 22px;
    padding: 0 6px;
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border};
    border-bottom-width: 2px;
    border-radius: {FEISHU_R.sm};
    color: {FEISHU_C.text_secondary};
    font-family: {FEISHU_MONO};
    font-size: {F_XS:.3f}rem;
    font-weight: 600;
    line-height: 1;
    box-shadow: {FEISHU_C.shadow_sm};
}}

/* ── Toast 横条 ─────────────────────────────────────────── */
.feishu-toast {{
    display: flex;
    align-items: center;
    gap: {PAD_SM}px;
    padding: {PAD_SM}px {PAD_MD}px;
    border-radius: {FEISHU_R.md};
    border-left: 4px solid;
    margin: {PAD_SM}px 0;
    font-size: {F_SM:.3f}rem;
    box-shadow: {FEISHU_C.shadow_sm};
}}
.feishu-toast-success {{ background: {FEISHU_C.success_light}; border-left-color: {FEISHU_C.success}; color: {FEISHU_C.text_primary}; }}
.feishu-toast-error {{ background: {FEISHU_C.error_light}; border-left-color: {FEISHU_C.error}; color: {FEISHU_C.text_primary}; }}
.feishu-toast-warning {{ background: {FEISHU_C.warning_light}; border-left-color: {FEISHU_C.warning}; color: {FEISHU_C.text_primary}; }}
.feishu-toast-info {{ background: {FEISHU_C.primary_light}; border-left-color: {FEISHU_C.primary}; color: {FEISHU_C.text_primary}; }}
.feishu-toast-icon {{ font-size: 1.1rem; }}
.feishu-toast-text {{ font-weight: 500; }}

/* ── Timeline (审计时间线) ─────────────────────────────── */
.feishu-timeline {{ position: relative; padding-left: {PAD_LG + 4}px; }}
.feishu-timeline::before {{
    content: "";
    position: absolute;
    left: {PAD_SM + 4}px;
    top: {PAD_XS}px; bottom: {PAD_XS}px;
    width: 2px;
    background: {FEISHU_C.border_light};
}}
.feishu-timeline-item {{
    position: relative;
    padding: {PAD_SM}px 0;
    padding-left: {PAD_SM}px;
}}
.feishu-timeline-dot {{
    position: absolute;
    left: -{PAD_LG}px;
    top: {PAD_SM + 2}px;
    width: {PAD_SM + 2}px;
    height: {PAD_SM + 2}px;
    border-radius: 50%;
    background: {FEISHU_C.border_strong};
    box-shadow: 0 0 0 3px {FEISHU_C.bg};
}}
.feishu-timeline-dot.success {{ background: {FEISHU_C.success}; }}
.feishu-timeline-dot.warning {{ background: {FEISHU_C.warning}; }}
.feishu-timeline-dot.error {{ background: {FEISHU_C.error}; }}
.feishu-timeline-dot.primary {{ background: {FEISHU_C.primary}; }}
.feishu-timeline-time {{
    font-size: {F_XS:.3f}rem;
    color: {FEISHU_C.text_tertiary};
    font-family: {FEISHU_MONO};
    margin-bottom: 2px;
}}
.feishu-timeline-title {{
    font-size: {F_SM:.3f}rem;
    font-weight: 600;
    color: {FEISHU_C.text_primary};
    margin-bottom: 2px;
}}
.feishu-timeline-desc {{
    font-size: {F_SM:.3f}rem;
    color: {FEISHU_C.text_secondary};
    line-height: 1.5;
}}

/* ── Skeleton (骨架屏) ─────────────────────────────────── */
.feishu-skeleton {{
    display: flex;
    flex-direction: column;
    gap: {PAD_SM}px;
    padding: {PAD_SM}px 0;
}}
.feishu-skeleton-bar {{
    background: linear-gradient(90deg,
        {FEISHU_C.surface} 0%,
        {FEISHU_C.surface_hover} 50%,
        {FEISHU_C.surface} 100%);
    background-size: 200% 100%;
    border-radius: {FEISHU_R.sm};
    animation: feishuShimmer 1.6s ease-in-out infinite;
}}
@keyframes feishuShimmer {{
    0% {{ background-position: 200% 0; }}
    100% {{ background-position: -200% 0; }}
}}

/* ── Breadcrumb (面包屑) ───────────────────────────────── */
.feishu-breadcrumb {{
    display: flex;
    align-items: center;
    gap: {PAD_XS + 2}px;
    font-size: {F_SM:.3f}rem;
    color: {FEISHU_C.text_secondary};
    margin-bottom: {PAD_MD}px;
}}
.feishu-crumb {{ color: {FEISHU_C.text_tertiary}; }}
.feishu-crumb.current {{ color: {FEISHU_C.text_primary}; font-weight: 600; }}
.feishu-crumb-sep {{ color: {FEISHU_C.border_strong}; margin: 0 2px; }}

/* ── Progress Ring (圆环) ──────────────────────────────── */
.feishu-ring-wrap {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: {PAD_XS}px;
}}

/* ── Info Panel (大信息面板) ───────────────────────────── */
.feishu-info-panel {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-left: 4px solid {FEISHU_C.primary};
    border-radius: {FEISHU_R.md};
    padding: {PAD_MD}px {PAD_LG - 5}px;
    margin: {PAD_SM}px 0;
    box-shadow: {FEISHU_C.shadow_sm};
}}
.feishu-info-success {{ border-left-color: {FEISHU_C.success}; background: {FEISHU_C.success_light}; }}
.feishu-info-warning {{ border-left-color: {FEISHU_C.warning}; background: {FEISHU_C.warning_light}; }}
.feishu-info-error {{ border-left-color: {FEISHU_C.error}; background: {FEISHU_C.error_light}; }}
.feishu-info-info {{ border-left-color: {FEISHU_C.primary}; background: {FEISHU_C.primary_light}; }}
.feishu-info-head {{
    display: flex;
    align-items: center;
    gap: {PAD_XS + 2}px;
    margin-bottom: {PAD_XS + 2}px;
}}
.feishu-info-icon {{ font-size: 1.1rem; }}
.feishu-info-title {{ font-weight: 600; color: {FEISHU_C.text_primary}; font-size: 1rem; }}
.feishu-info-body {{ color: {FEISHU_C.text_secondary}; font-size: {F_SM:.3f}rem; line-height: 1.6; }}

/* ── Link Card (大入口卡) ──────────────────────────────── */
.feishu-link-card {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.lg};
    padding: {PAD_LG}px {PAD_MD}px;
    text-align: center;
    transition: all {T_MED}s ease;
    cursor: pointer;
    height: 100%;
    box-shadow: {FEISHU_C.shadow_sm};
}}
.feishu-link-card:hover {{
    box-shadow: {FEISHU_C.shadow_lg};
    transform: translateY(-2px);
    border-color: {FEISHU_C.primary_light};
}}
.feishu-link-icon {{
    font-size: {F_HERO:.3f}rem;
    margin-bottom: {PAD_SM}px;
    display: block;
}}
.feishu-link-title {{
    font-size: 1rem;
    font-weight: 600;
    color: {FEISHU_C.text_primary};
    margin-bottom: {PAD_XS + 2}px;
}}
.feishu-link-desc {{
    font-size: {F_SM:.3f}rem;
    color: {FEISHU_C.text_secondary};
    line-height: 1.5;
    min-height: {PAD_LG}px;
}}

/* ── KV List (键值对) ─────────────────────────────────── */
.feishu-kv-list {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.md};
    overflow: hidden;
}}
.feishu-kv-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: {PAD_SM + 2}px {PAD_MD}px;
    border-bottom: 1px solid {FEISHU_C.border_light};
    font-size: {F_SM:.3f}rem;
}}
.feishu-kv-row:last-child {{ border-bottom: none; }}
.feishu-kv-row:hover {{ background: {FEISHU_C.surface_alt}; }}
.feishu-kv-key {{ color: {FEISHU_C.text_secondary}; font-weight: 500; }}
.feishu-kv-val {{ color: {FEISHU_C.text_primary}; font-weight: 600; }}

/* ── Pill (圆角标签) ───────────────────────────────────── */
.feishu-pill {{
    display: inline-flex;
    align-items: center;
    gap: {PAD_XS}px;
    padding: {PAD_XS}px {PAD_SM + 2}px;
    border-radius: {FEISHU_R.pill};
    font-size: {F_XS:.3f}rem;
    font-weight: 500;
    line-height: 1.5;
}}
.feishu-pill-default {{ background: {FEISHU_C.surface}; color: {FEISHU_C.text_secondary}; }}
.feishu-pill-primary {{ background: {FEISHU_C.primary_light}; color: {FEISHU_C.primary}; }}
.feishu-pill-success {{ background: {FEISHU_C.success_light}; color: {FEISHU_C.success}; }}
.feishu-pill-warning {{ background: {FEISHU_C.warning_light}; color: {FEISHU_C.warning}; }}
.feishu-pill-error {{ background: {FEISHU_C.error_light}; color: {FEISHU_C.error}; }}

/* ── Greeting Banner (欢迎横幅) ────────────────────────── */
.feishu-greeting {{
    background: linear-gradient(135deg,
        {FEISHU_C.primary_bg} 0%,
        #ffffff 100%);
    border: 1px solid {FEISHU_C.primary_light};
    border-radius: {FEISHU_R.lg};
    padding: {PAD_MD + 2}px {PAD_LG}px;
    margin-bottom: {PAD_LG}px;
    box-shadow: {FEISHU_C.shadow_sm};
}}
.feishu-greeting-text {{
    font-size: 1.05rem;
    color: {FEISHU_C.text_primary};
    font-weight: 500;
}}
.feishu-greet-role {{
    color: {FEISHU_C.text_secondary};
    font-weight: 400;
    font-size: {F_SM:.3f}rem;
}}

/* ── Feature Card (功能介绍) ───────────────────────────── */
.feishu-feature-card {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.md};
    padding: {PAD_MD}px {PAD_LG - 5}px;
    height: 100%;
    transition: all {T_MED}s ease;
    box-shadow: {FEISHU_C.shadow_sm};
}}
.feishu-feature-card:hover {{
    border-color: {FEISHU_C.primary_light};
    box-shadow: {FEISHU_C.shadow_md};
}}
.feishu-feature-head {{
    display: flex;
    align-items: center;
    gap: {PAD_XS + 2}px;
    margin-bottom: {PAD_XS + 2}px;
}}
.feishu-feature-icon {{ font-size: 1.25rem; }}
.feishu-feature-title {{ font-weight: 600; color: {FEISHU_C.text_primary}; flex: 1; }}
.feishu-feature-desc {{
    font-size: {F_SM:.3f}rem;
    color: {FEISHU_C.text_secondary};
    line-height: 1.5;
}}
</style>
"""


def apply_feishu_theme() -> None:
    """注入飞书浅色主题 — 每个 page 顶部调用一次, 幂等.

    设计原则: 用 st.markdown(unsafe_allow_html=True) 注入,
    Streamlit 不会重复 set_page_config (后续 sub-page 调用同样安全).
    """
    st.markdown(_feishu_css(), unsafe_allow_html=True)


def feishu_fade_in(content: str) -> None:
    """用淡入动画包裹一段 HTML."""
    st.markdown(
        f'<div class="feishu-fade-in">{content}</div>',
        unsafe_allow_html=True,
    )


__all__ = [
    "FEISHU_C",
    "FEISHU_R",
    "FEISHU_S",
    "FEISHU_FONT",
    "FEISHU_MONO",
    "FeishuColors",
    "FeishuRadius",
    "FeishuSpace",
    "apply_feishu_theme",
    "feishu_fade_in",
]
