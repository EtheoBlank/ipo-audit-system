"""季度跟踪报告子包 (Quarterly Tracking Report).

子模块:
    trigger.py            # 季报触发入口 (手动/财务数据上传后)
    aggregator.py         # 拉取窗口期 [start,end] 简报 + event 集合
    financial_input.py    # 季报关键数据接收 (手工录入/PDF/Excel)
    generator.py          # 4 轮 LLM 协议 (与简报同结构)
    verifier.py           # 双数据源对账 (financial vs 简报期 audit_verification_json 数字)
    word_exporter.py      # Markdown → .docx
"""
from __future__ import annotations

__all__: list[str] = []
