"""Round 28 P0-7: PeerCompetitionService 关键词白名单 + 拒绝通用词 + 防 SQL 注入.

白名单是项目特定 — 行业名 (金融/制造/医疗/教育) + 业务类型 (贷款/存款/投资/研发/生产)
+ 关系类型 (母/子/兄弟/合资/联营). 拒绝通用词 (公司/集团/企业) 单独出现.
"""
from __future__ import annotations

import pytest

from app.services.related_parties import (
    PeerCompetitionService,
    filter_keywords,
    _is_valid_keyword,
    _KEYWORD_WHITELIST,
)


class TestKeywordWhitelist:
    def test_industry_keyword_match(self):
        """关键词 "金融" 命中白名单, 重合度计算 OK."""
        score = PeerCompetitionService.overlap_score(
            ["金融", "银行"],
            "本公司主营金融服务, 包括对公贷款, 涉及银行间市场",
        )
        # 2/2 = 100
        assert score == 100.0

    def test_generic_keyword_rejected(self):
        """关键词 "公司" 是通用词, 单独出现被拒绝 — overlap_score 返 0."""
        score = PeerCompetitionService.overlap_score(
            ["公司", "集团"],
            "本公司主营金融服务",
        )
        # 通用词全被拒绝 → valid_kws 空 → score 0
        assert score == 0.0
        # 校验 filter_keywords 把 "公司" "集团" 拒了
        valid, rejected = filter_keywords(["公司", "集团", "金融"])
        assert "金融" in valid
        assert "公司" in rejected
        assert "集团" in rejected

    def test_sql_injection_blocked(self):
        """SQL 注入字符串 `'; DROP TABLE--` 不在白名单, 被拒绝."""
        # 关键词清洗 — 注入 token 走 filter_keywords 时被拒
        valid, rejected = filter_keywords(["'; DROP TABLE--", "金融", "1"])
        assert "金融" in valid
        assert "'; DROP TABLE--" in rejected
        assert "1" in rejected  # 纯数字
        # overlap_score 不抛错
        score = PeerCompetitionService.overlap_score(
            ["'; DROP TABLE--", "金融"],
            "金融服务业",
        )
        # 只算 "金融" (1/1) = 100
        assert score == 100.0


class TestKeywordHelper:
    def test_single_char_rejected(self):
        """单字符被拒绝."""
        assert not _is_valid_keyword("金")
        assert not _is_valid_keyword("A")

    def test_pure_number_rejected(self):
        """纯数字被拒绝."""
        assert not _is_valid_keyword("123")
        assert not _is_valid_keyword("0")

    def test_whitelist_size_reasonable(self):
        """白名单 30-100 个种子词 (含英文缩写)."""
        assert 30 <= len(_KEYWORD_WHITELIST) <= 100

    def test_industry_in_whitelist(self):
        """核心行业词在白名单."""
        for k in ["金融", "制造", "医疗", "教育", "研发", "生产", "投资"]:
            assert k in _KEYWORD_WHITELIST, f"{k} 应在白名单"

    def test_relation_in_whitelist(self):
        """关系类型在白名单."""
        for k in ["母", "子", "兄弟", "合资", "联营"]:
            assert k in _KEYWORD_WHITELIST, f"{k} 应在白名单"

    def test_generic_words_in_rejection_set(self):
        """通用词在 filter 时被拒 (黑名单)."""
        for k in ["公司", "集团", "企业", "业务", "服务"]:
            assert not _is_valid_keyword(k)
