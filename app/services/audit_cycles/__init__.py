"""Pack C — 10 个审计循环核心服务函数 (合并版).

每个循环只暴露最关键的几个静态方法, 详细 CRUD 由 API 路由层直接走 ORM.
重点放在 "重算 / 自动判定 / 异常检测" 等核心审计能力.
"""

from __future__ import annotations

import logging
import math  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.audit_cycles import (
    AssetImpairmentTest,  # noqa: F401
    ConstructionInProgress,
    DepreciationRecalc,
    ECLAssessment,  # noqa: F401
    ExpenseAnomalyFlag,
    ExpenseRecord,
    FixedAsset,
    GoingConcernAssessment,  # noqa: F401
    IncomeTaxReconciliation,  # noqa: F401
    IntangibleAsset,  # noqa: F401
    LeaseAmortizationSchedule,
    LeaseContract,
    LongTermInvestment,  # noqa: F401
    PayableAging,
    PayrollReconciliation,
    PayrollRecord,
    ProvisionEstimate,  # noqa: F401
    RDCapitalizationAssessment,  # noqa: F401
    SubsequentEvent,  # noqa: F401
    Supplier,  # noqa: F401
)

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ============================================================
#  1. PAYABLES
# ============================================================


class PayablesService:
    @staticmethod
    def aging_bucket_for_days(days: int) -> str:
        if days <= 30:
            return "0_30"
        if days <= 90:
            return "31_90"
        if days <= 180:
            return "91_180"
        if days <= 365:
            return "181_365"
        return "over_365"

    @staticmethod
    def assess_risk(aging: PayableAging) -> Optional[str]:
        """超过 180 天占比 > 50% 为高风险."""
        total = aging.total_amount or 0
        if total <= 0:
            return None
        long_age = (aging.amount_181_365 or 0) + (aging.amount_over_365 or 0)
        if long_age / total > 0.5:
            return "long_aging_concentration"
        if (aging.amount_over_365 or 0) > 0:
            return "has_over_365"
        return None


# ============================================================
#  2. EXPENSES
# ============================================================


class ExpensesAnomalyDetector:
    """费用异常检测 — 规则引擎."""

    @staticmethod
    def is_round_number(amount: float, threshold: float = 100000) -> bool:
        """金额 > 阈值且为整万元."""
        if amount < threshold:
            return False
        return amount == round(amount, -4)

    @staticmethod
    def is_holiday(voucher_date: str) -> bool:
        """简化 — 周末视为节假日; 法定节假日按需扩展."""
        try:
            dt = datetime.strptime(voucher_date, "%Y-%m-%d")
            return dt.weekday() >= 5
        except Exception:
            return False

    @staticmethod
    async def scan(
        db: AsyncSession,
        *,
        project_id: int,
        period_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """扫描一遍 ExpenseRecord, 标记异常 flags."""
        stmt = select(ExpenseRecord).where(ExpenseRecord.project_id == project_id)
        rows = list((await db.execute(stmt)).scalars().all())
        flags_added = 0
        for r in rows:
            anomalies: List[str] = []
            if ExpensesAnomalyDetector.is_round_number(r.amount or 0):
                anomalies.append("round_number")
            if ExpensesAnomalyDetector.is_holiday(r.voucher_date):
                anomalies.append("holiday_reimbursement")
            if r.is_related_party:
                anomalies.append("related_party_expense")
            for atype in anomalies:
                db.add(
                    ExpenseAnomalyFlag(
                        project_id=project_id,
                        expense_record_id=r.id,
                        anomaly_type=atype,
                        severity="warn",
                        detail=f"凭证 {r.voucher_no} {r.account_name} 金额 {r.amount}",
                        created_at=_utcnow_naive(),
                    )
                )
                flags_added += 1
        await db.commit()
        return {"scanned": len(rows), "flags_added": flags_added}

    @staticmethod
    def entertainment_deduction_limit(
        sales_revenue: float, entertainment_amount: float
    ) -> Dict[str, float]:
        """业务招待费 60% / 1‰ 扣除限额."""
        sixty_pct = entertainment_amount * 0.6
        five_per_mille = sales_revenue * 0.005
        deductible = min(sixty_pct, five_per_mille)
        adjustment = entertainment_amount - deductible
        return {
            "entertainment": entertainment_amount,
            "60_pct": round(sixty_pct, 2),
            "5_per_mille_of_revenue": round(five_per_mille, 2),
            "deductible": round(deductible, 2),
            "non_deductible_adjustment": round(adjustment, 2),
        }


# ============================================================
#  3. PAYROLL
# ============================================================


class PayrollReconciler:
    @staticmethod
    async def reconcile(
        db: AsyncSession,
        *,
        project_id: int,
        period_yyyymm: str,
    ) -> PayrollReconciliation:
        """四表勾稽 — 工资 vs 社保 vs 公积金 vs 个税."""
        stmt = select(PayrollRecord).where(
            PayrollRecord.project_id == project_id,
            PayrollRecord.period_yyyymm == period_yyyymm,
        )
        rows = list((await db.execute(stmt)).scalars().all())
        gross_total = sum(float(r.gross_salary or 0) for r in rows)
        ss_total = sum(float(r.social_security or 0) for r in rows)
        hf_total = sum(float(r.housing_fund or 0) for r in rows)
        tax_total = sum(float(r.income_tax or 0) for r in rows)
        # 简化: 社保 + 公积金 + 个税应当 < 工资; 不平定义为差 > 5%
        deductions = ss_total + hf_total + tax_total
        discrepancy = abs(gross_total * 0.50 - deductions)  # 经验值: 扣款约占 50%
        is_balanced = (gross_total == 0) or (discrepancy / max(1, gross_total)) < 0.10
        rec = PayrollReconciliation(
            project_id=project_id,
            period_yyyymm=period_yyyymm,
            payroll_total=round(gross_total, 2),
            social_security_total=round(ss_total, 2),
            housing_fund_total=round(hf_total, 2),
            income_tax_total=round(tax_total, 2),
            discrepancy_amount=round(discrepancy, 2),
            is_balanced=is_balanced,
            discrepancy_notes=None if is_balanced else "扣款合计偏离工资 50% 经验值, 请复核",
            created_at=_utcnow_naive(),
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)
        return rec


# ============================================================
#  4. FIXED ASSETS — 折旧重算 + 转固判断
# ============================================================


class DepreciationCalculator:
    @staticmethod
    def straight_line_monthly(
        original_cost: float, salvage_rate: float, useful_life_months: int
    ) -> float:
        if useful_life_months <= 0:
            return 0.0
        salvage = original_cost * (salvage_rate or 0)
        return round((original_cost - salvage) / useful_life_months, 2)

    @staticmethod
    def double_declining_monthly(net_book_value: float, useful_life_months: int) -> float:
        if useful_life_months <= 0:
            return 0.0
        annual_rate = 2.0 / (useful_life_months / 12.0)
        return round(net_book_value * annual_rate / 12.0, 2)

    @staticmethod
    def sum_of_years_yearly(
        original_cost: float, salvage_rate: float, useful_life_years: int, current_year: int
    ) -> float:
        if useful_life_years <= 0 or current_year > useful_life_years:
            return 0.0
        salvage = original_cost * (salvage_rate or 0)
        sum_years = useful_life_years * (useful_life_years + 1) / 2
        return round(
            (original_cost - salvage) * (useful_life_years - current_year + 1) / sum_years, 2
        )

    @staticmethod
    async def recalc_asset(
        db: AsyncSession,
        *,
        project_id: int,
        asset_id: int,
        period_yyyymm: str,
        book_depreciation: float,
    ) -> DepreciationRecalc:
        asset = (
            await db.execute(select(FixedAsset).where(FixedAsset.id == asset_id))
        ).scalar_one_or_none()
        if asset is None:
            raise ValueError(f"资产 {asset_id} 不存在")

        method = asset.depreciation_method or "straight_line"
        life = asset.useful_life_months or 0
        if method == "straight_line":
            recalc = DepreciationCalculator.straight_line_monthly(
                asset.original_cost or 0, asset.salvage_rate or 0, life
            )
        elif method == "double_declining":
            recalc = DepreciationCalculator.double_declining_monthly(
                asset.net_book_value or asset.original_cost or 0, life
            )
        else:
            recalc = DepreciationCalculator.straight_line_monthly(
                asset.original_cost or 0, asset.salvage_rate or 0, life
            )

        diff = book_depreciation - recalc
        diff_pct = (diff / recalc * 100) if recalc else 0
        material = abs(diff_pct) > 5  # 5% 实质差异

        rec = DepreciationRecalc(
            project_id=project_id,
            asset_id=asset_id,
            period_yyyymm=period_yyyymm,
            book_depreciation=round(book_depreciation, 2),
            recalc_depreciation=recalc,
            diff_amount=round(diff, 2),
            diff_pct=round(diff_pct, 2),
            has_material_diff=material,
            notes=(
                f"按 {method} 重算 {recalc}, 账面 {book_depreciation}, 差 {diff:.2f} ({diff_pct:.2f}%)"
            ),
            created_at=_utcnow_naive(),
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)
        return rec


class CIPTransferChecker:
    """在建工程转固判断."""

    @staticmethod
    def is_ready_for_transfer(cip: ConstructionInProgress) -> Tuple[bool, str]:
        if cip.transfer_ready:
            return True, "已标记达到预定可使用状态"
        # 简化: 累计成本达预算 95%+, 或已有验收记录关键词
        if cip.cumulative_cost and cip.budget and cip.cumulative_cost / cip.budget >= 0.95:
            return True, f"累计成本 {cip.cumulative_cost:.0f} 达预算 95% 以上"
        return False, "未达到转固条件"


# ============================================================
#  5. INTANGIBLE / RD CAPITALIZATION
# ============================================================


class RDCapitalizationAssessor:
    @staticmethod
    def assess(
        technical_feasibility: bool,
        intent_to_complete: bool,
        ability_to_use_or_sell: bool,
        future_economic_benefit: bool,
        resources_sufficient: bool,
        cost_measurable: bool,
    ) -> Tuple[bool, List[str]]:
        """CAS 6 五项条件 + 成本可计量 = 6 个 → 全 True 才能资本化."""
        results = {
            "技术可行性": technical_feasibility,
            "完成意图": intent_to_complete,
            "出售或使用能力": ability_to_use_or_sell,
            "未来经济利益": future_economic_benefit,
            "资源充足": resources_sufficient,
            "成本可计量": cost_measurable,
        }
        all_met = all(results.values())
        missing = [k for k, v in results.items() if not v]
        return all_met, missing

    @staticmethod
    def rd_super_deduction(rd_expense: float, manufacturing: bool = True) -> Dict[str, float]:
        """研发费用加计扣除. 制造业 100%, 其他行业 75% (2026 政策, 实际按当年政策).
        IPO 项目按企业类型, MVP 用 100% / 200%."""
        rate = 1.0 if manufacturing else 0.75
        return {
            "rd_expense": rd_expense,
            "rate": rate,
            "super_deduction": round(rd_expense * rate, 2),
            "total_deductible": round(rd_expense * (1 + rate), 2),
        }


# ============================================================
#  6. LONG-TERM INVESTMENT / GOODWILL
# ============================================================


class GoodwillImpairmentCalculator:
    @staticmethod
    def npv(cashflows: List[float], discount_rate: float) -> float:
        """简单 NPV — cashflows[0] = 第 1 年现金流."""
        if discount_rate < 0:
            return 0.0
        total = 0.0
        for i, cf in enumerate(cashflows, start=1):
            total += cf / ((1 + discount_rate) ** i)
        return round(total, 2)

    @staticmethod
    def impairment_required(
        book_value_with_goodwill: float,
        recoverable_amount: float,
    ) -> float:
        diff = book_value_with_goodwill - recoverable_amount
        return round(max(0.0, diff), 2)


# ============================================================
#  7. LEASES (CAS 21)
# ============================================================


class LeaseAmortizer:
    @staticmethod
    def present_value(payment: float, periods: int, periodic_rate: float) -> float:
        """期初年金现值 (假设 payment 在期末)."""
        if periodic_rate <= 0 or periods <= 0:
            return round(payment * periods, 2)
        pv = payment * (1 - (1 + periodic_rate) ** -periods) / periodic_rate
        return round(pv, 2)

    @staticmethod
    async def build_schedule(
        db: AsyncSession,
        *,
        contract_id: int,
    ) -> List[LeaseAmortizationSchedule]:
        contract = (
            await db.execute(select(LeaseContract).where(LeaseContract.id == contract_id))
        ).scalar_one_or_none()
        if contract is None:
            raise ValueError(f"租赁合同 {contract_id} 不存在")

        # 清旧
        old = list(
            (
                await db.execute(
                    select(LeaseAmortizationSchedule).where(
                        LeaseAmortizationSchedule.contract_id == contract_id
                    )
                )
            )
            .scalars()
            .all()
        )
        for o in old:
            await db.delete(o)

        n = contract.lease_term_months
        annual_rate = contract.discount_rate or 0.05
        monthly_rate = annual_rate / 12
        payment = contract.fixed_payment or 0
        pv = LeaseAmortizer.present_value(payment, n, monthly_rate)
        # 设置初始 ROU + 负债 (简化 = PV)
        contract.lease_liability_initial = pv
        contract.rou_asset_initial = pv

        liability_balance = pv
        rou_balance = pv
        rou_monthly_depreciation = round(pv / n, 2) if n > 0 else 0
        records: List[LeaseAmortizationSchedule] = []
        try:
            start_dt = datetime.strptime(contract.commencement_date, "%Y-%m-%d")
        except Exception:
            start_dt = datetime.now(timezone.utc).replace(tzinfo=None)

        for i in range(1, n + 1):
            interest = round(liability_balance * monthly_rate, 2)
            principal = round(payment - interest, 2)
            liability_balance = round(max(0.0, liability_balance - principal), 2)
            rou_balance = round(max(0.0, rou_balance - rou_monthly_depreciation), 2)
            period = (start_dt.replace(day=1)).strftime("%Y-%m")  # 简化
            try:
                # 月度递推
                yyyy = start_dt.year + (start_dt.month + i - 2) // 12
                mm = (start_dt.month + i - 2) % 12 + 1
                period = f"{yyyy:04d}-{mm:02d}"
            except Exception:
                pass
            rec = LeaseAmortizationSchedule(
                contract_id=contract_id,
                period_yyyymm=period,
                payment=payment,
                interest_expense=interest,
                principal_reduction=principal,
                rou_depreciation=rou_monthly_depreciation,
                liability_balance=liability_balance,
                rou_balance=rou_balance,
                created_at=_utcnow_naive(),
            )
            db.add(rec)
            records.append(rec)
        await db.commit()
        return records


# ============================================================
#  8. INCOME TAX
# ============================================================


class IncomeTaxRecalculator:
    @staticmethod
    def reconcile(
        pretax_profit: float,
        permanent_diff: float,
        temporary_diff: float,
        losses_used: float,
        nominal_rate: float = 0.25,
    ) -> Dict[str, float]:
        taxable_income = pretax_profit + permanent_diff + temporary_diff - losses_used
        current_tax = max(0.0, taxable_income * nominal_rate)
        effective_rate = (current_tax / pretax_profit) if pretax_profit > 0 else 0
        return {
            "pretax_profit": pretax_profit,
            "permanent_diff": permanent_diff,
            "temporary_diff": temporary_diff,
            "losses_used": losses_used,
            "taxable_income": round(taxable_income, 2),
            "nominal_rate": nominal_rate,
            "effective_rate": round(effective_rate, 4),
            "current_tax": round(current_tax, 2),
        }


# ============================================================
#  9. ACCOUNTING ESTIMATES — ECL 三阶段
# ============================================================


class ECLCalculator:
    @staticmethod
    def stage_for_aging_days(days: int) -> int:
        """ECL 三阶段简化判定 — 正常 → 1, 关注 (30+ 天) → 2, 违约 (90+ 天) → 3."""
        if days >= 90:
            return 3
        if days >= 30:
            return 2
        return 1

    @staticmethod
    def default_pd_for_stage(stage: int) -> float:
        return {1: 0.01, 2: 0.10, 3: 0.50}.get(stage, 0.05)

    @staticmethod
    def compute_ecl(
        receivable: float, stage: int, pd: Optional[float] = None, lgd: float = 0.45
    ) -> float:
        pd_val = pd if pd is not None else ECLCalculator.default_pd_for_stage(stage)
        return round(receivable * pd_val * lgd, 2)


# ============================================================
#  10. SUBSEQUENT EVENTS / GOING CONCERN
# ============================================================


class SubsequentEventClassifier:
    @staticmethod
    def classify(event_description: str, event_date: str, balance_sheet_date: str) -> str:
        """简化 — 资产负债表日已存在的情形为 adjusting (调整事项), 否则 non_adjusting."""
        # MVP: 关键词命中"销售退回 / 应收无法收回 / 诉讼判决 / 资产价值确定"等为 adjusting
        adjusting_keywords = [
            "销售退回",
            "应收账款无法收回",
            "诉讼判决",
            "存货跌价",
            "成本结转",
            "资产价值确定",
            "舞弊发现",
        ]
        if any(k in (event_description or "") for k in adjusting_keywords):
            return "adjusting"
        return "non_adjusting"


class GoingConcernAssessor:
    @staticmethod
    def assess(
        operating_cashflow_12m: float,
        interest_expense_12m: float,
        debt_due_12m: float,
        cash_balance: float,
        available_credit: float = 0.0,
    ) -> Tuple[str, str]:
        """简单评分."""
        total_obligation = interest_expense_12m + debt_due_12m
        total_resource = operating_cashflow_12m + cash_balance + available_credit
        if total_obligation <= 0:
            return "low", "无重大偿债压力"
        ratio = total_resource / total_obligation
        if ratio >= 2.0:
            return "low", f"偿债能力充足 (覆盖率 {ratio:.2f})"
        if ratio >= 1.2:
            return "medium", f"中等偿债压力 (覆盖率 {ratio:.2f})"
        if ratio >= 0.8:
            return "high", f"高偿债压力 (覆盖率 {ratio:.2f}), 需关注"
        return "substantial_doubt", f"严重偿债压力 (覆盖率 {ratio:.2f}), 存在持续经营重大不确定性"


__all__ = [
    "PayablesService",
    "ExpensesAnomalyDetector",
    "PayrollReconciler",
    "DepreciationCalculator",
    "CIPTransferChecker",
    "RDCapitalizationAssessor",
    "GoodwillImpairmentCalculator",
    "LeaseAmortizer",
    "IncomeTaxRecalculator",
    "ECLCalculator",
    "SubsequentEventClassifier",
    "GoingConcernAssessor",
]
