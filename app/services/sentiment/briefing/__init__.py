"""每日简报子包 (Briefing).

子模块:
    detector.py     # "今天有没有新消息" 判定 (落库唯一约束兜底)
    generator.py    # 4 轮 LLM 协议 (提取 / 自检 / 挑刺 / 拼装)
    verifier.py     # 独立校验: 数字/事件引用回查原始 event
    word_exporter.py# Markdown → .docx (照搬 report_generator.py 风格)
"""

from __future__ import annotations

__all__: list[str] = []
