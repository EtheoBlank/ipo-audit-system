"""知识库 API.

  - POST   /books/upload          上传书籍 (启动后台索引)
  - GET    /books                  列表
  - GET    /books/{id}             详情
  - DELETE /books/{id}             删除 (含文件)
  - POST   /books/{id}/reindex     重建索引
  - POST   /search                 语义+关键词检索
  - POST   /match-case             面向"审计说明生成"的便捷接口 — 输入科目/风险点 → 返回案例
"""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import KnowledgeBook
from app.services.knowledge_base import KnowledgeBaseService
from app.services.knowledge_base.document_loader import detect_file_type

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/knowledge-base", tags=["知识库"])

_kb_service = KnowledgeBaseService()


_ALLOWED_SUFFIXES = {"pdf", "epub", "docx", "txt", "md", "markdown"}


# ----------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------


class BookOut(BaseModel):
    id: int
    title: str
    author: Optional[str] = None
    publisher: Optional[str] = None
    file_type: str
    file_size: int
    category: Optional[str] = None
    tags: Optional[str] = None
    description: Optional[str] = None
    status: str
    chunk_count: int
    total_chars: int
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = None
    error_msg: Optional[str] = None
    uploaded_at: datetime
    indexed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="检索 query — 一般是审计场景或科目描述")
    top_k: int = Field(default=5, ge=1, le=20)
    book_ids: Optional[List[int]] = None
    category: Optional[str] = None
    project_id: Optional[int] = None
    context: Optional[str] = None


class SearchResult(BaseModel):
    chunk_id: int
    book_id: int
    book_title: str
    chapter: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    content: str
    score: float
    semantic_score: float
    keyword_score: float


class MatchCaseRequest(BaseModel):
    """根据底稿数据查找相似实务案例。"""

    account_code: Optional[str] = None
    account_name: Optional[str] = None
    risk_description: Optional[str] = None
    industry: Optional[str] = None
    extra_keywords: Optional[List[str]] = None
    project_id: Optional[int] = None
    top_k: int = Field(default=5, ge=1, le=20)
    category: Optional[str] = Field(default=None, description="只检索某类书，例如 案例集")


# ----------------------------------------------------------------------
# 上传 / 列表 / 详情 / 删除
# ----------------------------------------------------------------------


@router.post("/books/upload", response_model=BookOut)
async def upload_book(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    author: Optional[str] = Form(None),
    publisher: Optional[str] = Form(None),
    isbn: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """上传一本书 / 一份文档 — 落盘后启动后台索引任务。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持 .{suffix}，可用：{sorted(_ALLOWED_SUFFIXES)}",
        )

    # 落盘
    safe_name = re.sub(r"[^\w\-.一-龥]", "_", Path(filename).stem)[:80]
    unique = f"{safe_name}_{uuid.uuid4().hex[:8]}.{suffix}"
    target = settings.KNOWLEDGE_BASE_DIR / unique
    target.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    with target.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)
            if size > settings.KB_MAX_BOOK_SIZE:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"文件过大 (>{settings.KB_MAX_BOOK_SIZE // (1024*1024)}MB)",
                )

    book = KnowledgeBook(
        title=(title or Path(filename).stem)[:500],
        author=author,
        publisher=publisher,
        isbn=isbn,
        filename=filename,
        file_path=str(target),
        file_type=detect_file_type(filename),
        file_size=size,
        category=category,
        tags=tags,
        description=description,
        status="pending",
    )
    db.add(book)
    await db.commit()
    await db.refresh(book)

    bg.add_task(_safe_index, book.id)
    return book


async def _safe_index(book_id: int) -> None:
    try:
        await _kb_service.index_book(book_id)
    except Exception:  # noqa: BLE001
        logger.exception("书籍索引任务失败 (book_id=%s)", book_id)


@router.get("/books", response_model=List[BookOut])
async def list_books(
    category: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(KnowledgeBook)
    if category:
        q = q.where(KnowledgeBook.category == category)
    if status:
        q = q.where(KnowledgeBook.status == status)
    q = q.order_by(KnowledgeBook.uploaded_at.desc())
    return (await db.execute(q)).scalars().all()


@router.get("/books/{book_id}", response_model=BookOut)
async def get_book(book_id: int, db: AsyncSession = Depends(get_db)):
    book = (await db.execute(
        select(KnowledgeBook).where(KnowledgeBook.id == book_id)
    )).scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    return book


@router.delete("/books/{book_id}")
async def delete_book(book_id: int):
    await _kb_service.delete_book(book_id)
    return {"message": "已删除"}


@router.post("/books/{book_id}/reindex")
async def reindex_book(book_id: int, bg: BackgroundTasks):
    async def _runner():
        try:
            await _kb_service.reindex_book(book_id)
        except Exception:  # noqa: BLE001
            logger.exception("reindex 失败")

    bg.add_task(_runner)
    return {"message": "已加入后台重建索引队列", "book_id": book_id}


# ----------------------------------------------------------------------
# 检索
# ----------------------------------------------------------------------


@router.post("/search", response_model=List[SearchResult])
async def search_kb(req: SearchRequest, db: AsyncSession = Depends(get_db)):
    results = await _kb_service.search(
        db,
        query=req.query,
        top_k=req.top_k,
        book_ids=req.book_ids,
        category=req.category,
        project_id=req.project_id,
        context=req.context,
    )
    return [SearchResult(**r.__dict__) for r in results]


@router.post("/match-case", response_model=List[SearchResult])
async def match_case(req: MatchCaseRequest, db: AsyncSession = Depends(get_db)):
    """便捷接口：根据科目 / 风险 / 行业组合 query → 返回相似案例 chunk。

    比 ``/search`` 多了一层"组装 query"逻辑，让前端不用自己拼字符串。
    """
    parts: list[str] = []
    if req.account_code or req.account_name:
        parts.append(
            f"科目 {(req.account_code or '').strip()} {(req.account_name or '').strip()}".strip()
        )
    if req.industry:
        parts.append(f"行业 {req.industry}")
    if req.risk_description:
        parts.append(req.risk_description)
    if req.extra_keywords:
        parts.append(" ".join(req.extra_keywords))
    query = " ".join(p for p in parts if p).strip()
    if not query:
        raise HTTPException(
            status_code=400,
            detail="account / risk / industry / keywords 至少传一项",
        )

    results = await _kb_service.search(
        db,
        query=query,
        top_k=req.top_k,
        category=req.category,
        project_id=req.project_id,
        context=(
            f"account={req.account_code or ''};"
            f"industry={req.industry or ''}"
        ),
    )
    return [SearchResult(**r.__dict__) for r in results]


# ----------------------------------------------------------------------
# 分类聚合
# ----------------------------------------------------------------------


@router.get("/categories")
async def kb_categories(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func

    q = select(
        KnowledgeBook.category, func.count(KnowledgeBook.id)
    ).group_by(KnowledgeBook.category)
    rows = (await db.execute(q)).all()
    return [{"category": c or "未分类", "count": n} for c, n in rows]


@router.get("/stats")
async def kb_stats(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func

    total_books = (await db.execute(
        select(func.count(KnowledgeBook.id))
    )).scalar() or 0
    ready_books = (await db.execute(
        select(func.count(KnowledgeBook.id)).where(KnowledgeBook.status == "ready")
    )).scalar() or 0
    total_chunks = (await db.execute(
        select(func.coalesce(func.sum(KnowledgeBook.chunk_count), 0))
    )).scalar() or 0
    total_chars = (await db.execute(
        select(func.coalesce(func.sum(KnowledgeBook.total_chars), 0))
    )).scalar() or 0
    return {
        "total_books": total_books,
        "ready_books": ready_books,
        "total_chunks": int(total_chunks),
        "total_chars": int(total_chars),
    }
