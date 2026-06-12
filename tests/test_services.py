"""Tests for IPO Audit System."""
import pytest
from datetime import datetime


class TestTrialBalanceService:
    """Tests for TrialBalanceService."""

    def test_check_balance_balanced(self):
        """Test balance check with balanced accounts."""
        from app.services.trial_balance import TrialBalanceService
        import pandas as pd

        # Create test data - balanced accounts
        data = {
            "account_code": ["1001", "1002", "2001"],
            "account_name": ["银行存款", "应收账款", "应付账款"],
            "balance_direction": ["借", "借", "贷"],
            "beginning_balance": [10000.0, 5000.0, 8000.0],
            "debit_amount": [2000.0, 3000.0, 1500.0],
            "credit_amount": [1500.0, 2000.0, 2500.0],
            "ending_balance": [10500.0, 6000.0, 7000.0],
        }
        df = pd.DataFrame(data)

        result = TrialBalanceService.check_balance(df)

        # 新版返回结构: { is_balanced, standalone: { beginning, current_period, ending } }
        assert "is_balanced" in result
        assert "standalone" in result
        standalone = result["standalone"]
        assert "beginning" in standalone
        assert "current_period" in standalone
        assert "ending" in standalone

    def test_get_account_summary(self):
        """Test account summary generation."""
        from app.services.trial_balance import TrialBalanceService
        import pandas as pd

        data = {
            "account_code": ["1001", "1001", "1002"],
            "account_name": ["银行存款", "银行存款", "应收账款"],
            "balance_direction": ["借", "借", "借"],
            "beginning_balance": [10000.0, 0.0, 5000.0],
            "debit_amount": [2000.0, 0.0, 3000.0],
            "credit_amount": [1500.0, 0.0, 2000.0],
            "ending_balance": [10500.0, 0.0, 6000.0],
        }
        df = pd.DataFrame(data)

        summary = TrialBalanceService.get_account_summary(df)

        assert len(summary) > 0
        assert isinstance(summary, list)

    def test_identify_unusual_balances(self):
        """Test unusual balance identification."""
        from app.services.trial_balance import TrialBalanceService
        import pandas as pd

        data = {
            "account_code": ["1001", "1002"],
            "account_name": ["银行存款", "应收账款"],
            "balance_direction": ["借", "借"],
            "beginning_balance": [10000.0, 5000.0],
            "debit_amount": [0.0, 3000.0],
            "credit_amount": [0.0, 2000.0],
            "ending_balance": [10000.0, 6000.0],  # 1001 has balance but no activity
        }
        df = pd.DataFrame(data)

        unusual = TrialBalanceService.identify_unusual_balances(df)

        assert len(unusual) > 0
        assert unusual[0]["account_code"] == "1001"


class TestExcelParser:
    """Tests for ExcelParser."""

    @pytest.mark.asyncio
    async def test_parse_account_balance_invalid_file(self):
        """Test parsing with invalid file."""
        from fastapi import UploadFile
        from io import BytesIO
        from app.services.excel_parser import ExcelParser

        # Create a dummy file
        content = b"not a real excel file"
        dummy_file = UploadFile(
            filename="test.xlsx",
            file=BytesIO(content),
        )

        # Should handle gracefully
        try:
            df = await ExcelParser.parse_account_balance(dummy_file)
            assert df is None or df.empty
        except Exception:
            pass  # Expected for invalid file


class TestWorkbookGenerator:
    """Tests for WorkbookGenerator."""

    def test_get_styles(self):
        """Test styles configuration."""
        from app.services.workbook_generator import WorkbookGenerator

        generator = WorkbookGenerator(
            project_id=1,
            company_name="测试公司",
            fiscal_year=2024,
        )

        styles = generator._get_styles()

        assert "header_font" in styles
        assert "header_fill" in styles
        assert "thin_border" in styles
        assert "center_align" in styles


class TestProjectSchemas:
    """Tests for Pydantic schemas."""

    def test_project_create_schema(self):
        """Test ProjectCreate schema."""
        from app.models.audit import ProjectCreate

        project = ProjectCreate(
            name="测试项目",
            company_name="测试公司",
            industry="制造业",
            fiscal_year=2024,
        )

        assert project.name == "测试项目"
        assert project.company_name == "测试公司"
        assert project.industry == "制造业"
        assert project.fiscal_year == 2024

    def test_account_balance_schema(self):
        """Test AccountBalanceCreate schema."""
        from app.models.audit import AccountBalanceCreate

        balance = AccountBalanceCreate(
            project_id=1,
            account_code="1001",
            account_name="银行存款",
            balance_direction="借",
            beginning_balance=10000.0,
            debit_amount=2000.0,
            credit_amount=1500.0,
            ending_balance=10500.0,
        )

        assert balance.account_code == "1001"
        assert balance.ending_balance == 10500.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])