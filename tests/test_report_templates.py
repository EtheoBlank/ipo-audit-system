"""Pack A — Report Template 模块单元测试.

覆盖:
  - schemas 校验 (report_type / format)
  - placeholder 抽取 (从 docx/xlsx zip 提取)
  - 渲染 docx/xlsx (替换 + 嵌套字段 + 容错)
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.models.report_template import (
    ReportTemplateBase,
    ReportTemplateCreate,
)
from app.services.report_template import (
    _flatten_context,
    _render_placeholder_in_text,
    analyze_template,
    render_docx,
    render_xlsx,
)


def _make_fake_docx(text_inside: str) -> bytes:
    """构造一个最小可识别的 .docx (zip) 包含 word/document.xml."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t>{text_inside}</w:t></w:r></w:p></w:body></w:document>',
        )
    return buf.getvalue()


def _make_fake_xlsx(text_inside: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        zf.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<si><t>{text_inside}</t></si></sst>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row><c t="s"><v>0</v></c></row></sheetData></worksheet>',
        )
    return buf.getvalue()


class TestSchemas:
    def test_unknown_report_type_rejected(self):
        with pytest.raises(Exception):
            ReportTemplateBase(
                template_code="x",
                template_name="x",
                report_type="bogus",
            )

    def test_unknown_format_rejected(self):
        with pytest.raises(Exception):
            ReportTemplateBase(
                template_code="x",
                template_name="x",
                report_type="audit_report",
                output_format="txt",
            )

    def test_template_code_pattern(self):
        # 含中文应被 pattern 拒绝
        with pytest.raises(Exception):
            ReportTemplateBase(
                template_code="模板代码",
                template_name="x",
                report_type="audit_report",
            )

    def test_normal_create(self):
        c = ReportTemplateCreate(
            template_code="audit_v1",
            template_name="标准审计报告",
            report_type="audit_report",
            output_format="docx",
        )
        assert c.version == "v1"


class TestPlaceholderExtraction:
    def test_extract_docx_placeholders(self):
        buf = _make_fake_docx("公司: ${company_name}, 年度: ${fiscal_year}")
        analysis = analyze_template(buf, "docx")
        assert "company_name" in analysis.placeholders
        assert "fiscal_year" in analysis.placeholders
        assert analysis.is_valid

    def test_extract_xlsx_placeholders(self):
        buf = _make_fake_xlsx("项目: ${project_name}")
        analysis = analyze_template(buf, "xlsx")
        assert "project_name" in analysis.placeholders

    def test_duplicates_detected(self):
        buf = _make_fake_docx("${a} 重复 ${a} 出现")
        analysis = analyze_template(buf, "docx")
        assert "a" in analysis.duplicates

    def test_no_placeholders_is_valid(self):
        buf = _make_fake_docx("纯文本无占位符")
        analysis = analyze_template(buf, "docx")
        assert analysis.is_valid
        assert analysis.placeholders == []


class TestRender:
    def test_render_docx_basic(self):
        buf = _make_fake_docx("Hello ${name}!")
        out = render_docx(buf, {"name": "Alice"})
        # 解开 zip 取 word/document.xml 看替换结果
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            content = zf.read("word/document.xml").decode("utf-8")
        assert "Alice" in content
        assert "${name}" not in content

    def test_render_docx_missing_key_lenient(self):
        buf = _make_fake_docx("Hello ${unknown}!")
        out = render_docx(buf, {})
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            content = zf.read("word/document.xml").decode("utf-8")
        assert "[未填:unknown]" in content

    def test_render_docx_strict_raises(self):
        buf = _make_fake_docx("Hello ${unknown}!")
        with pytest.raises(KeyError):
            render_docx(buf, {}, strict=True)

    def test_render_xlsx_basic(self):
        buf = _make_fake_xlsx("项目: ${pn}")
        out = render_xlsx(buf, {"pn": "示例项目"})
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            content = zf.read("xl/sharedStrings.xml").decode("utf-8")
        assert "示例项目" in content

    def test_render_bad_zip_raises(self):
        with pytest.raises(ValueError):
            render_docx(b"not_a_zip", {"x": 1})


class TestNestedContext:
    def test_flatten_nested(self):
        ctx = {"a": "1", "b": {"c": "2", "d": {"e": "3"}}}
        flat = _flatten_context(ctx)
        assert flat["a"] == "1"
        assert flat["b.c"] == "2"
        assert flat["b.d.e"] == "3"

    def test_render_nested_placeholder(self):
        text = "Section ${a.b.c} value"
        out = _render_placeholder_in_text(text, {"a.b.c": "OK"})
        assert "OK" in out
