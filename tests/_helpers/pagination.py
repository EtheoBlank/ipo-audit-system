"""分页 / 列表响应断言 helper."""
from __future__ import annotations

from typing import Iterable, Optional


def assert_paginated(
    payload: dict,
    *,
    expected_total: Optional[int] = None,
    expected_page_size: Optional[int] = None,
    has_items_key: str = "items",
    has_total_key: str = "total",
) -> None:
    """校验分页响应结构: ``{items: [...], total: N, ...}``.

    用法::

        r = client.get("/api/projects?page=1&size=20")
        assert_paginated(r.json(), expected_total=3, expected_page_size=20)
    """
    assert has_items_key in payload, f"响应缺 {has_items_key} 字段: keys={list(payload)}"
    items = payload[has_items_key]
    assert isinstance(items, list), f"{has_items_key} 应是 list, 实得 {type(items)}"

    if expected_total is not None:
        assert has_total_key in payload, f"响应缺 {has_total_key} 字段"
        assert payload[has_total_key] == expected_total, (
            f"{has_total_key} 应 {expected_total}, 实得 {payload[has_total_key]}"
        )

    if expected_page_size is not None:
        assert len(items) <= expected_page_size, (
            f"items 长度 {len(items)} 超过 page_size {expected_page_size}"
        )


def assert_all_unique(items: Iterable, *, key=None, msg: str = "") -> None:
    """断言序列里无重复. ``key`` 可选: 对 item 取属性/方法后比较."""
    seq = list(items)
    if key is None:
        seen = set()
        for x in seq:
            assert x not in seen, f"重复元素: {x} {msg}"
            seen.add(x)
    else:
        seen = set()
        for x in seq:
            k = key(x) if callable(key) else getattr(x, key)
            assert k not in seen, f"重复 key={k}: {x} {msg}"
            seen.add(k)
