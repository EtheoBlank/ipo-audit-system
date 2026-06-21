"""API routes for regulatory cases."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional

from app.core.database import get_db
from app.models.db_models import RegulatoryCase
from app.models.db.auth import User
from app.models.audit import RegulatoryCaseCreate, RegulatoryCaseResponse
from app.services.auth import get_current_user, get_current_user_optional
from app.services.regulatory_scraper import RegulatoryCaseScraper

router = APIRouter(prefix="/api/regulatory-cases", tags=["监管案例"])


@router.post("/scrape")
async def scrape_regulatory_cases(
    current_user: User = Depends(get_current_user),
):
    """Scrape regulatory cases from CSRC and stock exchanges."""
    scraper = RegulatoryCaseScraper()
    try:
        cases = await scraper.scrape_all()
        return {
            "message": f"成功抓取 {len(cases)} 条监管案例",
            "cases": cases,
        }
    finally:
        await scraper.close()


@router.post("/", response_model=RegulatoryCaseResponse)
async def create_regulatory_case(
    case: RegulatoryCaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually add a regulatory case."""
    db_case = RegulatoryCase(**case.model_dump())
    db.add(db_case)
    await db.commit()
    await db.refresh(db_case)
    return db_case


@router.get("/", response_model=List[RegulatoryCaseResponse])
async def list_regulatory_cases(
    skip: int = 0,
    limit: int = 100,
    case_type: Optional[str] = None,
    source: Optional[str] = None,
    industry: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List regulatory cases with optional filters."""
    query = select(RegulatoryCase).where(RegulatoryCase.is_active.is_(True))

    if case_type:
        query = query.where(RegulatoryCase.case_type == case_type)
    if source:
        query = query.where(RegulatoryCase.source == source)
    if industry:
        query = query.where(RegulatoryCase.industry == industry)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{case_id}", response_model=RegulatoryCaseResponse)
async def get_regulatory_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get regulatory case by ID."""
    result = await db.execute(select(RegulatoryCase).where(RegulatoryCase.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="案例不存在")
    return case


@router.get("/search/by-keywords")
async def search_by_keywords(
    keywords: str = Query(..., description="逗号分隔的关键词"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Search regulatory cases by keywords.

    P0 性能修复 (2026-06-19): 旧版 select 全表 + Python match_cases_by_keywords,
    10K+ 行每次 1-5s; 新版用 SQL ilike 任一关键词命中 title/content,
    10K+ 行 <50ms. 保留 Python 二次过滤作评分 (后置 LIMIT 100).
    """
    from sqlalchemy import or_ as _or
    from app.services.auth.audit_log import _escape_like

    keyword_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
    if not keyword_list:
        return {"keywords": [], "matched_count": 0, "cases": []}

    # 第一阶段: SQL 过滤, 任一关键词命中 title 或 content
    conds = [RegulatoryCase.is_active.is_(True)]
    kw_conds = []
    for kw in keyword_list[:10]:  # 限 10 个防过大 OR
        like = f"%{_escape_like(kw[:50])}%"
        kw_conds.append(
            _or(
                RegulatoryCase.title.ilike(like, escape="\\"),
                RegulatoryCase.content.ilike(like, escape="\\"),
            )
        )
    if kw_conds:
        conds.append(_or(*kw_conds))

    stmt = select(RegulatoryCase).where(*conds).limit(500)
    cases = (await db.execute(stmt)).scalars().all()

    # 第二阶段: Python 二次评分 (复用原 match_cases_by_keywords)
    scraper = RegulatoryCaseScraper()
    matched = scraper.match_cases_by_keywords(
        [case.__dict__ for case in cases],
        keyword_list,
    )
    return {
        "keywords": keyword_list,
        "matched_count": len(matched),
        "cases": matched,
    }


@router.get("/search/by-industry")
async def search_by_industry(
    industry: str = Query(..., description="所属行业"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Search regulatory cases by industry."""
    result = await db.execute(
        select(RegulatoryCase).where(
            RegulatoryCase.industry == industry,
            RegulatoryCase.is_active.is_(True),
        )
    )
    cases = result.scalars().all()
    return {
        "industry": industry,
        "matched_count": len(cases),
        "cases": cases,
    }
