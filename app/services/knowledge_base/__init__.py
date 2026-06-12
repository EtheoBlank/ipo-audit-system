"""知识库子包 (Knowledge Base).

按职责拆分：
  - document_loader: 把 PDF/EPUB/DOCX/TXT/MD 转为带定位信息的纯文本片段
  - chunker:         中文友好的文本切分
  - embedder:        把文本片段向量化 (远端 API + 本地 TF-IDF 双轨)
  - retriever:       根据查询找相似 chunk
  - service:         对外的统一入口 — 索引一本书 / 检索 / 删除
"""

from app.services.knowledge_base.service import KnowledgeBaseService

__all__ = ["KnowledgeBaseService"]
