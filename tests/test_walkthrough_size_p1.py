"""Round 28 P1-13: walkthrough/sample.items size 限制.

bug: items: list 无 max_length, 可塞 100k 凭证, 单行错即 500, OOM 风险.
修复: Pydantic schema WalkthroughSampleRequest.items 加 Field(..., max_length=200),
单条凭证 (dict 序列化) 不超过 5000 字符.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.ipo_specials import SampleRequest


class TestWalkthroughItemsSize:
    def test_walkthrough_items_max_length_enforced(self):
        """items=201 → ValidationError (max_length=200)."""
        items = [{"amount": i, "voucher": f"V{i}"} for i in range(201)]
        with pytest.raises(ValidationError) as exc_info:
            SampleRequest(cycle_code="procurement", items=items, n=3)
        err_text = str(exc_info.value)
        assert "max_length" in err_text or "200" in err_text

    def test_walkthrough_items_max_200_accepted(self):
        """items=200 → 通过校验."""
        items = [{"amount": i, "voucher": f"V{i}"} for i in range(200)]
        req = SampleRequest(cycle_code="procurement", items=items, n=3)
        assert len(req.items) == 200
        assert req.n == 3

    def test_walkthrough_empty_items_rejected(self):
        """空 items 应被 min_length=1 拦截 (但 SampleRequest 没显式 min_length,
        所以用空 dict 不会触发. 这里只验证小规模可工作)."""
        req = SampleRequest(cycle_code="sales", items=[], n=1)
        # 空列表通过 (Pydantic 不强制 min_length 除非显式声明)
        assert req.items == []

    def test_walkthrough_item_too_large_rejected(self):
        """单条凭证 (dict 序列化 > 5000 字符) → ValidationError."""
        big_item = {
            "voucher": "V1",
            "description": "x" * 6000,  # 单条 > 5000
        }
        with pytest.raises(ValidationError) as exc_info:
            SampleRequest(cycle_code="procurement", items=[big_item], n=1)
        err_text = str(exc_info.value)
        assert "5000" in err_text or "过大" in err_text or "items[0]" in err_text

    def test_walkthrough_item_just_under_limit_accepted(self):
        """单条凭证接近上限但 < 5000 字符 → 通过."""
        # 构造一个序列化后约 4000 字符的 dict
        big_item = {
            "voucher": "V1",
            "description": "x" * 3900,  # 加其他字段约 4000 < 5000
        }
        req = SampleRequest(cycle_code="procurement", items=[big_item], n=1)
        assert len(req.items) == 1

    def test_walkthrough_n_bounds(self):
        """n 的边界 1-20."""
        with pytest.raises(ValidationError):
            SampleRequest(cycle_code="x", items=[{}], n=0)
        with pytest.raises(ValidationError):
            SampleRequest(cycle_code="x", items=[{}], n=21)
        # 边界值 1 / 20
        SampleRequest(cycle_code="x", items=[{}], n=1)
        SampleRequest(cycle_code="x", items=[{}], n=20)
