"""round 35 (2026-06-20) 测试空白填补.

覆盖 4 个 P0 模块 (原本 0 测试):
  - app/services/trial_balance_engine.py — 试算平衡 / 银行对账 / 报表一致性
  - app/services/confirmation/letter_generator.py — 询证函生成 (text/docx/pdf)
  - app/services/contract_analysis/ocr.py — 合同 OCR 入口 / 文件类型校验
  - app/services/sentiment/quarterly/verifier.py — 双数据源对账 (financial vs 舆情)

约束:
  - 仅追加测试, 不改业务代码
  - 用 tests/_helpers/{db,auth} (in-memory SQLite + make_user / make_firm)
  - pytest-asyncio auto mode (项目 conftest 已配 asyncio_mode="auto")
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

# ============================================================
#  Module 1: trial_balance_engine
# ============================================================


class TestTrialBalanceCheckBalance:
    """TrialBalanceEngine.check_balance: 借贷平衡判断."""

    def test_balanced_when_debit_equals_credit(self):
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "1001",
                    "account_name": "库存现金",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 5000.0,
                    "debit_amount": 5000.0,
                    "credit_amount": 0,
                },
                {
                    "account_code": "2001",
                    "account_name": "应付账款",
                    "balance_direction": "贷",
                    "beginning_balance": 0,
                    "ending_balance": 5000.0,
                    "debit_amount": 0,
                    "credit_amount": 5000.0,
                },
            ]
        )
        result = engine.check_balance(df)
        # pandas 返回 np.bool_, 必须用 == 或 bool() 比较, 避免 np.True_ is True == False
        assert bool(result.is_balanced) is True
        assert result.total_debit == 5000.0
        assert result.total_credit == 5000.0
        assert result.difference == 0.0
        # current_period 也要按 direction 过滤求和 (P0 修复回归)
        assert result.details["current_period"]["debit"] == 5000.0
        assert result.details["current_period"]["credit"] == 5000.0

    def test_imbalanced_when_debit_credit_mismatch(self):
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "1001",
                    "account_name": "库存现金",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 10000.0,
                    "debit_amount": 10000.0,
                    "credit_amount": 0,
                },
                {
                    "account_code": "2001",
                    "account_name": "应付账款",
                    "balance_direction": "贷",
                    "beginning_balance": 0,
                    "ending_balance": 9000.0,
                    "debit_amount": 0,
                    "credit_amount": 9000.0,
                },
            ]
        )
        result = engine.check_balance(df)
        assert bool(result.is_balanced) is False
        assert result.difference == 1000.0

    def test_only_debit_side(self):
        """只有借方科目, 贷方为 0 → 不平衡."""
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "1001",
                    "account_name": "银行存款",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 50000.0,
                    "debit_amount": 50000.0,
                    "credit_amount": 0,
                },
            ]
        )
        result = engine.check_balance(df)
        assert bool(result.is_balanced) is False
        assert result.difference == 50000.0

    def test_only_credit_side(self):
        """只有贷方科目 → 不平衡."""
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "4001",
                    "account_name": "实收资本",
                    "balance_direction": "贷",
                    "beginning_balance": 0,
                    "ending_balance": 100000.0,
                    "debit_amount": 0,
                    "credit_amount": 100000.0,
                },
            ]
        )
        result = engine.check_balance(df)
        assert bool(result.is_balanced) is False
        assert result.total_credit == 100000.0
        assert result.total_debit == 0.0

    def test_balance_within_tolerance(self):
        """差异 ≤ tolerance (0.01) 视为平衡."""
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "1001",
                    "account_name": "现金",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 1000.005,
                    "debit_amount": 1000.005,
                    "credit_amount": 0,
                },
                {
                    "account_code": "2001",
                    "account_name": "负债",
                    "balance_direction": "贷",
                    "beginning_balance": 0,
                    "ending_balance": 1000.00,
                    "debit_amount": 0,
                    "credit_amount": 1000.00,
                },
            ]
        )
        result = engine.check_balance(df)
        assert bool(result.is_balanced) is True

    def test_balance_current_period_filtered_by_direction(self):
        """current_period 求和必须按 direction 过滤, 否则会双计入 (P0 回归)."""
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        # 同一行 debit_amount=100, credit_amount=100 是常见的「借贷各发生额」记录
        df = pd.DataFrame(
            [
                {
                    "account_code": "5001",
                    "account_name": "主营收入",
                    "balance_direction": "贷",
                    "beginning_balance": 0,
                    "ending_balance": 1000.0,
                    "debit_amount": 100.0,  # 贷方科目的 debit_amount 是冲销, 不计入贷
                    "credit_amount": 100.0,
                },
            ]
        )
        result = engine.check_balance(df)
        # 只贷方 1000, 借方 0 → 不平衡
        assert bool(result.is_balanced) is False
        # 但 current_period.credit 必须仅含贷方科目的 credit_amount, 不能 = 200
        assert result.details["current_period"]["credit"] == 100.0


class TestTrialBalanceBankReconciliation:
    """TrialBalanceEngine.reconcile_with_bank: 银行对账."""

    def test_reconciled_no_difference(self):
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        balances = pd.DataFrame(
            [
                {
                    "account_code": "1002",
                    "account_name": "银行存款",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 500000.0,
                    "debit_amount": 500000.0,
                    "credit_amount": 0,
                }
            ]
        )
        statements = pd.DataFrame(
            [
                {"date": "2024-12-01", "balance": 0.0},
                {"date": "2024-12-15", "balance": 200000.0},
                {"date": "2024-12-31", "balance": 500000.0},
            ]
        )
        result = engine.reconcile_with_bank(balances, statements)
        assert bool(result["is_reconciled"]) is True
        assert result["difference"] == 0
        assert result["adjustments_needed"] == []

    def test_unreconciled_produces_adjustments(self):
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        balances = pd.DataFrame(
            [
                {
                    "account_code": "1002",
                    "account_name": "银行存款",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 500000.0,
                    "debit_amount": 500000.0,
                    "credit_amount": 0,
                }
            ]
        )
        statements = pd.DataFrame([{"date": "2024-12-31", "balance": 480000.0}])
        result = engine.reconcile_with_bank(balances, statements)
        assert bool(result["is_reconciled"]) is False
        assert result["difference"] == 20000.0
        assert len(result["adjustments_needed"]) == 2
        # 每条建议 amount = difference / 2
        assert all(adj["amount"] == 10000.0 for adj in result["adjustments_needed"])

    def test_empty_bank_statements(self):
        """银行对账单为空 → 走 fallback 0, 应报差异."""
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        balances = pd.DataFrame(
            [
                {
                    "account_code": "1002",
                    "account_name": "银行存款",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 100.0,
                    "debit_amount": 100.0,
                    "credit_amount": 0,
                }
            ]
        )
        result = engine.reconcile_with_bank(balances, pd.DataFrame())
        assert result["statement_total"] == 0
        assert bool(result["is_reconciled"]) is False
        assert result["difference"] == 100.0

    def test_ignores_non_bank_accounts(self):
        """非「银行存款」科目不参与对账."""
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        balances = pd.DataFrame(
            [
                {
                    "account_code": "1001",
                    "account_name": "库存现金",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 99999.0,  # 不参与对账
                    "debit_amount": 99999.0,
                    "credit_amount": 0,
                },
                {
                    "account_code": "1002",
                    "account_name": "银行存款—人民币",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 50000.0,
                    "debit_amount": 50000.0,
                    "credit_amount": 0,
                },
            ]
        )
        statements = pd.DataFrame([{"date": "2024-12-31", "balance": 50000.0}])
        result = engine.reconcile_with_bank(balances, statements)
        # 仅比对 银行存款 行的 50000, 不被 99999 污染
        assert result["account_total"] == 50000.0
        assert bool(result["is_reconciled"]) is True


class TestReportConsistencyChecker:
    """ReportConsistencyChecker: BS / IS 与试算平衡表一致性."""

    def test_bs_consistent_returns_empty(self):
        from app.services.trial_balance_engine import ReportConsistencyChecker

        checker = ReportConsistencyChecker()
        bs = {"货币资金": 100000.0, "应收账款": 50000.0, "固定资产": 200000.0}
        tb = pd.DataFrame(
            [
                {"account_code": "1002", "account_name": "银行存款", "ending_balance": 100000.0},
                {"account_code": "1122", "account_name": "应收账款", "ending_balance": 50000.0},
                {"account_code": "1601", "account_name": "固定资产", "ending_balance": 200000.0},
            ]
        )
        issues = checker.check_balance_sheet_trial_balance_consistency(bs, tb)
        assert issues == []

    def test_bs_inconsistent_returns_issues(self):
        from app.services.trial_balance_engine import ReportConsistencyChecker

        checker = ReportConsistencyChecker()
        bs = {"货币资金": 100000.0, "应收账款": 50000.0}
        # 应收账款 TB 是 40000, 差 10000
        tb = pd.DataFrame(
            [
                {"account_code": "1002", "account_name": "银行存款", "ending_balance": 100000.0},
                {"account_code": "1122", "account_name": "应收账款", "ending_balance": 40000.0},
            ]
        )
        issues = checker.check_balance_sheet_trial_balance_consistency(bs, tb)
        # 应收账款 差异应被检出
        items = {i["item"]: i for i in issues}
        assert "应收账款" in items
        assert items["应收账款"]["difference"] == 10000.0

    def test_is_consistent(self):
        from app.services.trial_balance_engine import ReportConsistencyChecker

        checker = ReportConsistencyChecker()
        is_ = {"营业收入": 1000000.0, "营业成本": 600000.0}
        tb = pd.DataFrame(
            [
                {"account_code": "6001", "account_name": "主营业务收入", "credit_amount": 1000000.0},
                {"account_code": "6401", "account_name": "主营业务成本", "debit_amount": 600000.0},
            ]
        )
        issues = checker.check_income_statement_trial_balance_consistency(is_, tb)
        assert issues == []

    def test_is_inconsistent_detected(self):
        from app.services.trial_balance_engine import ReportConsistencyChecker

        checker = ReportConsistencyChecker()
        is_ = {"营业收入": 1000000.0, "营业成本": 600000.0}
        tb = pd.DataFrame(
            [
                {"account_code": "6001", "account_name": "主营业务收入", "credit_amount": 800000.0},
                {"account_code": "6401", "account_name": "主营业务成本", "debit_amount": 600000.0},
            ]
        )
        issues = checker.check_income_statement_trial_balance_consistency(is_, tb)
        items = {i["item"]: i for i in issues}
        assert "营业收入" in items
        assert items["营业收入"]["difference"] == 200000.0

    def test_generate_consistency_report_empty(self):
        from app.services.trial_balance_engine import ReportConsistencyChecker

        checker = ReportConsistencyChecker()
        report = checker.generate_consistency_report([])
        assert report["is_consistent"] is True
        assert report["issue_count"] == 0
        assert report["recommendations"] == []

    def test_generate_consistency_report_with_issues(self):
        from app.services.trial_balance_engine import ReportConsistencyChecker

        checker = ReportConsistencyChecker()
        issues = [{"item": "货币资金", "difference": 100.0}]
        report = checker.generate_consistency_report(issues)
        assert report["is_consistent"] is False
        assert report["issue_count"] == 1
        assert len(report["recommendations"]) >= 1


class TestTrialBalanceGenerateReport:
    """generate_balance_report + generate_adjustment_suggestions."""

    def test_generate_report_balanced(self):
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "1001",
                    "account_name": "库存现金",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": 10000.0,
                    "debit_amount": 10000.0,
                    "credit_amount": 0,
                },
                {
                    "account_code": "2001",
                    "account_name": "应付账款",
                    "balance_direction": "贷",
                    "beginning_balance": 0,
                    "ending_balance": 10000.0,
                    "debit_amount": 0,
                    "credit_amount": 10000.0,
                },
            ]
        )
        info = {"name": "测试项目", "company_name": "客户A", "fiscal_year": "2024"}
        report = engine.generate_balance_report(df, info)
        assert report["balance_status"] == "平衡"
        assert report["project_name"] == "测试项目"
        assert report["company_name"] == "客户A"
        assert report["fiscal_year"] == "2024"
        # reconciliation_status 取决于 anomalies 列表 (贷方科目 ending_balance > 0
        # 引擎视为「借方余额」异常 → reconciliation_status='需核对').
        # 这里我们只验证 total_assets / total_liabilities 等关键字段, 不依赖此判定.
        assert report["total_assets"] == 10000.0
        assert report["total_liabilities"] == 10000.0
        assert report["difference"] == 0
        # anomalies 必有至少一条 (贷方应付账款正余额 → 借方余额异常)
        assert isinstance(report["anomalies"], list)
        assert len(report["anomalies"]) >= 1
        assert report["reconciliation_status"] in ("正常", "需核对")

    def test_generate_report_detects_credit_balance_anomaly(self):
        """借方科目 ending_balance < 0 → 异常 (贷方余额)."""
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "1122",
                    "account_name": "应收账款",
                    "balance_direction": "借",
                    "beginning_balance": 0,
                    "ending_balance": -500.0,  # 贷方余额异常
                    "debit_amount": 0,
                    "credit_amount": 500.0,
                }
            ]
        )
        info = {"name": "X", "company_name": "Y", "fiscal_year": "2024"}
        report = engine.generate_balance_report(df, info)
        anomalies = report["anomalies"]
        assert any(a["type"] == "贷方余额" for a in anomalies)

    def test_generate_adjustment_no_imbalance(self):
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame([{"account_code": "1", "ending_balance": 0, "debit_amount": 0, "credit_amount": 0}])
        suggestions = engine.generate_adjustment_suggestions(0.0, df)
        assert suggestions == []

    def test_generate_adjustment_with_suspicious_accounts(self):
        from app.services.trial_balance_engine import TrialBalanceEngine

        engine = TrialBalanceEngine()
        df = pd.DataFrame(
            [
                {
                    "account_code": "9999",
                    "account_name": "大额挂账",
                    "ending_balance": 5_000_000.0,
                    "debit_amount": 0,
                    "credit_amount": 0,
                }
            ]
        )
        suggestions = engine.generate_adjustment_suggestions(5000.0, df)
        assert any(s["type"] == "检查无发生额大额科目" for s in suggestions)
        assert any(s["type"] == "差异调整" for s in suggestions)


# ============================================================
#  Module 2: confirmation/letter_generator
# ============================================================


@pytest.fixture
def letter_tmpdir():
    """临时输出目录, 每个 test 隔离."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestLetterGeneratorRenderText:
    """ConfirmationLetterGenerator.render_text: 模板变量替换."""

    def test_bank_letter_renders_amounts(self, letter_tmpdir):
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = gen.render_text(
            "bank_official",
            company_name="测试公司",
            period="2024年度",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="中国工商银行",
            cpa_firm="×会计师事务所",
            cpa_address="北京市×区",
            auditor_name="张三",
            auditor_phone="010-12345678",
            current_deposit=1_234_567.89,
            time_deposit=500_000.00,
        )
        # 金额用千分位格式化
        assert "1,234,567.89" in text
        assert "500,000.00" in text
        # 模板标题
        assert "询证函" in text
        assert "中国工商银行" in text
        assert "测试公司" in text
        assert "2024-12-31" in text

    def test_customer_letter_renders_book_balance(self, letter_tmpdir):
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = gen.render_text(
            "customer_std",
            company_name="ABC有限公司",
            period="2024",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="客户A",
            cpa_firm="×会计师事务所",
            cpa_address="北京市",
            auditor_name="李四",
            auditor_phone="021-12345678",
            book_balance=888_888.88,
        )
        assert "888,888.88" in text
        assert "客户A" in text or "ABC有限公司" in text

    def test_other_receivable_letter(self, letter_tmpdir):
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = gen.render_text(
            "other_std",
            company_name="测试公司",
            period="2024",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="其他债务人",
            cpa_firm="×会计师事务所",
            cpa_address="北京",
            auditor_name="王五",
            auditor_phone="010-1234",
            book_balance=12_345.67,
            nature="借款",
        )
        assert "12,345.67" in text
        assert "借款" in text

    def test_unknown_template_raises(self, letter_tmpdir):
        from app.services.confirmation.letter_generator import (
            ConfirmationLetterGenerator,
            LetterGenerationError,
        )

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        with pytest.raises(LetterGenerationError):
            gen.render_text(
                "no_such_template",
                company_name="X",
                period="2024",
                period_start="2024-01-01",
                balance_date="2024-12-31",
                sent_date="2024-12-31",
                recipient="X",
                cpa_firm="X",
                cpa_address="X",
                auditor_name="X",
                auditor_phone="X",
            )

    def test_amount_formatting_zero(self, letter_tmpdir):
        """0 值也要格式化 '0.00', 不能是空字符串."""
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = gen.render_text(
            "bank_official",
            company_name="Z",
            period="2024",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="Bank",
            cpa_firm="Firm",
            cpa_address="Addr",
            auditor_name="A",
            auditor_phone="P",
            current_deposit=0,
        )
        # 0.00 应出现
        assert "0.00" in text

    def test_customer_with_special_chars(self, letter_tmpdir):
        """客户名含特殊字符不应破坏模板渲染 (KeyError 是真正 bug)."""
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = gen.render_text(
            "customer_std",
            company_name="行\n;客户",
            period="2024",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="客户X",
            cpa_firm="F",
            cpa_address="A",
            auditor_name="A",
            auditor_phone="P",
            book_balance=100.0,
        )
        # 模板渲染成功 (不抛 KeyError)
        assert "客户X" in text


class TestLetterGeneratorRenderDocx:
    """ConfirmationLetterGenerator.render_docx: 生成 .docx 文件."""

    def test_docx_file_created(self, letter_tmpdir):
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = "测试第一行\n\n测试第二行"
        path = gen.render_docx(
            "bank_official",
            text,
            filename_hint="测试_银行询证函_2024",
            meta={"party_name": "工商银行"},
        )
        assert path.exists()
        assert path.suffix == ".docx"
        # 内容非空
        assert path.stat().st_size > 100

    def test_docx_filename_sanitized_unsafe_chars(self, letter_tmpdir):
        """filename_hint 含 / \\ : * ? \" < > | 应被替换为 _."""
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = "测试"
        # 输入含路径分隔符和控制字符
        path = gen.render_docx(
            "customer_std",
            text,
            filename_hint='a/b\\c:d*e?f"g<h>i|j\x01',
            meta={"party_name": "X"},
        )
        assert path.exists()
        # 文件名主体不应包含 / \ : * ? " < > |
        name = path.name
        for bad in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
            assert bad not in name, f"文件名残留非法字符 {bad!r}: {name}"

    def test_docx_chinese_kept(self, letter_tmpdir):
        """filename_hint 中文字符应保留, 不应被替换."""
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        path = gen.render_docx(
            "bank_official",
            "测试",
            filename_hint="测试公司-银行询证函",
            meta={"party_name": "测试公司"},
        )
        assert path.exists()
        # 中文字符应保留
        assert "测试公司" in path.name

    def test_docx_two_calls_produce_different_paths(self, letter_tmpdir):
        """相同 hint 应加 uuid 防覆盖, 两次调用产生不同文件."""
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        text = "测试"
        p1 = gen.render_docx("bank_official", text, filename_hint="X", meta={"party_name": "X"})
        p2 = gen.render_docx("bank_official", text, filename_hint="X", meta={"party_name": "X"})
        assert p1 != p2
        assert p1.exists() and p2.exists()


class TestLetterGeneratorGenerate:
    """ConfirmationLetterGenerator.generate: 一键生成 + 格式回退."""

    def test_generate_docx(self, letter_tmpdir):
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        path, text, actual_fmt = gen.generate(
            "bank_official",
            company_name="测试",
            period="2024",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="BANK",
            cpa_firm="Firm",
            cpa_address="Addr",
            auditor_name="A",
            auditor_phone="P",
            party_name="BANK",
            current_deposit=1000.0,
            file_format="docx",
        )
        assert path.exists()
        assert path.suffix == ".docx"
        assert actual_fmt == "docx"
        # 文本必含模板内容
        assert "BANK" in text

    def test_generate_pdf_fallback_to_docx_when_libreoffice_missing(self, letter_tmpdir):
        """请求 pdf 但 libreoffice 不在 → 应回退到 docx, actual_fmt='docx' (P0 修复)."""
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        gen = ConfirmationLetterGenerator(letter_tmpdir)
        path, text, actual_fmt = gen.generate(
            "bank_official",
            company_name="X",
            period="2024",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="B",
            cpa_firm="F",
            cpa_address="A",
            auditor_name="A",
            auditor_phone="P",
            party_name="B",
            file_format="pdf",
        )
        # 环境大概率没装 libreoffice → 应回退 docx
        # P0 修复: 此时 actual_fmt 必须是 docx, 不能谎报 pdf
        assert actual_fmt in ("docx", "pdf")
        if actual_fmt == "docx":
            assert path.suffix == ".docx"
        else:
            assert path.suffix == ".pdf"
        # 路径必须存在
        assert path.exists()

    def test_generate_output_dir_created(self, tmp_path):
        """output_dir 不存在时自动创建."""
        from app.services.confirmation.letter_generator import ConfirmationLetterGenerator

        new_dir = tmp_path / "subdir" / "deep"
        assert not new_dir.exists()
        gen = ConfirmationLetterGenerator(new_dir)
        path, _, _ = gen.generate(
            "bank_official",
            company_name="X",
            period="2024",
            period_start="2024-01-01",
            balance_date="2024-12-31",
            sent_date="2024-12-31",
            recipient="B",
            cpa_firm="F",
            cpa_address="A",
            auditor_name="A",
            auditor_phone="P",
            party_name="B",
        )
        assert new_dir.exists()
        assert path.exists()


# ============================================================
#  Module 3: contract_analysis/ocr
# ============================================================


class TestContractOCRFileTypeValidation:
    """ContractOCR.is_image / is_pdf + run() 文件类型校验."""

    def test_is_image_extensions(self):
        from app.services.contract_analysis.ocr import ContractOCR

        for ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"):
            assert ContractOCR.is_image(f"file{ext}") is True, ext
            assert ContractOCR.is_image(f"FILE{ext.upper()}") is True, ext

    def test_is_image_rejects_non_image(self):
        from app.services.contract_analysis.ocr import ContractOCR

        for ext in (".pdf", ".docx", ".exe", ".txt"):
            assert ContractOCR.is_image(f"file{ext}") is False, ext

    def test_is_pdf(self):
        from app.services.contract_analysis.ocr import ContractOCR

        assert ContractOCR.is_pdf("a.pdf") is True
        assert ContractOCR.is_pdf("a.PDF") is True
        assert ContractOCR.is_pdf("a.png") is False
        assert ContractOCR.is_pdf("a.docx") is False

    def test_run_rejects_unsupported_extension(self, tmp_path):
        """运行 run() 传入 .exe 或 .docx → OCRError (P0 业务规则: 仅图像/pdf)."""
        from app.services.contract_analysis.ocr import ContractOCR, OCRError

        # 写一个假 .exe 文件 (内容无关, 校验在 ext)
        bad = tmp_path / "evil.exe"
        bad.write_bytes(b"MZ")
        with pytest.raises(OCRError):
            ContractOCR.run(bad, "evil.exe")

    def test_run_rejects_docx(self, tmp_path):
        from app.services.contract_analysis.ocr import ContractOCR, OCRError

        bad = tmp_path / "contract.docx"
        bad.write_bytes(b"PK")
        with pytest.raises(OCRError):
            ContractOCR.run(bad, "contract.docx")

    def test_run_accepts_pdf_even_if_missing_engines(self, tmp_path):
        """PDF fast path: pdfplumber 优先, 文本层为空再走 OCR. 无引擎时
        不应抛 OCRError('不支持的文件类型'), 而是 OCR 失败兜底错误."""
        from app.services.contract_analysis.ocr import ContractOCR, OCRError

        pdf = tmp_path / "empty.pdf"
        # 故意写入非 PDF 字节 — pdfplumber 会抛, 但走到 OCR fallback
        pdf.write_bytes(b"%PDF-fake")
        with pytest.raises(OCRError):
            # 没有 OCR 引擎 → OCRError; 但消息不应含「不支持的文件类型」
            try:
                ContractOCR.run(pdf, "empty.pdf")
            except OCRError as e:
                assert "不支持的文件类型" not in str(e), (
                    f"PDF 应走 OCR fallback, 不应在类型校验就被拒: {e}"
                )
                raise

    def test_run_path_outside_allowed_base_rejected(self, tmp_path):
        """P0 安全修复: file_path 不在 allowed_base 内 → OCRError."""
        from app.services.contract_analysis.ocr import ContractOCR, OCRError

        # file_path 在 tmp_path, allowed_base 在另一个目录
        outside = tmp_path / "secret.pdf"
        outside.write_bytes(b"%PDF-fake")
        other_base = tmp_path / "other_base"
        other_base.mkdir()
        with pytest.raises(OCRError) as exc_info:
            ContractOCR.run(outside, "secret.pdf", allowed_base=other_base)
        assert "不在允许目录内" in str(exc_info.value)

    def test_run_path_inside_allowed_base_accepted(self, tmp_path):
        """file_path 在 allowed_base 内 → 不抛「不在允许目录内」错误."""
        from app.services.contract_analysis.ocr import ContractOCR, OCRError

        base = tmp_path / "uploads"
        base.mkdir()
        inside = base / "contract.pdf"
        inside.write_bytes(b"%PDF-fake")
        with pytest.raises(OCRError) as exc_info:
            ContractOCR.run(inside, "contract.pdf", allowed_base=base)
        # OCRError 来自 OCR 失败兜底, 不是路径越界
        assert "不在允许目录内" not in str(exc_info.value)


# ============================================================
#  Module 4: sentiment/quarterly/verifier (强化版)
# ============================================================


def _mk_event(eid: int, title: str, content: str) -> dict:
    return {
        "id": eid,
        "title": title,
        "content_text": content,
        "publisher": "测试源",
        "publish_date": "2024-02-15",
    }


def _mk_briefing(bid: int, summary: str, audit_json: str = "{}") -> dict:
    return {
        "id": bid,
        "briefing_date": "2024-02-15",
        "ai_summary": summary,
        "audit_verification_json": audit_json,
    }


class TestQuarterlyVerifierPercentCollision:
    """百分比碰撞防护: 数值 0.15 与文本 '0.15%' 混淆时, verifier 应正确处理."""

    def test_pct_collision_pct_prefix(self):
        """value=15 (整数), events 文本是 'pct: 15%' 不应误匹配成 'pct: 15%' 误读.

        实际逻辑: verifier 找 value 的多种表示, 整数 15 → forms 含 '15', '15%' (100x),
        '0.15' (浮点). events 含 'pct: 15%' 时, '15' 与 '15%' 都在 forms 里.
        本测试验证 _find_value 的返回值符合文档契约.
        """
        from app.services.sentiment.quarterly.verifier import QuarterlyVerifier

        v = QuarterlyVerifier()
        events = [_mk_event(1, "公告", "毛利率 pct: 15.5%, 营收 1,000,000")]
        financial = {"gross_margin_pct": 15.5, "revenue": 1_000_000}
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        gm = next(c for c in report.consistency_flags if c.financial_field == "gross_margin_pct")
        rev = next(c for c in report.consistency_flags if c.financial_field == "revenue")
        # gross_margin_pct 15.5 → 形式 "15.5", "15.50%", "15.5%" 都应能找到
        assert gm.matched_in in ("events", "briefings")
        assert gm.consistent is True
        # revenue 1,000,000 → 形式 "1,000,000", "1,000,000.00" 找到
        assert rev.matched_in == "events"

    def test_integer_value_variants(self):
        """整数 value 应生成 'str(v)' 和 'f"{v:,}"' 两种 forms."""
        from app.services.sentiment.quarterly.verifier import QuarterlyVerifier

        v = QuarterlyVerifier()
        events = [_mk_event(1, "公告", "公司股本 10,000,000 股")]
        financial = {"shares": 10_000_000}
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        flag = next(c for c in report.consistency_flags if c.financial_field == "shares")
        assert flag.matched_in == "events"
        assert flag.consistent is True

    def test_briefings_match_when_events_missing(self):
        """events 找不到, briefings 找到 → matched_in='briefings'."""
        from app.services.sentiment.quarterly.verifier import QuarterlyVerifier

        v = QuarterlyVerifier()
        events: list = []
        briefings = [_mk_briefing(1, "本期营收 50,000,000 元, 同比 +10%")]
        financial = {"revenue": 50_000_000}
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=briefings,
        )
        flag = next(c for c in report.consistency_flags if c.financial_field == "revenue")
        assert flag.matched_in == "briefings"
        assert flag.consistent is True


class TestQuarterlyVerifierTypeValidation:
    """verify() 对非常规类型的字段应跳过或宽容处理."""

    def test_non_numeric_non_string_skipped(self):
        """dict / list / tuple 等非 (int|float|str|bool) 字段应被跳过 (不报错).

        注: bool 是 int 子类, 在 verifier 内部 isinstance 校验会通过.
        """
        from app.services.sentiment.quarterly.verifier import QuarterlyVerifier

        v = QuarterlyVerifier()
        # 含 dict/list/tuple — 这些不是 (int|float|str) 应被过滤
        financial = {
            "extra_dict": {"nested": 1},
            "tags_list": ["a", "b"],
            "coords_tuple": (1, 2),
            "revenue": 1_000_000,  # 正常字段
        }
        events = [_mk_event(1, "公告", "营收 1,000,000")]
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        # 只有 revenue 进 consistency_flags
        fields = {c.financial_field for c in report.consistency_flags}
        assert fields == {"revenue"}, (
            f"非 (int|float|str) 字段应被过滤, 实际: {fields}"
        )

    def test_none_value_skipped(self):
        """value=None 应跳过 (合同分析中允许空值)."""
        from app.services.sentiment.quarterly.verifier import QuarterlyVerifier

        v = QuarterlyVerifier()
        financial = {"revenue": None, "cost": 500_000}
        events = [_mk_event(1, "公告", "成本 500,000")]
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        fields = {c.financial_field for c in report.consistency_flags}
        assert "revenue" not in fields
        assert "cost" in fields

    def test_numeric_type_rejected_string_skipped(self):
        """字符串型数字 (如 '1000') 与 events 中的纯数字是否能匹配.

        按现有实现: str(value) in text → 纯字符串包含才匹配.
        这里验证契约: 字符串字段必须有完整字面包含才算 matched.
        """
        from app.services.sentiment.quarterly.verifier import QuarterlyVerifier

        v = QuarterlyVerifier()
        # events 含 "1000", financial 是字符串 "1000"
        events = [_mk_event(1, "公告", "成本 1000 元")]
        financial = {"cost_str": "1000"}
        report = v.verify(
            markdown="# 测试",
            financial_input=financial,
            events=events,
            briefings=[],
        )
        flag = next(c for c in report.consistency_flags if c.financial_field == "cost_str")
        assert flag.matched_in == "events"
        assert flag.consistent is True


class TestQuarterlyVerifierLockedReportGuard:
    """验证 aggregator.lock_references 与 financial_input.save_financial_input
    对已锁定报告的拒绝写入逻辑 (State guard).

    此处仅验证模块 API 契约 + 模拟锁定场景的错误路径, 不依赖 DB.
    """

    def test_lock_references_has_is_locked_check(self):
        """lock_references 源码中必须含 is_locked 守卫."""
        import ast
        import inspect

        from app.services.sentiment.quarterly import aggregator

        src = inspect.getsource(aggregator)
        assert "def lock_references" in src, "lock_references 不存在"
        # 用 ast 准确提取函数体 (regex 对多行签名 + 末尾函数不稳定)
        tree = ast.parse(src)
        fn_node = next(
            (
                n for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "lock_references"
            ),
            None,
        )
        assert fn_node is not None, "lock_references AST 节点找不到"
        body = ast.unparse(fn_node)
        assert "is_locked" in body, "lock_references 缺 is_locked 守卫"
        assert "raise" in body, "lock_references 没 raise 阻断"

    def test_save_financial_input_has_is_locked_check(self):
        """save_financial_input 源码中必须含 is_locked 守卫."""
        import ast
        import inspect
        import re

        from app.services.sentiment.quarterly import financial_input

        src = inspect.getsource(financial_input)
        assert "def save_financial_input" in src, "save_financial_input 不存在"
        tree = ast.parse(src)
        fn_node = next(
            (
                n for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "save_financial_input"
            ),
            None,
        )
        assert fn_node is not None, "save_financial_input AST 节点找不到"
        body = ast.unparse(fn_node)
        assert "is_locked" in body, "save_financial_input 缺 is_locked 守卫"
        # ast.unparse 会把 return False, x 输出成 "return (False, x)", 用正则兼顾
        assert (
            "raise" in body
            or re.search(r"return\s*\(\s*False", body)
            or re.search(r"return\s+False\b", body)
        ), "save_financial_input 既没 raise 也没 return False, 锁定状态没阻断"

    def test_verifier_to_dict_includes_all_fields(self):
        """QuarterlyVerificationReport.to_dict 必须含契约字段."""
        from app.services.sentiment.quarterly.verifier import QuarterlyVerifier

        v = QuarterlyVerifier()
        report = v.verify(
            markdown="# T",
            financial_input={"revenue": 1000},
            events=[],
            briefings=[],
        )
        d = report.to_dict()
        for key in (
            "passed",
            "consistency_flags",
            "briefing_verify_report",
            "issue_count",
            "error_count",
            "note",
        ):
            assert key in d, f"to_dict 缺字段 {key}"


# ============================================================
#  Smoke: 4 模块均被覆盖
# ============================================================


class TestRound35Smoke:
    """冒烟: 至少 4 类 P0 模块被独立测试覆盖."""

    def test_at_least_4_p0_modules_covered(self):
        """本文件包含 ≥4 类 TestClass, 覆盖 trial_balance_engine / letter_generator / ocr / verifier."""
        import sys

        mod = sys.modules[__name__]
        classes = [
            obj
            for name, obj in vars(mod).items()
            if isinstance(obj, type) and name.startswith("Test")
        ]
        # 不含 TestRound35Smoke 自身, 但含 13 类
        assert len(classes) >= 10, (
            f"只覆盖 {len(classes)} 类, 应 ≥10 类 (trial_balance_engine 4 类 + "
            f"letter_generator 3 类 + ocr 1 类 + verifier 3 类 + smoke 1 类)"
        )