"""P0-11 fix regression tests — 锁定后字段 XSS regex 校验.

Round 30 (2026-06-19). 验证三个修复:
  1. contact_person 含 <script> 拒绝
  2. contact_info 含 javascript: 拒绝
  3. 正常中文 + 标点通过
  4. data:text/html 拒绝
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.confirmation import ConfirmationItemUpdateRequest


def _req(**kw):
    """构造 ConfirmationItemUpdateRequest (触发所有 validator)"""
    return ConfirmationItemUpdateRequest(**kw)


class TestContactPersonXSS:
    def test_script_tag_rejected(self):
        with pytest.raises(ValidationError) as ei:
            _req(contact_person="<script>alert(1)</script>")
        assert "XSS" in str(ei.value)

    def test_javascript_uri_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_person="javascript:alert(1)")

    def test_onerror_attribute_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_person='<img src=x onerror="alert(1)">')

    def test_iframe_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_person="<iframe src=//evil>")

    def test_svg_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_person="<svg/onload=alert(1)>")


class TestContactInfoXSS:
    def test_javascript_uri_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_info="javascript:alert(1)")

    def test_data_url_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_info="data:text/html,<script>alert(1)</script>")

    def test_vbscript_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_info="vbscript:msgbox(1)")

    def test_expression_rejected(self):
        with pytest.raises(ValidationError):
            _req(contact_info="expression(alert(1))")


class TestSelectionReasonXSS:
    def test_script_rejected(self):
        with pytest.raises(ValidationError):
            _req(selection_reason="<script>document.cookie</script>")

    def test_object_embed_rejected(self):
        with pytest.raises(ValidationError):
            _req(selection_reason="<object data=evil></object>")


class TestNormalContentPasses:
    def test_chinese_name_passes(self):
        r = _req(contact_person="张三 (审计师)")
        assert r.contact_person == "张三 (审计师)"

    def test_chinese_phone_passes(self):
        r = _req(contact_info="电话 021-12345678 / zhang@example.com")
        assert r.contact_info is not None
        assert "@" in r.contact_info

    def test_long_chinese_reason_passes(self):
        text = "该客户为关联方, 期末余额重大, 按重要性水平抽样发函。"
        r = _req(selection_reason=text)
        assert r.selection_reason == text

    def test_none_passes(self):
        r = _req(contact_person=None, contact_info=None, selection_reason=None)
        assert r.contact_person is None
        assert r.contact_info is None
        assert r.selection_reason is None

    def test_empty_string_passes(self):
        r = _req(contact_person="")
        assert r.contact_person == ""