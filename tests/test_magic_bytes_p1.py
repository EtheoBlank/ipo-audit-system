"""Round 31 P1-5: 上传文件 magic bytes 校验防扩展名绕过.

覆盖:
  - test_xlsx_magic_bytes_match: ZIP header 通过
  - test_exe_with_xlsx_extension_rejected: MZ header + .xlsx ext → 拒绝
  - test_csv_no_magic_check: .csv 跳过 magic 检查
  - test_text_with_pdf_ext_rejected: 普通文本 + .pdf ext → 拒绝
"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from app.services.inventory.importer import (
    InventoryImporter,
    InventoryImportError,
)
from app.utils.upload_safety import check_magic_bytes


class TestMagicBytesHelper:
    """``check_magic_bytes`` 单元测试."""

    def test_xlsx_magic_bytes_match(self):
        """ZIP header → 通过 (.xlsx)."""
        # xlsx 本质是 zip, 头部是 PK\x03\x04
        content = b"PK\x03\x04rest of the zip content..."
        assert check_magic_bytes(content, ".xlsx") is True
        # 不带点也接受
        assert check_magic_bytes(content, "xlsx") is True
        # 大小写无关
        assert check_magic_bytes(content, ".XLSX") is True

    def test_exe_with_xlsx_extension_rejected(self):
        """MZ header (.exe) + .xlsx ext → 拒绝."""
        # Windows .exe 头部: MZ
        content = b"MZ\x90\x00\x03\x00\x00\x00binary exe payload..."
        assert check_magic_bytes(content, ".xlsx") is False

    def test_csv_no_magic_check(self):
        """.csv 无 magic 检查 (纯文本)."""
        content = b"some,plain,csv,content"
        assert check_magic_bytes(content, ".csv") is True
        assert check_magic_bytes(content, ".txt") is True

    def test_text_with_pdf_ext_rejected(self):
        """普通文本 + .pdf ext → 拒绝."""
        content = b"hello world, this is not a pdf"
        assert check_magic_bytes(content, ".pdf") is False

    def test_short_content_rejected(self):
        """content 短于 magic 长度 → 拒绝."""
        # PDF magic 是 b"%PDF" (4 字节), 只给 2 字节
        assert check_magic_bytes(b"%P", ".pdf") is False

    def test_png_jpg_match(self):
        """PNG / JPEG 头部匹配."""
        png_sig = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        assert check_magic_bytes(png_sig, ".png") is True

        jpg_sig = b"\xff\xd8\xff\xe0" + b"\x00" * 10
        assert check_magic_bytes(jpg_sig, ".jpg") is True
        assert check_magic_bytes(jpg_sig, ".jpeg") is True


class TestImporterMagicBytesIntegration:
    """``InventoryImporter.parse_bytes`` 应在校验失败时抛 InventoryImportError."""

    def test_evil_exe_as_xlsx_rejected(self):
        """.xlsx 文件名但 MZ header → 拒绝."""
        # MZ header (executable, 不是 ZIP)
        content = b"MZ\x90\x00\x03\x00\x00\x00fake xlsx content here"
        with pytest.raises(InventoryImportError) as exc_info:
            InventoryImporter.parse_bytes(content, "inventory.xlsx")
        assert "不匹配" in str(exc_info.value) or "伪造" in str(exc_info.value)

    def test_legit_xlsx_still_accepted(self):
        """合法 xlsx (ZIP header + 真实 xlsx 数据) 仍接受."""
        df = pd.DataFrame({
            "物料编码": ["M001"],
            "物料名称": ["A"],
            "期末数量": [10],
            "期末金额": [100.0],
        })
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        content = buf.getvalue()
        # 真实 xlsx 头部是 ZIP (PK\x03\x04), 应通过 magic 检查
        result = InventoryImporter.parse_bytes(content, "test.xlsx")
        assert len(result) == 1

    def test_csv_no_magic_check_in_integration(self):
        """.csv 走纯文本路径, 不查 magic, 仍接受."""
        csv_content = (
            "物料编码,物料名称,期末数量,期末金额\n"
            "M001,A,10,100\n"
        ).encode("utf-8")
        result = InventoryImporter.parse_bytes(csv_content, "test.csv")
        assert len(result) == 1
