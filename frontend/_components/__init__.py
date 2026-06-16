"""Frontend 共享 UI 组件 / 工具.

抽出来降低 9 个 pages_*.py 的重复代码, 行为保持一致:

- project_picker: 选项目 (4+ 页面都写)
- data_grid:     st.dataframe (10+ 处, 统一 hide_index + use_container_width)
- charts:        简单的 bar/line 辅助
- period_picker: 期末日期 (text_input / date_input) + 校验
- download_excel: 一致的下载按钮
"""
