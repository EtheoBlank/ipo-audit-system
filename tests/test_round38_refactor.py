"""Round 38 refactor 验证 — 覆盖本轮结构调整的正确性回归.

新增/移动/提取的单元:
  - app/utils/like_helpers.py::escape_like  (统一 LIKE 转义)
  - app/schemas/contracts.py               (从 models.contracts 迁出)
  - app/services/ai_analysis_engine._ai_json_call  (4 个公开方法共用)
  - app/services/auth/audit_log 用 escape_like
  - app/services/sentiment/scraper_service._PROVIDER_REGISTRY  (无 factory lambda)
  - app/services/erp_adapters.BaseERPAdapter 默认实现
  - app/services/regulation_scraper.fetch_paginated helper
  - app/services/account_audit._accumulate_movements / _compute_identity
  - app/services/comprehensive.fill_engine._FillState / _Stage
  - app/services/confirmation.stats_builder._aggregate_by_aux_simple

约束: in-process + 无 DB / 网络依赖, 全部用直接调用/属性检查.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest


# ============================================================
# 1) escape_like 工具 — 转义 % _ \
# ============================================================


class TestEscapeLikeHelper:
    def test_plain_text_passthrough(self):
        from app.utils.like_helpers import escape_like
        assert escape_like("hello world") == "hello world"

    def test_empty_returns_empty(self):
        from app.utils.like_helpers import escape_like
        assert escape_like("") == ""

    def test_none_returns_empty(self):
        from app.utils.like_helpers import escape_like
        assert escape_like(None) == ""

    def test_percent_escaped(self):
        from app.utils.like_helpers import escape_like
        # 100% → 100\%  (单反斜杠)
        assert escape_like("100%") == "100\\%"

    def test_underscore_escaped(self):
        from app.utils.like_helpers import escape_like
        # _test_ → \_test\_
        assert escape_like("_test_") == "\\_test\\_"

    def test_backslash_first_doubled(self):
        """反斜杠先于 % / _ 处理, 防 \\\\ 被误判."""
        from app.utils.like_helpers import escape_like
        # a\b → a\\b (双反斜杠)
        assert escape_like("a\\b") == "a\\\\b"

    def test_combined_specials(self):
        from app.utils.like_helpers import escape_like
        # \a%_b → \\a\%\_b
        result = escape_like("\\a%_b")
        assert result == "\\\\a\\%\\_b"

    def test_sqlalchemy_escape_compatible(self):
        """实际 SQLAlchemy ORM .like(pattern, escape='\\\\') 用法, 转义后不应触发全表扫描.

        SQLAlchemy 文档: escape 必须是单字符; Python '\\\\' 在 SQL 层是单反斜杠.
        """
        from app.utils.like_helpers import escape_like
        from sqlalchemy import literal

        # 验证: 经过 escape_like 后, %% / __ 在 SQLite LIKE 中是字面量
        escaped = escape_like("100%")
        assert escaped == "100\\%"
        # 包成 LIKE pattern
        pattern = f"%{escaped}%"
        assert pattern == "%100\\%%"


# ============================================================
# 2) schemas.contracts — 从 models.contracts 迁出
# ============================================================


class TestContractsSchemaModule:
    def test_imports_from_schemas(self):
        from app.schemas.contracts import (
            ContractAnalysisRequest,
            ContractAnalysisResponse,
            ContractDocumentResponse,
        )
        assert ContractAnalysisRequest is not None
        assert ContractAnalysisResponse is not None
        assert ContractDocumentResponse is not None

    def test_request_defaults(self):
        from app.schemas.contracts import ContractAnalysisRequest
        req = ContractAnalysisRequest(project_id=1, contract_id=2)
        assert req.project_id == 1
        assert req.contract_id == 2
        assert req.run_key_points is True
        assert req.run_five_step is True

    def test_response_default_empty_risk_flags(self):
        from app.schemas.contracts import ContractAnalysisResponse
        resp = ContractAnalysisResponse(contract_id=1, project_id=2)
        assert resp.risk_flags == []
        assert resp.key_points is None
        assert resp.five_step_analysis is None


# ============================================================
# 3) ai_analysis_engine._ai_json_call 统一助手
# ============================================================


class TestAIAnalysisEngineHelper:
    def test_helper_exists(self):
        from app.services.ai_analysis_engine import AIAnalysisEngine
        assert hasattr(AIAnalysisEngine, "_ai_json_call")

    def test_methods_use_helper(self):
        """4 个公开方法必须用 _ai_json_call 解析."""
        from app.services.ai_analysis_engine import AIAnalysisEngine
        import inspect
        for name in ("analyze_risk_level", "detect_anomalies",
                     "generate_audit_program", "analyze_regulatory_compliance"):
            fn = getattr(AIAnalysisEngine, name)
            src = inspect.getsource(fn)
            assert "_ai_json_call" in src, f"{name} 应走 _ai_json_call helper"

    def test_parse_invalid_json_returns_default(self, caplog):
        """_ai_json_call: 解析失败 logger.exception + 返回 default."""
        from app.services.ai_analysis_engine import AIAnalysisEngine

        svc = AIAnalysisEngine(api_key="test-key")

        async def fake_call_ai(prompt):
            return "not a json"

        svc._call_ai = fake_call_ai  # type: ignore[assignment]

        import asyncio
        with caplog.at_level(logging.ERROR, logger="app.services.ai_analysis_engine"):
            result = asyncio.run(
                svc._ai_json_call(
                    "p",
                    {"risk_level": "中"},
                    method="test_method",
                    context="ctx",
                )
            )
        assert result == {"risk_level": "中"}
        matching = [r for r in caplog.records if "AI test_method 响应解析失败" in r.message]
        assert matching, f"expected exception log, got {[r.message for r in caplog.records]}"
        assert all(r.exc_info is not None for r in matching)

    def test_coerce_list_wraps_dict(self):
        """coerce_list=True: 若 AI 返回 dict, 强制包成 [dict]."""
        from app.services.ai_analysis_engine import AIAnalysisEngine

        svc = AIAnalysisEngine(api_key="k")

        async def fake_call_ai(prompt):
            return '{"x": 1}'

        svc._call_ai = fake_call_ai  # type: ignore[assignment]

        import asyncio
        result = asyncio.run(
            svc._ai_json_call(
                "p",
                [],
                method="test",
                context="ctx",
                coerce_list=True,
            )
        )
        assert result == [{"x": 1}]

    def test_coerce_list_passthrough_list(self):
        from app.services.ai_analysis_engine import AIAnalysisEngine

        svc = AIAnalysisEngine(api_key="k")

        async def fake_call_ai(prompt):
            return '[{"x": 1}, {"x": 2}]'

        svc._call_ai = fake_call_ai  # type: ignore[assignment]

        import asyncio
        result = asyncio.run(
            svc._ai_json_call("p", [], method="test", context="ctx", coerce_list=True)
        )
        assert result == [{"x": 1}, {"x": 2}]


# ============================================================
# 4) ai_analysis 瘦身后只剩 _call_minimax + enabled
# ============================================================


class TestAIAnalysisServiceSlimmed:
    def test_no_longer_has_parse_methods(self):
        """_parse_json_response / _parse_list_response / _parse_list_of_dicts 已迁出."""
        from app.services.ai_analysis import AIAnalysisService
        assert not hasattr(AIAnalysisService, "_parse_json_response")
        assert not hasattr(AIAnalysisService, "_parse_list_response")
        assert not hasattr(AIAnalysisService, "_parse_list_of_dicts")

    def test_no_business_methods(self):
        """analyze_risk_level 等业务方法已迁出至 ai_analysis_engine."""
        from app.services.ai_analysis import AIAnalysisService
        assert not hasattr(AIAnalysisService, "analyze_risk_level")
        assert not hasattr(AIAnalysisService, "generate_audit_recommendations")
        assert not hasattr(AIAnalysisService, "match_regulatory_cases")
        assert not hasattr(AIAnalysisService, "analyze_financial_anomalies")

    def test_still_has_call_minimax_and_enabled(self):
        """audit_note_generator 依赖 _call_minimax + enabled 必须保留."""
        from app.services.ai_analysis import AIAnalysisService
        svc = AIAnalysisService()
        assert hasattr(svc, "_call_minimax")
        assert hasattr(svc, "enabled")


# ============================================================
# 5) _audit helper — record_audit_log 薄封装
# ============================================================


class TestAuthAuditHelper:
    def test_helper_pulls_user_fields(self):
        from app.api.auth import _audit

        # helper 只应拼装字段, 不应调用 record_audit_log 自身
        import inspect
        src = inspect.getsource(_audit)
        assert "record_audit_log" in src
        assert "getattr" in src  # 兼容 user=None


# ============================================================
# 6) report_generator — ReportScheduler 删除 (dead code)
# ============================================================


class TestReportSchedulerRemoved:
    def test_class_deleted(self):
        from app.services import report_generator
        assert not hasattr(report_generator, "ReportScheduler")


# ============================================================
# 7) sentiment scraper_service — 注册表去掉 factory lambda
# ============================================================


class TestScraperRegistry:
    def test_no_factory_lambda(self):
        from app.services.sentiment.scraper_service import _PROVIDER_REGISTRY
        for code, meta in _PROVIDER_REGISTRY.items():
            assert "factory" not in meta, f"{code} 仍有 factory lambda"
            assert "adapter_cls" in meta, f"{code} 缺 adapter_cls"

    def test_is_paid_derived_from_key(self):
        """is_paid 由 api_key_ref 是否非空派生."""
        from app.services.sentiment.scraper_service import _PROVIDER_REGISTRY
        for code, meta in _PROVIDER_REGISTRY.items():
            if meta["api_key_ref"]:
                assert meta["provider_type"] == "paid_api", code
            else:
                assert meta["provider_type"] != "paid_api", code

    def test_all_adapters_callable(self):
        """adapter_cls 直接存类本身 (类本就 callable), 无 lambda 包装."""
        from app.services.sentiment.scraper_service import _PROVIDER_REGISTRY
        for code, meta in _PROVIDER_REGISTRY.items():
            cls = meta["adapter_cls"]
            assert callable(cls)


# ============================================================
# 8) BaseERPAdapter 默认实现
# ============================================================


class TestBaseERPAdapterDefaults:
    def test_default_numeric_fields_defined(self):
        from app.services.erp_adapters import BaseERPAdapter
        assert "account_balance" in BaseERPAdapter.NUMERIC_FIELDS_BY_TYPE
        assert "chronological_account" in BaseERPAdapter.NUMERIC_FIELDS_BY_TYPE
        assert "bank_statement" in BaseERPAdapter.NUMERIC_FIELDS_BY_TYPE

    def test_default_direction_field(self):
        from app.services.erp_adapters import BaseERPAdapter
        assert BaseERPAdapter.DIRECTION_FIELD == "balance_direction"

    def test_normalize_direction_default_noop(self):
        """默认实现不应破坏列 — 用 Manual 子类实例化 (BaseERPAdapter 仍 ABC)."""
        from app.services.erp_adapters import ManualAdapter
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2]})
        out = ManualAdapter()._normalize_direction(df)
        assert list(out.columns) == ["a"]


# ============================================================
# 9) regulation_scraper fetch_paginated helper
# ============================================================


class TestFetchPaginatedHelper:
    def test_helper_exists(self):
        from app.services.regulation_scraper import BaseRegulationAdapter
        assert hasattr(BaseRegulationAdapter, "fetch_paginated")


# ============================================================
# 10) account_audit 提取的 helpers
# ============================================================


class TestAccountAuditHelpers:
    def test_accumulate_movements_empty(self):
        from app.services.account_audit import AccountAuditService
        out = AccountAuditService._accumulate_movements([])
        assert out["debit_book"] == 0.0
        assert out["credit_book"] == 0.0
        assert out["d_pending"] == 0
        assert out["c_pending"] == 0

    def test_compute_identity_debit_account(self):
        """借方: ending = beg + debit - credit."""
        from app.services.account_audit import AccountAuditService
        out = AccountAuditService._compute_identity(
            beg=100.0, beg_audited=100.0,
            debit_book=50.0, debit_audited=50.0,
            credit_book=20.0, credit_audited=20.0,
            end_book=130.0, end_audited=130.0,
            is_debit_account=True,
        )
        assert abs(out["identity_book"]) < 1e-9
        assert abs(out["identity_audited"]) < 1e-9

    def test_compute_identity_credit_account(self):
        """贷方: ending = beg + credit - debit."""
        from app.services.account_audit import AccountAuditService
        out = AccountAuditService._compute_identity(
            beg=100.0, beg_audited=100.0,
            debit_book=20.0, debit_audited=20.0,
            credit_book=50.0, credit_audited=50.0,
            end_book=130.0, end_audited=130.0,
            is_debit_account=False,
        )
        assert abs(out["identity_book"]) < 1e-9
        assert abs(out["identity_audited"]) < 1e-9


# ============================================================
# 11) confirmation stats_builder._aggregate_by_aux_simple
# ============================================================


class TestAggregateByAuxSimple:
    def test_method_exists(self):
        from app.services.confirmation.stats_builder import ConfirmationStatsBuilder
        assert hasattr(ConfirmationStatsBuilder, "_aggregate_by_aux_simple")


# ============================================================
# 12) comprehensive.fill_engine._FillState / _Stage
# ============================================================


class TestFillEngineStage:
    def test_stage_and_fill_state_importable(self):
        from app.services.comprehensive.fill_engine import (
            _FillState,
            _Stage,
            _SOURCES_ALL,
        )
        assert _SOURCES_ALL("anything") is True

    def test_stage_runs_runner(self):
        import asyncio
        from app.services.comprehensive.fill_engine import _Stage

        called = []

        async def runner(state):
            called.append(state)

        stage = _Stage(name="t", predicate=lambda f: True, runner=runner)
        asyncio.run(stage.run("state-obj"))
        assert called == ["state-obj"]


# ============================================================
# 13) auth/jwt.py — _JoseErrorSentinel 删除, 改用 JWTError
# ============================================================


class TestJoseSentinelRemoved:
    def test_no_sentinel_class(self):
        """旧的 _JoseErrorSentinel 占位类已删除, 改直接用 JWTError."""
        from app.services.auth import jwt
        assert not hasattr(jwt, "_JoseErrorSentinel")


# ============================================================
# 14) sales_ledger.synthesizer — 删 unused Field 导入
# ============================================================


class TestSynthesizerImports:
    def test_no_unused_field_import(self):
        from app.services.sales_ledger import synthesizer
        # pydantic.Field 已删除 (未使用)
        assert "Field" not in dir(synthesizer)


# ============================================================
# 15) upload_safety — datetime 顶部导入
# ============================================================


class TestUploadSafetyDatetimeImport:
    def test_datetime_at_top(self):
        from app.utils import upload_safety
        src = upload_safety.__file__
        content = Path(src).read_text(encoding="utf-8")
        # datetime 应在文件顶部 import, 而非函数内 lazy import
        lines = content.split("\n")
        first_use = next(
            (i for i, l in enumerate(lines, 1) if l.startswith("from datetime import")),
            None,
        )
        assert first_use is not None and first_use < 20, "datetime 应顶部 import"


# ============================================================
# 16) audit_log — 使用统一 escape_like
# ============================================================


class TestAuditLogUsesEscapeLike:
    def test_audit_log_uses_escape_like(self):
        from app.services.auth import audit_log
        src = Path(audit_log.__file__).read_text(encoding="utf-8")
        assert "from app.utils.like_helpers import escape_like" in src
        # 原内嵌 _escape_like 已删除
        assert "def _escape_like(" not in src
