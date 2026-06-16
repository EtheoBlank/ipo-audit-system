"""Inventory (收发存 / 盘点 / 跌价) ORM namespace.

本文件是**逻辑分组 / re-export 容器**, 不再重复定义 ORM 类.
所有类仍由 ``app.models.db_models`` 统一定义, 本模块只把它们按"成本循环"汇总.

包含:
  - ``InventoryMovement``       收发存明细
  - ``InventoryCountPlan``      监盘计划
  - ``InventoryCountSheet``     监盘表
  - ``InventoryImpairment``     跌价 / 库龄
  - ``InventoryCodeMapping``    物料编码跨年映射
  - ``InventoryCountPhoto``     盘点现场照片

**PEP 562 懒加载**: 避开 ``db_models`` ↔ ``db.__init__`` 循环依赖.

调用约定 (推荐):
  >>> from app.models.db_models import InventoryMovement  # 老式, 仍兼容
  >>> from app.models.db.inventory import InventoryMovement  # 新式, 语义化
"""

from typing import Any

_LAZY_NAMES = frozenset({
    "InventoryMovement",
    "InventoryCountPlan",
    "InventoryCountSheet",
    "InventoryImpairment",
    "InventoryCodeMapping",
    "InventoryCountPhoto",
})


def __getattr__(name: str) -> Any:
    if name in _LAZY_NAMES:
        from app.models import db_models

        value = getattr(db_models, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_LAZY_NAMES)
