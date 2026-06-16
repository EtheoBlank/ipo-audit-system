"""综合底稿自动生成 ORM namespace — 多所模板 + 历史底稿库.

本文件是**逻辑分组 / re-export 容器**, 不再重复定义 ORM 类.
所有类仍由 ``app.models.db_models`` 统一定义, 本模块只把它们按"综合底稿"汇总.

包含:
  - ``FirmTemplate``         事务所综合底稿模板 (多所隔离 + 版本管理)
  - ``HistoricalWorkpaper``  事务所历史综合底稿 (脱敏后入库)

调用约定 (推荐):
  >>> from app.models.db_models import FirmTemplate  # 老式, 仍兼容
  >>> from app.models.db.workpaper import FirmTemplate  # 新式, 语义化
"""

from app.models.db_models import (  # noqa: F401  re-export
    FirmTemplate,
    HistoricalWorkpaper,
)


__all__ = [
    "FirmTemplate",
    "HistoricalWorkpaper",
]
