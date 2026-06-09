"""Workbook generation service for audit workpapers."""
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.chart import BarChart, LineChart, Reference
from pathlib import Path
from datetime import datetime
from app.core.config import settings


class WorkbookGenerator:
    """Generate audit workbooks in Excel format."""

    def __init__(self, project_id: int, company_name: str, fiscal_year: int):
        self.project_id = project_id
        self.company_name = company_name
        self.fiscal_year = fiscal_year
        self.output_dir = settings.OUTPUT_DIR / f"project_{project_id}"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_styles(self) -> dict:
        """Get standard Excel styles for audit workbooks."""
        return {
            "header_font": Font(name="微软雅黑", size=11, bold=True, color="FFFFFF"),
            "header_fill": PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid"),
            "subheader_font": Font(name="微软雅黑", size=10, bold=True),
            "subheader_fill": PatternFill(start_color="D6DCE5", end_color="D6DCE5", fill_type="solid"),
            "title_font": Font(name="微软雅黑", size=14, bold=True),
            "normal_font": Font(name="微软雅黑", size=10),
            "thin_border": Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin"),
            ),
            "center_align": Alignment(horizontal="center", vertical="center", wrap_text=True),
            "left_align": Alignment(horizontal="left", vertical="center", wrap_text=True),
            "right_align": Alignment(horizontal="right", vertical="center"),
        }

    def _apply_header_style(self, ws, row_num: int, col_num: int):
        """Apply header style to a cell."""
        styles = self._get_styles()
        cell = ws.cell(row=row_num, column=col_num)
        cell.font = styles["header_font"]
        cell.fill = styles["header_fill"]
        cell.alignment = styles["center_align"]
        cell.border = styles["thin_border"]

    def _apply_data_style(self, ws, row_num: int, col_num: int, is_number: bool = False):
        """Apply data cell style."""
        styles = self._get_styles()
        cell = ws.cell(row=row_num, column=col_num)
        cell.font = styles["normal_font"]
        cell.alignment = styles["right_align"] if is_number else styles["left_align"]
        cell.border = styles["thin_border"]

    def generate_account_detail(self, account_balances: pd.DataFrame) -> Path:
        """Generate account detail workbook (科目明细表).

        Args:
            account_balances: DataFrame with account balance data

        Returns:
            Path to generated Excel file
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "科目明细表"

        styles = self._get_styles()

        # Title
        ws.merge_cells("A1:H1")
        title_cell = ws["A1"]
        title_cell.value = f"{self.company_name} - 科目明细表 ({self.fiscal_year}年度)"
        title_cell.font = styles["title_font"]
        title_cell.alignment = styles["center_align"]
        ws.row_dimensions[1].height = 30

        # Headers
        headers = ["科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额", "余额方向", "备注"]
        for col, header in enumerate(headers, 1):
            ws.cell(row=2, column=col, value=header)
            self._apply_header_style(ws, 2, col)
        ws.row_dimensions[2].height = 25

        # Data rows
        for row_idx, (_, row) in enumerate(account_balances.iterrows(), 3):
            ws.cell(row=row_idx, column=1, value=row.get("account_code", ""))
            ws.cell(row=row_idx, column=2, value=row.get("account_name", ""))
            ws.cell(row=row_idx, column=3, value=row.get("beginning_balance", 0))
            ws.cell(row=row_idx, column=4, value=row.get("debit_amount", 0))
            ws.cell(row=row_idx, column=5, value=row.get("credit_amount", 0))
            ws.cell(row=row_idx, column=6, value=row.get("ending_balance", 0))
            ws.cell(row=row_idx, column=7, value=row.get("balance_direction", ""))
            ws.cell(row=row_idx, column=8, value="")

            for col in range(1, 9):
                is_number = col in [3, 4, 5, 6]
                self._apply_data_style(ws, row_idx, col, is_number)

        # Set column widths
        col_widths = [15, 25, 15, 15, 15, 15, 10, 20]
        for idx, width in enumerate(col_widths, 1):
            ws.column_dimensions[chr(64 + idx)].width = width

        # Add summary row
        last_row = len(account_balances) + 3
        ws.cell(row=last_row, column=1, value="合计")
        ws.cell(row=last_row, column=1).font = styles["subheader_font"]
        ws.cell(row=last_row, column=3, value=account_balances["beginning_balance"].sum())
        ws.cell(row=last_row, column=4, value=account_balances["debit_amount"].sum())
        ws.cell(row=last_row, column=5, value=account_balances["credit_amount"].sum())
        ws.cell(row=last_row, column=6, value=account_balances["ending_balance"].sum())

        for col in range(1, 9):
            cell = ws.cell(row=last_row, column=col)
            cell.fill = styles["subheader_fill"]
            cell.border = styles["thin_border"]

        # Add filter
        ws.auto_filter.ref = f"A2:H{last_row - 1}"

        output_path = self.output_dir / f"科目明细表_{self.fiscal_year}.xlsx"
        wb.save(output_path)
        return output_path

    def generate_income_statement(self, account_balances: pd.DataFrame) -> Path:
        """Generate income statement workbook (利润表).

        Args:
            account_balances: DataFrame with account balance data

        Returns:
            Path to generated Excel file
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "利润表"

        styles = self._get_styles()

        # Title
        ws.merge_cells("A1:E1")
        title_cell = ws["A1"]
        title_cell.value = f"{self.company_name} - 利润表 ({self.fiscal_year}年度)"
        title_cell.font = styles["title_font"]
        title_cell.alignment = styles["center_align"]
        ws.row_dimensions[1].height = 30

        # Headers
        headers = ["项目", "行次", "本期金额", "上期金额", "备注"]
        for col, header in enumerate(headers, 1):
            ws.cell(row=2, column=col, value=header)
            self._apply_header_style(ws, 2, col)
        ws.row_dimensions[2].height = 25

        # Income statement structure
        income_items = [
            ("营业收入", "5001"),
            ("营业成本", "5002"),
            ("研发费用", "5003"),
            ("销售费用", "5004"),
            ("管理费用", "5005"),
            ("财务费用", "5006"),
            ("公允价值变动收益", "5007"),
            ("投资收益", "5008"),
            ("其他收益", "5009"),
            ("营业利润", "5000"),
            ("加：营业外收入", "6001"),
            ("减：营业外支出", "6002"),
            ("利润总额", "6000"),
            ("减：所得税费用", "7001"),
            ("净利润", "7000"),
        ]

        # Filter income/expense accounts
        revenue_accounts = account_balances[
            account_balances["account_code"].str.startswith(("5", "6", "7"), na=False)
        ]

        for row_idx, (item_name, item_code) in enumerate(income_items, 3):
            ws.cell(row=row_idx, column=1, value=item_name)
            ws.cell(row=row_idx, column=2, value=item_code)
            ws.cell(row=row_idx, column=3, value=0)
            ws.cell(row=row_idx, column=4, value=0)
            ws.cell(row=row_idx, column=5, value="")

            for col in range(1, 6):
                self._apply_data_style(ws, row_idx, col, col in [3, 4])

        # Set column widths
        col_widths = [20, 10, 18, 18, 20]
        for idx, width in enumerate(col_widths, 1):
            ws.column_dimensions[chr(64 + idx)].width = width

        output_path = self.output_dir / f"利润表_{self.fiscal_year}.xlsx"
        wb.save(output_path)
        return output_path

    def generate_balance_sheet(self, account_balances: pd.DataFrame) -> Path:
        """Generate balance sheet workbook (资产负债表).

        Args:
            account_balances: DataFrame with account balance data

        Returns:
            Path to generated Excel file
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "资产负债表"

        styles = self._get_styles()

        # Title
        ws.merge_cells("A1:E1")
        title_cell = ws["A1"]
        title_cell.value = f"{self.company_name} - 资产负债表 ({self.fiscal_year}年度)"
        title_cell.font = styles["title_font"]
        title_cell.alignment = styles["center_align"]
        ws.row_dimensions[1].height = 30

        # Headers
        headers = ["项目", "行次", "期末余额", "年初余额", "备注"]
        for col, header in enumerate(headers, 1):
            ws.cell(row=2, column=col, value=header)
            self._apply_header_style(ws, 2, col)
        ws.row_dimensions[2].height = 25

        # Balance sheet structure
        assets_items = [
            ("流动资产合计", "1000"),
            ("非流动资产合计", "2000"),
            ("资产总计", "0000"),
        ]

        liabilities_items = [
            ("流动负债合计", "3000"),
            ("非流动负债合计", "4000"),
            ("负债合计", "0000"),
            ("所有者权益合计", "5000"),
            ("负债和所有者权益总计", "0000"),
        ]

        output_path = self.output_dir / f"资产负债表_{self.fiscal_year}.xlsx"
        wb.save(output_path)
        return output_path

    def generate_cash_flow(self, chronological_accounts: pd.DataFrame) -> Path:
        """Generate cash flow statement workbook (现金流量表).

        Args:
            chronological_accounts: DataFrame with chronological account data

        Returns:
            Path to generated Excel file
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "现金流量表"

        styles = self._get_styles()

        # Title
        ws.merge_cells("A1:E1")
        title_cell = ws["A1"]
        title_cell.value = f"{self.company_name} - 现金流量表 ({self.fiscal_year}年度)"
        title_cell.font = styles["title_font"]
        title_cell.alignment = styles["center_align"]
        ws.row_dimensions[1].height = 30

        # Headers
        headers = ["项目", "行次", "本期金额", "上期金额", "备注"]
        for col, header in enumerate(headers, 1):
            ws.cell(row=2, column=col, value=header)
            self._apply_header_style(ws, 2, col)
        ws.row_dimensions[2].height = 25

        output_path = self.output_dir / f"现金流量表_{self.fiscal_year}.xlsx"
        wb.save(output_path)
        return output_path

    def generate_trial_balance(
        self,
        account_balances: pd.DataFrame,
        consolidation_balances: pd.DataFrame = None,
    ) -> Path:
        """Generate trial balance workbook (试算平衡表) with consolidation columns.

        Args:
            account_balances: DataFrame with account balance data (单体报表)
            consolidation_balances: Optional DataFrame with consolidation balance data (合并报表)

        Returns:
            Path to generated Excel file
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "试算平衡表"

        styles = self._get_styles()

        # Title
        ws.merge_cells("A1:L1")
        title_cell = ws["A1"]
        title_cell.value = f"{self.company_name} - 试算平衡表 ({self.fiscal_year}年度)"
        title_cell.font = styles["title_font"]
        title_cell.alignment = styles["center_align"]
        ws.row_dimensions[1].height = 30

        # Headers - including consolidation columns if provided
        if consolidation_balances is not None:
            headers = [
                "科目编码", "科目名称",
                "期初借方(单)", "期初贷方(单)",
                "本期借方(单)", "本期贷方(单)",
                "期末借方(单)", "期末贷方(单)",
                "期末借方(合)", "期末贷方(合)",
                "内部抵销(资产)", "内部抵销(负债)",
            ]
        else:
            headers = [
                "科目编码", "科目名称",
                "期初借方", "期初贷方",
                "本期借方", "本期贷方",
                "期末借方", "期末贷方",
            ]

        for col, header in enumerate(headers, 1):
            ws.cell(row=2, column=col, value=header)
            self._apply_header_style(ws, 2, col)
        ws.row_dimensions[2].height = 30

        # Calculate trial balance for standalone
        debit_total_begin = account_balances[account_balances["balance_direction"] == "借"]["beginning_balance"].sum()
        credit_total_begin = account_balances[account_balances["balance_direction"] == "贷"]["beginning_balance"].sum()
        debit_total_current = account_balances["debit_amount"].sum()
        credit_total_current = account_balances["credit_amount"].sum()
        debit_total_end = account_balances[account_balances["balance_direction"] == "借"]["ending_balance"].sum()
        credit_total_end = account_balances[account_balances["balance_direction"] == "贷"]["ending_balance"].sum()

        # Calculate consolidation totals if provided
        cons_debit_end = 0
        cons_credit_end = 0
        if consolidation_balances is not None:
            cons_debit = consolidation_balances[consolidation_balances["balance_direction"] == "借"]
            cons_credit = consolidation_balances[consolidation_balances["balance_direction"] == "贷"]
            cons_debit_end = cons_debit["ending_balance"].sum()
            cons_credit_end = cons_credit["ending_balance"].sum()

        # Summary section
        summary_row = 4
        ws.cell(row=summary_row, column=1, value="合计")
        ws.cell(row=summary_row, column=1).font = styles["subheader_font"]
        ws.cell(row=summary_row, column=3, value=debit_total_begin)
        ws.cell(row=summary_row, column=4, value=credit_total_begin)
        ws.cell(row=summary_row, column=5, value=debit_total_current)
        ws.cell(row=summary_row, column=6, value=credit_total_current)
        ws.cell(row=summary_row, column=7, value=debit_total_end)
        ws.cell(row=summary_row, column=8, value=credit_total_end)

        if consolidation_balances is not None:
            ws.cell(row=summary_row, column=9, value=cons_debit_end)
            ws.cell(row=summary_row, column=10, value=cons_credit_end)
            ws.cell(row=summary_row, column=11, value=debit_total_end - cons_debit_end)  # 内部抵销资产
            ws.cell(row=summary_row, column=12, value=credit_total_end - cons_credit_end)  # 内部抵销负债

        # Apply summary style
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=summary_row, column=col)
            cell.fill = styles["subheader_fill"]
            cell.border = styles["thin_border"]

        # Balance check
        check_row = 6
        is_balanced = abs(debit_total_end - credit_total_end) < 0.01
        ws.cell(row=check_row, column=1, value="单体平衡状态")
        ws.cell(row=check_row, column=2, value="✅平衡" if is_balanced else "❌ 不平衡")

        if consolidation_balances is not None:
            cons_is_balanced = abs(cons_debit_end - cons_credit_end) < 0.01
            ws.cell(row=check_row + 1, column=1, value="合并平衡状态")
            ws.cell(row=check_row + 1, column=2, value="✅ 平衡" if cons_is_balanced else "❌ 不平衡")

            ws.cell(row=check_row + 2, column=1, value="内部交易抵销金额")
            ws.cell(row=check_row + 2, column=3, value=debit_total_end - cons_debit_end)
            ws.cell(row=check_row + 2, column=4, value=credit_total_end - cons_credit_end)

        # Set column widths
        col_widths = [15, 25, 12, 12, 12, 12, 12, 12]
        if consolidation_balances is not None:
            col_widths.extend([12, 12, 12, 12])
        for idx, width in enumerate(col_widths, 1):
            ws.column_dimensions[chr(64 + idx) if idx <= 26 else "A" + chr(64 + idx - 26)].width = width

        # Freeze panes
        ws.freeze_panes = "A3"

        output_path = self.output_dir / f"试算平衡表_{self.fiscal_year}.xlsx"
        wb.save(output_path)
        return output_path
        return output_path