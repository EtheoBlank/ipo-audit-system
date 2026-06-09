"""综合报告API - 第六阶段."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional

from app.core.database import get_db
from app.models.db_models import Project, AccountBalance, ChronologicalAccount
from app.models.audit import ApiResponse
from app.services.report_generator import (
    ComprehensiveReportGenerator,
    InteractiveReportGenerator,
    PDFReportGenerator,
)
from app.services.trial_balance_engine import TrialBalanceEngine
from app.services.ai_analysis_engine import RiskIdentifier, AnomalyDetector

router = APIRouter(prefix="/api/reports", tags=["综合报告"])


@router.post("/generate/word")
async def generate_word_report(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """生成Word格式综合报告."""
    # 获取项目信息
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 获取科目余额
    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == project_id)
    )
    balances = result.scalars().all()

    if not balances:
        raise HTTPException(status_code=400, detail="请先导入科目余额数据")

    import pandas as pd
    df_balances = pd.DataFrame([{
        "account_code": ab.account_code,
        "account_name": ab.account_name,
        "balance_direction": ab.balance_direction,
        "beginning_balance": ab.beginning_balance,
        "debit_amount": ab.debit_amount,
        "credit_amount": ab.credit_amount,
        "ending_balance": ab.ending_balance,
    } for ab in balances])

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
        "revenue": df_balances[df_balances["account_code"].str.startswith("5")]["credit_amount"].sum(),
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
        headers={"Content-Disposition": f"attachment; filename=审计报告_{project.fiscal_year}.docx"},
    )


@router.post("/generate/pdf")
async def generate_pdf_report(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """生成PDF格式综合报告."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == project_id)
    )
    balances = result.scalars().all()

    if not balances:
        raise HTTPException(status_code=400, detail="请先导入科目余额数据")

    import pandas as pd
    df_balances = pd.DataFrame([{
        "account_code": ab.account_code,
        "account_name": ab.account_name,
        "balance_direction": ab.balance_direction,
        "beginning_balance": ab.beginning_balance,
        "debit_amount": ab.debit_amount,
        "credit_amount": ab.credit_amount,
        "ending_balance": ab.ending_balance,
    } for ab in balances])

    generator = PDFReportGenerator()
    project_info = {
        "name": project.name,
        "company_name": project.company_name,
        "industry": project.industry,
        "fiscal_year": project.fiscal_year,
    }

    financial_data = {
        "total_assets": df_balances["ending_balance"].sum(),
        "revenue": df_balances[df_balances["account_code"].str.startswith("5")]["credit_amount"].sum(),
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
):
    """获取交互式仪表盘数据."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == project_id)
    )
    balances = result.scalars().all()

    import pandas as pd
    df_balances = pd.DataFrame([{
        "account_code": ab.account_code,
        "account_name": ab.account_name,
        "balance_direction": ab.balance_direction,
        "beginning_balance": ab.beginning_balance,
        "debit_amount": ab.debit_amount,
        "credit_amount": ab.credit_amount,
        "ending_balance": ab.ending_balance,
    } for ab in balances])

    # 检测异常
    anomalies = []
    risk_identifier = RiskIdentifier()
    anomalies.extend(risk_identifier.identify_revenue_recognition_risk(df_balances.to_dict("records")))
    anomalies.extend(risk_identifier.identify_goodwill_impairment_risk(df_balances.to_dict("records")))

    anomaly_detector = AnomalyDetector()
    anomalies.extend(anomaly_detector.detect_round_number_anomalies(df_balances.to_dict("records")))
    anomalies.extend(anomaly_detector.detect_balance_direction_anomalies(df_balances.to_dict("records")))

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
        "revenue": df_balances[df_balances["account_code"].str.startswith("5")]["credit_amount"].sum(),
    }

    dashboard_data = InteractiveReportGenerator.generate_dashboard_data(
        project_info, financial_data,
        {"risk_level": "中", "risk_points": [], "scores": {}},
        trial_balance_data,
        anomalies
    )

    return dashboard_data


@router.get("/anomalies")
async def get_anomalies(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取异常检测结果."""
    result = await db.execute(
        select(AccountBalance).where(AccountBalance.project_id == project_id)
    )
    balances = result.scalars().all()

    import pandas as pd
    df_balances = pd.DataFrame([{
        "account_code": ab.account_code,
        "account_name": ab.account_name,
        "balance_direction": ab.balance_direction,
        "beginning_balance": ab.beginning_balance,
        "debit_amount": ab.debit_amount,
        "credit_amount": ab.credit_amount,
        "ending_balance": ab.ending_balance,
    } for ab in balances])

    anomalies = []

    risk_identifier = RiskIdentifier()
    anomalies.extend(risk_identifier.identify_revenue_recognition_risk(df_balances.to_dict("records")))
    anomalies.extend(risk_identifier.identify_goodwill_impairment_risk(df_balances.to_dict("records")))

    anomaly_detector = AnomalyDetector()
    anomalies.extend(anomaly_detector.detect_round_number_anomalies(df_balances.to_dict("records")))
    anomalies.extend(anomaly_detector.detect_balance_direction_anomalies(df_balances.to_dict("records")))
    anomalies.extend(anomaly_detector.detect_zero_activity_anomalies(df_balances.to_dict("records")))

    return {"anomalies": anomalies, "total_count": len(anomalies)}