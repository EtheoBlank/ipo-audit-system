"""统一的时间工具 — 全项目共享的 ``utc_now`` 函数.

背景
----
项目中原本存在 8 个 ``_utcnow()`` 的私有定义, 一部分返回 timezone-aware
datetime, 一部分返回 timezone-naive datetime. 两种 datetime 混用在
SQLAlchemy 2.0 + aiosqlite 下会触发 ``TypeError: can't subtract
offset-naive and offset-aware datetimes``, 切到 PostgreSQL 时更会出现
``asyncpg.exceptions.DataError: invalid input syntax for type timestamp
with time zone``.

为消除这种隐性 bug, 整个仓库统一调用本模块的 :func:`utc_now`, 行为:

- 返回 **naive** UTC datetime (与 aiosqlite / 现有 SQLite schema 一致)
- 取自 ``datetime.now(timezone.utc).replace(tzinfo=None)``
- 所有 ``db/`` 子模块以及 ``db_models.py`` 的 ``_utcnow()`` 全部
  委托到这里.

迁移路径 (后续优化)
-------------------
如果将来要切到 PostgreSQL, 把这里的实现改成 ``datetime.now(timezone.utc)``,
让 SQLAlchemy 自己处理 tz-aware, 并把 schema 里的 ``DateTime`` 换成
``DateTime(timezone=True)``. 切换时一处改, 不用全仓库搜替换.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回当前的 naive UTC datetime — 全项目唯一时间源.

    Returns
    -------
    datetime
        naive UTC datetime, 与 aiosqlite 兼容.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
