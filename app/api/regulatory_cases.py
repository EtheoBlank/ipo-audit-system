"""API routes for regulatory cases."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional

from app.core.database import get_db
from app.models.db_models import RegulatoryCase
from app.models.audit import RegulatoryCaseCreate, RegulatoryCaseResponse
from app.services.regulatory_scraper import RegulatoryCaseScraper

router = APIRouter(prefix="/api/regulatory-cases", tags=["监管案例"])


@router.post("/scrape")
async def scrape_regulatory_cases():
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
):
    """List regulatory cases with optional filters."""
    query = select(RegulatoryCase).where(RegulatoryCase.is_active == True)

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
):
    """Get regulatory case by ID."""
    result = await db.execute(
        select(RegulatoryCase).where(RegulatoryCase.id == case_id)
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="案例不存在")
    return case


@router.get("/search/by-keywords")
async def search_by_keywords(
    keywords: str = Query(..., description="逗号分隔的关键词"),
    db: AsyncSession = Depends(get_db),
):
    """Search regulatory cases by keywords."""
    keyword_list = [kw.strip() for kw in keywords.split(",")]

    result = await db.execute(
        select(RegulatoryCase).where(RegulatoryCase.is_active == True)
    )
    all_cases = result.scalars().all()

    scraper = RegulatoryCaseScraper()
    matched = scraper.match_cases_by_keywords(
        [case.__dict__ for case in all_cases],
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
):
    """Search regulatory cases by industry."""
    result = await db.execute(
        select(RegulatoryCase).where(
            RegulatoryCase.industry == industry,
            RegulatoryCase.is_active == True,
        )
    )
    cases = result.scalars().all()
    return {
        "industry": industry,
        "matched_count": len(cases),
        "cases": cases,
    }