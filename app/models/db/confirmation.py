"""Confirmation (函证) ORM namespace — 银行/客户/供应商/其他往来.

本文件是**逻辑分组 / re-export 容器**, 不再重复定义 ORM 类.
所有类仍由 ``app.models.db_models`` 统一定义, 本模块只把它们按"函证域"汇总,
方便代码搜索与业务导航 (e.g. ``from app.models.db.confirmation import ConfirmationCase``).

包含 5 张表 + 状态机常量:
  - ``ConfirmationCase``          函证案卷 (一份统计表)
  - ``ConfirmationItem``          函证对象 (一行 = 一个被函证方)
  - ``ConfirmationLetter``        发函记录 (锁定快照)
  - ``ConfirmationResponse``      回函记录 (含差异)
  - ``ConfirmationResponsePhoto`` 回函照片 (OCR + AI 解析)

**PEP 562 懒加载**: 鉴于 ``app.models.db_models`` 在加载时会先 ``from app.models.db
import *`` 触发本模块, 而本模块若直接 ``from app.models.db_models import X`` 会
形成循环依赖 — 所以使用 ``__getattr__`` 在用户真正访问名字时才从
``app.models.db_models`` 取, 避免初始化时循环.

调用约定 (推荐):
  >>> from app.models.db_models import ConfirmationCase  # 老式, 仍兼容
  >>> from app.models.db.confirmation import ConfirmationCase  # 新式, 语义化
  >>> from app.models.db.confirmation import PARTY_TYPE_BANK  # 常量
"""

from typing import Any

# 这些名字实际定义在 app.models.db_models; 访问时通过 __getattr__ 懒加载.
_LAZY_NAMES = frozenset({
    # ORM
    "ConfirmationCase",
    "ConfirmationItem",
    "ConfirmationLetter",
    "ConfirmationResponse",
    "ConfirmationResponsePhoto",
    # 函证对象类型
    "PARTY_TYPE_BANK",
    "PARTY_TYPE_CUSTOMER",
    "PARTY_TYPE_SUPPLIER",
    "PARTY_TYPE_OTHER_RECEIVABLE",
    "PARTY_TYPE_OTHER_PAYABLE",
    "PARTY_TYPE_LOAN",
    "PARTY_TYPE_INVESTMENT",
    "PARTY_TYPE_REGULATOR",
    "PARTY_TYPE_LITIGATION",
    "PARTY_TYPE_OTHER",
    "PARTY_TYPE_LABELS",
    # 函证状态
    "ITEM_STATUS_DRAFT",
    "ITEM_STATUS_CONFIRMED",
    "ITEM_STATUS_SENT",
    "ITEM_STATUS_RESPONDED",
    "ITEM_STATUS_PARTIAL",
    "ITEM_STATUS_NO_REPLY",
    "ITEM_STATUS_REJECTED",
    "ITEM_STATUS_MISMATCH",
    "ITEM_STATUS_VOIDED",
    "ITEM_STATUS_LABELS",
    # 回函差异
    "RESPONSE_MATCH",
    "RESPONSE_PARTIAL",
    "RESPONSE_MISMATCH",
    "RESPONSE_REJECT",
    "RESPONSE_UNCLEAR",
    "RESPONSE_STATUS_LABELS",
})


def __getattr__(name: str) -> Any:
    """PEP 562: 延迟从 ``app.models.db_models`` 取值, 避免循环 import."""
    if name in _LAZY_NAMES:
        from app.models import db_models

        value = getattr(db_models, name)
        # 缓存到本模块, 后续直接走模块属性, 避免每次查 dict.
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_LAZY_NAMES)
