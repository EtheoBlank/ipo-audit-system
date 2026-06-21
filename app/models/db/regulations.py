"""Regulations + Knowledge Base ORM namespace — 法规库 + 知识库.

本文件是**逻辑分组 / re-export 容器**, 不再重复定义 ORM 类.
所有类仍由 ``app.models.db_models`` 统一定义, 本模块只把它们按"法规 / 知识库"汇总.

包含 5 张表:
  - ``Regulation`` / ``RegulationFavorite``
  - ``KnowledgeBook`` / ``KnowledgeChunk`` / ``KnowledgeRetrievalLog``

调用约定 (推荐):
  >>> from app.models.db_models import Regulation  # 老式, 仍兼容
  >>> from app.models.db.regulations import Regulation  # 新式, 语义化
"""

from app.models.db_models import (  # noqa: F401  re-export
    Regulation,
    RegulationFavorite,
    KnowledgeBook,
    KnowledgeChunk,
    KnowledgeRetrievalLog,
)


__all__ = [
    "Regulation",
    "RegulationFavorite",
    "KnowledgeBook",
    "KnowledgeChunk",
    "KnowledgeRetrievalLog",
]
