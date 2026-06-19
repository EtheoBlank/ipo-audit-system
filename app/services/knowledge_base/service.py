"""知识库统一入口 (Service).

对外暴露三个高层动作：
  - ``index_book(book_id)``  解析 → 切块 → 向量化 → 入库
  - ``search(query, ...)``   语义 + 关键词混合检索
  - ``delete_book(book_id)`` 删除书 + 文件

设计点：
  - **向量化失败可降级**：远端 API 不通时回退到 TF-IDF；
    用户重配 .env 后可一键 reindex 升级为远端嵌入。
  - **批量索引在事务中分批 commit**，避免单本大书 OOM。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    KnowledgeBook,
    KnowledgeChunk,
    KnowledgeRetrievalLog,
)
from app.services.knowledge_base.chunker import (
    TextChunk,
    chunk_segments,
    extract_keywords,
)
from app.services.knowledge_base.document_loader import (
    load_document,
)
from app.services.knowledge_base.embedder import (
    DeepSeekEmbedder,
    MinimaxEmbedder,
    TfidfEmbedder,
    vec_to_json,
)
from app.services.knowledge_base.retriever import (
    RetrievedChunk,
    retrieve,
)

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """知识库高层服务，按需异步使用。

    所有方法都接受 / 内部创建自己的 AsyncSession (取决于是否要后台跑)。
    """

    # —————————————————————————————————————————————————————————
    # 索引
    # —————————————————————————————————————————————————————————

    async def index_book(self, book_id: int) -> dict:
        """解析并索引一本书 — 适合 BackgroundTasks 调用。"""
        async with AsyncSessionLocal() as db:
            book = (
                await db.execute(select(KnowledgeBook).where(KnowledgeBook.id == book_id))
            ).scalar_one_or_none()
            if not book:
                raise ValueError(f"书籍不存在: {book_id}")
            book.status = "parsing"
            book.error_msg = None
            await db.commit()

        try:
            # load_document / chunk_segments 是同步 CPU+I/O (pdfplumber + re.split),
            # 大文件会阻塞事件循环 → 用 to_thread 推到线程池
            segs = await asyncio.to_thread(load_document, Path(book.file_path))
            text_chunks = await asyncio.to_thread(
                chunk_segments,
                segs,
                settings.KB_CHUNK_SIZE,
                settings.KB_CHUNK_OVERLAP,
            )
            if not text_chunks:
                raise ValueError("文档解析后无可用文本")

            embedder = self._build_embedder()
            # 跨书共享 TF-IDF 词表: 从最近一次索引恢复词表状态
            if isinstance(embedder, TfidfEmbedder):
                await self._restore_shared_tfidf(db_session=None, embedder=embedder)
            vectors = await self._compute_vectors(embedder, text_chunks)
            model_name = embedder.model_name if hasattr(embedder, "model_name") else "tfidf"
            dim = len(vectors[0]) if vectors else 0

            await self._persist_chunks(book_id, text_chunks, vectors, model_name, dim)

            async with AsyncSessionLocal() as db:
                book = (
                    await db.execute(select(KnowledgeBook).where(KnowledgeBook.id == book_id))
                ).scalar_one()
                book.status = "ready"
                book.chunk_count = len(text_chunks)
                book.total_chars = sum(c.char_count for c in text_chunks)
                book.embedding_model = model_name
                book.embedding_dim = dim
                # 保存 TF-IDF 词表状态供下一本书共享
                if isinstance(embedder, TfidfEmbedder):
                    book.tfidf_state = json.dumps(embedder.to_state(), ensure_ascii=False)
                book.indexed_at = datetime.now(timezone.utc)
                await db.commit()

            return {
                "book_id": book_id,
                "chunks": len(text_chunks),
                "embedding_model": model_name,
                "embedding_dim": dim,
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("书籍索引失败 (book_id=%s)", book_id)
            async with AsyncSessionLocal() as db:
                book = (
                    await db.execute(select(KnowledgeBook).where(KnowledgeBook.id == book_id))
                ).scalar_one_or_none()
                if book:
                    book.status = "failed"
                    book.error_msg = str(e)[:1000]
                    await db.commit()
            raise

    async def _restore_shared_tfidf(
        self,
        db_session: Optional[AsyncSession],
        embedder: "TfidfEmbedder",
    ) -> None:
        """从最近一次成功索引的书籍恢复 TF-IDF 词表状态。"""
        close_after = db_session is None
        try:
            async with AsyncSessionLocal() as session:
                last = (
                    await session.execute(
                        select(KnowledgeBook)
                        .where(
                            KnowledgeBook.tfidf_state.is_not(None),
                            KnowledgeBook.status == "ready",
                        )
                        .order_by(KnowledgeBook.indexed_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if last and last.tfidf_state:
                    try:
                        embedder.from_state(json.loads(last.tfidf_state))
                        logger.info(
                            "TF-IDF 词表已从 book_id=%d 恢复 (词表大小 %d)",
                            last.id, embedder.dim,
                        )
                    except (json.JSONDecodeError, KeyError) as exc:
                        logger.warning("恢复 TF-IDF 词表失败: %s", exc)
        finally:
            if close_after:
                pass  # async with handles cleanup

    async def reindex_book(self, book_id: int) -> dict:
        """重新索引 — 先清掉旧 chunk 再重建。"""
        async with AsyncSessionLocal() as db:
            await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.book_id == book_id))
            await db.commit()
        return await self.index_book(book_id)

    # —————————————————————————————————————————————————————————
    # 删除
    # —————————————————————————————————————————————————————————

    async def delete_book(self, book_id: int) -> None:
        async with AsyncSessionLocal() as db:
            book = (
                await db.execute(select(KnowledgeBook).where(KnowledgeBook.id == book_id))
            ).scalar_one_or_none()
            if not book:
                return
            file_path = book.file_path
            await db.delete(book)
            await db.commit()

        # 文件落盘 — 删除前确认它确实在 KB 目录下，避免误删任意路径
        try:
            p = Path(file_path).resolve()
            kb_dir = settings.KNOWLEDGE_BASE_DIR.resolve()
            # P0 安全修复: 用 is_relative_to (pathlib 3.9+) 防前缀穿透
            try:
                if not Path(p).is_relative_to(Path(kb_dir)):
                    raise ValueError(f"路径越界：{p} 不在 {kb_dir} 下")
            except (OSError, ValueError):
                raise
            if p.exists():
                os.remove(p)
        except (OSError, ValueError):
            logger.debug("书籍文件删除失败：%s", file_path, exc_info=True)

    # —————————————————————————————————————————————————————————
    # 检索
    # —————————————————————————————————————————————————————————

    async def search(
        self,
        db: AsyncSession,
        query: str,
        *,
        top_k: int = None,
        book_ids: Optional[Sequence[int]] = None,
        category: Optional[str] = None,
        project_id: Optional[int] = None,
        context: Optional[str] = None,
        firm_id: Optional[int] = None,
    ) -> List[RetrievedChunk]:
        """检索 — 自动选择 embedder.

        P0 (2026-06-19): 加 firm_id 过滤. 之前任意用户可检索全所书籍,
        等同知识库内容跨所泄露. API 层传 current_user.firm_id.
        """
        top_k = top_k or settings.KB_DEFAULT_TOP_K

        # 准备 query 向量
        query_vector: Optional[List[float]] = None
        try:
            embedder = self._build_embedder()
            if isinstance(embedder, TfidfEmbedder):
                # 单查询临时 fit 没意义；改为在检索时只用关键词得分
                query_vector = None
            else:
                vectors = await embedder.aembed([query])
                query_vector = vectors[0] if vectors else None
        except Exception:  # noqa: BLE001
            logger.warning("Query embedding 失败，退回关键词检索", exc_info=True)
            query_vector = None

        results = await retrieve(
            db,
            query=query,
            query_vector=query_vector,
            top_k=top_k,
            book_ids=book_ids,
            category=category,
            firm_id=firm_id,  # P0 多租户隔离
            keyword_weight=0.4 if query_vector is None else 0.3,
        )

        # 落检索日志 (便于回溯审计说明依据)
        try:
            db.add(
                KnowledgeRetrievalLog(
                    project_id=project_id,
                    query_text=query[:1000],
                    query_context=context,
                    top_chunk_ids=json.dumps([r.chunk_id for r in results]),
                    top_scores=json.dumps([r.score for r in results]),
                    result_count=len(results),
                )
            )
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.debug("检索日志写入失败", exc_info=True)

        return results

    # —————————————————————————————————————————————————————————
    # 内部
    # —————————————————————————————————————————————————————————

    def _build_embedder(self):
        provider = (settings.KB_EMBEDDING_PROVIDER or "tfidf").lower()
        if provider == "minimax":
            try:
                return MinimaxEmbedder()
            except RuntimeError as e:
                logger.warning("MiniMax embedder 不可用，回退到 TF-IDF: %s", e)
        elif provider == "deepseek":
            try:
                return DeepSeekEmbedder()
            except RuntimeError as e:
                logger.warning("DeepSeek embedder 不可用，回退到 TF-IDF: %s", e)
        return TfidfEmbedder()

    async def _compute_vectors(
        self,
        embedder,
        chunks: List[TextChunk],
    ) -> List[List[float]]:
        """根据 embedder 类型决定 fit/transform 还是远端 aembed。"""
        texts = [c.content for c in chunks]
        if isinstance(embedder, TfidfEmbedder):
            # TfidfEmbedder.fit() 已实现跨书词表累加 (通过 _corpus 累积 + tfidf_state 持久化),
            # 此处 fit 会将新书文本合并到已有词表中, 保证跨书 cosine 有效.
            embedder.fit(texts)
            return embedder.transform(texts)
        # 远端 API
        return await embedder.aembed(texts)

    async def _persist_chunks(
        self,
        book_id: int,
        chunks: List[TextChunk],
        vectors: List[List[float]],
        model_name: str,
        dim: int,
    ) -> None:
        BATCH = 200
        async with AsyncSessionLocal() as db:
            for i in range(0, len(chunks), BATCH):
                batch_chunks = chunks[i : i + BATCH]
                batch_vecs = vectors[i : i + BATCH] if vectors else [None] * len(batch_chunks)
                for c, v in zip(batch_chunks, batch_vecs):
                    db.add(
                        KnowledgeChunk(
                            book_id=book_id,
                            chunk_index=c.chunk_index,
                            chapter=c.chapter,
                            section=c.section,
                            page=c.page,
                            content=c.content,
                            char_count=c.char_count,
                            keywords=",".join(extract_keywords(c.content)),
                            embedding=vec_to_json(v) if v else None,
                        )
                    )
                await db.commit()
