"""试算平衡系统 - 第五阶段."""
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class BalanceCheckResult:
    """平衡检查结果."""
    is_balanced: bool
    total_debit: float
    total_credit: float
    difference: float
    details: Dict


class TrialBalanceEngine:
    """增强版试算平衡引擎."""

    def __init__(self):
        self.tolerance = 0.01  # 容差

    def check_balance(self, account_balances: pd.DataFrame) -> BalanceCheckResult:
        """检查试算平衡."""
        debit_accounts = account_balances[account_balances["balance_direction"] == "借"]
        credit_accounts = account_balances[account_balances["balance_direction"] == "贷"]

        total_debit = debit_accounts["ending_balance"].sum()
        total_credit = credit_accounts["ending_balance"].sum()
        difference = abs(total_debit - total_credit)

        return BalanceCheckResult(
            is_balanced=difference < self.tolerance,
            total_debit=total_debit,
            total_credit=total_credit,
            difference=difference,
            details={
                "beginning": {
                    "debit": debit_accounts["beginning_balance"].sum(),
                    "credit": credit_accounts["beginning_balance"].sum(),
                },
                "current_period": {
                    "debit": account_balances["debit_amount"].sum(),
                    "credit": account_balances["credit_amount"].sum(),
                },
            },
        )

    def reconcile_with_bank(self, account_balances: pd.DataFrame, bank_statements: pd.DataFrame) -> Dict:
        """银行对账."""
        bank_accounts = account_balances[account_balances["account_name"].str.contains("银行存款", na=False)]
        account_total = bank_accounts["ending_balance"].sum()
        statement_total = bank_statements["balance"].iloc[-1] if len(bank_statements) > 0 else 0

        difference = abs(account_total - statement_total)

        return {
            "account_total": account_total,
            "statement_total": statement_total,
            "difference": difference,
            "is_reconciled": difference < self.tolerance,
            "adjustments_needed": self._generate_bank_reconciliation_adjustments(difference),
        }

    def _generate_bank_reconciliation_adjustments(self, difference: float) -> List[Dict]:
        """生成银行对账调整建议."""
        if difference < self.tolerance:
            return []
        return [
            {"type": "未达账项", "description": "检查是否存在银行已收企业未收款项", "amount": difference / 2},
            {"type": "未达账项", "description": "检查是否存在企业已付银行未付款项", "amount": difference / 2},
        ]

    def generate_adjustment_suggestions(self, imbalance: float, account_balances: pd.DataFrame) -> List[Dict]:
        """生成调整分录建议."""
        suggestions = []
        if abs(imbalance) < self.tolerance:
            return suggestions

        # 查找可疑科目
        suspicious_accounts = account_balances[
            (account_balances["ending_balance"].abs() > 1000000) &
            (account_balances["debit_amount"] == 0) &
            (account_balances["credit_amount"] == 0)
        ]

        if len(suspicious_accounts) > 0:
            suggestions.append({
                "type": "检查无发生额大额科目",
                "accounts": suspicious_accounts["account_code"].tolist(),
                "description": "存在大额余额但无本期发生额的科目，需核实真实性",
            })

        suggestions.append({
            "type": "差异调整",
            "description": f"试算不平衡，差异额{imbalance:,.2f}，建议逐科目核对",
        })

        return suggestions

    def check_account_reconciliation(self, account_balances: pd.DataFrame, chronological_accounts: pd.DataFrame) -> List[Dict]:
        """核对科目余额与序时账一致性."""
        results = []

        # 按科目汇总序时账
        if "account_code" in chronological_accounts.columns:
            chronological_summary = chronological_accounts.groupby("account_code").agg({
                "debit_amount": "sum",
                "credit_amount": "sum",
            }).reset_index()

            # 比较
            for _, ca_summary in chronological_summary.iterrows():
                code = ca_summary["account_code"]
                ca_debit = ca_summary["debit_amount"]
                ca_credit = ca_summary["credit_amount"]

                ab_match = account_balances[account_balances["account_code"] == code]
                if len(ab_match) > 0:
                    ab_debit = ab_match["debit_amount"].iloc[0]
                    ab_credit = ab_match["credit_amount"].iloc[0]

                    if abs(ca_debit - ab_debit) > self.tolerance or abs(ca_credit - ab_credit) > self.tolerance:
                        results.append({
                            "account_code": code,
                            "account_name": ab_match["account_name"].iloc[0] if "account_name" in ab_match.columns else "",
                            "chronological_debit": ca_debit,
                            "chronological_credit": ca_credit,
                            "balance_debit": ab_debit,
                            "balance_credit": ab_credit,
                            "status": "不一致",
                        })

        return results

    def generate_balance_report(self, account_balances: pd.DataFrame, project_info: Dict) -> Dict:
        """生成试算平衡报告."""
        result = self.check_balance(account_balances)

        # 按资产/负债分类汇总
        asset_total = account_balances[account_balances["balance_direction"] == "借"]["ending_balance"].sum()
        liability_total = account_balances[account_balances["balance_direction"] == "贷"]["ending_balance"].sum()

        # 大额科目
        large_accounts = account_balances.nlargest(10, "ending_balance")[["account_code", "account_name", "ending_balance", "balance_direction"]]

        # 异常检测
        anomalies = []
        for _, ab in account_balances.iterrows():
            if ab["balance_direction"] == "借" and ab["ending_balance"] < 0:
                anomalies.append({"code": ab["account_code"], "name": ab["account_name"], "type": "贷方余额"})
            elif ab["balance_direction"] == "贷" and ab["ending_balance"] > 0:
                anomalies.append({"code": ab["account_code"], "name": ab["account_name"], "type": "借方余额"})

        return {
            "project_name": project_info.get("name", ""),
            "company_name": project_info.get("company_name", ""),
            "fiscal_year": project_info.get("fiscal_year", ""),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "balance_status": "平衡" if result.is_balanced else "不平衡",
            "total_assets": asset_total,
            "total_liabilities": liability_total,
            "difference": result.difference,
            "large_accounts": large_accounts.to_dict("records"),
            "anomalies": anomalies,
            "reconciliation_status": "需核对" if anomalies else "正常",
        }


class ReportConsistencyChecker:
    """报表一致性检查器."""

    def __init__(self):
        self.tolerance = 0.01

    def check_balance_sheet_trial_balance_consistency(
        self, balance_sheet: Dict, trial_balance: pd.DataFrame
    ) -> List[Dict]:
        """核对资产负债表与试算平衡表一致性."""
        issues = []

        # 核对货币资金
        cash_in_bs = balance_sheet.get("货币资金", 0)
        cash_in_tb = trial_balance[trial_balance["account_name"].str.contains("银行存款|库存现金", na=False)]["ending_balance"].sum()
        if abs(cash_in_bs - cash_in_tb) > self.tolerance:
            issues.append({
                "item": "货币资金",
                "balance_sheet": cash_in_bs,
                "trial_balance": cash_in_tb,
                "difference": cash_in_bs - cash_in_tb,
            })

        # 核对应收账款
        ar_in_bs = balance_sheet.get("应收账款", 0)
        ar_in_tb = trial_balance[trial_balance["account_name"].str.contains("应收账款", na=False)]["ending_balance"].sum()
        if abs(ar_in_bs - ar_in_tb) > self.tolerance:
            issues.append({
                "item": "应收账款",
                "balance_sheet": ar_in_bs,
                "trial_balance": ar_in_tb,
                "difference": ar_in_bs - ar_in_tb,
            })

        # 核对固定资产
        fa_in_bs = balance_sheet.get("固定资产", 0)
        fa_in_tb = trial_balance[trial_balance["account_name"].str.contains("固定资产", na=False)]["ending_balance"].sum()
        if abs(fa_in_bs - fa_in_tb) > self.tolerance:
            issues.append({
                "item": "固定资产",
                "balance_sheet": fa_in_bs,
                "trial_balance": fa_in_tb,
                "difference": fa_in_bs - fa_in_tb,
            })

        return issues

    def check_income_statement_trial_balance_consistency(
        self, income_statement: Dict, trial_balance: pd.DataFrame
    ) -> List[Dict]:
        """核对利润表与试算平衡表一致性."""
        issues = []

        # 核对营业收入
        revenue_in_is = income_statement.get("营业收入", 0)
        revenue_in_tb = trial_balance[trial_balance["account_name"].str.contains("主营业务收入|其他业务收入", na=False)]["credit_amount"].sum()
        if abs(revenue_in_is - revenue_in_tb) > self.tolerance:
            issues.append({
                "item": "营业收入",
                "income_statement": revenue_in_is,
                "trial_balance": revenue_in_tb,
                "difference": revenue_in_is - revenue_in_tb,
            })

        # 核对营业成本
        cost_in_is = income_statement.get("营业成本", 0)
        cost_in_tb = trial_balance[trial_balance["account_name"].str.contains("主营业务成本", na=False)]["debit_amount"].sum()
        if abs(cost_in_is - cost_in_tb) > self.tolerance:
            issues.append({
                "item": "营业成本",
                "income_statement": cost_in_is,
                "trial_balance": cost_in_tb,
                "difference": cost_in_is - cost_in_tb,
            })

        return issues

    def generate_consistency_report(self, issues: List[Dict]) -> Dict:
        """生成一致性报告."""
        return {
            "is_consistent": len(issues) == 0,
            "issue_count": len(issues),
            "issues": issues,
            "recommendations": [
                "如有不一致，请检查报表取数口径是否一致",
                "核对是否存在跨期调整未入账",
                "检查明细表与总账是否脱节",
            ] if issues else [],
        }