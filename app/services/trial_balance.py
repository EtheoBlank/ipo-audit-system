"""Trial balance service for IPO Audit System."""
import pandas as pd
from typing import Dict, List, Tuple, Optional


class TrialBalanceService:
    """Service for trial balance calculations and validations."""

    @staticmethod
    def check_balance(
        account_balances: pd.DataFrame,
        consolidation_balances: Optional[pd.DataFrame] = None
    ) -> Dict:
        """Check if trial balance is balanced.

        Args:
            account_balances: DataFrame with account balance data (单体报表)
            consolidation_balances: Optional DataFrame with consolidation balance data (合并报表)

        Returns:
            Dictionary with balance check results
        """
        # Calculate totals by balance direction
        debit_balances = account_balances[account_balances["balance_direction"] == "借"]
        credit_balances = account_balances[account_balances["balance_direction"] == "贷"]

        total_debit_begin = debit_balances["beginning_balance"].sum()
        total_credit_begin = credit_balances["beginning_balance"].sum()

        total_debit_current = debit_balances["debit_amount"].sum()
        total_credit_current = credit_balances["credit_amount"].sum()

        total_debit_ending = debit_balances["ending_balance"].sum()
        total_credit_ending = credit_balances["ending_balance"].sum()

        # Check balance
        beginning_diff = abs(total_debit_begin - total_credit_begin)
        current_diff = abs(total_debit_current - total_credit_current)
        ending_diff = abs(total_debit_ending - total_credit_ending)

        is_balanced = (beginning_diff < 0.01 and
                      current_diff < 0.01 and
                      ending_diff < 0.01)

        result = {
            "is_balanced": is_balanced,
            "standalone": {
                "beginning": {"debit": total_debit_begin, "credit": total_credit_begin, "difference": beginning_diff},
                "current_period": {"debit": total_debit_current, "credit": total_credit_current, "difference": current_diff},
                "ending": {"debit": total_debit_ending, "credit": total_credit_ending, "difference": ending_diff},
            },
        }

        # Consolidation balance check if provided
        if consolidation_balances is not None:
            cons_debit = consolidation_balances[consolidation_balances["balance_direction"] == "借"]
            cons_credit = consolidation_balances[consolidation_balances["balance_direction"] == "贷"]

            cons_debit_end = cons_debit["ending_balance"].sum()
            cons_credit_end = cons_credit["ending_balance"].sum()
            cons_diff = abs(cons_debit_end - cons_credit_end)

            cons_is_balanced = cons_diff < 0.01

            result["consolidation"] = {
                "is_balanced": cons_is_balanced,
                "ending": {"debit": cons_debit_end, "credit": cons_credit_end, "difference": cons_diff},
            }

            # Calculate elimination entries (抵销分录)
            #合并报表与单体报表的差异 = 内部交易抵销金额
            internal_elimination = {
                "asset_elimination": total_debit_ending - cons_debit_end,
                "liability_elimination": total_credit_ending - cons_credit_end,
            }
            result["consolidation"]["internal_elimination"] = internal_elimination

        return result

    @staticmethod
    def get_account_summary(account_balances: pd.DataFrame) -> List[Dict]:
        """Get account balance summary by account code.

        Args:
            account_balances: DataFrame with account balance data

        Returns:
            List of account summaries
        """
        summary = account_balances.groupby(["account_code", "account_name", "balance_direction"]).agg({
            "beginning_balance": "sum",
            "debit_amount": "sum",
            "credit_amount": "sum",
            "ending_balance": "sum",
        }).reset_index()

        return summary.to_dict("records")

    @staticmethod
    def identify_unusual_balances(account_balances: pd.DataFrame, threshold: float = 0.01) -> List[Dict]:
        """Identify unusual account balances that may need attention.

        Args:
            account_balances: DataFrame with account balance data
            threshold: Threshold ratio for unusual balance detection

        Returns:
            List of unusual balance records
        """
        unusual = []

        for _, row in account_balances.iterrows():
            issues = []

            # Check for large ending balance with no activity
            if row["ending_balance"] != 0 and row["debit_amount"] == 0 and row["credit_amount"] == 0:
                issues.append("期末余额有数据但本期无发生额")

            # Check for abnormal balance direction
            if row["balance_direction"] == "借" and row["ending_balance"] < 0:
                issues.append("借方科目出现贷方余额")
            elif row["balance_direction"] == "贷" and row["ending_balance"] > 0:
                issues.append("贷方科目出现借方余额")

            # Check for round numbers that might indicate estimation
            if row["ending_balance"] != 0 and row["ending_balance"] % 10000 == 0:
                issues.append("期末余额为整万，可能存在估计")

            if issues:
                unusual.append({
                    "account_code": row["account_code"],
                    "account_name": row["account_name"],
                    "ending_balance": row["ending_balance"],
                    "issues": issues,
                })

        return unusual

    @staticmethod
    def reconcile_with_bank(account_balances: pd.DataFrame, bank_statements: pd.DataFrame) -> Dict:
        """Reconcile account balances with bank statements.

        Args:
            account_balances: DataFrame with account balance data
            bank_statements: DataFrame with bank statement data

        Returns:
            Reconciliation report
        """
        # Find bank-related accounts (银行存款)
        bank_accounts = account_balances[
            account_balances["account_name"].str.contains("银行存款", na=False)
        ]

        # Sum bank statement balances
        total_bank_balance = bank_statements["balance"].iloc[-1] if len(bank_statements) > 0 else 0

        # Find difference
        account_bank_total = bank_accounts["ending_balance"].sum()
        difference = abs(account_bank_total - total_bank_balance)

        return {
            "account_total": account_bank_total,
            "bank_statement_total": total_bank_balance,
            "difference": difference,
            "is_reconciled": difference < 0.01,
            "bank_account_count": len(bank_accounts),
        }