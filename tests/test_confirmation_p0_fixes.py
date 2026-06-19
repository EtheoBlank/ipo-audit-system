"""Confirmation P0 修复测试 (2026-06-17).

覆盖:
  - #5 response_status 白名单校验 (sanitize_response_status)
  - #4 _aggregate_by_aux 按 balance_by_code 比例分摊 ending_balance,
          含无活动科目的"(未指定对方)" 桶兜底
"""
from __future__ import annotations

from types import SimpleNamespace


from app.services.confirmation.response_processor import ConfirmationResponseProcessor
from app.services.confirmation.stats_builder import (
    ConfirmationStatsBuilder,
    PAYABLE_ACCOUNTS,
    RECEIVABLE_ACCOUNTS,
)


# ============================================================
# 工具: 用 SimpleNamespace 模拟 ORM 行 (避免拉起 DB)
# ============================================================
def _ab(code: str, ending: float = 0.0, beginning: float = 0.0, direction: str = "借"):
    return SimpleNamespace(
        account_code=code, account_name=f"科目{code}",
        ending_balance=ending, beginning_balance=beginning,
        debit_amount=0.0, credit_amount=0.0, balance_direction=direction,
        auxiliary_accounting=None,
    )


def _j(code: str, debit: float = 0.0, credit: float = 0.0, aux: str = "", name: str = ""):
    return SimpleNamespace(
        account_code=code, account_name=name or f"科目{code}",
        debit_amount=debit, credit_amount=credit,
        summary="", voucher_date="2024-12-01", voucher_no="X",
        auxiliary_accounting=aux or None,
    )


# ============================================================
# #5 response_status enum 白名单
# ============================================================
class TestResponseStatusEnum:
    """P0 修复: AI 返回非法 response_status → fallback 'unclear'."""

    def test_valid_match(self):
        assert ConfirmationResponseProcessor._sanitize_response_status("match") == "match"

    def test_valid_partial(self):
        assert ConfirmationResponseProcessor._sanitize_response_status("partial") == "partial"

    def test_valid_mismatch(self):
        assert ConfirmationResponseProcessor._sanitize_response_status("mismatch") == "mismatch"

    def test_valid_reject(self):
        assert ConfirmationResponseProcessor._sanitize_response_status("reject") == "reject"

    def test_valid_unclear(self):
        assert ConfirmationResponseProcessor._sanitize_response_status("unclear") == "unclear"

    def test_invalid_falls_back_to_unclear(self):
        # 旧版直接入库, 现在记 warning + fallback
        assert ConfirmationResponseProcessor._sanitize_response_status("garbage") == "unclear"
        assert ConfirmationResponseProcessor._sanitize_response_status("confirmed") == "unclear"  # 不在白名单

    def test_empty_falls_back_to_unclear(self):
        assert ConfirmationResponseProcessor._sanitize_response_status("") == "unclear"
        assert ConfirmationResponseProcessor._sanitize_response_status(None) == "unclear"

    def test_uppercase_normalized(self):
        # AI 有时返回大写, 应当 lower 归一
        assert ConfirmationResponseProcessor._sanitize_response_status("MATCH") == "match"
        assert ConfirmationResponseProcessor._sanitize_response_status(" Partial ") == "partial"

    def test_whitespace_stripped(self):
        assert ConfirmationResponseProcessor._sanitize_response_status("  match  ") == "match"


# ============================================================
# #4 _aggregate_by_aux 按 balance_by_code 分摊
# ============================================================
class TestAggregateByAuxEndingBalance:
    """P0 修复: ending_balance 不再用本期发生额近似, 而按 balance_by_code 比例分摊."""

    def _builder(self):
        # _aggregate_by_aux 不依赖 self.db, 只用 self._normalize_party_name (静态方法)
        return ConfirmationStatsBuilder(db=None)

    def test_proportional_split_two_parties(self):
        """场景: 1122 应收账款余额 10w, 两个客户 A (借 6w) B (借 4w),
        按本期发生比例分: A 应得 6w, B 应得 4w."""
        balances = [_ab("1122", ending=100_000)]
        journals = [
            _j("1122", debit=60_000, aux="客户A"),
            _j("1122", debit=40_000, aux="客户B"),
        ]
        result = self._builder()._aggregate_by_aux(balances, journals, RECEIVABLE_ACCOUNTS)
        assert len(result) == 2
        # A: 100k * 60/100 = 60k, B: 100k * 40/100 = 40k
        a_key = self._builder()._normalize_party_name("客户A")
        b_key = self._builder()._normalize_party_name("客户B")
        assert result[a_key]["ending_balance"] == 60_000.0
        assert result[b_key]["ending_balance"] == 40_000.0

    def test_proportional_split_three_parties(self):
        """场景: 2202 应付账款余额 9w, 三家供应商比例 1:2:1."""
        balances = [_ab("2202", ending=90_000)]
        journals = [
            _j("2202", debit=10_000, aux="供应商甲"),
            _j("2202", debit=20_000, aux="供应商乙"),
            _j("2202", debit=10_000, aux="供应商丙"),
        ]
        result = self._builder()._aggregate_by_aux(balances, journals, PAYABLE_ACCOUNTS)
        assert result["供应商甲"]["ending_balance"] == 22_500.0
        assert result["供应商乙"]["ending_balance"] == 45_000.0
        assert result["供应商丙"]["ending_balance"] == 22_500.0

    def test_no_current_activity_unassigned_bucket(self):
        """场景: 1122 余额 5w 但本期无任何发生 (长年挂账) → 全归 (未指定对方)."""
        balances = [_ab("1122", ending=50_000)]
        journals = []  # 本期无任何凭证
        result = self._builder()._aggregate_by_aux(balances, journals, RECEIVABLE_ACCOUNTS)
        assert "(未指定对方)" in result
        assert result["(未指定对方)"]["ending_balance"] == 50_000.0
        assert result["(未指定对方)"]["account_codes"] == {"1122"}

    def test_mixed_active_and_dormant(self):
        """场景: 1122 余额 12w, 客户A 有活动 (借 4w), 客户B 无活动.
        A 分 12w * 4w/4w = 12w (因为 B 无活动), B 应得 0."""
        # 注: B 无活动则 total_activity_for_code = A 的 4w, A 占 100% → A 得全部 12w
        balances = [_ab("1122", ending=120_000)]
        journals = [
            _j("1122", debit=40_000, aux="客户A"),
            # 客户B 没凭证 (无活动)
        ]
        result = self._builder()._aggregate_by_aux(balances, journals, RECEIVABLE_ACCOUNTS)
        a_key = self._builder()._normalize_party_name("客户A")
        assert result[a_key]["ending_balance"] == 120_000.0

    def test_empty_aux_journal_goes_to_unassigned(self):
        """场景: 凭证有 1122 行但没填 auxiliary_accounting → 落到 (未指定对方)."""
        balances = [_ab("1122", ending=30_000)]
        journals = [
            _j("1122", debit=10_000, aux=""),  # 空 aux
        ]
        result = self._builder()._aggregate_by_aux(balances, journals, RECEIVABLE_ACCOUNTS)
        # 整段余额归 (未指定对方), 因为没有 aux 走到 by_party 之外
        assert "(未指定对方)" in result
        assert result["(未指定对方)"]["ending_balance"] == 30_000.0

    def test_only_journals_with_matching_codes_counted(self):
        """场景: journal 里有 5001 行 (不属 RECEIVABLE_ACCOUNTS) → 不应影响余额."""
        balances = [_ab("1122", ending=100_000)]
        journals = [
            _j("1122", debit=50_000, aux="客户A"),
            _j("5001", debit=50_000, aux="客户A"),  # 不相关科目, 应被过滤
        ]
        result = self._builder()._aggregate_by_aux(balances, journals, RECEIVABLE_ACCOUNTS)
        a_key = self._builder()._normalize_party_name("客户A")
        # A 在 1122 上的活动 = 5w, 占 100% → A 得 10w
        assert result[a_key]["ending_balance"] == 100_000.0

    def test_whitespace_in_aux_normalized(self):
        """场景: 客户名带空格 → 归一后合并到同一 key."""
        balances = [_ab("1122", ending=100_000)]
        journals = [
            _j("1122", debit=30_000, aux=" 客户 X "),
            _j("1122", debit=20_000, aux="客户X"),
        ]
        result = self._builder()._aggregate_by_aux(balances, journals, RECEIVABLE_ACCOUNTS)
        # 归一后是同一 key
        keys = [k for k in result if k != "(未指定对方)"]
        assert len(keys) == 1
        assert result[keys[0]]["ending_balance"] == 100_000.0  # 50k/50k 比例 → 全部