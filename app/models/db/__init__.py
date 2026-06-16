"""Modular ORM submodules.

为避免 ``app/models/db_models.py`` 越来越大无法维护, 新模块的 ORM 一律放到
本包下的独立文件里 (``auth.py`` / ``notification.py`` / ``account_audit.py``
/ ``report_template.py`` 等)。``db_models.py`` 在顶部 ``import *``
聚合, 确保 ``from app.models.db_models import X`` 老调用 100% 兼容,
同时 ``Base.metadata`` 在模块加载时收齐所有新表。

新增模块步骤:
  1. 在本目录建 ``<module>.py``, 定义 SQLAlchemy ORM 类
  2. 在本 ``__init__.py`` 的 ``from .<module> import *`` 加一行
  3. 现有 ``app/models/db_models.py`` 已经做了 ``from app.models.db import *``,
     无需手动改

注意:
- 子文件里不要再 ``from app.core.database import Base``——使用本包
  ``__init__`` 一并暴露的 ``Base``, 避免循环依赖
- 所有子文件必须导出 ``__all__``, 不然 ``from .<m> import *``
  只会拿到不带下划线的顶层名, 可能漏掉常量/枚举
"""

from app.core.database import Base  # noqa: F401  re-export 给子文件用

# 各子模块按字母序汇总, 任何新增模块在这里追加一行即可
# 7 个"真正定义 ORM 类"的子模块必须先 import (它们定义类, 注册到 Base.metadata)
# 7 个"逻辑分组 / 懒加载"子模块不放在这里 — 用户直接 ``from app.models.db.<x> import Y`` 即可,
# 因为它们用 PEP 562 __getattr__ 懒加载, 无需在 __init__ 中触发; 一旦在 __init__ 里 import,
# 会形成 ``db_models → db.__init__ → confirmation → db_models`` 的循环, 把 ConfirmationCase
# 还没初始化就重新访问.
from .account_audit import *  # noqa: F401, F403
from .audit_cycles import *  # noqa: F401, F403
from .auth import *  # noqa: F401, F403
from .ipo_specials import *  # noqa: F401, F403
from .notification import *  # noqa: F401, F403
from .related_parties import *  # noqa: F401, F403
from .report_template import *  # noqa: F401, F403
# 逻辑分组 (PEP 562 懒加载): confirmation / inventory / project / regulations /
# sentiment / team_management / workpaper
