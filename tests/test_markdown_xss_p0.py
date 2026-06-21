"""P0-15 fix regression tests — markdown XSS 脱敏.

Round 30 (2026-06-19). 验证四个修复:
  1. ![alt](javascript:...) 脱敏
  2. [text](javascript:...) 脱敏
  3. <script>...</script> 整段脱敏
  4. onerror 内联事件移除
  5. 中文 + 正常 markdown 保持不变
"""
from __future__ import annotations

from app.services.audit_note_generator import _sanitize_markdown


class TestImageXSS:
    def test_image_javascript_link_sanitized(self):
        md = "正常 ![evil](javascript:alert(1)) 文字"
        out = _sanitize_markdown(md)
        assert "javascript:" not in out
        assert "已脱敏" in out

    def test_image_data_text_html_sanitized(self):
        md = "![alt](data:text/html,<script>alert(1)</script>)"
        out = _sanitize_markdown(md)
        assert "data:text/html" not in out
        assert "已脱敏" in out


class TestLinkXSS:
    def test_link_javascript_sanitized(self):
        md = "点这里 [click](javascript:alert(1)) 看更多"
        out = _sanitize_markdown(md)
        assert "javascript:" not in out
        assert "已脱敏" in out

    def test_link_data_url_sanitized(self):
        md = "[bad](data:text/html;base64,PHNjcmlwdD4=)"
        out = _sanitize_markdown(md)
        assert "data:text/html" not in out


class TestScriptTag:
    def test_script_tag_sanitized(self):
        md = "前文 <script>alert('xss')</script> 后文"
        out = _sanitize_markdown(md)
        assert "<script>" not in out
        assert "alert" not in out
        assert "已脱敏" in out

    def test_script_with_attrs_sanitized(self):
        md = '<script type="text/javascript">fetch("//evil")</script>'
        out = _sanitize_markdown(md)
        assert "<script" not in out
        assert "fetch" not in out


class TestInlineEventHandlers:
    def test_onerror_double_quote_removed(self):
        md = '<img src="x" onerror="alert(1)">'
        out = _sanitize_markdown(md)
        assert "onerror" not in out
        # 保留其余属性
        assert "<img" in out
        assert 'src="x"' in out

    def test_onload_single_quote_removed(self):
        md = "<body onload='steal()'>正文</body>"
        out = _sanitize_markdown(md)
        assert "onload" not in out

    def test_onclick_removed(self):
        md = '<a href="x" onclick="bad()">link</a>'
        out = _sanitize_markdown(md)
        assert "onclick" not in out


class TestNormalContent:
    def test_chinese_content_unchanged(self):
        md = """## 审计说明 — 1001 应收账款

### 一、科目情况
应收账款余额 12,345,678.90 元，审计目标：余额完整性与准确性。

### 二、参考案例
1. **出处**：审计工作底稿 / 函证程序 / 应收账款
   > 期末余额构成明细，包含 12 笔函证…

### 三、法规依据
- 《中国注册会计师审计准则第 1312 号——函证》

### 四、建议执行的审计程序
- 复核期末余额构成；
- 抽样检查原始凭证；
- 实施替代程序 / 函证；
- 关注与同行业相似科目的处理差异。
"""
        out = _sanitize_markdown(md)
        assert out == md  # 完全不变

    def test_none_and_empty_safe(self):
        assert _sanitize_markdown(None) is None
        assert _sanitize_markdown("") == ""

    def test_safe_url_unchanged(self):
        md = "见 [指引](https://example.com/doc) 与 ![图](https://x.com/a.png)"
        out = _sanitize_markdown(md)
        assert out == md  # http(s) 不动


class TestRealisticComposition:
    """模拟 _compose_note 实际产出路径"""

    def test_kb_chunk_with_xss_neutralized(self):
        """KB 检索结果若含 <script> 标签, _compose_note 后必须脱敏"""
        from app.services.audit_note_generator import (
            AuditNoteGenerator,
            AuditNoteContext,
        )
        from types import SimpleNamespace

        kb = [SimpleNamespace(
            book_title="用户上传",
            chapter="函证程序",
            section="异常处理",
            content="<script>alert('kb-xss')</script> 实务要点：…",
        )]
        gen = AuditNoteGenerator()
        ctx = AuditNoteContext(
            project_id=1,
            account_code="1001",
            account_name="应收账款",
            balance_amount=1234.0,
            risk_description=None,
            audit_objective=None,
        )
        note = gen._compose_note(ctx, kb, [], None)
        assert "<script>" not in note
        assert "alert" not in note

    def test_ai_text_with_link_sanitized(self):
        """AI 返回含 javascript: 链接时脱敏"""
        from app.services.audit_note_generator import (
            AuditNoteGenerator,
            AuditNoteContext,
        )

        ctx = AuditNoteContext(project_id=1, account_code="1001", account_name="应收账款")
        gen = AuditNoteGenerator()
        ai_text = "详见 [来源](javascript:alert(1))。建议补充附件。"
        note = gen._compose_note(ctx, [], [], ai_text)
        assert "javascript:" not in note
        assert "已脱敏" in note