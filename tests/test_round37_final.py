"""Round 37 P1 收尾 — silent except 修复 + walrus SyntaxError 修复 验证.

覆盖:
  - app/services/report_generator.py: import + 3 个类可实例化 (本文件无 except, smoke 即可)
  - app/services/excel_parser.py: import + 4 个 parse_* 方法可调用 (本文件无 except, smoke 即可)
  - app/services/ai_analysis.py: _call_minimax 1 处 except 现在 logger.exception 留 traceback
  - _probe/round32_repro.py: 之前 walrus in kwarg SyntaxError, 修复后可 ast.parse

不依赖 DB / 网络 / 真实 API — 全部 in-process.
"""
from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================
# 报告生成器 — smoke (本文件无 except, 只验 import + 关键类可构造)
# ============================================================


class TestReportGeneratorSmoke:
    """app/services/report_generator.py — 3 类 (ReportScheduler 已删) + 综合集成 smoke."""

    def test_report_generator_importable(self):
        from app.services import report_generator  # noqa: F401
        mod = report_generator
        assert hasattr(mod, "ComprehensiveReportGenerator")
        assert hasattr(mod, "InteractiveReportGenerator")
        assert hasattr(mod, "PDFReportGenerator")
        # ReportScheduler 已删除 (dead code, 0 引用)

    def test_comprehensive_constructible(self):
        from app.services.report_generator import ComprehensiveReportGenerator
        gen = ComprehensiveReportGenerator()
        assert gen.report_date  # 非空字符串

    def test_pdf_report_generator_callable(self):
        from app.services.report_generator import PDFReportGenerator

        assert callable(PDFReportGenerator.generate_pdf)

    def test_interactive_report_generator_callable(self):
        from app.services.report_generator import InteractiveReportGenerator

        assert callable(InteractiveReportGenerator.generate_dashboard_data)
        assert callable(InteractiveReportGenerator.generate_trend_data)


# ============================================================
# Excel parser — smoke (本文件无 except, 验 4 个 parse_* 方法可调用 + _safe_temp_path 防穿越)
# ============================================================


class TestExcelParserSmoke:
    """app/services/excel_parser.py — 4 个 parse_* 方法 + _safe_temp_path 安全."""

    def test_excel_parser_importable(self):
        from app.services import excel_parser  # noqa: F401
        mod = excel_parser
        assert hasattr(mod, "ExcelParser")
        assert hasattr(mod, "_safe_temp_path")

    def test_safe_temp_path_rejects_traversal(self):
        """_safe_temp_path 必须拒绝 '.'/'..' 字面文件名 (其他 traversal 被 Path.name 剥离)."""
        from app.services.excel_parser import _safe_temp_path

        class FakeUpload:
            filename = ".."

        with pytest.raises(ValueError, match="非法的文件名"):
            _safe_temp_path(FakeUpload())

        class FakeUploadDot:
            filename = "."

        with pytest.raises(ValueError, match="非法的文件名"):
            _safe_temp_path(FakeUploadDot())

    def test_safe_temp_path_strips_traversal_via_name(self):
        """'../etc/passwd' 经 Path.name 剥离成 'passwd' → 落地到 UPLOAD_DIR/temp_passwd, 不越界."""
        from pathlib import Path

        from app.core.config import settings
        from app.services.excel_parser import _safe_temp_path

        class FakeUpload:
            filename = "../etc/passwd"

        # 不抛, 但路径必须在 UPLOAD_DIR 内
        result = _safe_temp_path(FakeUpload())
        assert result.name == "temp_passwd"
        assert result.resolve().is_relative_to(settings.UPLOAD_DIR.resolve())

    def test_safe_temp_path_accepts_clean_name(self):
        """_safe_temp_path 接受干净文件名."""
        from fastapi import UploadFile

        from app.services.excel_parser import _safe_temp_path

        class FakeUpload:
            filename = "ledger.xlsx"

        path = _safe_temp_path(FakeUpload())
        assert path.name == "temp_ledger.xlsx"
        assert path.exists() is False  # 没真写

    def test_parse_methods_callable(self):
        from app.services.excel_parser import ExcelParser

        assert callable(ExcelParser.parse_account_balance)
        assert callable(ExcelParser.parse_chronological_account)
        assert callable(ExcelParser.parse_bank_statement)
        assert callable(ExcelParser.parse_csv)


# ============================================================
# AI 分析服务 — _call_minimax except 现在 logger.exception 留 traceback
# (4 个 _parse_* 已迁出至 ai_analysis_engine.py, 此处不再覆盖)
# ============================================================


class TestAIAnalysisSilentExcepts:
    """app/services/ai_analysis.py — _call_minimax except 路径走 logger.exception."""

    def test_ai_analysis_importable(self):
        from app.services import ai_analysis  # noqa: F401
        assert hasattr(ai_analysis, "AIAnalysisService")
        assert hasattr(ai_analysis, "logger")

    def test_call_minimax_logs_exception_on_httpx_error(self, caplog):
        """_call_minimax: httpx 抛错时现在 logger.exception 留痕 (原来纯 silent return)."""
        from app.services.ai_analysis import AIAnalysisService

        svc = AIAnalysisService(api_key="test-key")

        # Mock httpx.AsyncClient 让 post 抛 httpx.HTTPError
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=RuntimeError("simulated network failure"))

        with caplog.at_level(logging.ERROR, logger="app.services.ai_analysis"):
            with patch("httpx.AsyncClient", return_value=mock_client):
                import asyncio

                result = asyncio.run(svc._call_minimax("hello", "sys"))

        # 仍返回错误字符串 (fallback 保留)
        assert "AI分析调用失败" in result
        # 现在必须 logger.exception 留 traceback
        matching = [r for r in caplog.records if "AIAnalysisService._call_minimax 调用失败" in r.message]
        assert len(matching) >= 1, (
            f"round37 修复后 _call_minimax 必须 logger.exception, "
            f"got {[r.message for r in caplog.records]}"
        )
        assert all(r.exc_info is not None for r in matching), "logger.exception 必须带 exc_info"


# ============================================================
# _probe/round32_repro.py — walrus in kwarg SyntaxError 修复
# ============================================================


class TestRound32ReproSyntax:
    """_probe/round32_repro.py — 之前 walrus 语法报错, 修复后 ast.parse 通过 + 关键 token 已替换."""

    def test_round32_repro_syntax_ok(self):
        """ast.parse 应不再 SyntaxError."""
        path = Path(__file__).resolve().parent.parent / "_probe" / "round32_repro.py"
        src = path.read_text(encoding="utf-8")
        try:
            ast.parse(src)
        except SyntaxError as e:
            pytest.fail(f"round32_repro.py 仍有 SyntaxError: {e}")

    def test_round32_repro_no_walrus_in_kwarg(self):
        """扫描源码: 实际代码行 (非注释) 中不能存在 own_token=...:= 这种 walrus in kwarg."""
        path = Path(__file__).resolve().parent.parent / "_probe" / "round32_repro.py"
        src = path.read_text(encoding="utf-8")
        import re
        # 逐行扫描, 跳过注释行 (# 开头) 和纯字符串行
        bad: list[tuple[int, str]] = []
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # 检查 active code 行
            if re.search(r"\bown_token\s*=\s*\w+\s*:=", line):
                bad.append((i, line.rstrip()))
        assert bad == [], f"仍存在 walrus in kwarg (行号 + 内容): {bad}"

    def test_round32_repro_qc_token_assigned_separately(self):
        """修复方式: 先 qc_token = make_token(...) 再 own_token=qc_token."""
        path = Path(__file__).resolve().parent.parent / "_probe" / "round32_repro.py"
        src = path.read_text(encoding="utf-8")
        # 验证存在独立的赋值语句
        assert "qc_token = make_token(" in src, "修复后应存在 qc_token = make_token(...) 独立赋值"
        # 验证 _probe 调用 own_token=qc_token (无 walrus)
        assert "own_token=qc_token," in src or "own_token=qc_token)" in src, (
            "_probe 调用应传 own_token=qc_token"
        )

    def test_round32_repro_can_compile(self):
        """compile 也应通过 (更严格)."""
        path = Path(__file__).resolve().parent.parent / "_probe" / "round32_repro.py"
        src = path.read_text(encoding="utf-8")
        try:
            compile(src, str(path), "exec")
        except SyntaxError as e:
            pytest.fail(f"compile 失败: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])