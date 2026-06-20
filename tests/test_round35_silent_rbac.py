"""Round 35 (2026-06-20) silent except + 弱密码 + RBAC P1 回归测试.

覆盖范围:
  - ai_analysis_engine: 4 处 except 静默 → logger.exception
  - ipo_specials: RevenueCutoffTester / FeedbackSLAMonitor 静默 + 999 哨兵
  - audit_cycles: is_holiday / LeaseAmortizer commencement_date 静默
  - sentiment.quarterly.verifier: 百分比形碰撞 — 要求 subject 上下文匹配
  - auth.password: WEAK_PASSWORDS 黑名单 (≥30) + service 层前置校验
  - sentiment.quarterly.financial_input: is_complete 类型校验
  - team_management.progress_tracker: NULL member_id 单独成桶 + is_unassigned flag
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("AUTH_ENABLED", "false")

from app.services.ai_analysis_engine import AIAnalysisEngine
from app.services.ipo_specials import (
    FeedbackSLAMonitor,
    RevenueCutoffTester,
    SLA_UNPARSEABLE,
    is_sla_unparseable,
)
from app.services.audit_cycles import ExpensesAnomalyDetector, LeaseAmortizer
from app.services.auth.password import (
    WEAK_PASSWORDS,
    hash_password,
    is_weak_password,
    verify_password,
)
from app.services.auth.service import (
    AuthenticationError,
    change_password,
    reset_password,
)
from app.services.sentiment.quarterly.financial_input import (
    REQUIRED_FIELDS,
    FinancialInput,
    _is_numeric,
)
from app.services.sentiment.quarterly.verifier import QuarterlyVerifier
from app.services.team_management.progress_tracker import (
    UNASSIGNED_MEMBER_ID,
    ProgressTracker,
)


# ============================================================
#  P1-1: ai_analysis_engine silent except → logger.exception
# ============================================================


class TestAIAnalysisSilentExcept:
    """4 处 except 静默 + logger.exception 留痕."""

    def _engine(self) -> AIAnalysisEngine:
        return AIAnalysisEngine(api_key="dummy-test-key")

    async def test_analyze_risk_level_logs_on_bad_json(self, caplog):
        eng = self._engine()
        with patch.object(
            eng, "_call_ai", new=AsyncMock(return_value="<html>error 500</html>")
        ):
            with caplog.at_level(logging.ERROR, logger="app.services.ai_analysis_engine"):
                result = await eng.analyze_risk_level(
                    {"total_assets": 100, "revenue": 200, "net_profit": 30},
                    "制造业",
                )
        assert result["risk_level"] == "中"  # fallback 仍返
        assert any(
            "AI analyze_risk_level 响应解析失败" in r.message for r in caplog.records
        ), f"应记录 logger.exception, 实际 log: {[r.message for r in caplog.records]}"

    async def test_detect_anomalies_logs_on_bad_json(self, caplog):
        eng = self._engine()
        with patch.object(
            eng, "_call_ai", new=AsyncMock(return_value="not json at all")
        ):
            with caplog.at_level(logging.ERROR, logger="app.services.ai_analysis_engine"):
                result = await eng.detect_anomalies([], [])
        assert result == []
        assert any(
            "AI detect_anomalies 响应解析失败" in r.message for r in caplog.records
        )

    async def test_generate_audit_program_logs_on_bad_json(self, caplog):
        eng = self._engine()
        with patch.object(
            eng, "_call_ai", new=AsyncMock(return_value="truncated garbage")
        ):
            with caplog.at_level(logging.ERROR, logger="app.services.ai_analysis_engine"):
                result = await eng.generate_audit_program([], [])
        assert result == []
        assert any(
            "AI generate_audit_program 响应解析失败" in r.message for r in caplog.records
        )

    async def test_analyze_regulatory_compliance_logs_on_bad_json(self, caplog):
        eng = self._engine()
        with patch.object(
            eng, "_call_ai", new=AsyncMock(return_value="<html>bad gateway</html>")
        ):
            with caplog.at_level(logging.ERROR, logger="app.services.ai_analysis_engine"):
                result = await eng.analyze_regulatory_compliance({"name": "AC"}, "制造业")
        assert result == {}
        assert any(
            "AI analyze_regulatory_compliance 响应解析失败" in r.message for r in caplog.records
        )

    async def test_analyze_risk_level_passes_through_valid_json(self):
        """正常路径仍能正常解析 — 不被新 except 改动破坏."""
        eng = self._engine()
        valid = json.dumps({"risk_level": "高", "risk_points": ["X"], "recommendations": ["Y"]})
        with patch.object(eng, "_call_ai", new=AsyncMock(return_value=valid)):
            result = await eng.analyze_risk_level({}, "制造业")
        assert result["risk_level"] == "高"


# ============================================================
#  P1-2: ipo_specials silent except + SLA 哨兵
# ============================================================


class TestIPOSpecialsSilentExceptAndSLA:
    """RevenueCutoffTester.judge period_end 解析失败 + SLA 哨兵."""

    def test_revenue_cutoff_logs_on_bad_period_end(self, caplog):
        """period_end 损坏时静默返 'normal' + logger.exception 留痕."""
        with caplog.at_level(logging.ERROR, logger="app.services.ipo_specials"):
            j, d = RevenueCutoffTester.judge("2024-12-30", "2024-12-31", "not-a-date")
        assert j == "normal"
        assert d == 0
        assert any(
            "period_end 解析失败" in r.message for r in caplog.records
        ), f"应记录 logger.exception, 实际: {[r.message for r in caplog.records]}"

    def test_sla_unparseable_today(self, caplog):
        """today 损坏 → 返 SLA_UNPARSEABLE + logger.exception."""
        with caplog.at_level(logging.ERROR, logger="app.services.ipo_specials"):
            d = FeedbackSLAMonitor.days_to_deadline("2026-06-30", today="garbage")
        assert d == SLA_UNPARSEABLE
        assert is_sla_unparseable(d) is True
        assert any("today 解析失败" in r.message for r in caplog.records)

    def test_sla_unparseable_deadline(self, caplog):
        with caplog.at_level(logging.ERROR, logger="app.services.ipo_specials"):
            d = FeedbackSLAMonitor.days_to_deadline("not-a-date", today="2026-06-20")
        assert d == SLA_UNPARSEABLE
        assert is_sla_unparseable(d) is True
        assert any("deadline 解析失败" in r.message for r in caplog.records)

    def test_sla_unparseable_flag_distinguishes_from_real_value(self):
        """is_sla_unparseable 可区分 '真 999 天' vs '日期错误'."""
        assert is_sla_unparseable(None) is True
        assert is_sla_unparseable(SLA_UNPARSEABLE) is True
        assert is_sla_unparseable(5) is False
        assert is_sla_unparseable(0) is False
        assert is_sla_unparseable(-3) is False

    def test_urgency_level_unknown_on_unparseable(self):
        """哨兵值不能归类为 normal, 必须显式 'unknown'."""
        assert FeedbackSLAMonitor.urgency_level(SLA_UNPARSEABLE) == "unknown"
        assert FeedbackSLAMonitor.urgency_level(None) == "unknown"
        # 真实值分类仍正常
        assert FeedbackSLAMonitor.urgency_level(-1) == "overdue"
        assert FeedbackSLAMonitor.urgency_level(2) == "critical"
        assert FeedbackSLAMonitor.urgency_level(5) == "warn"
        assert FeedbackSLAMonitor.urgency_level(20) == "normal"

    def test_sla_works_for_valid_dates(self):
        d = FeedbackSLAMonitor.days_to_deadline("2026-06-25", today="2026-06-20")
        assert d == 5
        assert is_sla_unparseable(d) is False
        assert FeedbackSLAMonitor.urgency_level(d) == "warn"


# ============================================================
#  P1-3: audit_cycles silent except
# ============================================================


class TestAuditCyclesSilentExcept:
    def test_is_holiday_logs_on_bad_date(self, caplog):
        with caplog.at_level(logging.ERROR, logger="app.services.audit_cycles"):
            assert ExpensesAnomalyDetector.is_holiday("not-a-date") is False
        assert any("is_holiday 解析失败" in r.message for r in caplog.records)

    def test_is_holiday_normal_paths_still_work(self):
        # 周六 / 周一
        assert ExpensesAnomalyDetector.is_holiday("2026-06-13") is True  # 周六
        assert ExpensesAnomalyDetector.is_holiday("2026-06-15") is False  # 周一

    def test_lease_compute_periods_static_no_silent(self):
        """LeaseAmortizer.compute_periods 是纯函数, 不会被静默吞 (回归保护)."""
        # 正常路径
        assert LeaseAmortizer.compute_periods("2026-01", 3) == ["2026-01", "2026-02", "2026-03"]
        # 跨年
        assert LeaseAmortizer.compute_periods("2026-12", 2) == ["2026-12", "2027-01"]
        # 坏输入
        assert LeaseAmortizer.compute_periods("not-a-date", 5) == []
        assert LeaseAmortizer.compute_periods("2026-01", 0) == []


# ============================================================
#  P1-4: sentiment.quarterly.verifier percentage form collision
# ============================================================


class TestQuarterlyVerifierPercentageCollision:
    """百分比形 0.5 不应匹配 '50.0%' 除非 subject 上下文."""

    def _v(self) -> QuarterlyVerifier:
        return QuarterlyVerifier()

    def test_50pct_matches_when_subject_present(self):
        """毛利率 50.0% 出现在 '毛利率 50.0%' 上下文中 → 应匹配 (field_name=gross_margin)."""
        v = self._v()
        events_text = "本期毛利率 50.0%, 行业平均 35.0%"
        matched_in, matched_val, note = v._find_value(
            0.5, events_text, "", field_name="gross_margin",
        )
        assert matched_in == "events"
        assert matched_val == "50.0%"

    def test_50pct_does_not_match_unrelated_mention(self):
        """营收同比 50.0% 出现, 但 financial field 是毛利率 → 不应误匹配."""
        v = self._v()
        events_text = "营收同比增长 50.0%, 净利润大幅提升"
        matched_in, matched_val, note = v._find_value(
            0.5, events_text, "", field_name="gross_margin",
        )
        # 旧版: 误匹配 'events' (matched_val='50.0%')
        # 新版: field_name=gross_margin 触发 '毛利率' subject 检查, '营收' 不匹配, 返 'none'
        assert matched_in == "none", (
            f"百分比形不应在无 subject 上下文时匹配, 实际 matched_in={matched_in}"
        )

    def test_revenue_field_accepts_revenue_subject(self):
        """field_name=revenue + 文本含 '营收 50.0%' → 应匹配."""
        v = self._v()
        events_text = "本期营收增长 50.0%, 业绩亮眼"
        matched_in, matched_val, note = v._find_value(
            0.5, events_text, "", field_name="yoy_revenue",
        )
        assert matched_in == "events"

    def test_plain_number_50_still_matches(self):
        """纯数值 50 (非百分比) 不受 subject 约束, 直接匹配."""
        v = self._v()
        events_text = "营收 50 亿元, 业绩稳健"
        matched_in, matched_val, note = v._find_value(50, events_text, "")
        assert matched_in == "events"

    def test_pct_with_subject_in_briefings(self):
        v = self._v()
        matched_in, matched_val, note = v._find_value(
            0.5, "", "毛利率达到 50.0%, 行业领先", field_name="gross_margin",
        )
        assert matched_in == "briefings"

    def test_subject_window_80_chars(self):
        """subject token 距离百分比形 ≤80 字符时仍算上下文."""
        v = self._v()
        padding = "x" * 50
        events_text = f"毛利率{padding}50.0%"
        matched_in, matched_val, note = v._find_value(
            0.5, events_text, "", field_name="gross_margin",
        )
        assert matched_in == "events"

    def test_subject_too_far_no_match(self):
        """subject 距百分比 > 80 字符 → 不匹配."""
        v = self._v()
        padding = "x" * 200
        events_text = f"毛利率{padding}50.0%"
        matched_in, matched_val, note = v._find_value(
            0.5, events_text, "", field_name="gross_margin",
        )
        assert matched_in == "none"

    def test_no_match_when_text_empty(self):
        v = self._v()
        matched_in, matched_val, note = v._find_value(
            0.5, "", "", field_name="gross_margin",
        )
        assert matched_in == "none"

    def test_full_verify_uses_field_name(self):
        """verify() 端到端 — field_name 透传避免百分比误匹配."""
        v = self._v()
        # financial_input 里 gross_margin=0.5, 但事件只提了营收同比 50%
        result = v.verify(
            markdown="",
            financial_input={"gross_margin": 0.5},
            events=[
                {"title": "增长", "content_text": "营收同比增长 50.0%, 净利大幅提升"}
            ],
            briefings=[],
        )
        # gross_margin 不应被误认为 events 命中 (旧 bug)
        flags = result.consistency_flags
        assert len(flags) == 1
        assert flags[0].financial_field == "gross_margin"
        assert flags[0].matched_in == "none", (
            f"百分比 50% 在营收上下文, 不应匹配 gross_margin 字段, "
            f"实际 matched_in={flags[0].matched_in}"
        )
        # 'none' 不视为错误, 所以 passed=True, error_count=0
        assert result.error_count == 0


# ============================================================
#  P1-5: 弱密码黑名单 + service 层前置校验
# ============================================================


class TestWeakPasswordBlacklist:
    def test_blacklist_size(self):
        """黑名单至少 30 条."""
        assert len(WEAK_PASSWORDS) >= 30

    def test_common_weak_passwords_caught(self):
        for pw in [
            "password", "Password", "PASSWORD",
            "12345678", "qwerty", "abc123", "admin",
            "iloveyou", "dragon", "monkey",
            "woaini", "nihao",
        ]:
            assert is_weak_password(pw), f"{pw!r} 应识别为弱密码"

    def test_strong_passwords_pass(self):
        for pw in [
            "MyP@ssw0rd!", "xK9#mQ$vL2", "Zh-CN-Strong-Pwd-2026",
            "Aud1t0r@Acme", "Tr0ub4dor&3",
        ]:
            assert not is_weak_password(pw), f"{pw!r} 不应被识别为弱密码"

    def test_empty_password_is_weak(self):
        assert is_weak_password("") is True

    def test_whitespace_and_case_normalized(self):
        """前后空格 + 大小写归一."""
        assert is_weak_password(" password ") is True
        assert is_weak_password("Password1") is True
        assert is_weak_password("ADMIN123") is True

    async def test_change_password_rejects_weak(self, async_session):
        """change_password 在写库前拒绝弱密码."""
        from tests._helpers.auth import ROLE_ADMIN, make_user

        user = await make_user(async_session, role=ROLE_ADMIN)
        user.password_hash = hash_password("OldStr0ng#Pass1")
        await async_session.flush()
        with pytest.raises(AuthenticationError) as exc:
            await change_password(
                async_session, user, "OldStr0ng#Pass1", "password"
            )
        assert "弱密码" in str(exc.value)

    async def test_reset_password_rejects_weak(self, async_session):
        """reset_password (管理员强制) 同样拒绝弱密码."""
        from tests._helpers.auth import ROLE_ADMIN, make_user

        user = await make_user(async_session, role=ROLE_ADMIN)
        with pytest.raises(AuthenticationError) as exc:
            await reset_password(async_session, user, "12345678")
        assert "弱密码" in str(exc.value)

    async def test_change_password_accepts_strong(self, async_session):
        from tests._helpers.auth import ROLE_ADMIN, make_user

        user = await make_user(async_session, role=ROLE_ADMIN)
        user.password_hash = hash_password("OldStr0ng#Pass1")
        await async_session.flush()
        await change_password(
            async_session, user, "OldStr0ng#Pass1", "N3wStr0ng#Pass2"
        )
        assert verify_password("N3wStr0ng#Pass2", user.password_hash)


# ============================================================
#  P1-6: financial_input is_complete 类型校验
# ============================================================


class TestFinancialInputTypeValidation:
    def test_numeric_values_complete(self):
        """全部字段是 int/float/Decimal → complete."""
        fin = FinancialInput(data={
            "revenue": 1_000_000.0,
            "net_profit": 200_000,
            "non_recurring_pnl": 180_000,
            "gross_margin": 25.5,
            "yoy_revenue": 12.3,
            "yoy_net_profit": -5.0,
            "total_assets": 5_000_000,
            "operating_cash_flow": 300_000,
        })
        assert fin.is_complete() is True
        assert fin.invalid_fields() == []

    def test_string_value_rejected(self):
        """'abc' 字符串误判完整 (P1 旧 bug)."""
        fin = FinancialInput(data={
            "revenue": "abc",
            "net_profit": 200_000,
            "non_recurring_pnl": 180_000,
            "gross_margin": 25.5,
            "yoy_revenue": 12.3,
            "yoy_net_profit": -5.0,
            "total_assets": 5_000_000,
            "operating_cash_flow": 300_000,
        })
        assert fin.is_complete() is False
        invalid = fin.invalid_fields()
        assert ("revenue", "non_numeric_type=str") in invalid

    def test_mixed_types_rejected(self):
        """data={'revenue':'abc','cost':100} 旧版误判完整."""
        fin = FinancialInput(data={
            "revenue": "abc",
            "net_profit": 100,
            "non_recurring_pnl": 100,
            "gross_margin": 25.0,
            "yoy_revenue": 5.0,
            "yoy_net_profit": 5.0,
            "total_assets": 1000,
            "operating_cash_flow": 100,
        })
        assert fin.is_complete() is False

    def test_bool_rejected_even_though_int(self):
        """bool 是 int 子类, 必须显式排除."""
        fin = FinancialInput(data={
            "revenue": True,
            "net_profit": 200_000,
            "non_recurring_pnl": 180_000,
            "gross_margin": 25.5,
            "yoy_revenue": 12.3,
            "yoy_net_profit": -5.0,
            "total_assets": 5_000_000,
            "operating_cash_flow": 300_000,
        })
        assert fin.is_complete() is False

    def test_list_dict_rejected(self):
        fin = FinancialInput(data={
            "revenue": [1, 2, 3],
            "net_profit": 200_000,
            "non_recurring_pnl": 180_000,
            "gross_margin": 25.5,
            "yoy_revenue": 12.3,
            "yoy_net_profit": -5.0,
            "total_assets": 5_000_000,
            "operating_cash_flow": 300_000,
        })
        assert fin.is_complete() is False

    def test_decimal_accepted(self):
        """Decimal 也算合法数值 (财务数据常用)."""
        fin = FinancialInput(data={
            "revenue": Decimal("1000000.50"),
            "net_profit": 200_000,
            "non_recurring_pnl": 180_000,
            "gross_margin": 25.5,
            "yoy_revenue": 12.3,
            "yoy_net_profit": -5.0,
            "total_assets": 5_000_000,
            "operating_cash_flow": 300_000,
        })
        assert fin.is_complete() is True

    def test_required_fields_present(self):
        """REQUIRED_FIELDS 至少含 8 个核心字段."""
        assert len(REQUIRED_FIELDS) >= 8
        for f in ["revenue", "net_profit", "gross_margin", "total_assets"]:
            assert f in REQUIRED_FIELDS

    def test_is_numeric_helper(self):
        assert _is_numeric(100) is True
        assert _is_numeric(100.5) is True
        assert _is_numeric(Decimal("1.1")) is True
        assert _is_numeric(None) is False
        assert _is_numeric("100") is False
        assert _is_numeric("abc") is False
        assert _is_numeric([1]) is False
        assert _is_numeric({"a": 1}) is False
        assert _is_numeric(True) is False
        assert _is_numeric(False) is False


# ============================================================
#  P1-7: progress_tracker NULL member_id → unassigned bucket
# ============================================================


class TestProgressTrackerUnassigned:
    """WorkPlanItem.member_id IS NULL 不应被吞, 单独成桶 + is_unassigned flag."""

    def test_unassigned_member_id_sentinel(self):
        assert UNASSIGNED_MEMBER_ID == -1
        assert UNASSIGNED_MEMBER_ID != 0  # 与合法 member.id=0 区分

    async def test_unassigned_items_separated_into_own_row(self, async_session):
        """NULL member_id 的 WorkPlanItem 单独成 MemberProgressData 行,
        而不是消失 / 与 member_id=0 撞车."""
        from app.models.db_models import (
            TASK_STATUS_DONE,
            TASK_STATUS_PENDING,
            Project,
            ProjectAssignment,
            TeamMember,
            WorkPlan,
            WorkPlanItem,
        )

        proj = Project(
            name="P1", company_name="AC测试", fiscal_year=2026,
            created_at=datetime.utcnow(),
        )
        async_session.add(proj)
        await async_session.flush()
        member = TeamMember(
            full_name="测试员", level="经理",
            created_at=datetime.utcnow(),
        )
        async_session.add(member)
        await async_session.flush()
        async_session.add(ProjectAssignment(
            project_id=proj.id, member_id=member.id,
            role_in_project="auditor",
        ))
        plan = WorkPlan(project_id=proj.id, name="plan1", created_at=datetime.utcnow())
        async_session.add(plan)
        await async_session.flush()
        # 2 条分给 member
        for status in [TASK_STATUS_DONE, TASK_STATUS_PENDING]:
            async_session.add(WorkPlanItem(
                plan_id=plan.id, member_id=member.id, status=status,
                title=f"m-{status}", created_at=datetime.utcnow(),
            ))
        # 3 条 unassigned
        for i in range(3):
            async_session.add(WorkPlanItem(
                plan_id=plan.id, member_id=None, status=TASK_STATUS_PENDING,
                title=f"u-{i}", created_at=datetime.utcnow(),
            ))

        out = await ProgressTracker.collect_member_progress(async_session, proj.id)
        assert len(out) == 2, f"应有 1 成员 + 1 unassigned, 实际: {len(out)}"
        assigned = [m for m in out if not m.is_unassigned]
        unassigned = [m for m in out if m.is_unassigned]
        assert len(assigned) == 1
        assert len(unassigned) == 1
        assert assigned[0].total_items == 2
        assert assigned[0].completed_items == 1
        assert unassigned[0].member_id == UNASSIGNED_MEMBER_ID
        assert unassigned[0].total_items == 3
        assert unassigned[0].is_unassigned is True
        assert unassigned[0].full_name == "(未分配)"

    async def test_no_unassigned_row_when_all_assigned(self, async_session):
        """全部任务都分配了 → 不应出现 unassigned 行."""
        from app.models.db_models import (
            TASK_STATUS_PENDING,
            Project,
            ProjectAssignment,
            TeamMember,
            WorkPlan,
            WorkPlanItem,
        )

        proj = Project(
            name="P2", company_name="AC测试2", fiscal_year=2026,
            created_at=datetime.utcnow(),
        )
        async_session.add(proj)
        await async_session.flush()
        m = TeamMember(full_name="A", level="经理", created_at=datetime.utcnow())
        async_session.add(m)
        await async_session.flush()
        async_session.add(ProjectAssignment(
            project_id=proj.id, member_id=m.id, role_in_project="auditor",
        ))
        plan = WorkPlan(project_id=proj.id, name="p", created_at=datetime.utcnow())
        async_session.add(plan)
        await async_session.flush()
        async_session.add(WorkPlanItem(
            plan_id=plan.id, member_id=m.id, status=TASK_STATUS_PENDING,
            title="x", created_at=datetime.utcnow(),
        ))

        out = await ProgressTracker.collect_member_progress(async_session, proj.id)
        assert len(out) == 1
        assert out[0].is_unassigned is False
