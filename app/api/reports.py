"""综合报告API - 第六阶段."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.core.database import get_db
from app.models.db_models import AccountBalance
from app.models.db.auth import User
from app.services.auth import (
    ensure_project_in_firm,
    get_current_user,
    get_current_user_optional,
)
from app.services.report_generator import (
    ComprehensiveReportGenerator,
    InteractiveReportGenerator,
    PDFReportGenerator,
)
from app.services.trial_balance_engine import TrialBalanceEngine
from app.services.ai_analysis_engine import RiskIdentifier, AnomalyDetector
from app.utils.db_helpers import account_balances_to_df

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["综合报告"])


@router.post("/generate/word")
async def generate_word_report(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成Word格式综合报告."""
    # 多租户硬隔离
    project = await ensure_project_in_firm(db, project_id, current_user)

    # 获取科目余额
    result = await db.execute(select(AccountBalance).where(AccountBalance.project_id == project_id))
    balances = result.scalars().all()

    if not balances:
        raise HTTPException(status_code=400, detail="请先导入科目余额数据")

    df_balances = account_balances_to_df(balances)

    # 生成报告
    generator = ComprehensiveReportGenerator()
    project_info = {
        "name": project.name,
        "company_name": project.company_name,
        "industry": project.industry,
        "fiscal_year": project.fiscal_year,
    }

    financial_data = {
        "total_assets": df_balances["ending_balance"].sum(),
        "revenue": df_balances[df_balances["account_code"].str.startswith("5")][
            "credit_amount"
        ].sum(),
        "registered_capital": 0,
        "net_profit": 0,
        "gross_margin": 0,
    }

    risk_analysis = {"risk_level": "中", "risk_points": [], "recommendations": []}
    trial_balance = {"is_balanced": True, "total_debit": 0, "total_credit": 0, "difference": 0}

    report_bytes = generator.generate_word_report(
        project_info, financial_data, risk_analysis, trial_balance, []
    )

    return StreamingResponse(
        iter([report_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=审计报告_{project.fiscal_year}.docx"
        },
    )


@router.post("/generate/pdf")
async def generate_pdf_report(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成PDF格式综合报告."""
    project = await ensure_project_in_firm(db, project_id, current_user)

    result = await db.execute(select(AccountBalance).where(AccountBalance.project_id == project_id))
    balances = result.scalars().all()

    if not balances:
        raise HTTPException(status_code=400, detail="请先导入科目余额数据")

    df_balances = account_balances_to_df(balances)

    generator = PDFReportGenerator()
    project_info = {
        "name": project.name,
        "company_name": project.company_name,
        "industry": project.industry,
        "fiscal_year": project.fiscal_year,
    }

    financial_data = {
        "total_assets": df_balances["ending_balance"].sum(),
        "revenue": df_balances[df_balances["account_code"].str.startswith("5")][
            "credit_amount"
        ].sum(),
    }

    risk_analysis = {"risk_level": "中", "risk_points": []}
    trial_balance = {"is_balanced": True, "total_debit": 0, "total_credit": 0}

    report_bytes = generator.generate_pdf(
        project_info, financial_data, risk_analysis, trial_balance
    )

    return StreamingResponse(
        iter([report_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=审计报告_{project.fiscal_year}.pdf"},
    )


@router.get("/dashboard")
async def get_dashboard_data(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """获取交互式仪表盘数据."""
    project = await ensure_project_in_firm(db, project_id, current_user)

    result = await db.execute(select(AccountBalance).where(AccountBalance.project_id == project_id))
    balances = result.scalars().all()

    df_balances = account_balances_to_df(balances)

    # 检测异常
    anomalies = []
    risk_identifier = RiskIdentifier()
    anomalies.extend(
        risk_identifier.identify_revenue_recognition_risk(df_balances.to_dict("records"))
    )
    anomalies.extend(
        risk_identifier.identify_goodwill_impairment_risk(df_balances.to_dict("records"))
    )

    anomaly_detector = AnomalyDetector()
    anomalies.extend(anomaly_detector.detect_round_number_anomalies(df_balances.to_dict("records")))
    anomalies.extend(
        anomaly_detector.detect_balance_direction_anomalies(df_balances.to_dict("records"))
    )

    # 试算平衡
    engine = TrialBalanceEngine()
    balance_result = engine.check_balance(df_balances)

    trial_balance_data = {
        "is_balanced": balance_result.is_balanced,
        "total_debit": balance_result.total_debit,
        "total_credit": balance_result.total_credit,
        "difference": balance_result.difference,
    }

    project_info = {
        "name": project.name,
        "company_name": project.company_name,
        "industry": project.industry,
        "fiscal_year": project.fiscal_year,
    }

    financial_data = {
        "total_assets": df_balances["ending_balance"].sum(),
        "revenue": df_balances[df_balances["account_code"].str.startswith("5")][
            "credit_amount"
        ].sum(),
    }

    dashboard_data = InteractiveReportGenerator.generate_dashboard_data(
        project_info,
        financial_data,
        {"risk_level": "中", "risk_points": [], "scores": {}},
        trial_balance_data,
        anomalies,
    )

    return dashboard_data


@router.get("/anomalies")
async def get_anomalies(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """获取异常检测结果.

    P0 修复 (2026-06-18 Bug 扫描): 之前没有 ensure_project_in_firm, 任何登录用户
    传 project_id 都可读其他事务所的完整科目余额 + 风险检测结果. 现在加多租户校验.
    """
    # P0 多租户: 先校验 project 属于 current_user.firm
    await ensure_project_in_firm(db, project_id, current_user)
    result = await db.execute(select(AccountBalance).where(AccountBalance.project_id == project_id))
    balances = result.scalars().all()

    df_balances = account_balances_to_df(balances)

    anomalies = []

    risk_identifier = RiskIdentifier()
    anomalies.extend(
        risk_identifier.identify_revenue_recognition_risk(df_balances.to_dict("records"))
    )
    anomalies.extend(
        risk_identifier.identify_goodwill_impairment_risk(df_balances.to_dict("records"))
    )

    anomaly_detector = AnomalyDetector()
    anomalies.extend(anomaly_detector.detect_round_number_anomalies(df_balances.to_dict("records")))
    anomalies.extend(
        anomaly_detector.detect_balance_direction_anomalies(df_balances.to_dict("records"))
    )
    anomalies.extend(
        anomaly_detector.detect_zero_activity_anomalies(df_balances.to_dict("records"))
    )

    return {"anomalies": anomalies, "total_count": len(anomalies)}
