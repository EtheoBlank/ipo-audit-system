"""法律法规查询 API.

提供：
  - 触发抓取（按来源 / 全量）
  - 列表查询（按来源 / 分类 / 关键词 / 日期 范围过滤）
  - 全文搜索 (SQLite LIKE，多关键词 AND/OR)
  - 收藏 / 取消收藏 / 收藏夹列表
  - 来源 & 分类 聚合统计
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.models.db_models import Regulation, RegulationFavorite
from app.models.db.auth import User
from app.services.auth import get_current_user, get_current_user_optional
from app.services.regulation_scraper import (
    RegulationScraperService,
    item_to_dict,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/regulations", tags=["法律法规"])


# ----------------------------------------------------------------------
# Pydantic schemas
# ----------------------------------------------------------------------


class RegulationOut(BaseModel):
    id: int
    source: str
    issuing_authority: Optional[str] = None
    category: Optional[str] = None
    title: str
    document_no: Optional[str] = None
    publish_date: Optional[str] = None
    effective_date: Optional[str] = None
    is_effective: bool = True
    summary: Optional[str] = None
    full_text: Optional[str] = None
    keywords: Optional[str] = None
    source_url: Optional[str] = None
    fetched_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ScrapeRequest(BaseModel):
    sources: Optional[List[str]] = Field(
        default=None, description="来源代码列表 (CSRC/MOF/STA/SAFE/PBOC)；不传则全抓"
    )
    max_pages: int = Field(default=0, ge=0, le=20, description="每栏目最大页数，0 = 默认")


class ScrapeResult(BaseModel):
    requested_sources: List[str]
    fetched: int
    inserted: int
    updated: int
    skipped: int
    duration_seconds: float


class FavoriteRequest(BaseModel):
    project_id: Optional[int] = None
    note: Optional[str] = None
    tag: Optional[str] = None


class FavoriteOut(BaseModel):
    id: int
    regulation_id: int
    project_id: Optional[int] = None
    note: Optional[str] = None
    tag: Optional[str] = None
    created_at: datetime
    regulation: RegulationOut

    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------------
# 抓取
# ----------------------------------------------------------------------


async def _persist_items(items: list) -> dict:
    """批量入库 — 用 content_hash 去重，已存在的更新 fetched_at。"""
    inserted = updated = skipped = 0
    async with AsyncSessionLocal() as db:
        for item in items:
            payload = item_to_dict(item)
            ch = payload.get("content_hash")
            if not ch:
                skipped += 1
                continue
            existing = await db.execute(select(Regulation).where(Regulation.content_hash == ch))
            row = existing.scalar_one_or_none()
            if row:
                row.fetched_at = datetime.now(timezone.utc)
                # 补完缺失字段（首次抓只拿到标题，第二次拿到详情）
                for k, v in payload.items():
                    if k == "content_hash":
                        continue
                    if not getattr(row, k, None) and v:
                        setattr(row, k, v)
                updated += 1
            else:
                db.add(Regulation(**payload))
                inserted += 1
        await db.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


@router.post("/scrape", response_model=ScrapeResult)
async def scrape_regulations(
    req: ScrapeRequest,
    current_user: User = Depends(get_current_user),
):
    """触发法规抓取并入库。

    同步返回结果（适合手动触发）。如果要在后台跑，前端用 ``/scrape/async``。
    """
    start = datetime.now(timezone.utc)
    service = RegulationScraperService()
    try:
        items = await service.scrape(sources=req.sources, max_pages=req.max_pages)
    finally:
        await service.close()
    stats = await _persist_items(items)
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    requested = req.sources or service.SUPPORTED_SOURCES
    return ScrapeResult(
        requested_sources=requested,
        fetched=len(items),
        duration_seconds=duration,
        **stats,
    )


@router.post("/scrape/async")
async def scrape_regulations_async(
    req: ScrapeRequest,
    bg: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """异步触发抓取 — 立即返回任务确认。"""

    async def _runner():
        service = RegulationScraperService()
        try:
            items = await service.scrape(sources=req.sources, max_pages=req.max_pages)
        finally:
            await service.close()
        try:
            await _persist_items(items)
        except Exception:
            logger.exception("法规入库失败")

    bg.add_task(_runner)
    return {
        "message": "法规抓取任务已加入后台队列",
        "sources": req.sources or RegulationScraperService.SUPPORTED_SOURCES,
    }


# ----------------------------------------------------------------------
# 查询
# ----------------------------------------------------------------------


@router.get("/", response_model=List[RegulationOut])
async def list_regulations(
    source: Optional[str] = Query(None, description="来源代码 CSRC/MOF/STA/SAFE/PBOC"),
    category: Optional[str] = None,
    is_effective: Optional[bool] = None,
    publish_after: Optional[str] = Query(None, description="YYYY-MM-DD"),
    publish_before: Optional[str] = Query(None, description="YYYY-MM-DD"),
    keyword: Optional[str] = Query(None, description="标题/正文模糊匹配"),
    skip: int = 0,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """列出法规 — 支持多维过滤。"""
    q = select(Regulation)
    conditions = []
    if source:
        conditions.append(Regulation.source == source.upper())
    if category:
        conditions.append(Regulation.category == category)
    if is_effective is not None:
        conditions.append(Regulation.is_effective == is_effective)
    if publish_after:
        conditions.append(Regulation.publish_date >= publish_after)
    if publish_before:
        conditions.append(Regulation.publish_date <= publish_before)
    if keyword:
        # 转义 LIKE 通配符 % _ \, 防止用户输入破坏搜索意图
        escaped = (
            keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        like = f"%{escaped}%"
        conditions.append(
            or_(
                Regulation.title.like(like, escape="\\"),
                Regulation.full_text.like(like, escape="\\"),
                Regulation.keywords.like(like, escape="\\"),
                Regulation.document_no.like(like, escape="\\"),
            )
        )
    if conditions:
        q = q.where(and_(*conditions))
    q = q.order_by(Regulation.publish_date.desc().nullslast()).offset(skip).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/sources")
async def list_sources(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """来源聚合 — 每个来源已抓的条数 + 最新发布日期。"""
    q = select(
        Regulation.source,
        func.count(Regulation.id),
        func.max(Regulation.publish_date),
    ).group_by(Regulation.source)
    rows = (await db.execute(q)).all()
    label_map = {
        "CSRC": "证监会",
        "MOF": "财政部",
        "STA": "国家税务总局",
        "SAFE": "国家外汇管理局",
        "PBOC": "中国人民银行",
        "LOCAL": "地方财税",
        "OTHER": "其他",
    }
    return [
        {
            "code": code,
            "name": label_map.get(code, code),
            "count": count,
            "latest_publish_date": latest,
        }
        for code, count, latest in rows
    ]


@router.get("/categories")
async def list_categories(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """分类聚合 — 用于前端构建筛选器。"""
    q = (
        select(Regulation.category, func.count(Regulation.id))
        .where(Regulation.category.is_not(None))
        .group_by(Regulation.category)
    )
    rows = (await db.execute(q)).all()
    return [{"category": c, "count": n} for c, n in rows if c]


@router.get("/search")
async def search_regulations(
    q: str = Query(..., min_length=1, description="搜索文本，支持多关键词空格分隔"),
    mode: str = Query("and", pattern="^(and|or)$", description="多关键词组合方式"),
    source: Optional[str] = None,
    limit: int = Query(30, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """多关键词全文搜索 — 用于审计师在生成审计说明时即时查规。"""
    keywords = [k for k in q.split() if k]
    if not keywords:
        return {"keywords": [], "count": 0, "results": []}

    # 转义 LIKE 通配符 % _ \
    def _escape_like(s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    field_matches = [
        or_(
            Regulation.title.like(f"%{_escape_like(kw)}%", escape="\\"),
            Regulation.full_text.like(f"%{_escape_like(kw)}%", escape="\\"),
            Regulation.keywords.like(f"%{_escape_like(kw)}%", escape="\\"),
            Regulation.document_no.like(f"%{_escape_like(kw)}%", escape="\\"),
            Regulation.summary.like(f"%{_escape_like(kw)}%", escape="\\"),
        )
        for kw in keywords
    ]
    combine = and_(*field_matches) if mode == "and" else or_(*field_matches)

    stmt = select(Regulation).where(combine)
    if source:
        stmt = stmt.where(Regulation.source == source.upper())
    stmt = stmt.order_by(Regulation.publish_date.desc().nullslast()).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "keywords": keywords,
        "mode": mode,
        "count": len(rows),
        "results": [RegulationOut.model_validate(r).model_dump(mode="json") for r in rows],
    }


@router.get("/{regulation_id}", response_model=RegulationOut)
async def get_regulation(
    regulation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    result = await db.execute(select(Regulation).where(Regulation.id == regulation_id))
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=404, detail="法规不存在")
    return reg


# ----------------------------------------------------------------------
# 收藏
# ----------------------------------------------------------------------


@router.post("/{regulation_id}/favorite", response_model=FavoriteOut)
async def favorite_regulation(
    regulation_id: int,
    req: FavoriteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reg = (
        await db.execute(select(Regulation).where(Regulation.id == regulation_id))
    ).scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=404, detail="法规不存在")
    fav = RegulationFavorite(
        regulation_id=regulation_id,
        project_id=req.project_id,
        note=req.note,
        tag=req.tag,
    )
    db.add(fav)
    await db.commit()
    await db.refresh(fav)
    # 显式 expire-load regulation 关联
    result = await db.execute(select(RegulationFavorite).where(RegulationFavorite.id == fav.id))
    return result.scalar_one()


@router.delete("/favorites/{favorite_id}")
async def unfavorite(
    favorite_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    fav = (
        await db.execute(select(RegulationFavorite).where(RegulationFavorite.id == favorite_id))
    ).scalar_one_or_none()
    if not fav:
        raise HTTPException(status_code=404, detail="收藏记录不存在")
    await db.delete(fav)
    await db.commit()
    return {"message": "已取消收藏"}


@router.get("/favorites/list", response_model=List[FavoriteOut])
async def list_favorites(
    project_id: Optional[int] = None,
    tag: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = select(RegulationFavorite)
    if project_id is not None:
        q = q.where(RegulationFavorite.project_id == project_id)
    if tag:
        q = q.where(RegulationFavorite.tag == tag)
    q = q.order_by(RegulationFavorite.created_at.desc())
    rows = (await db.execute(q)).scalars().all()
    return rows
