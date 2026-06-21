"""Round 28 P1-8: naive/aware datetime 混算 — 统一转 naive.

bug: period_end 是 naive datetime, 但 ship_date 若从 CSV 解析成 aware datetime,
ref_dt <= period_end 抛 TypeError. 修复: 比较前 _to_naive() 统一.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from app.services.inventory.aging_engine import (
    InventoryAgingEngine,
    _to_naive,
    _parse_dt,
)


class TestToNaiveHelper:
    def test_aware_to_naive_strips_tz(self):
        """aware datetime 去 tzinfo, 值不变 (instant 不变)."""
        aware = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        result = _to_naive(aware)
        assert result is not None
        assert result.tzinfo is None
        # 值保持不变
        assert result == datetime(2024, 1, 15, 10, 0)

    def test_naive_unchanged(self):
        """naive datetime 保持不变."""
        naive = datetime(2024, 1, 15, 10, 0)
        result = _to_naive(naive)
        assert result is naive
        assert result.tzinfo is None

    def test_none_passthrough(self):
        """None 直接返回 None."""
        assert _to_naive(None) is None

    def test_china_tz_strips(self):
        """Asia/Shanghai tz (UTC+8) 也正常去 tzinfo."""
        tz = timezone(timedelta(hours=8))
        aware = datetime(2024, 6, 1, 12, 0, tzinfo=tz)
        result = _to_naive(aware)
        assert result is not None
        assert result.tzinfo is None
        assert result.year == 2024 and result.month == 6 and result.day == 1


class TestNRVAwareDatetime:
    """nrv_unit_price_from_sales 在 ship_date/confirm_date 是 aware 时不应抛 TypeError."""

    def test_aware_ship_date_no_crash(self):
        """ship_date 带 tzinfo → 不抛 TypeError, 正常返回或 None."""
        # aware datetime 设为 12月30日 (期末前) — 期末后销售判定不应纳入
        aware_dt = datetime(2024, 12, 30, 10, 0, tzinfo=timezone.utc)
        period_end_naive = datetime(2024, 12, 31)
        # 构造一个销售记录, ship_date 是 aware
        sales = [
            {
                "product_code": "M001",
                "revenue_confirm_date": aware_dt,
                "ship_date": aware_dt,
                "quantity": 10.0,
                "revenue_amount": 1000.0,
            }
        ]
        # 不应抛 TypeError
        result = InventoryAgingEngine.nrv_unit_price_from_sales(
            sales, "M001", period_end_naive
        )
        # 12月30日 < 12月31日, 期末前销售, 返 None
        assert result is None

    def test_aware_ship_date_post_period_works(self):
        """aware ship_date 在期末后, 仍能正确识别 (确认日 2025-01-15 > 2024-12-31)."""
        aware_dt = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)
        period_end_naive = datetime(2024, 12, 31)
        sales = [
            {
                "product_code": "M002",
                "revenue_confirm_date": aware_dt,
                "ship_date": None,
                "quantity": 10.0,
                "revenue_amount": 1500.0,
            }
        ]
        result = InventoryAgingEngine.nrv_unit_price_from_sales(
            sales, "M002", period_end_naive
        )
        # 1500/10 = 150
        assert result is not None
        unit_price, count = result
        assert unit_price == pytest.approx(150.0, rel=0.01)
        assert count == 1

    def test_naive_ship_date_unchanged(self):
        """naive ship_date 仍按原行为工作."""
        naive_dt = datetime(2025, 1, 15, 10, 0)
        period_end_naive = datetime(2024, 12, 31)
        sales = [
            {
                "product_code": "M003",
                "revenue_confirm_date": naive_dt,
                "ship_date": None,
                "quantity": 5.0,
                "revenue_amount": 500.0,
            }
        ]
        result = InventoryAgingEngine.nrv_unit_price_from_sales(
            sales, "M003", period_end_naive
        )
        assert result is not None
        unit_price, count = result
        assert unit_price == pytest.approx(100.0, rel=0.01)
        assert count == 1

    def test_parse_dt_passthrough_aware(self):
        """_parse_dt aware datetime 原样返回 (调用方负责 _to_naive)."""
        aware = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        result = _parse_dt(aware)
        assert result is not None
        assert result.tzinfo is timezone.utc
