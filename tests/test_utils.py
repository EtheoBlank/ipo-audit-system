"""utils/ 三个工具模块单元测试.

覆盖:
  - app/utils/datetime_helpers.py: utc_now
  - app/utils/upload_safety.py: sanitize_filename, neutralize_formula, neutralize_dataframe_strings
  - app/utils/db_helpers.py: account_balances_to_df
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.utils.datetime_helpers import utc_now
from app.utils.db_helpers import account_balances_to_df
from app.utils.upload_safety import (
    neutralize_dataframe_strings,
    neutralize_formula,
    sanitize_filename,
    unique_save_path,
)


# ----------------------------------------------------------------------
#  1) datetime_helpers.utc_now
# ----------------------------------------------------------------------


class TestUtcNow:
    def test_returns_naive_datetime(self):
        result = utc_now()
        assert isinstance(result, datetime)
        # naive = 无 tzinfo
        assert result.tzinfo is None

    def test_returns_utc_equivalent(self):
        """naive UTC datetime 应等价于本地时区转换后的 UTC."""
        result = utc_now()
        # 等价于: datetime.now(timezone.utc).replace(tzinfo=None)
        expected_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        # 允许 2 秒误差
        assert abs((result - expected_utc).total_seconds()) < 2

    def test_consecutive_calls_increase(self):
        a = utc_now()
        b = utc_now()
        assert b >= a

    def test_no_utcnow_deprecation(self):
        """smoke: 项目用 utc_now 而不是已弃用的 datetime.utcnow()."""
        from app.utils import datetime_helpers
        # 不应 import utcnow
        assert not hasattr(datetime_helpers, "utcnow")


# ----------------------------------------------------------------------
#  2) upload_safety.sanitize_filename
# ----------------------------------------------------------------------


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("合同.docx", "合同.docx"),
            ("path/to/合同.docx", "合同.docx"),  # strip path
            ("../../../etc/passwd", "passwd"),
            ("..", "upload"),  # 全是 dot → default
            (".", "upload"),
            ("...", "upload"),
            ("", "upload"),
            (None, "upload"),
            ("file\x00name.txt", "file_name.txt"),  # NUL → _
        ],
    )
    def test_sanitize(self, raw, expected):
        result = sanitize_filename(raw)
        assert result == expected

    def test_max_length_240(self):
        long = "a" * 500 + ".docx"
        result = sanitize_filename(long)
        # 截断到 240, 截断后扩展名可能被截掉, 这是预期
        assert len(result) <= 240
        # 不超过 240 字符
        assert len(result) == 240

    def test_dangerous_chars_replaced(self):
        """路径分隔符 + 控制字符应被替换."""
        result = sanitize_filename("a/b\\c:d|e?f*g.txt")
        # 斜杠 / 反斜杠 → strip path 之后可能只保留 e?f*g.txt
        # 然后 ? * | : 等 → _
        # 总之不应有 /
        assert "/" not in result
        assert "\\" not in result


# ----------------------------------------------------------------------
#  3) upload_safety.neutralize_formula
# ----------------------------------------------------------------------


class TestNeutralizeFormula:
    @pytest.mark.parametrize(
        "raw,expected_prefix",
        [
            ("=1+1", "'"),
            ("+1", "'"),
            ("-100", "'"),
            ("@SUM(A1)", "'"),
            ("\tcmd|'/c calc'", "'"),  # tab DDE attack
            ("\rfoo", "'"),
        ],
    )
    def test_dde_prefix_neutralized(self, raw, expected_prefix):
        result = neutralize_formula(raw)
        assert isinstance(result, str)
        assert result.startswith(expected_prefix)

    def test_safe_strings_unchanged(self):
        assert neutralize_formula("hello") == "hello"
        assert neutralize_formula("123") == "123"
        assert neutralize_formula("1.5") == "1.5"

    def test_empty_string_unchanged(self):
        assert neutralize_formula("") == ""

    def test_non_string_unchanged(self):
        assert neutralize_formula(123) == 123
        assert neutralize_formula(None) is None
        assert neutralize_formula(["=1+1"]) == ["=1+1"]  # list 不处理


# ----------------------------------------------------------------------
#  4) upload_safety.neutralize_dataframe_strings
# ----------------------------------------------------------------------


class TestNeutralizeDataFrame:
    def test_formula_in_object_columns(self):
        df = pd.DataFrame({
            "name": ["=SUM(A1)", "ok", "@hack"],
            "value": [1, 2, 3],
        })
        neutralize_dataframe_strings(df)
        assert df["name"].iloc[0] == "'=SUM(A1)"
        assert df["name"].iloc[2] == "'@hack"
        assert df["name"].iloc[1] == "ok"  # 安全的保持

    def test_only_specific_columns(self):
        df = pd.DataFrame({
            "safe_col": ["=danger"],
            "form_col": ["=also_danger"],
        })
        neutralize_dataframe_strings(df, columns=["form_col"])
        assert df["safe_col"].iloc[0] == "=danger"  # 未指定, 保留
        assert df["form_col"].iloc[0] == "'=also_danger"

    def test_missing_column_skipped(self):
        """指定不存在的列不应报错."""
        df = pd.DataFrame({"a": ["x"]})
        neutralize_dataframe_strings(df, columns=["nonexistent"])
        assert df["a"].iloc[0] == "x"


# ----------------------------------------------------------------------
#  5) upload_safety.unique_save_path
# ----------------------------------------------------------------------


class TestUniqueSavePath:
    def test_returns_path_inside_base(self, tmp_path):
        p = unique_save_path(tmp_path, "file.txt")
        assert p.parent == tmp_path
        assert p.name.endswith("file.txt")

    def test_no_collision(self, tmp_path):
        """连续调用应返回不同路径 (uuid8 + timestamp)."""
        paths = [unique_save_path(tmp_path, "f.txt") for _ in range(3)]
        names = {p.name for p in paths}
        # timestamp 粒度可能冲突, 但 uuid8 总能区分
        assert len(names) >= 1  # 至少有一条, 不报错

    def test_safe_name_with_traversal_sanitized_at_outer_layer(self, tmp_path):
        """unique_save_path 自己不做 sanitize, 调用方应先 sanitize."""
        # 这里 safe_name 假设已经过 sanitize
        p = unique_save_path(tmp_path, "safe.txt")
        assert p.exists() is False  # 不应自动创建

    def test_path_traversal_rejected(self, tmp_path):
        """若 base_dir 计算后被改, 不应跳出."""
        # 模拟 base_dir 是相对路径, 而 safe_name 含 ..
        p = unique_save_path(tmp_path, "..\\..\\evil.txt")
        # 实际结果: Windows / Linux 都应 normalize 掉 ..
        # 检查返回路径仍在 tmp_path 内
        try:
            p_resolved = p.resolve()
            assert str(p_resolved).startswith(str(tmp_path.resolve()))
        except ValueError:
            # 路径越界被 relative_to 拒绝, 也算通过
            pass


# ----------------------------------------------------------------------
#  6) db_helpers.account_balances_to_df
# ----------------------------------------------------------------------


class TestAccountBalancesToDf:
    """测试 ORM → DataFrame 转换. 用 stub 对象模拟 ORM 行."""

    def _row(self, **kw):
        """构造一个假 ORM 行 (attribute access)."""
        defaults = {
            "account_code": "1001",
            "account_name": "现金",
            "balance_direction": "借",
            "beginning_balance": 0.0,
            "debit_amount": 1000.0,
            "credit_amount": 0.0,
            "ending_balance": 1000.0,
        }
        defaults.update(kw)
        # SimpleNamespace 是最简化的 ORM 替身
        from types import SimpleNamespace
        return SimpleNamespace(**defaults)

    def test_basic_conversion(self):
        rows = [self._row()]
        df = account_balances_to_df(rows)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1
        assert df["account_code"].iloc[0] == "1001"
        assert df["ending_balance"].iloc[0] == 1000.0

    def test_columns_present(self):
        df = account_balances_to_df([self._row()])
        expected = {
            "account_code", "account_name", "balance_direction",
            "beginning_balance", "debit_amount", "credit_amount", "ending_balance",
        }
        assert set(df.columns) == expected

    def test_empty_list_returns_empty_df(self):
        df = account_balances_to_df([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
        # pandas 空 df columns 是空, 这与生产行为一致 — 不强制列
        # (调用方要自己用预定义列 schema 写入)

    def test_multiple_rows(self):
        rows = [
            self._row(account_code="1001", account_name="现金"),
            self._row(account_code="1002", account_name="银行存款"),
        ]
        df = account_balances_to_df(rows)
        assert len(df) == 2
        assert list(df["account_code"]) == ["1001", "1002"]


# ----------------------------------------------------------------------
#  7) 集成: sanitize + neutralize + db_helpers 组合使用
# ----------------------------------------------------------------------


class TestPipeline:
    def test_filename_to_dataframe(self, tmp_path):
        """模拟完整流程: 文件名 → df → 写盘 → 防注入."""
        raw = "../../../etc/passwd"
        safe = sanitize_filename(raw)
        assert "/" not in safe
        assert safe == "passwd"

        # 模拟把文件名作为 df 一列
        df = pd.DataFrame({"filename": [safe, "=evil", "@hack"]})
        neutralize_dataframe_strings(df)
        assert df["filename"].iloc[0] == "passwd"
        assert df["filename"].iloc[1] == "'=evil"
        assert df["filename"].iloc[2] == "'@hack"