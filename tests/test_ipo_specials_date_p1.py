"""P1 修复 (2026-06-19): ChecklistItemUpdate.upload_date 必须是 YYYY-MM-DD 格式."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.ipo_specials import ChecklistItemUpdate


class TestChecklistItemUpdateDate:
    def test_upload_date_validator_rejects_invalid(self):
        """中文日期"5月6日" 应当被拒绝."""
        with pytest.raises(ValidationError) as exc_info:
            ChecklistItemUpdate(is_uploaded=True, upload_date="5月6日")
        # 应当含 upload_date 字段错误
        errors = exc_info.value.errors()
        assert any("upload_date" in str(e["loc"]) for e in errors)

    def test_upload_date_validator_rejects_dot_format(self):
        """YYYY.MM.DD 也应当被拒绝 (前端会传 ISO 才能落库)."""
        with pytest.raises(ValidationError):
            ChecklistItemUpdate(is_uploaded=True, upload_date="2024.12.31")

    def test_upload_date_validator_accepts_iso(self):
        """合法 ISO 格式应当通过."""
        c = ChecklistItemUpdate(is_uploaded=True, upload_date="2025-06-13")
        assert c.upload_date == "2025-06-13"

    def test_upload_date_validator_accepts_none(self):
        """upload_date 可选为 None (默认)."""
        c = ChecklistItemUpdate(is_uploaded=False)
        assert c.upload_date is None

    def test_upload_date_validator_accepts_empty_string(self):
        """空字符串应当被允许 (前端可能传 '' 作为未填)."""
        c = ChecklistItemUpdate(is_uploaded=True, upload_date="")
        assert c.upload_date == ""
