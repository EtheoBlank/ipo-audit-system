"""Tests for the team management module.

覆盖：
  - Schemas 字段验证
  - WorkPlanGenerator AI 路径 + 降级路径
  - MeetingQualityAssessor 规则评估
  - ManagementRecommendationGenerator 规则评估
  - ProgressTracker 聚合（不依赖 DB，使用 mock）
"""
from __future__ import annotations

import json
import pytest

from app.models.team_management import (
    TeamMemberCreate,
    TeamMemberUpdate,
    WorkPlanItemCreate,
    WorkPlanItemUpdate,
    BlockerCreate,
    BlockerUpdate,
    MeetingCreate,
    MeetingRecordCreate,
    DailyReportCreate,
    ManagementRecommendationConfirm,
    ProjectAssignmentCreate,
)
from app.services.team_management.work_plan_generator import (
    WorkPlanContext,
    WorkPlanGenerator,
    _FALLBACK_PLAN_TEMPLATES,
)
from app.services.team_management.quality_assessor import (
    MeetingQualityAssessor,
    MeetingQualityContext,
    _fallback_assessment,
)
from app.services.team_management.recommendation_generator import (
    ManagementRecommendationGenerator,
    RecommendationContext,
    _fallback_recommendations,
)
from app.services.sales_ledger.deepseek_client import DeepSeekError


# ============================================================
#  Schemas
# ============================================================


class TestTeamMemberSchemas:
    def test_create_minimal(self):
        m = TeamMemberCreate(full_name="张三")
        assert m.full_name == "张三"
        assert m.level == "auditor"
        assert m.status == "active"

    def test_update_all_optional(self):
        u = TeamMemberUpdate()
        assert u.full_name is None
        assert u.level is None

    def test_create_with_all_fields(self):
        m = TeamMemberCreate(
            full_name="李四",
            email="li@example.com",
            phone="13800000000",
            level="manager",
            specialties='["收入循环"]',
            joined_at="2024-01-15",
        )
        assert m.level == "manager"
        assert m.email == "li@example.com"


class TestWorkPlanItemSchemas:
    def test_create(self):
        it = WorkPlanItemCreate(
            title="应收函证",
            priority="high",
            estimated_hours=16.0,
            recommended_level="auditor",
        )
        assert it.priority == "high"
        assert it.estimated_hours == 16.0

    def test_update_partial(self):
        u = WorkPlanItemUpdate(status="done", actual_hours=12.0)
        assert u.status == "done"
        assert u.actual_hours == 12.0
        assert u.title is None


class TestOtherSchemas:
    def test_blocker_create(self):
        b = BlockerCreate(title="缺少客户函证回函", severity="high")
        assert b.severity == "high"

    def test_meeting_create(self):
        m = MeetingCreate(
            title="IPO 周会",
            meeting_type="weekly",
            scheduled_at="2025-01-15 14:00",
        )
        assert m.duration_minutes == 60  # default

    def test_meeting_record(self):
        r = MeetingRecordCreate(
            content="本周完成 5 张底稿。",
            decisions=[{"decision": "X", "owner": "Y"}],
            attendees=["张三", "李四"],
        )
        assert r.decisions and r.decisions[0]["decision"] == "X"

    def test_daily_report(self):
        d = DailyReportCreate(
            report_date="2025-01-15",
            completed_work="完成应收账款明细表",
            hours_logged=8.0,
        )
        assert d.hours_logged == 8.0

    def test_recommendation_confirm(self):
        c = ManagementRecommendationConfirm(confirmed_by="项目负责人")
        assert c.confirmed_by == "项目负责人"
        assert c.manager_notes is None

    def test_assignment_create(self):
        a = ProjectAssignmentCreate(member_id=1, role_in_project="lead", workload_pct=100.0)
        assert a.member_id == 1


# ============================================================
#  枚举常量 (审计 multi-agent review 后补的回归测试)
# ============================================================


class TestEnums:
    """db_models.py 中所有枚举常量的回归测试。"""

    def test_member_level_includes_intern(self):
        """v0.2 修复：审计 multi-agent 建议增加实习生级别。"""
        from app.models.db_models import (
            MEMBER_LEVEL_LABELS,
            MEMBER_LEVEL_INTERN,
            MEMBER_LEVEL_ORDER,
        )
        assert "intern" in MEMBER_LEVEL_LABELS
        assert MEMBER_LEVEL_INTERN == "intern"
        assert MEMBER_LEVEL_ORDER[MEMBER_LEVEL_INTERN] < MEMBER_LEVEL_ORDER["auditor"]

    def test_member_level_order_strictly_increasing(self):
        from app.models.db_models import MEMBER_LEVEL_ORDER
        levels = ["intern", "auditor", "senior_auditor", "manager", "senior_manager", "lead"]
        for prev, nxt in zip(levels, levels[1:]):
            assert MEMBER_LEVEL_ORDER[prev] < MEMBER_LEVEL_ORDER[nxt], (
                f"{prev} should rank below {nxt}"
            )

    def test_task_status_labels_complete(self):
        from app.models.db_models import TASK_STATUS_LABELS
        expected = {"pending", "in_progress", "blocked", "done", "cancelled"}
        assert set(TASK_STATUS_LABELS.keys()) >= expected

    def test_blocker_severity_and_status_labels(self):
        from app.models.db_models import (
            BLOCKER_SEVERITY_LABELS,
            BLOCKER_STATUS_LABELS,
        )
        assert set(BLOCKER_SEVERITY_LABELS.keys()) >= {
            "low", "medium", "high", "critical",
        }
        assert set(BLOCKER_STATUS_LABELS.keys()) >= {
            "open", "in_progress", "resolved", "escalated",
        }

    def test_work_plan_status_labels(self):
        from app.models.db_models import WORK_PLAN_STATUS_LABELS
        assert set(WORK_PLAN_STATUS_LABELS.keys()) >= {
            "draft", "active", "completed", "archived",
        }


# ============================================================
#  Cascade / relationship (db_models.py 修复后回归)
# ============================================================


class TestProjectReverseRelationships:
    """Project 应该反向关联到 7 张新表。"""

    def test_project_has_all_reverse_relations(self):
        from sqlalchemy import inspect
        from app.models.db_models import Project

        mapper = inspect(Project)
        rels = set(mapper.relationships.keys())
        expected = {
            "project_assignments",
            "work_plans",
            "meetings",
            "daily_reports",
            "blockers",
            "progress_snapshots",
            "management_recommendations",
        }
        missing = expected - rels
        assert not missing, f"Project 缺反向关系: {missing}"

    def test_team_member_no_delete_orphan_cascade(self):
        """v0.2 修复：删除成员不应级联 ProjectAssignment/DailyReport/Blocker。"""
        from sqlalchemy import inspect
        from app.models.db_models import TeamMember

        mapper = inspect(TeamMember)
        for rel_name in ("assignments", "work_plan_items", "daily_reports", "blockers"):
            rel = mapper.relationships[rel_name]
            # cascade 不应包含 'all, delete-orphan'
            cascade = rel.cascade or ""
            assert "delete-orphan" not in cascade, (
                f"TeamMember.{rel_name} 仍含 delete-orphan 级联，会误删历史数据"
            )


class TestProjectAssignmentUniqueConstraint:
    def test_unique_constraint_present(self):
        from sqlalchemy import inspect
        from app.models.db_models import ProjectAssignment

        # SQLAlchemy 2.0: inspect(orm_class) → Mapper; the underlying Table 是 .local_table
        table = inspect(ProjectAssignment).local_table
        constraints = {
            c.name for c in table.constraints if getattr(c, "name", None)
        }
        assert "uq_assignment_project_member" in constraints


# ============================================================
#  WorkPlanGenerator N+1 修复回归
# ============================================================


class TestWorkPlanGeneratorN1Fix:
    def test_build_context_uses_func_count(self):
        """v0.2 修复：build_context 应使用 func.count() 而非 select(id).all()。"""
        import inspect as _inspect
        from app.services.team_management.work_plan_generator import WorkPlanGenerator

        src = _inspect.getsource(WorkPlanGenerator.build_context)
        # 关键：必须导入并使用 func
        assert "func.count" in src or "count(" in src, (
            "build_context 应使用 func.count() 避免大表 N+1"
        )
        # 不应再用 select(...).all() + len() 模式
        assert ".all())" not in src or "len(" not in src, (
            "build_context 不应再把整张表 id 拉回内存 len()"
        )


# ============================================================
#  QualityAssessor owner 检查 bug 修复
# ============================================================


class TestQualityAssessorOwnerBugFix:
    def test_decision_without_owner_lowers_score(self):
        """v0.2 修复：decision 缺 owner 时应扣分。"""
        from app.services.team_management.quality_assessor import (
            _fallback_assessment,
            MeetingQualityContext,
        )
        ctx = MeetingQualityContext(
            meeting_title="周会",
            meeting_type="weekly",
            content="会议内容" * 100,  # 充分长
            decisions=[{"decision": "建议增加样本量"}],  # 无 owner
            action_items=[{"action": "补充函证"}],  # 无 owner
            attendees=["张三", "李四", "王五"],
        )
        result = _fallback_assessment(ctx)
        # 缺 owner 应被识别为 weakness
        assert any("owner" in w.lower() for w in result.weaknesses), (
            f"应至少有一条 weakness 提到 owner 缺失，实际: {result.weaknesses}"
        )


# ============================================================
#  Service 层 work_plan_item 字段白名单
# ============================================================


class TestUpdateWorkPlanItemWhitelist:
    @pytest.mark.asyncio
    async def test_rejects_forbidden_field(self):
        """v0.2 修复：PUT work-plan-items 不允许改 plan_id / id 等系统字段。"""
        from app.services.team_management.service import TeamManagementService
        from app.services.team_management.progress_tracker import ProgressTracker

        svc = TeamManagementService()
        # 假设 item_id=99999 不存在，先校验 forbidden 字段
        with pytest.raises(ValueError, match="不允许修改"):
            await svc.update_work_plan_item(
                db=None,  # type: ignore
                item_id=99999,
                payload={"plan_id": 999, "title": "x"},  # plan_id 非法
            )


# ============================================================
#  WorkPlanGenerator
# ============================================================


class _StubDeepSeek:
    """替代 DeepSeekClient 用于测试 — 不发真实请求。"""

    def __init__(self, configured: bool = True, raise_exc: bool = False, payload: dict | None = None):
        self._configured = configured
        self._raise = raise_exc
        self._payload = payload or {}

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def chat_json(self, *args, **kwargs):
        if self._raise:
            raise DeepSeekError("stub failure")
        return self._payload


class TestWorkPlanGenerator:
    @pytest.mark.asyncio
    async def test_ai_disabled_returns_fallback(self):
        stub = _StubDeepSeek(configured=False)
        gen = WorkPlanGenerator(deepseek=stub)
        ctx = WorkPlanContext(
            project_id=1,
            project_name="X 项目",
            company_name="X 公司",
            account_count=100,
        )
        result = await gen.generate(ctx)
        assert result.ai_enabled is False
        assert len(result.items) == len(_FALLBACK_PLAN_TEMPLATES)
        assert "标准模板" in result.name

    @pytest.mark.asyncio
    async def test_ai_raises_returns_fallback(self):
        stub = _StubDeepSeek(configured=True, raise_exc=True)
        gen = WorkPlanGenerator(deepseek=stub)
        ctx = WorkPlanContext(
            project_id=1,
            project_name="X",
            company_name="X 公司",
            account_count=200,
        )
        result = await gen.generate(ctx)
        assert result.ai_enabled is False
        # 200 笔 → scale=1.2
        assert result.items[0]["estimated_hours"] >= _FALLBACK_PLAN_TEMPLATES[0]["estimated_hours"]

    @pytest.mark.asyncio
    async def test_ai_returns_valid_items(self):
        payload = {
            "plan_name": "Y 公司 IPO 审计计划",
            "items": [
                {
                    "title": "AI 任务 1",
                    "description": "做点什么",
                    "related_module": "底稿",
                    "priority": "high",
                    "estimated_hours": 8.0,
                    "recommended_level": "auditor",
                },
                {
                    "title": "AI 任务 2",
                    "description": "做点别的",
                    "related_module": "盘点",
                    "priority": "medium",
                    "estimated_hours": 16.0,
                    "recommended_level": "senior_auditor",
                },
                # 异常项 — 应被忽略
                {"no_title": True},
            ],
        }
        stub = _StubDeepSeek(configured=True, payload=payload)
        gen = WorkPlanGenerator(deepseek=stub)
        ctx = WorkPlanContext(project_id=1, project_name="X", company_name="Y")
        result = await gen.generate(ctx)
        assert result.ai_enabled is True
        assert result.name == "Y 公司 IPO 审计计划"
        assert len(result.items) == 2  # 第三条没有 title，被丢弃
        assert result.items[0]["title"] == "AI 任务 1"
        assert result.items[0]["recommended_level"] == "auditor"

    @pytest.mark.asyncio
    async def test_ai_returns_empty_falls_back(self):
        stub = _StubDeepSeek(configured=True, payload={"plan_name": "空", "items": []})
        gen = WorkPlanGenerator(deepseek=stub)
        ctx = WorkPlanContext(project_id=1, project_name="X", company_name="Y")
        result = await gen.generate(ctx)
        # items 为空时退回模板
        assert result.ai_enabled is False
        assert len(result.items) == len(_FALLBACK_PLAN_TEMPLATES)

    def test_scale_large_data(self):
        gen = WorkPlanGenerator(deepseek=_StubDeepSeek(configured=False))
        ctx = WorkPlanContext(
            project_id=1, project_name="X", company_name="Y",
            account_count=600, voucher_count=12000,
        )
        # 通过 fallback_plan 验证 scaling
        result = gen._fallback_plan(ctx, "prompt")
        # scale=1.5
        assert result.items[0]["estimated_hours"] == round(
            _FALLBACK_PLAN_TEMPLATES[0]["estimated_hours"] * 1.5, 1
        )


# ============================================================
#  MeetingQualityAssessor
# ============================================================


class TestMeetingQualityAssessor:
    @pytest.mark.asyncio
    async def test_ai_disabled_uses_rule(self):
        ass = MeetingQualityAssessor(deepseek=_StubDeepSeek(configured=False))
        ctx = MeetingQualityContext(
            meeting_title="周会",
            meeting_type="weekly",
            content="会议内容 " * 100,  # 充分长
            decisions=[{"decision": "X", "owner": "Y"}],
            action_items=[{"action": "A", "owner": "B", "due": "2025-02-01"}],
            attendees=["张三", "李四", "王五"],
        )
        result = await ass.assess(ctx)
        assert result.ai_enabled is False
        assert 0 <= result.quality_score <= 100
        assert result.strengths

    def test_fallback_empty_meeting_low_score(self):
        ctx = MeetingQualityContext(
            meeting_title="空会议",
            meeting_type="adhoc",
            content="稍后再说",
        )
        result = _fallback_assessment(ctx)
        assert result.quality_score < 60
        assert result.weaknesses

    def test_fallback_robust_meeting_high_score(self):
        ctx = MeetingQualityContext(
            meeting_title="完备周会",
            meeting_type="weekly",
            content=("本次周会讨论了收入截止性测试方案。" * 50),
            decisions=[
                {"decision": "建议增加样本量", "owner": "李四"},
                {"decision": "10 日前提交底稿", "owner": "王五"},
            ],
            action_items=[
                {"action": "补充函证", "owner": "张三", "due": "2025-02-01"},
                {"action": "复核底稿", "owner": "李四", "due": "2025-02-05"},
            ],
            attendees=["张三", "李四", "王五", "赵六"],
        )
        result = _fallback_assessment(ctx)
        assert result.quality_score >= 70
        assert result.strengths


# ============================================================
#  ManagementRecommendationGenerator
# ============================================================


class TestManagementRecommendationGenerator:
    def test_fallback_critical_blockers(self):
        ctx = RecommendationContext(
            project_id=1,
            project_name="测试项目",
            completion_rate=0.6,
            blocked_count=3,
            critical_blockers=2,
            overdue_items=0,
            members_load=[{"name": "张三", "completion_rate": 0.5, "hours_logged_7d": 12}],
        )
        result = _fallback_recommendations(ctx)
        assert result.ai_enabled is False
        cats = {f.get("category") for f in result.findings}
        assert "进度风险" in cats  # critical 触发
        assert any(a.get("action") for a in result.priority_actions)

    def test_fallback_balanced_workload(self):
        ctx = RecommendationContext(
            project_id=1,
            project_name="P",
            completion_rate=0.9,
            blocked_count=0,
            critical_blockers=0,
            overdue_items=0,
            members_load=[
                {"name": "A", "completion_rate": 0.8, "hours_logged_7d": 60},
                {"name": "B", "completion_rate": 0.5, "hours_logged_7d": 5},
            ],
        )
        result = _fallback_recommendations(ctx)
        cats = {f.get("category") for f in result.findings}
        assert "资源分配" in cats

    def test_fallback_low_progress(self):
        ctx = RecommendationContext(
            project_id=1,
            project_name="P",
            completion_rate=0.15,
            blocked_count=0,
            critical_blockers=0,
            overdue_items=0,
        )
        result = _fallback_recommendations(ctx)
        cats = {f.get("category") for f in result.findings}
        assert "进度滞后" in cats

    @pytest.mark.asyncio
    async def test_ai_disabled_returns_fallback(self):
        gen = ManagementRecommendationGenerator(deepseek=_StubDeepSeek(configured=False))
        ctx = RecommendationContext(
            project_id=1, project_name="P", completion_rate=0.5,
            blocked_count=1, critical_blockers=0, overdue_items=0,
        )
        result = await gen.generate(ctx)
        assert result.ai_enabled is False
        assert result.findings or result.priority_actions


# ============================================================
#  ProgressTracker (轻量)
# ============================================================


class TestProgressTrackerParse:
    """ProgressTracker 主要用 SQL 聚合；这里只做轻量校验 — 不连库。"""

    def test_memberprogress_dataclass(self):
        from app.services.team_management.progress_tracker import MemberProgressData

        m = MemberProgressData(
            member_id=1,
            full_name="张三",
            level="auditor",
            total_items=5,
            completed_items=3,
            in_progress_items=1,
            blocked_items=1,
            completion_rate=0.6,
            hours_logged_7d=12.0,
            open_blockers=1,
            last_report_date="2025-01-15",
        )
        assert m.completion_rate == 0.6
        assert m.last_report_date == "2025-01-15"


# ============================================================
#  End-to-end JSON shape
# ============================================================


class TestAISchemaCompat:
    """AI 返回的 JSON 应能被 schema 序列化。"""

    def test_management_recommendation_response_serialization(self):
        # 模拟 AI 返回的 findings / actions 形态
        from app.models.team_management import (
            MemberProgress,
            BlockerSummary,
            ProjectProgress,
            ProgressDashboardResponse,
        )

        resp = ProgressDashboardResponse(
            project=ProjectProgress(
                project_id=1,
                project_name="X",
                total_items=10,
                completed_items=4,
                in_progress_items=3,
                blocked_items=1,
                completion_rate=0.4,
                total_estimated_hours=120.0,
                total_actual_hours=50.0,
                open_blockers=2,
                critical_blockers=1,
                members=[
                    MemberProgress(
                        member_id=1,
                        full_name="张三",
                        level="auditor",
                        total_items=5,
                        completed_items=2,
                        in_progress_items=1,
                        blocked_items=1,
                        completion_rate=0.4,
                        hours_logged_7d=10.0,
                        open_blockers=1,
                        last_report_date="2025-01-15",
                    )
                ],
            ),
            blockers=BlockerSummary(
                total_open=2, critical=1, high=0, medium=1, low=0, avg_age_hours=24.0
            ),
        )
        d = resp.model_dump()
        assert d["project"]["total_items"] == 10
        assert d["blockers"]["critical"] == 1
        assert d["project"]["members"][0]["full_name"] == "张三"
