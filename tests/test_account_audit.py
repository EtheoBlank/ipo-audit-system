"""Pack A — Account Audit (长期资产发生额审定) 单元测试.

覆盖:
  - 长期资产判定 (默认前缀 + 全局额外 + 项目级覆盖)
  - workbook_generator.is_long_term_asset_account 公共 helper
  - schemas 校验 (audited_amount 必须有限数)
  - 恒等式校验逻辑 (借方科目 vs 贷方科目)
"""
from __future__ import annotations

import math

import pytest

from app.models.account_audit import (
    MovementAuditBulkItem,
    MovementAuditUpdate,
)
from app.models.db.account_audit import (
    DEFAULT_LONG_TERM_ASSET_PREFIXES,
    MOVEMENT_DIRECTION_CREDIT,
    MOVEMENT_DIRECTION_DEBIT,
)
from app.services.workbook_generator import is_long_term_asset_account


class TestLongTermAssetPrefixes:
    def test_default_includes_typical_accounts(self):
        # 必含科目: 固定资产 / 累计折旧 / 在建工程 / 无形资产 / 商誉 / 使用权资产
        for code in ("1601", "1602", "1604", "1701", "1711", "1821"):
            assert code in DEFAULT_LONG_TERM_ASSET_PREFIXES

    def test_helper_matches_prefix(self):
        # 1601 (固定资产) → True
        assert is_long_term_asset_account("1601")
        assert is_long_term_asset_account("160101")  # 子科目
        # 1001 (库存现金) → False
        assert not is_long_term_asset_account("1001")
        assert not is_long_term_asset_account("")
        assert not is_long_term_asset_account(None)  # type: ignore[arg-type]

    def test_helper_respects_extra_excludes(self):
        # 排除 1901, 不应再命中
        assert not is_long_term_asset_account("1901", extra_excludes=["1901"])
        # 不排除时命中 (默认在前缀清单里)
        assert is_long_term_asset_account("1901")

    def test_helper_respects_extra_includes(self):
        # 默认不含 6601 (制造费用), 加 include 后命中
        assert not is_long_term_asset_account("6601")
        assert is_long_term_asset_account("6601", extra_includes=["6601"])


class TestMovementAuditSchemas:
    def test_audited_amount_must_be_finite(self):
        with pytest.raises(Exception):
            MovementAuditUpdate(audited_amount=float("nan"))
        with pytest.raises(Exception):
            MovementAuditUpdate(audited_amount=float("inf"))

    def test_audited_amount_normal(self):
        m = MovementAuditUpdate(audited_amount=12345.67, adjustment_reason="核对发票")
        assert m.audited_amount == 12345.67
        assert m.adjustment_reason == "核对发票"

    def test_status_must_be_known(self):
        with pytest.raises(Exception):
            MovementAuditUpdate(audited_amount=1.0, status="bogus")

    def test_bulk_item_direction_only_debit_credit(self):
        m = MovementAuditBulkItem(
            account_code="1601",
            voucher_no="JZ-1",
            direction="debit",
            audited_amount=1.0,
        )
        assert m.direction == "debit"
        with pytest.raises(Exception):
            MovementAuditBulkItem(
                account_code="1601",
                voucher_no="JZ-1",
                direction="invalid",
                audited_amount=1.0,
            )


class TestIdentityCheckLogic:
    """模拟服务层的恒等式计算 (借方科目 vs 贷方科目)."""

    @staticmethod
    def _identity_debit_acct(beg, debit, credit, end):
        return beg + debit - credit - end

    @staticmethod
    def _identity_credit_acct(beg, debit, credit, end):
        return beg - debit + credit - end

    def test_balanced_debit_account(self):
        # 固定资产: 期初 100 + 借 50 (购入) - 贷 20 (折旧) = 期末 130
        assert abs(self._identity_debit_acct(100, 50, 20, 130)) < 0.01

    def test_unbalanced_debit_account(self):
        # 期末算错 100 (少算 30)
        assert abs(self._identity_debit_acct(100, 50, 20, 100)) > 0.01

    def test_balanced_credit_account(self):
        # 累计折旧 (贷方科目): 期初 50 - 借 0 + 贷 10 (本期计提) = 期末 60
        assert abs(self._identity_credit_acct(50, 0, 10, 60)) < 0.01


class TestMovementBulkItemEdgeCases:
    def test_voucher_line_no_defaults_to_1(self):
        m = MovementAuditBulkItem(
            account_code="1601",
            voucher_no="JZ-1",
            direction="debit",
            audited_amount=1.0,
        )
        assert m.voucher_line_no == 1

    def test_voucher_line_no_explicit(self):
        m = MovementAuditBulkItem(
            account_code="1601",
            voucher_no="JZ-1",
            voucher_line_no=3,
            direction="credit",
            audited_amount=100.0,
        )
        assert m.voucher_line_no == 3
        assert m.direction == "credit"


class TestDirectionConstants:
    def test_constants(self):
        assert MOVEMENT_DIRECTION_DEBIT == "debit"
        assert MOVEMENT_DIRECTION_CREDIT == "credit"
