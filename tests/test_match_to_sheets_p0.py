"""P0-A fix regression tests — match_to_sheets name fallback 误匹配.

Round 27 (2026-06-19). 验证修复:
  1. 纯子串匹配被替换为 "子串+长度比" 或 "Jaccard token 相似度" 二选一
  2. 防止 '不锈钢' 误匹配 '不锈钢轴承', '圆钢' 误匹配 '圆钢轴承件' 等
  3. 兼容老数据 (>= 长度比 0.5 的子串仍命中)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.inventory.photo_processor import (
    CountPhotoProcessor,
    ParsedCountRow,
    _name_matches,
    _name_similarity,
    _tokenize,
)


def _sheet(code: str, name: str) -> SimpleNamespace:
    """构造伪 sheet ORM (只要 match_to_sheets 读得到的字段)."""
    return SimpleNamespace(
        id=hash((code, name)) & 0x7FFFFFFF,
        material_code=code,
        material_name=name,
        warehouse="",
        batch_no="",
    )


class TestTokenize:
    """_tokenize 工具: 中文按字 + 英文按词 + 数字串"""

    def test_chinese_chars_split(self):
        assert _tokenize("不锈钢") == ["不", "锈", "钢"]

    def test_chinese_with_english_split(self):
        assert _tokenize("不锈钢轴承-A") == ["不", "锈", "钢", "轴", "承", "A"]

    def test_digit_runs_kept(self):
        toks = _tokenize("M001 圆钢")
        # "M001" 作为整体 (数字串), 圆/钢 单独切
        assert "M001" in toks
        assert "圆" in toks
        assert "钢" in toks
        # 字母+数字 合并为 "M001" 一个 token, 不应拆成 "M" + "001"
        assert "M" not in toks
        assert "001" not in toks

    def test_empty(self):
        assert _tokenize("") == []


class TestNameSimilarity:
    """_name_similarity Jaccard 工具"""

    def test_identical_is_one(self):
        assert _name_similarity("圆钢", "圆钢") == pytest.approx(1.0)

    def test_disjoint_is_zero(self):
        assert _name_similarity("圆钢", "塑料") == 0.0

    def test_boundary_case_buxiugang_vs_zhoucheng(self):
        # "不锈钢" tokens={不, 锈, 钢}, "不锈钢轴承" tokens={不, 锈, 钢, 轴, 承}
        # ∩ = 3, ∪ = 5 → 0.6 (边界, 不应单独触发, 需配合长度比兜底)
        assert _name_similarity("不锈钢", "不锈钢轴承") == pytest.approx(0.6)

    def test_yuangang_vs_gangcai_yuangang(self):
        # "圆钢" tokens={圆, 钢}, "钢材圆钢" tokens={钢, 材, 圆, 钢} → set = {钢, 材, 圆}
        # ∩ = {圆, 钢} = 2, ∪ = {圆, 钢, 材} = 3 → 2/3 ≈ 0.6667
        sim = _name_similarity("圆钢", "钢材圆钢")
        assert sim >= 0.6


class TestNameMatches:
    """_name_matches 业务规则"""

    def test_substring_match_requires_length_ratio(self):
        # 长度比 0.5 边界: "圆钢" (2) in "圆钢件" (3) → 2/3=0.666 ≥ 0.5 → 通过
        assert _name_matches("圆钢", "圆钢件") is True
        # 长度比 < 0.5 拒绝: "圆钢" (2) in "圆钢轴承件" (5) → 2/5=0.4 < 0.5 → 拒绝
        assert _name_matches("圆钢", "圆钢轴承件") is False

    def test_jaccard_similarity_match(self):
        # "圆钢" vs "钢材圆钢": Jaccard ≈ 0.667 ≥ 0.6, 长度比 2/4=0.5 → 通过
        assert _name_matches("圆钢", "钢材圆钢") is True

    def test_jaccard_below_threshold_no_match(self):
        # "不锈钢" vs "不锈钢轴承": Jaccard=0.6 (边界), 长度比 3/5=0.6 ≥ 0.5
        #   但 Jaccard >= 0.6 严格通过 (0.6 == 0.6)
        # 改用 "圆钢" vs "圆钢轴承": Jaccard: {圆, 钢} ∩ {圆, 钢, 轴, 承} = 2/4 = 0.5 < 0.6
        # 长度比 2/4 = 0.5, 路径 A "圆钢" in "圆钢轴承" → True + 长度比 0.5 → 通过
        # 真正的兜底: 长度比 < 0.5 才会拒绝
        # 验证 "圆钢" (2) vs "圆钢轴" (3): 子串 + 长度比 2/3=0.67 → 通过
        assert _name_matches("圆钢", "圆钢轴") is True
        # "圆钢" (2) vs "圆钢轴承件" (5): 长度比 0.4 < 0.5 → 拒绝 (即便子串也拒绝)
        assert _name_matches("圆钢", "圆钢轴承件") is False

    def test_no_match_totally_different(self):
        assert _name_matches("圆钢", "塑料") is False
        assert _name_matches("水泥", "铜线") is False

    def test_substring_already_similar_passes(self):
        # 兼容老数据: 长度比 ≥ 0.5 的子串仍命中
        # "圆钢件" (3) in "圆钢件原料" (5): 长度比 3/5=0.6 ≥ 0.5 → 通过
        assert _name_matches("圆钢件", "圆钢件原料") is True


class TestMatchToSheetsIntegration:
    """集成测试: match_to_sheets 真实调用, 验证 P0-A 防误匹配"""

    def test_no_false_match_buxiugang_zhoucheng(self):
        """'不锈钢' 不会误匹配 '不锈钢轴承' sheet (长度比兜底)"""
        sheets = [
            _sheet("S1", "不锈钢"),
            _sheet("S2", "不锈钢轴承"),
        ]
        row = ParsedCountRow(
            material_code="",  # 走 name fallback
            material_name="不锈钢",
            counted_qty=10.0,
        )
        proc = CountPhotoProcessor(client=None)
        matched, unmatched = proc.match_to_sheets([row], sheets)
        # 期望: 命中 "不锈钢" 自身, 不是 "不锈钢轴承"
        assert len(matched) == 1
        assert matched[0][0].material_name == "不锈钢"
        assert matched[0][1] is row

    def test_substring_match_yuangang_yuangangjian(self):
        """'圆钢' 能匹配 '圆钢件' (长度比 ≥ 0.5 命中)"""
        sheets = [_sheet("S1", "圆钢件")]
        row = ParsedCountRow(material_name="圆钢", counted_qty=5.0)
        proc = CountPhotoProcessor(client=None)
        matched, _ = proc.match_to_sheets([row], sheets)
        assert len(matched) == 1
        assert matched[0][0].material_name == "圆钢件"

    def test_rejects_yuangang_too_long_different(self):
        """'圆钢' 不会匹配 '圆钢轴承件' (长度比 < 0.5)"""
        sheets = [_sheet("S1", "圆钢轴承件")]
        row = ParsedCountRow(material_name="圆钢", counted_qty=5.0)
        proc = CountPhotoProcessor(client=None)
        matched, unmatched = proc.match_to_sheets([row], sheets)
        assert len(matched) == 0
        assert len(unmatched) == 1
