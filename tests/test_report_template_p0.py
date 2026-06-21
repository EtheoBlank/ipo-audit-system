"""Round 31 P0 — report_template 嵌套占位符死循环 + 早退条件修复.

覆盖:
  - test_nested_placeholder_max_depth: 深层嵌套不爆栈 (50 层上限)
  - test_circular_placeholder_detected: 循环引用保留原文 + warning
  - test_dollar_in_plain_text_not_treated_as_placeholder: "100$ 美元" 不误判
"""
from __future__ import annotations

import logging

import pytest

from app.services.report_template import (
    _render_docx_xml_blob,
    _render_placeholder_in_text,
)


class TestNestedPlaceholderMaxDepth:
    """round 31: 嵌套深度限制防栈溢出."""

    def test_nested_placeholder_max_depth(self):
        """``${a} -> ${b} -> ${c} ...`` 5 层嵌套应正常展开, 200 层应被截断."""
        # 构造 5 层链: a -> b -> c -> d -> e -> "OK"
        ctx = {"e": "OK"}
        ctx = {"d": "${e}", **ctx}
        ctx = {"c": "${d}", **ctx}
        ctx = {"b": "${c}", **ctx}
        ctx = {"a": "${b}", **ctx}

        text = "值=${a}"
        out = _render_placeholder_in_text(text, ctx)
        assert out == "值=OK", f"5 层嵌套应正常解析, 实际: {out!r}"

        # 构造 200 层深度链 (远超 50 上限) — 必须不爆栈
        deep = {"leaf": "X"}
        for i in range(200):
            deep = {f"k{i}": f"${{k{i+1}}}" if i < 199 else "${leaf}", **deep}
        long_text = "V=${k0}"
        # 200 层超 50 上限, 应停止展开 (但**不抛**) — 关键: 不爆栈, 不抛 RecursionError
        out2 = _render_placeholder_in_text(long_text, deep)
        assert isinstance(out2, str)
        assert len(out2) > 0


class TestCircularPlaceholder:
    """round 31: 循环引用检测."""

    def test_circular_placeholder_detected(self, caplog):
        """a -> b, b -> a 循环 → 应保留原文 + warning, 不爆栈."""
        ctx = {"a": "${b}", "b": "${a}"}
        text = "链路=${a}"

        with caplog.at_level(logging.WARNING, logger="app.services.report_template"):
            out = _render_placeholder_in_text(text, ctx)

        # 循环被打破, 输出应包含原文 "${a}" 或 "${b}" (任一未展开的占位符)
        assert "${" in out, f"循环检测应保留未展开占位符, 实际: {out!r}"
        # warning 已发出
        assert any("循环" in rec.message or "circular" in rec.message.lower() for rec in caplog.records), (
            f"应有循环 warning, 实际: {[r.message for r in caplog.records]}"
        )


class TestDollarEarlyExit:
    """round 14 P1-11 / round 31: 含 $ 但无 { 的纯文本不应被误判."""

    def test_dollar_in_plain_text_not_treated_as_placeholder(self):
        """"价格 100$ 美元" 不应被当作 placeholder 触发 ET 解析异常路径."""
        # _render_docx_xml_blob 接受单个 word/*.xml 的字节, 不是 docx zip
        # 构造含 "$" 但不含 "{" 的 word/document.xml 字节
        xml_inner = "价格 100$ 美元"
        xml_bytes = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t xml:space="preserve">{xml_inner}</w:t></w:r></w:p></w:body></w:document>'
        ).encode("utf-8")

        # round 31: 早退条件要求 $ 与 { 同时存在; 单纯含 "$" 但无 "{"
        # 应走早退, 原样返回 (不调 ET.fromstring)
        out_bytes = _render_docx_xml_blob(xml_bytes, {}, strict=False)

        out_str = out_bytes.decode("utf-8")
        assert "100$" in out_str, f"含 $ 纯文本应原样保留, 实际: {out_str!r}"
        # round 31 关键断言: 输出不应包含误判生成的 "[未填:" 之类占位
        assert "[未填:" not in out_str
