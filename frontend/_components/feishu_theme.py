"""飞书浅色主题 — 设计令牌 + 注入函数.

设计参考:
  - 飞书默认浅色 (Lark Light) — 白底 + 蓝紫主色 #3370FF
  - 卡片化布局 + 柔和阴影 + 8/12px 圆角
  - 思源黑体 / Inter / PingFang SC 字体栈
  - 间距 8/16/24 三档

用法:
    from frontend._components.feishu_theme import apply_feishu_theme
    apply_feishu_theme()  # 在每个 page 顶部调用一次, 幂等

提供:
    FEISHU_*             — 设计令牌 (色板/字号/圆角/阴影/间距)
    apply_feishu_theme() — 注入全局 CSS
    feishu_fade_in()     — 内容淡入动画
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import streamlit as st


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
    sm: str = "6px"
    md: str = "8px"
    lg: str = "12px"
    xl: str = "16px"
    pill: str = "9999px"


@dataclass(frozen=True)
class FeishuSpace:
    xs: str = "4px"
    sm: str = "8px"
    md: str = "16px"
    lg: str = "24px"
    xl: str = "32px"
    xxl: str = "48px"


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
    """生成飞书主题完整 CSS — 通过 st.markdown 注入, 覆盖 Streamlit 默认样式."""
    return f"""
<style>
/* ============================================================
 * 飞书浅色主题 — IPO 审计系统
 * 设计参考: Lark Light v3
 * ============================================================ */

/* ── 字体 & 全局 ───────────────────────────────────────── */
html, body, [class*="css"], .stApp {{
    font-family: {FEISHU_FONT} !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    color: {FEISHU_C.text_primary};
}}

.stApp {{
    background: {FEISHU_C.surface};
}}

/* ── 主区块留白 ───────────────────────────────────────── */
.main .block-container {{
    padding-top: 2rem;
    padding-bottom: 3rem;
    max-width: 1400px;
}}

/* ── 顶部 toolbar / header 改成飞书蓝 ───────────────────── */
header[data-testid="stHeader"] {{
    background: linear-gradient(135deg, {FEISHU_C.primary} 0%, #5B8DEF 100%);
    color: white;
    box-shadow: {FEISHU_C.shadow_sm};
}}
header[data-testid="stHeader"] * {{
    color: white !important;
}}
header[data-testid="stHeader"] [data-testid="stToolbar"] button {{
    color: white !important;
}}

/* ── 侧栏 — 飞书化 ─────────────────────────────────────── */
section[data-testid="stSidebar"] {{
    background: {FEISHU_C.bg};
    border-right: 1px solid {FEISHU_C.border_light};
    box-shadow: 2px 0 8px rgba(31,35,41,0.02);
}}
section[data-testid="stSidebar"] .stRadio label {{
    padding: 0.55rem 0.85rem;
    border-radius: {FEISHU_R.md};
    transition: all 0.15s ease;
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
    font-size: 1.15rem !important;
    font-weight: 600 !important;
    color: {FEISHU_C.text_primary} !important;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid {FEISHU_C.border_light};
    margin-bottom: 0.75rem !important;
}}

/* ── 标题 ───────────────────────────────────────────────── */
h1, h2, h3, h4, h5, h6 {{
    color: {FEISHU_C.text_primary} !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
}}
h1 {{ font-size: 1.75rem !important; }}
h2 {{ font-size: 1.4rem !important; }}
h3 {{ font-size: 1.2rem !important; }}
h4 {{ font-size: 1.05rem !important; }}

/* ── 页头 (page-header) ─────────────────────────────────── */
.feishu-page-header {{
    background: linear-gradient(135deg, {FEISHU_C.primary_bg} 0%, #ffffff 100%);
    border: 1px solid {FEISHU_C.border_light};
    border-left: 4px solid {FEISHU_C.primary};
    border-radius: {FEISHU_R.lg};
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.5rem;
    box-shadow: {FEISHU_C.shadow_sm};
}}
.feishu-page-header h1 {{
    margin: 0 !important;
    font-size: 1.5rem !important;
    color: {FEISHU_C.text_primary} !important;
    display: flex;
    align-items: center;
    gap: 0.6rem;
}}
.feishu-page-header .subtitle {{
    margin-top: 0.4rem;
    color: {FEISHU_C.text_secondary};
    font-size: 0.9rem;
}}

/* ── 卡片 ───────────────────────────────────────────────── */
.feishu-card {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.lg};
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
    box-shadow: {FEISHU_C.shadow_sm};
    transition: box-shadow 0.2s ease, transform 0.2s ease;
}}
.feishu-card:hover {{
    box-shadow: {FEISHU_C.shadow_md};
}}
.feishu-card-title {{
    font-size: 1rem;
    font-weight: 600;
    color: {FEISHU_C.text_primary};
    margin-bottom: 0.75rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}}
.feishu-card-title::before {{
    content: "";
    display: inline-block;
    width: 3px;
    height: 16px;
    background: {FEISHU_C.primary};
    border-radius: 2px;
}}

/* ── 指标卡 (metric card) ───────────────────────────────── */
.feishu-metric-card {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.lg};
    padding: 1.25rem 1.5rem;
    box-shadow: {FEISHU_C.shadow_sm};
    transition: all 0.2s ease;
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
    font-size: 0.85rem;
    color: {FEISHU_C.text_secondary};
    margin-bottom: 0.5rem;
    font-weight: 500;
}}
.feishu-metric-card .value {{
    font-size: 1.75rem;
    font-weight: 700;
    color: {FEISHU_C.text_primary};
    line-height: 1.2;
    font-feature-settings: "tnum";
    font-variant-numeric: tabular-nums;
}}
.feishu-metric-card .delta {{
    font-size: 0.8rem;
    margin-top: 0.4rem;
    color: {FEISHU_C.text_tertiary};
}}
.feishu-metric-card .delta.up {{ color: {FEISHU_C.success}; }}
.feishu-metric-card .delta.down {{ color: {FEISHU_C.error}; }}

/* ── 状态徽章 (status badge) ────────────────────────────── */
.feishu-badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.2rem 0.65rem;
    border-radius: {FEISHU_R.pill};
    font-size: 0.78rem;
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
    margin-right: 0.4rem;
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
    padding: 3rem 1.5rem;
    text-align: center;
    color: {FEISHU_C.text_secondary};
}}
.feishu-empty .icon {{
    font-size: 2.5rem;
    margin-bottom: 0.75rem;
    opacity: 0.5;
}}
.feishu-empty .title {{
    font-size: 1rem;
    color: {FEISHU_C.text_primary};
    font-weight: 500;
    margin-bottom: 0.4rem;
}}
.feishu-empty .hint {{
    font-size: 0.85rem;
    color: {FEISHU_C.text_tertiary};
}}

/* ── 按钮 (button) ──────────────────────────────────────── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    background: {FEISHU_C.bg} !important;
    color: {FEISHU_C.text_primary} !important;
    border: 1px solid {FEISHU_C.border} !important;
    border-radius: {FEISHU_R.md} !important;
    padding: 0.5rem 1.1rem !important;
    font-weight: 500 !important;
    font-family: {FEISHU_FONT} !important;
    transition: all 0.15s ease !important;
    box-shadow: {FEISHU_C.shadow_sm} !important;
}}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
    background: {FEISHU_C.primary_bg} !important;
    color: {FEISHU_C.primary} !important;
    border-color: {FEISHU_C.primary} !important;
    transform: translateY(-1px) !important;
}}
.stButton > button:active, .stDownloadButton > button:active, .stFormSubmitButton > button:active {{
    transform: translateY(0) !important;
}}
/* primary 按钮 */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
    background: linear-gradient(135deg, {FEISHU_C.primary} 0%, #5B8DEF 100%) !important;
    color: white !important;
    border: none !important;
    box-shadow: 0 2px 4px rgba(51, 112, 255, 0.3) !important;
}}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {{
    background: linear-gradient(135deg, {FEISHU_C.primary_hover} 0%, #4A7AE5 100%) !important;
    color: white !important;
    box-shadow: 0 4px 12px rgba(51, 112, 255, 0.4) !important;
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
    transition: all 0.15s ease !important;
    font-family: {FEISHU_FONT} !important;
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
    font-size: 0.9rem !important;
    margin-bottom: 0.3rem !important;
}}

/* ── Selectbox ──────────────────────────────────────────── */
.stSelectbox > div > div, .stMultiSelect > div > div {{
    border: 1px solid {FEISHU_C.border} !important;
    border-radius: {FEISHU_R.md} !important;
    background: {FEISHU_C.bg} !important;
    transition: all 0.15s ease !important;
}}
.stSelectbox > div > div:hover, .stMultiSelect > div > div:hover {{
    border-color: {FEISHU_C.primary} !important;
}}

/* ── Tabs ───────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {{
    gap: 0.5rem;
    background: transparent;
    border-bottom: 1px solid {FEISHU_C.border_light};
    padding-bottom: 0;
}}
.stTabs [data-baseweb="tab"] {{
    height: 2.75rem;
    padding: 0 1.25rem;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {FEISHU_C.text_secondary};
    font-weight: 500;
    border-radius: 0;
    transition: all 0.15s ease;
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
    padding: 0.6rem 1rem !important;
    transition: all 0.15s ease !important;
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
    padding: 0.75rem 1rem !important;
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
}}

/* ── File uploader ──────────────────────────────────────── */
[data-testid="stFileUploaderDropzone"] {{
    background: {FEISHU_C.surface_alt} !important;
    border: 1.5px dashed {FEISHU_C.border} !important;
    border-radius: {FEISHU_R.lg} !important;
    transition: all 0.15s ease;
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
    top: 0.7rem;
    z-index: 999;
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.25rem 0.7rem;
    border-radius: {FEISHU_R.pill};
    font-size: 0.78rem;
    font-weight: 500;
    box-shadow: {FEISHU_C.shadow_md};
    backdrop-filter: blur(8px);
}}
.feishu-top-badge.right {{ right: 1rem; background: rgba(245, 63, 63, 0.92); color: white; }}
.feishu-top-badge.right-muted {{ right: 1rem; background: rgba(143, 149, 158, 0.85); color: white; }}
.feishu-top-badge.left-right {{ right: 7rem; background: rgba(255, 125, 0, 0.92); color: white; }}
.feishu-top-badge.left-right-muted {{ right: 7rem; background: rgba(143, 149, 158, 0.85); color: white; }}

/* ── Metric (Streamlit 内置) 飞书化 ────────────────────── */
[data-testid="stMetric"] {{
    background: {FEISHU_C.bg};
    border: 1px solid {FEISHU_C.border_light};
    border-radius: {FEISHU_R.lg};
    padding: 1rem 1.25rem;
    box-shadow: {FEISHU_C.shadow_sm};
}}
[data-testid="stMetric"] label {{
    color: {FEISHU_C.text_secondary} !important;
    font-size: 0.85rem !important;
    font-weight: 500 !important;
}}
[data-testid="stMetricValue"] {{
    color: {FEISHU_C.text_primary} !important;
    font-weight: 700 !important;
    font-size: 1.6rem !important;
}}

/* ── 滚动条 ─────────────────────────────────────────────── */
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
}}
.feishu-table th {{
    background: {FEISHU_C.surface};
    color: {FEISHU_C.text_secondary};
    font-weight: 600;
    text-align: left;
    padding: 0.65rem 0.85rem;
    border-bottom: 1px solid {FEISHU_C.border_light};
    font-size: 0.85rem;
}}
.feishu-table td {{
    padding: 0.7rem 0.85rem;
    border-bottom: 1px solid {FEISHU_C.border_light};
    color: {FEISHU_C.text_primary};
    font-size: 0.9rem;
}}
.feishu-table tr:hover td {{ background: {FEISHU_C.surface_alt}; }}
.feishu-table tr:last-child td {{ border-bottom: none; }}

/* ── 飞书动画: 渐入 ─────────────────────────────────────── */
@keyframes feishuFadeIn {{
    from {{ opacity: 0; transform: translateY(4px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}
.feishu-fade-in {{
    animation: feishuFadeIn 0.25s ease-out;
}}

/* ── 全局分隔线 ─────────────────────────────────────────── */
hr {{
    border: none;
    border-top: 1px solid {FEISHU_C.border_light};
    margin: 1.25rem 0;
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
