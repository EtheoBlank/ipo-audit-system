"""Sales Ledger P0 修复测试 (2026-06-17).

覆盖 #6: _summary + _pivot profit 必须扣 return_amount / discount_amount / rebate_amount,
        缺列时不报错.
"""
from __future__ import annotations

from types import SimpleNamespace


from app.services.sales_ledger.analyzer import RevenueAnalyzer


def _rec(
    customer: str = "客户A",
    product: str = "P1",
    revenue: float = 0.0,
    cost: float = 0.0,
    shipping: float = 0.0,
    customs: float = 0.0,
    other_direct: float = 0.0,
    return_amt: float = 0.0,
    discount_amt: float = 0.0,
    rebate_amt: float = 0.0,
    quantity: float = 1.0,
):
    return SimpleNamespace(
        customer_name=customer,
        product_code=product,
        revenue_amount=revenue,
        cost_amount=cost,
        shipping_fee=shipping,
        customs_fee=customs,
        other_direct_fee=other_direct,
        return_amount=return_amt,
        discount_amount=discount_amt,
        rebate_amount=rebate_amt,
        quantity=quantity,
        voucher_date="2024-06-15",
    )


class TestSummaryDeductsReturnDiscountRebate:
    """P0 修复: profit 必须扣 return/discount/rebate."""

    def test_profit_deducts_return(self):
        # revenue=1000, cost=600, return=50 → profit = 1000 - 50 - 600 = 350
        records = [_rec(revenue=1000, cost=600, return_amt=50)]
        analyzer = RevenueAnalyzer(records)
        s = analyzer._summary()
        assert s["total_revenue"] == 1000.0
        assert s["total_return"] == 50.0
        assert s["total_cost"] == 600.0
        assert s["gross_profit"] == 350.0

    def test_profit_deducts_discount(self):
        records = [_rec(revenue=1000, cost=600, discount_amt=80)]
        s = RevenueAnalyzer(records)._summary()
        assert s["total_discount"] == 80.0
        assert s["gross_profit"] == 1000 - 80 - 600  # 320

    def test_profit_deducts_rebate(self):
        records = [_rec(revenue=1000, cost=600, rebate_amt=30)]
        s = RevenueAnalyzer(records)._summary()
        assert s["total_rebate"] == 30.0
        assert s["gross_profit"] == 1000 - 30 - 600  # 370

    def test_profit_deducts_all_three(self):
        # revenue=10000, cost=4000, return=200, discount=300, rebate=100, direct=500
        # net_revenue = 10000 - 200 - 300 - 100 = 9400
        # profit = 9400 - 4000 - 500 = 4900
        records = [
            _rec(
                revenue=10000,
                cost=4000,
                return_amt=200,
                discount_amt=300,
                rebate_amt=100,
                shipping=500,
            )
        ]
        s = RevenueAnalyzer(records)._summary()
        assert s["total_revenue"] == 10000.0
        assert s["total_return"] == 200.0
        assert s["total_discount"] == 300.0
        assert s["total_rebate"] == 100.0
        assert s["total_cost"] == 4000.0
        assert s["total_direct_fee"] == 500.0
        assert s["gross_profit"] == 4900.0
        assert s["gross_margin"] == 0.49

    def test_missing_columns_graceful(self):
        """P0 修复: 列不存在时 → 0, 不报错."""
        # 构造 SimpleNamespace 但不设 return/discount/rebate → getattr 返 0
        records = [
            SimpleNamespace(
                customer_name="A", product_code="P", revenue_amount=1000, cost_amount=600,
                shipping_fee=0, customs_fee=0, other_direct_fee=0, quantity=1,
            )
        ]
        s = RevenueAnalyzer(records)._summary()
        # 没有 return_amount 等列 → 0
        assert s["total_return"] == 0.0
        assert s["total_discount"] == 0.0
        assert s["total_rebate"] == 0.0
        # profit 仍正常算
        assert s["gross_profit"] == 400.0

    def test_empty_df(self):
        s = RevenueAnalyzer([])._summary()
        assert s["record_count"] == 0
        assert s["gross_profit"] == 0.0
        assert s["gross_margin"] == 0.0


class TestPivotDeductsReturnDiscountRebate:
    """_pivot (按客户/产品分组) 也需扣减 return/discount/rebate."""

    def test_by_customer_deducts_rebate(self):
        records = [
            _rec(customer="客户A", revenue=1000, cost=600, rebate_amt=50),
            _rec(customer="客户B", revenue=2000, cost=1200),
        ]
        pivots = RevenueAnalyzer(records)._pivot("customer_name")
        # 找 A 和 B
        a = next(p for p in pivots if p["customer_name"] == "客户A")
        b = next(p for p in pivots if p["customer_name"] == "客户B")
        # A: 1000 - 50 - 600 = 350, B: 2000 - 0 - 1200 = 800
        assert a["profit"] == 350.0
        assert b["profit"] == 800.0

    def test_by_product_deducts_return(self):
        records = [
            _rec(product="P1", revenue=1000, cost=600, return_amt=100),
            _rec(product="P2", revenue=500, cost=300),
        ]
        pivots = RevenueAnalyzer(records)._pivot("product_code", name_col="product_code")
        p1 = next(p for p in pivots if p["product_code"] == "P1")
        p2 = next(p for p in pivots if p["product_code"] == "P2")
        # P1: 1000 - 100 - 600 = 300
        assert p1["profit"] == 300.0
        assert p2["profit"] == 200.0