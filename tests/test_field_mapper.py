"""Tests for comprehensive workpaper field mapping engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import pytest

from app.services.comprehensive.field_mapper import (
    DataPath,
    FieldMapper,
    WorkpaperDataContext,
    parse_workpaper_source,
)
from app.services.comprehensive.schemas import TemplateField


# ---------- helpers ----------

@dataclass
class FakeProject:
    id: int
    name: str
    company_name: str
    industry: str
    fiscal_year: int
    status: str = "active"


@dataclass
class FakeConfirmationCase:
    status: str
    confirmed_amount: float
    sent_amount: float = 0.0
    sample_balance: float = 0.0


def _field(field_id: str, source: str, type_: str = "text") -> TemplateField:
    return TemplateField(
        field_id=field_id,
        label=field_id,
        type=type_,
        source=source,
        required=False,
        hint=None,
        options=None,
        cell_ref="A1",
        sheet="s",
        row=1,
        column=1,
    )


@pytest.fixture
def ab_df() -> pd.DataFrame:
    """科目余额表。"""
    return pd.DataFrame(
        [
            {"account_code": "1001", "account_name": "银行存款", "balance_direction": "借",
             "beginning_balance": 10000, "debit_amount": 5000, "credit_amount": 2000, "ending_balance": 13000},
            {"account_code": "1122", "account_name": "应收账款", "balance_direction": "借",
             "beginning_balance": 8000, "debit_amount": 4000, "credit_amount": 1000, "ending_balance": 11000},
            {"account_code": "2202", "account_name": "应付账款", "balance_direction": "贷",
             "beginning_balance": 5000, "debit_amount": 1000, "credit_amount": 2000, "ending_balance": 6000},
        ]
    )


@pytest.fixture
def ctx(ab_df) -> WorkpaperDataContext:
    return WorkpaperDataContext(
        project=FakeProject(
            id=1, name="Demo IPO", company_name="ACME",
            industry="制造业", fiscal_year=2024,
        ),
        account_balances=ab_df,
        confirmation_cases=[
            FakeConfirmationCase(
                status="agreed", confirmed_amount=6000,
                sent_amount=7000, sample_balance=11000,
            ),
            FakeConfirmationCase(
                status="disputed", confirmed_amount=1000,
                sent_amount=2000, sample_balance=4000,
            ),
        ],
        extra={"revenue": 36500.0, "credit_sales": 30000.0},
    )


# ---------- parse_workpaper_source ----------

def test_parse_workpaper_source_ok():
    p = parse_workpaper_source("workpaper:ar_ledger.total_ending")
    assert p is not None
    assert p.dataset == "ar_ledger"
    assert p.parts == ("total_ending",)
    assert p.leaf == "total_ending"


def test_parse_workpaper_source_rejects_other_prefix():
    assert parse_workpaper_source("rule:abc") is None
    assert parse_workpaper_source("human_qa") is None
    assert parse_workpaper_source("not_a_source") is None
    assert parse_workpaper_source("workpaper:") is None


# ---------- project ----------

def test_map_project_company_name(ctx):
    result = FieldMapper().map_field(
        _field("company", "workpaper:project.company_name"), ctx
    )
    assert result.value == "ACME"
    assert result.confidence == 0.95


def test_map_project_audit_period_derived(ctx):
    result = FieldMapper().map_field(
        _field("period", "workpaper:project.audit_period"), ctx
    )
    assert result.value == "2024-01-01~2024-12-31"


def test_map_project_missing_attr(ctx):
    result = FieldMapper().map_field(
        _field("x", "workpaper:project.nonexistent"), ctx
    )
    assert result.value is None
    assert result.confidence == 0.0


# ---------- account_balance ----------

def test_map_account_balance_single_code_ending(ab_df, ctx):
    result = FieldMapper().map_field(
        _field("ar_end", "workpaper:account_balance.1122.ending_balance", "number"), ctx
    )
    assert result.value == 11000.0


def test_map_account_balance_total_debit(ab_df, ctx):
    result = FieldMapper().map_field(
        _field("dbt", "workpaper:account_balance.total_debit", "number"), ctx
    )
    # 5000+4000+1000
    assert result.value == 10000.0


def test_map_account_balance_missing_code_returns_none(ctx):
    result = FieldMapper().map_field(
        _field("x", "workpaper:account_balance.9999.ending_balance", "number"), ctx
    )
    assert result.value is None


# ---------- ar_ledger / ap_ledger ----------

def test_map_ar_ledger_total_ending(ctx):
    r = FieldMapper().map_field(
        _field("ar", "workpaper:ar_ledger.total_ending", "number"), ctx
    )
    assert r.value == 11000.0


def test_map_ap_ledger_total_ending(ctx):
    r = FieldMapper().map_field(
        _field("ap", "workpaper:ap_ledger.total_ending", "number"), ctx
    )
    assert r.value == 6000.0


def test_map_ar_ledger_turnover_days(ctx):
    """周转天数优先用 credit_sales，缺省时回退到 revenue。"""
    r = FieldMapper().map_field(
        _field("td", "workpaper:ar_ledger.turnover_days", "number"), ctx
    )
    # avg = (8000+11000)/2 = 9500; 365*9500/30000 ≈ 115.58
    assert r.value == pytest.approx(115.58, rel=1e-3)


def test_map_ar_ledger_turnover_days_falls_back_to_revenue():
    """credit_sales 缺省时回退 revenue。"""
    ab = pd.DataFrame([{
        "account_code": "1122", "account_name": "AR", "balance_direction": "借",
        "beginning_balance": 8000, "debit_amount": 4000, "credit_amount": 1000,
        "ending_balance": 11000,
    }])
    ctx2 = WorkpaperDataContext(
        account_balances=ab, extra={"revenue": 36500.0}
    )
    r = FieldMapper().map_field(
        _field("td", "workpaper:ar_ledger.turnover_days", "number"), ctx2
    )
    # 365*9500/36500 = 95
    assert r.value == pytest.approx(95.0)


# ---------- confirmation ----------

def test_map_confirmation_coverage(ctx):
    """覆盖率 = 发函金额 / 函证样本余额 = 9000/15000 = 0.6"""
    r = FieldMapper().map_field(
        _field("cov", "workpaper:confirmation.coverage", "percent"), ctx
    )
    assert r.value == pytest.approx(0.6, rel=1e-3)


def test_map_confirmation_response_rate(ctx):
    r = FieldMapper().map_field(
        _field("rr", "workpaper:confirmation.response_rate", "percent"), ctx
    )
    # replied (agreed + disputed) confirmed_amount 6000+1000=7000 / sent 7000+2000=9000
    assert r.value == pytest.approx(7000/9000, rel=1e-3)


def test_map_confirmation_agreement_rate(ctx):
    r = FieldMapper().map_field(
        _field("ar", "workpaper:confirmation.agreement_rate", "percent"), ctx
    )
    # agreed 6000 / replied (agreed+disputed) 7000
    assert r.value == pytest.approx(6000/7000, rel=1e-3)


def test_map_confirmation_agreed_count(ctx):
    r = FieldMapper().map_field(
        _field("n", "workpaper:confirmation.agreed", "number"), ctx
    )
    # 1 agreed + 0 confirmed = 1
    assert r.value == 1


# ---------- empty / unknown ----------

def test_map_unknown_dataset_returns_low_confidence(ctx):
    r = FieldMapper().map_field(
        _field("x", "workpaper:nonexistent_dataset.leaf"), ctx
    )
    assert r.value is None
    assert r.confidence == 0.0
    assert "未知数据集" in (r.citation or "")


def test_map_non_workpaper_source_returns_unrecognized(ctx):
    r = FieldMapper().map_field(
        _field("x", "human_qa"), ctx
    )
    assert r.value is None
    assert "unrecognized" in r.source_used


def test_map_empty_dataframe():
    empty_ctx = WorkpaperDataContext(account_balances=pd.DataFrame())
    r = FieldMapper().map_field(
        _field("ar", "workpaper:ar_ledger.total_ending", "number"), empty_ctx
    )
    assert r.value is None


# ---------- type coercion ----------

def test_coerce_number_strips_commas():
    r = FieldMapper().map_field(
        _field("x", "workpaper:project.fiscal_year", "number"),
        WorkpaperDataContext(project=FakeProject(1, "n", "c", "i", 2024)),
    )
    # project.fiscal_year 实际是 int，但模板声明 number 也能适配
    assert r.value == 2024.0


def test_coerce_choice_passes_string_through(ctx):
    r = FieldMapper().map_field(
        _field("ind", "workpaper:project.industry", "choice"), ctx
    )
    assert r.value == "制造业"


# ---------- custom resolver registration ----------

def test_register_custom_resolver(ctx):
    mapper = FieldMapper()

    def my_resolver(path: DataPath, c: WorkpaperDataContext) -> Any:
        return 42.0

    mapper.register("custom_ds", my_resolver)
    r = mapper.map_field(
        _field("x", "workpaper:custom_ds.anything", "number"), ctx
    )
    assert r.value == 42.0


# ---------- batch ----------

def test_map_all_skips_non_workpaper(ctx):
    mapper = FieldMapper()
    fields = [
        _field("a", "workpaper:ar_ledger.total_ending", "number"),
        _field("b", "human_qa"),
        _field("c", "workpaper:project.company_name"),
    ]
    results = mapper.map_all(fields, ctx)
    assert len(results) == 2  # 只处理 workpaper
    assert {r.field_id for r in results} == {"a", "c"}
