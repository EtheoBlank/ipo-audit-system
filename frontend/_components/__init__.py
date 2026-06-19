"""Frontend 共享 UI 组件 / 工具.

抽出来降低 9 个 pages_*.py 的重复代码, 行为保持一致:

- project_picker: 选项目 (4+ 页面都写)
- data_grid:     st.dataframe (10+ 处, 统一 hide_index + use_container_width)
- charts:        简单的 bar/line 辅助
- period_picker: 期末日期 (text_input / date_input) + 校验
- download_excel: 一致的下载按钮
- feishu_theme:  飞书浅色主题设计令牌 + CSS 注入
- feishu_components: 飞书化组件库 (页头/指标卡/徽章/空状态/表格/红点)
"""
from frontend._components.feishu_theme import (  # noqa: F401
    FEISHU_C,
    FEISHU_FONT,
    FEISHU_MONO,
    FEISHU_R,
    FEISHU_S,
    FeishuColors,
    FeishuRadius,
    FeishuSpace,
    apply_feishu_theme,
    feishu_fade_in,
)
from frontend._components.feishu_components import (  # noqa: F401
    data_table,
    empty_state,
    feishu_divider,
    metric_card,
    page_header,
    render_status_badge,
    render_top_badges,
    section_card_end,
    section_card_start,
    status_badge,
    status_dot,
)
