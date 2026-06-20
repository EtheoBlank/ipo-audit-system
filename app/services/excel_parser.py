"""Excel file parsing service for IPO Audit System."""

import asyncio
from pathlib import Path

import pandas as pd
from fastapi import UploadFile
from app.core.config import settings


def _safe_temp_path(upload: UploadFile) -> Path:
    """P0 安全修复 (2026-06-18): 统一 sanitize 上传文件名, 防路径穿越.

    upload.filename 用户可控, 含 '../../../etc/passwd' 可越界. 取 .name 拿最后一段,
    再用 is_relative_to 校验 temp_path 必须落在 settings.UPLOAD_DIR 内.
    4 个 parse_* 方法 (科目余额表 / 序时账 / 银行对账单 / CSV) 共用.
    """
    safe_name = Path(upload.filename or "upload.xlsx").name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"非法的文件名: {upload.filename!r}")
    temp_path = settings.UPLOAD_DIR / f"temp_{safe_name}"
    if not temp_path.resolve().is_relative_to(settings.UPLOAD_DIR.resolve()):
        raise ValueError(f"非法的文件名: {upload.filename!r}")
    return temp_path


class ExcelParser:
    """Parse Excel files for audit data."""

    @staticmethod
    async def parse_account_balance(file: UploadFile) -> pd.DataFrame:
        """Parse account balance (科目余额表) Excel file.

        Expected columns: 科目编码, 科目名称, 期初余额, 借方发生额, 贷方发生额, 期末余额, 余额方向
        """
        temp_path = _safe_temp_path(file)

        # Save uploaded file — 同步 open() 走 to_thread (P0 round32 性能)
        content = await file.read()
        await asyncio.to_thread(temp_path.write_bytes, content)

        try:
            # Read Excel file — 同步 read_excel 走 to_thread (P0 round32 性能)
            df = await asyncio.to_thread(pd.read_excel, temp_path)

            # Standardize column names
            column_mapping = {
                "科目编码": "account_code",
                "科目名称": "account_name",
                "期初余额": "beginning_balance",
                "借方发生额": "debit_amount",
                "贷方发生额": "credit_amount",
                "期末余额": "ending_balance",
                "余额方向": "balance_direction",
            }

            df = df.rename(columns=column_mapping)

            # Validate required columns
            required_cols = ["account_code", "account_name", "balance_direction"]
            for col in required_cols:
                if col not in df.columns:
                    raise ValueError(f"Missing required column: {col}")

            # Fill NaN with 0 for numeric columns
            numeric_cols = ["beginning_balance", "debit_amount", "credit_amount", "ending_balance"]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            return df

        finally:
            # Clean up temp file
            if temp_path.exists():
                await asyncio.to_thread(temp_path.unlink)

    @staticmethod
    async def parse_chronological_account(file: UploadFile) -> pd.DataFrame:
        """Parse chronological account (序时账) Excel file.

        Expected columns: 凭证日期, 凭证号, 科目编码, 科目名称, 借方金额, 贷方金额, 摘要, 辅助核算

        P0 (round 32) 性能: open() + pd.read_excel() 走 to_thread.
        """
        # P0 安全: 统一走 _safe_temp_path
        temp_path = _safe_temp_path(file)

        content = await file.read()
        await asyncio.to_thread(temp_path.write_bytes, content)

        try:
            df = await asyncio.to_thread(pd.read_excel, temp_path)

            column_mapping = {
                "凭证日期": "voucher_date",
                "凭证号": "voucher_no",
                "科目编码": "account_code",
                "科目名称": "account_name",
                "借方金额": "debit_amount",
                "贷方金额": "credit_amount",
                "摘要": "summary",
                "辅助核算": "auxiliary_accounting",
            }

            df = df.rename(columns=column_mapping)

            numeric_cols = ["debit_amount", "credit_amount"]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            return df

        finally:
            if temp_path.exists():
                await asyncio.to_thread(temp_path.unlink)

    @staticmethod
    async def parse_bank_statement(file: UploadFile) -> pd.DataFrame:
        """Parse bank statement (银行对账单) Excel file.

        Expected columns: 对账单日期, 凭证号, 描述, 借方金额, 贷方金额, 余额, 银行账号

        P0 (round 32) 性能: open() + pd.read_excel() 走 to_thread.
        """
        # P0 安全
        temp_path = _safe_temp_path(file)

        content = await file.read()
        await asyncio.to_thread(temp_path.write_bytes, content)

        try:
            df = await asyncio.to_thread(pd.read_excel, temp_path)

            column_mapping = {
                "对账单日期": "statement_date",
                "凭证号": "voucher_no",
                "描述": "description",
                "借方金额": "debit_amount",
                "贷方金额": "credit_amount",
                "余额": "balance",
                "银行账号": "bank_account",
            }

            df = df.rename(columns=column_mapping)

            numeric_cols = ["debit_amount", "credit_amount", "balance"]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            return df

        finally:
            if temp_path.exists():
                await asyncio.to_thread(temp_path.unlink)

    @staticmethod
    async def parse_csv(file: UploadFile) -> pd.DataFrame:
        """Parse CSV file.

        P0 (round 32) 性能: open() + pd.read_csv() 走 to_thread.
        """
        # P0 安全
        temp_path = _safe_temp_path(file)

        content = await file.read()
        await asyncio.to_thread(temp_path.write_bytes, content)

        try:
            df = await asyncio.to_thread(pd.read_csv, temp_path, encoding="utf-8-sig")
            return df
        finally:
            if temp_path.exists():
                await asyncio.to_thread(temp_path.unlink)
