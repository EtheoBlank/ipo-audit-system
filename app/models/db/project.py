"""Project + 关联基表 ORM namespace.

本文件是**逻辑分组 / re-export 容器**, 不再重复定义 ORM 类.
所有类仍由 ``app.models.db_models`` 统一定义, 本模块只把它们按"项目核心 + 关联基表"汇总.

包含:
  - ``Project``             审计项目表
  - ``AccountBalance``      科目余额
  - ``ChronologicalAccount`` 序时账
  - ``BankStatement``       银行对账单
  - ``AuditRisk``           审计风险
  - ``RegulatoryCase``      监管案例库
  - ``SalesDocument``       销售原始文档
  - ``SalesRecord``         销售清单行
  - ``ContractDocument``    收入合同

调用约定 (推荐):
  >>> from app.models.db_models import Project  # 老式, 仍兼容
  >>> from app.models.db.project import Project  # 新式, 语义化
"""

from app.models.db_models import (  # noqa: F401  re-export
    Project,
    AccountBalance,
    ChronologicalAccount,
    BankStatement,
    AuditRisk,
    RegulatoryCase,
    SalesDocument,
    SalesRecord,
    ContractDocument,
)


__all__ = [
    "Project",
    "AccountBalance",
    "ChronologicalAccount",
    "BankStatement",
    "AuditRisk",
    "RegulatoryCase",
    "SalesDocument",
    "SalesRecord",
    "ContractDocument",
]
