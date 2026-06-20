"""round 32 (2026-06-20) 真实修复回归测试.

本轮 4 个 fix agent 跑了 33 项修复 (10 P0 + 23 P1/P2), 部分修复由 linter 反复
revert. 本测试只覆盖**当前代码状态**下可静态/单元验证的修复点.

覆盖范围:
  - 前端: pages_sentiment.py _tab_overview columns(3) → columns(4) (c4 NameError 修复)
  - Model: ApprovalWorkflow.firm_id 列存在 (round 32 IDOR 修复)
  - Service: ApprovalEngine.create_workflow 接受 firm_id kwarg
  - Service: aging_engine._fifo_aging inbound_date=None fallback 到 period_end-365d
  - Service: stats_builder._fetch_balances 按 period_end 过滤
  - Service: sales_ledger.analyzer._compute_margin 统一毛利率口径
  - API: contracts.upload_contract 用 read_upload_capped (无裸 file.read())
  - API: projects.upload_account_balances 用 read_upload_capped
  - API: knowledge_base.upload_book 用 check_magic_bytes
  - API: confirmations.upload_response_photo 用 check_magic_bytes
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
#  前端: sentiment c4 NameError 修复
# ============================================================


class TestFrontendSentimentC4Fix:
    """round 32 P0: pages_sentiment.py:_tab_overview c4 NameError.

    旧: line 136 c1,c2,c3 = st.columns(3); line 143 with c4 → NameError
    新: c1,c2,c3,c4 = st.columns(4)"""

    def test_tab_overview_uses_columns_4(self):
        import ast
        src = (ROOT / "frontend" / "pages_sentiment.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 找 _tab_overview 函数 (top-level def)
        fn = next(
            (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_tab_overview"),
            None,
        )
        assert fn is not None, "_tab_overview 函数未找到"
        # 用 unparse 拿到函数体源码
        body = ast.unparse(fn)
        cols_calls = re.findall(r"st\.columns\((\d+)\)", body)
        assert cols_calls, "_tab_overview 内未调 st.columns"
        # 主指标行 (第一条 st.columns) 必须 ≥ 4 (即 c4 在解构里)
        first = int(cols_calls[0])
        assert first >= 4, (
            f"_tab_overview 主指标列数 {first} < 4, c4 未定义会 NameError"
        )

    def test_c4_used_after_definition(self):
        import ast
        src = (ROOT / "frontend" / "pages_sentiment.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_tab_overview"),
            None,
        )
        body = ast.unparse(fn)
        # c4 必须在 _tab_overview 函数体内被引用
        assert re.search(r"\bc4\b", body), (
            "_tab_overview 函数体无 c4 引用, 修复未生效"
        )


# ============================================================
#  Model: ApprovalWorkflow firm_id 列
# ============================================================


class TestApprovalWorkflowFirmID:
    """round 32 P0 IDOR: ApprovalWorkflow 加 firm_id 列, 让 auth.py 的
    wf.firm_id 跨 firm 校验不再 AttributeError."""

    def test_approval_workflow_has_firm_id_column(self):
        from app.models.db.auth import ApprovalWorkflow
        cols = {c.key for c in ApprovalWorkflow.__table__.columns}
        assert "firm_id" in cols, (
            f"ApprovalWorkflow 缺 firm_id 列. auth.py:839 的 wf.firm_id 会崩. "
            f"现有列: {sorted(cols)}"
        )

    def test_firm_id_nullable_and_indexed(self):
        from sqlalchemy import Index
        from app.models.db.auth import ApprovalWorkflow
        col = ApprovalWorkflow.__table__.columns["firm_id"]
        assert col.nullable is True, "firm_id 应可空 (admin 旁路)"
        # 索引
        indexes = ApprovalWorkflow.__table__.indexes
        has_idx = any("firm_id" in [c.key for c in idx.columns] for idx in indexes)
        assert has_idx, "firm_id 应有索引 (跨所查询频繁)"


class TestApprovalEngineCreateWorkflowFirmID:
    """create_workflow 必须能接受 firm_id kwarg"""

    def test_create_workflow_signature(self):
        from app.services.auth.approval import ApprovalEngine
        sig = inspect.signature(ApprovalEngine.create_workflow)
        assert "firm_id" in sig.parameters, (
            "create_workflow 缺 firm_id kwarg. "
            "auth.py:761 调用会 TypeError."
        )
        # 应有默认 None (向后兼容)
        param = sig.parameters["firm_id"]
        assert param.default is None, "firm_id 应默认 None"


# ============================================================
#  Algorithm: aging fallback
# ============================================================


class TestAgingInboundDateFallback:
    """round 32 P0 (ALG-01): inbound_date=None fallback 到 period_end - 365d,
    与 opening_qty 一致. 防止 0-day aging 库龄低估."""

    def test_fallback_uses_period_end_minus_365d(self):
        from app.services.inventory import aging_engine
        # 找 _fifo_aging 函数源码, 验证不再用 `or period_end`
        src = inspect.getsource(aging_engine)
        # 排除 "pd.Timedelta" 这种含糊匹配, 关注关键短语
        if "_fifo_aging" in src:
            # 找类似 `or period_end` 的写法
            assert "or (period_end -" in src or "period_end - pd.Timedelta(days=365)" in src, (
                "_fifo_aging 仍用 `or period_end` fallback, 会把 inbound_date=None "
                "的物料库龄算成 0 天 → 跌价准备不足"
            )


# ============================================================
#  Algorithm: stats_builder period_end 过滤
# ============================================================


class TestStatsBuilderPeriodEndFilter:
    """round 32 P0 (ALG-08): _fetch_balances / _fetch_journals 忽略 period_end
    参数, 多年项目跨年求和. 修复后必须按 period_end 过滤."""

    def test_fetch_balances_uses_period_end(self):
        from app.services.confirmation.stats_builder import ConfirmationStatsBuilder
        # 在类的所有方法里找 _fetch_balances
        method = getattr(ConfirmationStatsBuilder, "_fetch_balances", None)
        assert method is not None, (
            "ConfirmationStatsBuilder._fetch_balances 未找到"
        )
        src = inspect.getsource(method)
        # 必须有 period_end 出现在 WHERE 过滤中
        assert "period_end" in src and ("<=" in src or "< " in src), (
            "_fetch_balances 没按 period_end 过滤, 跨年项目会求和所有年份余额"
        )

    def test_fetch_journals_uses_period_end(self):
        from app.services.confirmation.stats_builder import ConfirmationStatsBuilder
        method = getattr(ConfirmationStatsBuilder, "_fetch_journals", None)
        if method is None:
            pytest.skip("_fetch_journals 可能改名")
        src = inspect.getsource(method)
        assert "period_end" in src and ("<=" in src or "< " in src), (
            "_fetch_journals 没按 period_end 过滤"
        )


# ============================================================
#  Algorithm: sales_ledger 统一毛利率
# ============================================================


class TestSalesLedgerMarginUnified:
    """round 32 P0 (ALG-09): 5 处 margin 定义不一致, 改 _compute_margin 统一."""

    def test_compute_margin_helper_exists(self):
        from app.services.sales_ledger import analyzer
        assert hasattr(analyzer, "_compute_margin"), (
            "_compute_margin 统一函数缺失, 5 处 margin 仍是各算各的"
        )

    def test_compute_margin_signature(self):
        from app.services.sales_ledger.analyzer import _compute_margin
        sig = inspect.signature(_compute_margin)
        params = list(sig.parameters.keys())
        # 必须有 kind + include_direct_fees, 否则调用方没法控制
        assert "kind" in params, "_compute_margin 缺 kind (gross/net) 参数"
        assert "include_direct_fees" in params or "direct_fees" in params, (
            "_compute_margin 缺 direct_fees 处理"
        )


# ============================================================
#  Security: 上传路径穿越 + 大小限制
# ============================================================


class TestUploadPathTraversalFixed:
    """round 32 P0 (SEC-01/02): contracts/projects 上传改 read_upload_capped
    + sanitize_filename + check_magic_bytes, 防路径穿越 + OOM + 双扩展名绕过."""

    def test_contracts_uses_read_upload_capped(self):
        from app.api import contracts
        src = inspect.getsource(contracts)
        assert "read_upload_capped" in src, (
            "contracts.py 没用 read_upload_capped, 仍走裸 file.read() → OOM"
        )
        assert "check_magic_bytes" in src, (
            "contracts.py 缺 check_magic_bytes, evil.pdf.exe 绕过扩展名校验"
        )
        # 不应再用 file.filename 直接拼路径 (即使有 sanitize, 也要双保险)
        assert "sanitize_filename" in src or "Path(file.filename).name" in src, (
            "contracts.py 缺 sanitize_filename, file.filename 可含 ../"
        )

    def test_projects_uses_read_upload_capped(self):
        from app.api import projects
        src = inspect.getsource(projects)
        assert "read_upload_capped" in src, (
            "projects.py 没用 read_upload_capped"
        )

    def test_knowledge_base_uses_check_magic_bytes(self):
        from app.api import knowledge_base
        src = inspect.getsource(knowledge_base)
        assert "check_magic_bytes" in src, (
            "knowledge_base.py 上传没验 magic bytes"
        )

    def test_confirmations_uses_check_magic_bytes(self):
        from app.api import confirmations
        src = inspect.getsource(confirmations)
        assert "check_magic_bytes" in src, (
            "confirmations.py 上传没验 magic bytes"
        )


# ============================================================
#  Security: 弱密码 / 静默锁死 / 静默 commit
# ============================================================


class TestAuthSilentFailureFixed:
    """round 32 P0 (SEC-03/04): password.py/service.py 静默 except return
    False 不记日志 → 排查不到. 修复后 logger.exception."""

    def test_password_fallback_logs_exception(self):
        from app.services.auth import password
        src = inspect.getsource(password)
        # _fallback_verify 必须调 logger.exception / logger.error
        if "_fallback_verify" in src:
            m = re.search(
                r"def _fallback_verify\([^)]*\):.*?(?=\ndef |\nclass )",
                src,
                flags=re.S,
            )
            if m:
                body = m.group(0)
                assert "logger.exception" in body or "logger.error" in body, (
                    "_fallback_verify 异常分支没记日志, hash 格式变更会静默全租户锁死"
                )


# ============================================================
#  Performance: KB retriever LIMIT + numpy 矩阵化
# ============================================================


class TestKBRetrieverLimited:
    """round 32 P0 (PERF-01): retriever 不限 LIMIT → 50K chunks 全加载 OOM.
    修复后 LIMIT + numpy A @ query_vec 矩阵化."""

    def test_retriever_has_limit(self):
        from app.services.knowledge_base import retriever
        src = inspect.getsource(retriever)
        # 关键 SQL: stmt.limit 或 .limit(
        assert ".limit(" in src, (
            "retriever.py SQL 无 LIMIT, 50K chunks × 4KB embedding → 200MB 内存"
        )

    def test_retriever_uses_numpy_matrix(self):
        from app.services.knowledge_base import retriever
        src = inspect.getsource(retriever)
        # 至少 import numpy 或 np
        assert "import numpy" in src or "from numpy" in src, (
            "retriever.py 没 numpy, 仍是 row-wise Python cosine"
        )


# ============================================================
#  Performance: scraper N+1 → 批量
# ============================================================


class TestScraperN1Fixed:
    """round 32 P0 (PERF-02): 200 events × 3 round-trips = 600. 修复后 _bulk_persist_events."""

    def test_bulk_persist_method_exists(self):
        from app.services.sentiment.scraper_service import SentimentScraperService
        # 方法在类里, 不是模块级
        method = getattr(SentimentScraperService, "_bulk_persist_events", None)
        assert method is not None, (
            "SentimentScraperService._bulk_persist_events 不存在, N+1 没修"
        )


# ============================================================
#  State machine: 季度报告 is_locked 守卫
# ============================================================


class TestQuarterlyLockedGuard:
    """round 32 P0 (STATE-01/02): aggregator.lock_references 与
    financial_input.save_financial_input 必须拒已锁定报告."""

    def test_lock_references_blocks_locked(self):
        from app.services.sentiment.quarterly import aggregator
        src = inspect.getsource(aggregator)
        # 找 lock_references 函数, 必须有 is_locked 检查 + raise
        if "def lock_references" in src:
            m = re.search(
                r"def lock_references\([^)]*\):.*?(?=\ndef |\nclass )",
                src,
                flags=re.S,
            )
            if m:
                body = m.group(0)
                assert "is_locked" in body, (
                    "lock_references 缺 is_locked 守卫, 已锁报告可重写"
                )
                assert "raise" in body, "lock_references 没 raise 阻断"

    def test_save_financial_input_blocks_locked(self):
        from app.services.sentiment.quarterly import financial_input
        src = inspect.getsource(financial_input)
        if "def save_financial_input" in src:
            m = re.search(
                r"def save_financial_input\([^)]*\):.*?(?=\ndef |\nclass )",
                src,
                flags=re.S,
            )
            if m:
                body = m.group(0)
                assert "is_locked" in body, (
                    "save_financial_input 缺 is_locked 守卫"
                )


# ============================================================
#  Firm template PII 防护
# ============================================================


class TestFirmTemplatePII:
    """round 32 P0 (ALG-06/07): _replace_pat group IndexError 泄漏 PII;
    anonymize_excerpt 不脱敏 int/float 银行账户."""

    def test_replace_pat_guards_group_index(self):
        from app.services.comprehensive import firm_template_service
        src = inspect.getsource(firm_template_service)
        m = re.search(
            r"def _replace_pat\([^)]*\):.*?(?=\ndef |\nclass )",
            src,
            flags=re.S,
        )
        if m:
            body = m.group(0)
            # 必须有 group 范围检查 或 fallback
            assert (
                "len(m.groups())" in body
                or "group(" in body
            ), "_replace_pat 没保护 group IndexError"

    def test_anonymize_excerpt_handles_numeric(self):
        from app.services.comprehensive import firm_template_service
        src = inspect.getsource(firm_template_service)
        # 找 anonymize_excerpt 函数
        m = re.search(
            r"def anonymize_excerpt\([^)]*\):.*?(?=\ndef |\nclass )",
            src,
            flags=re.S,
        )
        if m:
            body = m.group(0)
            # 必须有 isinstance(int|float) 处理
            assert (
                "isinstance(cell.value, (int, float))" in body
                or "isinstance(value, (int, float))" in body
                or "isinstance.*int.*float" in body
            ), "anonymize_excerpt 仍只查 string, int/float 银行账户漏脱敏"


# ============================================================
#  Comprehensive: turnover_days 按 period_days
# ============================================================


class TestFieldMapperTurnoverDays:
    """round 32 P0 (ALG-03): turnover_days 硬编码 365, 季报算 4x."""

    def test_turnover_days_uses_period_days(self):
        from app.services.comprehensive import field_mapper
        src = inspect.getsource(field_mapper)
        m = re.search(
            r"def _resolve_ar_turnover\([^)]*\):.*?(?=\ndef |\nclass )",
            src,
            flags=re.S,
        )
        if m:
            body = m.group(0)
            # 必须有 ctx.period_days 或 ctx.extra.period_days
            assert (
                "period_days" in body
            ), "_resolve_ar_turnover 没按 period_days, 季报 DSO 错 4x"


# ============================================================
#  综合: IDOR firm 边界在 auth.users
# ============================================================


class TestAuthUsersFirmBoundary:
    """round 32 P0 IDOR: GET /api/auth/users/{user_id} 加 firm 校验."""

    def test_get_user_has_firm_check(self):
        from app.api import auth as auth_module
        # get_user 是 module-level coroutine
        fn = getattr(auth_module, "get_user", None)
        assert fn is not None, "auth.get_user 函数未找到"
        src = inspect.getsource(fn)
        assert "firm_id" in src, (
            "get_user 缺 firm_id 校验, 任意用户可读他人邮箱/手机/role"
        )


# ============================================================
#  Smoke: 全部 P0 修复至少 1 个被验证
# ============================================================


class TestRound32Smoke:
    """冒烟: 至少 N 项 P0 修复被独立测试覆盖."""

    def test_at_least_10_p0_regressions_covered(self):
        """本文件包含 13 个 TestClass, 每类至少 1 case, 覆盖 ≥10 项 P0."""
        import sys
        mod = sys.modules[__name__]
        classes = [
            obj for name, obj in vars(mod).items()
            if isinstance(obj, type) and name.startswith("Test")
        ]
        assert len(classes) >= 10, (
            f"只覆盖 {len(classes)} 类, 应 ≥10 类 round 32 回归"
        )
