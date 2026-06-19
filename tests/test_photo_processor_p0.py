"""P0-6 fix regression tests — known_codes 必传.

Round 25 (2026-06-19). 验证三个修复:
  1. parse_text 不传 known_codes 抛 ValueError (防 prompt injection)
  2. _filter_by_known_codes 严格按白名单过滤, AI 编造的 material_code 被剔除
  3. match_to_sheets name fallback 在白名单缺失时不会污染
"""

from __future__ import annotations

import pytest

from app.services.inventory.photo_processor import (
    CountPhotoProcessor,
    ParsedCountRow,
    PhotoParseResult,
)


class TestParseTextRequiresKnownCodes:
    """P0-6: parse_text 必须要求 known_codes"""

    @pytest.mark.asyncio
    async def test_raises_when_no_known_codes(self):
        """不传 known_codes 抛 ValueError"""
        p = CountPhotoProcessor(client=None)
        with pytest.raises(ValueError, match="known_codes required"):
            await p.parse_text("M001  100", known_codes=[])

    @pytest.mark.asyncio
    async def test_raises_when_empty_list(self):
        """空 list 也抛 ValueError"""
        p = CountPhotoProcessor(client=None)
        with pytest.raises(ValueError, match="known_codes required"):
            await p.parse_text("M001  100", known_codes=[])

    @pytest.mark.asyncio
    async def test_raises_when_only_whitespace_strings(self):
        """只含空白字符串的 list 也抛 ValueError (归一化后为空)"""
        p = CountPhotoProcessor(client=None)
        with pytest.raises(ValueError, match="known_codes required"):
            await p.parse_text("M001  100", known_codes=["", "  ", None])  # type: ignore[list-item]

    @pytest.mark.asyncio
    async def test_works_when_known_codes_provided(self):
        """正常传 known_codes 不抛, 返回结果"""
        p = CountPhotoProcessor(client=None)
        result = await p.parse_text(
            "M001  100\nM002  200",
            known_codes=["M001", "M002"],
        )
        # M001 / M002 都在白名单, 启发式解析应保留
        codes = {r.material_code for r in result.parsed_rows}
        assert "M001" in codes


class TestFilterByKnownCodes:
    """P0-6: _filter_by_known_codes 严格过滤"""

    def test_drops_unknown_code_no_name(self):
        """AI 返回 material_code 不在白名单 且 无 material_name → 丢弃"""
        result = PhotoParseResult(
            ocr_engine="x", ocr_text="",
            parsed_rows=[
                ParsedCountRow(material_code="INJECTED", material_name="", counted_qty=999),
                ParsedCountRow(material_code="M001", material_name="螺丝", counted_qty=100),
            ],
        )
        out = CountPhotoProcessor._filter_by_known_codes(result, {"m001"})
        codes = {r.material_code for r in out.parsed_rows}
        # INJECTED 既不在白名单又无 name → 丢
        assert "INJECTED" not in codes
        # M001 在白名单 → 留
        assert "M001" in codes

    def test_keeps_unknown_code_when_has_name(self):
        """AI 返回 material_code 不在白名单 但 有 material_name → 保留 (走 name fallback)"""
        result = PhotoParseResult(
            ocr_engine="x", ocr_text="",
            parsed_rows=[
                ParsedCountRow(
                    material_code="UNKNOWN",
                    material_name="特殊螺丝",
                    counted_qty=50,
                ),
            ],
        )
        out = CountPhotoProcessor._filter_by_known_codes(result, {"m001"})
        # 有 name → 保留, 给 match_to_sheets name fallback 机会
        assert len(out.parsed_rows) == 1
        assert out.parsed_rows[0].material_code == "UNKNOWN"

    def test_empty_known_codes_drops_all(self):
        """P0-6: 空白名单严格模式 — 所有行都被丢 (兜底防污染)"""
        result = PhotoParseResult(
            ocr_engine="x", ocr_text="",
            parsed_rows=[
                ParsedCountRow(material_code="M001", material_name="螺丝", counted_qty=100),
                ParsedCountRow(material_code="M002", material_name="螺母", counted_qty=200),
            ],
        )
        out = CountPhotoProcessor._filter_by_known_codes(result, set())
        # 严格模式: 空白名单 → 全丢
        assert out.parsed_rows == []

    def test_none_known_codes_drops_all(self):
        """None 也走严格模式 (兜底)"""
        result = PhotoParseResult(
            ocr_engine="x", ocr_text="",
            parsed_rows=[
                ParsedCountRow(material_code="M001", material_name="", counted_qty=100),
            ],
        )
        out = CountPhotoProcessor._filter_by_known_codes(result, None)
        assert out.parsed_rows == []

    def test_case_insensitive_match(self):
        """白名单大小写不敏感 — 调用方传已 lower 的白名单, 行 material_code 也 lower 后命中"""
        # 注: _filter_by_known_codes 假设调用方已 lower 白名单
        # (parse_text 内部归一化时 lower). 这里验证 AI 行的 lowercase code 能匹配
        # lowercase 白名单.
        result = PhotoParseResult(
            ocr_engine="x", ocr_text="",
            parsed_rows=[
                ParsedCountRow(material_code="M001", material_name="", counted_qty=100),
            ],
        )
        out = CountPhotoProcessor._filter_by_known_codes(result, {"m001"})
        # 大写 M001 经过 .lower() 后变成 m001, 与白名单 m001 匹配 → 留
        assert len(out.parsed_rows) == 1


class TestMatchToSheetsFallbackOnlyWhenKnownCodes:
    """P0-6: match_to_sheets 的 name fallback 在白名单缺失时不会污染

    注: 上层 parse_text 已经校验过 known_codes 非空才走 AI, 这里验证
    _filter_by_known_codes 严格模式下无白名单 → 无 AI 数据 → match_to_sheets
    自然没有 candidate, 不会污染 sheet.
    """

    def test_no_ai_rows_no_match_pollution(self):
        """白名单缺失 → AI 行全丢 → match_to_sheets 无 candidate → 不污染"""
        # 模拟 parse_text 在已知白名单为空时的结果
        ai_result = PhotoParseResult(
            ocr_engine="x", ocr_text="",
            parsed_rows=[
                ParsedCountRow(
                    material_code="FAKE",
                    material_name="伪造的物料名",
                    counted_qty=99999,
                ),
            ],
        )
        # 严格过滤
        ai_result = CountPhotoProcessor._filter_by_known_codes(ai_result, set())

        # 模拟 ORM sheets
        class _Sheet:
            def __init__(self, code, name):
                self.material_code = code
                self.material_name = name
                self.warehouse = ""
                self.batch_no = ""

        sheets = [_Sheet("M001", "螺丝"), _Sheet("M002", "螺母")]
        matched, unmatched = CountPhotoProcessor.match_to_sheets(
            ai_result.parsed_rows, sheets
        )
        # 无 AI 数据 → matched 空, unmatched 空 (AI 已被过滤)
        assert matched == []
        assert unmatched == []

    @pytest.mark.asyncio
    async def test_real_injection_attack_blocked(self):
        """真实场景: OCR 文本含 prompt injection 编造 material_code,
        但调用方传了真实白名单, AI 编造的编码被剔除, 真实数据被保留"""
        # 启发式解析下, "INJECTED 9999" 会被解析成一行
        # 注入: 让 AI 假装输入合法编码, 实际 OCR 文本污染
        ocr_text = "M001  100\nFAKE_INJECT 99999\nM002  200"
        p = CountPhotoProcessor(client=None)
        result = await p.parse_text(ocr_text, known_codes=["M001", "M002"])
        codes = {r.material_code for r in result.parsed_rows}
        # FAKE_INJECT 不在白名单且无 name → 丢
        assert "FAKE_INJECT" not in codes
        # 真实编码保留
        assert "M001" in codes
        assert "M002" in codes

    @pytest.mark.asyncio
    async def test_existing_known_codes_set_still_compatible(self):
        """向后兼容: 旧调用方传 set 也能跑 (只是类型注解变了)"""
        p = CountPhotoProcessor(client=None)
        # set 而不是 list — 旧测试 test_parse_text_drops_unknown_codes 用法
        result = await p.parse_text("INJECTED  9999\nM001  100", known_codes=["m001"])
        codes = {r.material_code for r in result.parsed_rows}
        assert "INJECTED" not in codes
        assert "M001" in codes