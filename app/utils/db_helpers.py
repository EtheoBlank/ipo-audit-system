"""Shared database-to-DataFrame conversion helpers.

Avoids repeating the same ORM → pandas DataFrame mapping across multiple
API modules.
"""

from __future__ import annotations

import pandas as pd
from app.models.db_models import AccountBalance


def account_balances_to_df(balances: list[AccountBalance]) -> pd.DataFrame:
    """Convert a list of AccountBalance ORM objects to a DataFrame.

    Columns: account_code, account_name, balance_direction,
             beginning_balance, debit_amount, credit_amount, ending_balance.
    """
    return pd.DataFrame([{
        "account_code": ab.account_code,
        "account_name": ab.account_name,
        "balance_direction": ab.balance_direction,
        "beginning_balance": ab.beginning_balance,
        "debit_amount": ab.debit_amount,
        "credit_amount": ab.credit_amount,
        "ending_balance": ab.ending_balance,
    } for ab in balances])
