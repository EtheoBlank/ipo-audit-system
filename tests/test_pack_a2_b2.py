"""Pack A.2 / B.2 — 本轮 6 项增强的单元测试.

覆盖:
  1. 老业务 API 全量加鉴权 (smoke test: 路由仍能注册 + 13 个 router 都 import ok)
  2. 多租户硬隔离 (scope_projects_to_firm / ensure_project_in_firm)
  3. ApprovalEngine 乐观锁 (expected_version → ApprovalConflict)
  4. 审计轨迹归档 (audit_log_stats / rotate_audit_logs)
  5. DeepSeek 关联方推断 (RelatedPartyAIInferer, mock client)
  6. Word 富格式渲染 (run-aware placeholder 替换)
"""
from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 关闭网络相关 env 避免误连
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTH_ENABLED", "false")

from app.core.database import Base
from app.models.db.auth import (
    AuditLog,
    ROLE_ADMIN,
    ROLE_ASSISTANT,
    ROLE_SIGNING_PARTNER,
    User,
)
from app.models.db_models import Project


# ----------------------------------------------------------------------
#  Shared in-memory SQLite fixture
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 默认 all ORM 关系都打开 greenlet 上下文 — 防止 lazy load 撞 MissingGreenlet
    from sqlalchemy.ext.asyncio import AsyncSession as _AS

    sm = async_sessionmaker(engine, expire_on_commit=False, class_=_AS)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sm() as s:
        yield s
    await engine.dispose()


def _user(uid: int, role: str = ROLE_ASSISTANT, firm_id=None) -> User:
    return User(
        id=uid,
        username=f"u{uid}",
        full_name=f"用户{uid}",
        role=role,
        is_active=True,
        is_locked=False,
        password_hash="!",
        firm_id=firm_id,
    )


def _project(pid: int, firm_id=None, company_name="X") -> Project:
    return Project(
        id=pid,
        name=f"P{pid}",
        company_name=company_name,
        fiscal_year=2024,
        status="active",
        firm_id=firm_id,
    )


# ============================================================
#  1) 老业务 API 全量加鉴权 (smoke import)
# ============================================================


class TestLegacyApisImport:
    """13 个老 router 全部 import 成功 + 包含鉴权依赖."""

    def test_all_routers_importable(self):
        # 一次 import 全 13 个老 router, 失败就是没接进去
        from app.api import (
            comprehensive,
            confirmations,
            contracts,
            inventory,
            knowledge_base,
            projects,
            regulations,
            regulatory_cases,
            reports,
            sales_ledger,
            sentiment,
            team_management,
            workbooks,
        )

        # 全部应有 router
        for mod in (
            comprehensive,
            confirmations,
            contracts,
            inventory,
            knowledge_base,
            projects,
            regulations,
            regulatory_cases,
            reports,
            sales_ledger,
            sentiment,
            team_management,
            workbooks,
        ):
            assert hasattr(mod, "router"), f"{mod.__name__} 缺 router"
            # 至少要 1 个路由
            assert len(mod.router.routes) > 0, f"{mod.__name__} 路由为空"

    def test_get_current_user_optional_exported(self):
        from app.services.auth import get_current_user, get_current_user_optional

        assert callable(get_current_user)
        assert callable(get_current_user_optional)

    def test_projects_router_uses_auth(self):
        """projects 的写端点应至少 import 了鉴权依赖."""
        from app.api import projects

        with open(projects.__file__, encoding="utf-8") as fh:
            src = fh.read()
        assert "get_current_user" in src or "get_current_user_optional" in src
        assert "ensure_project_in_firm" in src
        assert "scope_projects_to_firm" in src


# ============================================================
#  2) 多租户硬隔离
# ============================================================


class TestTenantIsolation:
    """scope_projects_to_firm / ensure_project_in_firm / project_default_firm_id."""

    @pytest.mark.asyncio
    async def test_scope_filters_by_firm(self, session: AsyncSession, monkeypatch):
        from sqlalchemy import select

        from app.core.config import settings
        from app.services.auth.tenant import scope_projects_to_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        session.add(_project(1, firm_id=1))
        session.add(_project(2, firm_id=2))
        session.add(_project(3, firm_id=None))  # 老数据全局可见
        await session.commit()

        user_firm1 = _user(1, role=ROLE_ASSISTANT, firm_id=1)
        user_firm2 = _user(2, role=ROLE_ASSISTANT, firm_id=2)
        admin = _user(99, role=ROLE_ADMIN, firm_id=None)

        q_firm1 = scope_projects_to_firm(select(Project), user_firm1)
        rows = (await session.execute(q_firm1)).scalars().all()
        ids = sorted(r.id for r in rows)
        # firm=1 自己的 + firm=None 老数据
        assert ids == [1, 3]

        q_firm2 = scope_projects_to_firm(select(Project), user_firm2)
        rows = (await session.execute(q_firm2)).scalars().all()
        assert sorted(r.id for r in rows) == [2, 3]

        # admin 跨事务所
        q_admin = scope_projects_to_firm(select(Project), admin)
        rows = (await session.execute(q_admin)).scalars().all()
        assert sorted(r.id for r in rows) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_scope_no_filter_when_auth_disabled(self, session: AsyncSession, monkeypatch):
        from sqlalchemy import select

        from app.core.config import settings
        from app.services.auth.tenant import scope_projects_to_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", False)
        session.add(_project(1, firm_id=1))
        session.add(_project(2, firm_id=2))
        await session.commit()

        user = _user(1, firm_id=1)
        q = scope_projects_to_firm(select(Project), user)
        rows = (await session.execute(q)).scalars().all()
        # AUTH_ENABLED=False → 不过滤
        assert sorted(r.id for r in rows) == [1, 2]

    @pytest.mark.asyncio
    async def test_scope_no_filter_when_user_firm_none(self, session: AsyncSession):
        from sqlalchemy import select

        from app.services.auth.tenant import scope_projects_to_firm

        session.add(_project(1, firm_id=1))
        session.add(_project(2, firm_id=2))
        await session.commit()

        user_no_firm = _user(1, firm_id=None)  # 软兼容
        q = scope_projects_to_firm(select(Project), user_no_firm)
        rows = (await session.execute(q)).scalars().all()
        assert sorted(r.id for r in rows) == [1, 2]

    @pytest.mark.asyncio
    async def test_ensure_project_404(self, session: AsyncSession):
        from app.services.auth.tenant import ensure_project_in_firm

        with pytest.raises(Exception) as ei:
            await ensure_project_in_firm(session, 999, _user(1, firm_id=1))
        assert "不存在" in str(ei.value.detail) or ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_ensure_project_403_cross_firm(self, session: AsyncSession, monkeypatch):
        from fastapi import HTTPException

        from app.core.config import settings
        from app.services.auth.tenant import ensure_project_in_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        session.add(_project(1, firm_id=10))
        await session.commit()

        user_other_firm = _user(1, firm_id=99)
        with pytest.raises(HTTPException) as ei:
            await ensure_project_in_firm(session, 1, user_other_firm)
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ensure_project_admin_bypass(self, session: AsyncSession, monkeypatch):
        from app.core.config import settings
        from app.services.auth.tenant import ensure_project_in_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        session.add(_project(1, firm_id=10))
        await session.commit()

        admin = _user(99, role=ROLE_ADMIN, firm_id=1)
        proj = await ensure_project_in_firm(session, 1, admin)
        assert proj.id == 1

    @pytest.mark.asyncio
    async def test_ensure_project_legacy_firm_none_allowed(
        self, session: AsyncSession, monkeypatch
    ):
        """老数据 firm_id=None → 任何用户可读 (向后兼容)."""
        from app.core.config import settings
        from app.services.auth.tenant import ensure_project_in_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        session.add(_project(1, firm_id=None))  # 老数据
        await session.commit()

        user = _user(1, firm_id=5)
        proj = await ensure_project_in_firm(session, 1, user)
        assert proj.id == 1

    def test_project_default_firm_id_admin_returns_none(self, monkeypatch):
        from app.core.config import settings
        from app.services.auth.tenant import project_default_firm_id

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        admin = _user(1, role=ROLE_ADMIN, firm_id=10)
        # admin 显式传 firm_id, 不自动落默认
        assert project_default_firm_id(admin) is None

    def test_project_default_firm_id_regular_user(self, monkeypatch):
        from app.core.config import settings
        from app.services.auth.tenant import project_default_firm_id

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)
        u = _user(1, role=ROLE_ASSISTANT, firm_id=7)
        assert project_default_firm_id(u) == 7

    @pytest.mark.asyncio
    async def test_team_member_idor_blocked(self, session: AsyncSession, monkeypatch):
        """TeamMember 不带 firm_id, 通过 ProjectAssignment→Project.firm_id 间接隔离.

        场景: firm=1 有 member M1 (在 firm=1 的项目里), firm=2 用户不能访问 M1.
        """
        from fastapi import HTTPException

        from app.core.config import settings
        from app.models.db_models import ProjectAssignment, TeamMember
        from app.services.auth.tenant import ensure_team_member_in_firm

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)

        session.add_all([
            _project(1, firm_id=1),
            _project(2, firm_id=2),
            TeamMember(id=10, full_name="甲", status="active"),
            TeamMember(id=20, full_name="乙", status="active"),
        ])
        # M10 分配到 firm=1 的项目 P1; M20 分配到 firm=2 的项目 P2
        session.add_all([
            ProjectAssignment(id=100, project_id=1, member_id=10, role_in_project="auditor"),
            ProjectAssignment(id=200, project_id=2, member_id=20, role_in_project="auditor"),
        ])
        await session.commit()

        user_firm1 = _user(1, role=ROLE_ASSISTANT, firm_id=1)
        user_firm2 = _user(2, role=ROLE_ASSISTANT, firm_id=2)

        # 同所成员可访问
        m = await ensure_team_member_in_firm(session, 10, user_firm1)
        assert m.id == 10
        # 跨所成员被 403
        with pytest.raises(HTTPException) as exc_info:
            await ensure_team_member_in_firm(session, 20, user_firm1)
        assert exc_info.value.status_code == 403
        # 跨所反向同样
        with pytest.raises(HTTPException) as exc_info:
            await ensure_team_member_in_firm(session, 10, user_firm2)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_team_member_visible_query_scoped(self, session: AsyncSession, monkeypatch):
        """ensure_team_member_visible_query 自动按 firm 过滤成员列表."""
        from app.core.config import settings
        from app.models.db_models import ProjectAssignment, TeamMember
        from app.services.auth.tenant import ensure_team_member_visible_query

        monkeypatch.setattr(settings, "AUTH_ENABLED", True)

        session.add_all([
            _project(1, firm_id=1),
            _project(2, firm_id=2),
            TeamMember(id=10, full_name="甲", status="active"),
            TeamMember(id=20, full_name="乙", status="active"),
            TeamMember(id=30, full_name="丙", status="active"),
        ])
        session.add_all([
            ProjectAssignment(id=100, project_id=1, member_id=10, role_in_project="auditor"),
            ProjectAssignment(id=200, project_id=2, member_id=20, role_in_project="auditor"),
            # M30 没有任何 assignment — 应被隐藏 (避免"游离"成员泄露)
        ])
        await session.commit()

        user_firm1 = _user(1, role=ROLE_ASSISTANT, firm_id=1)
        q = await ensure_team_member_visible_query(user_firm1)
        rows = (await session.execute(q)).scalars().all()
        ids = sorted(r.id for r in rows)
        assert ids == [10]  # 只看到 firm=1 的成员


# ============================================================
#  3) ApprovalEngine 乐观锁
# ============================================================


class TestApprovalOptimisticLock:
    """ApprovalEngine.decide/withdraw 接受 expected_version, 不一致抛 ApprovalConflict."""

    @pytest.mark.asyncio
    async def test_decide_wrong_version_raises_conflict(self, session: AsyncSession):
        from app.services.auth.approval import ApprovalConflict, ApprovalEngine

        u = _user(1)
        wf = await ApprovalEngine.create_workflow(
            session,
            initiator=u,
            resource_type="confirmation_case",
            resource_id=42,
            title="函证审批",
        )
        # 当前 version=0, 故意传 999
        approver = _user(2, role=ROLE_ASSISTANT)
        with pytest.raises(ApprovalConflict):
            await ApprovalEngine.decide(
                session,
                workflow_id=wf.id,
                actor=approver,
                action="approve",
                expected_version=999,
            )

    @pytest.mark.asyncio
    async def test_decide_correct_version_succeeds_and_increments(
        self, session: AsyncSession
    ):
        from app.services.auth.approval import ApprovalEngine
        from app.models.db.auth import APPROVAL_STATUS_IN_PROGRESS

        u = _user(1)
        wf = await ApprovalEngine.create_workflow(
            session,
            initiator=u,
            resource_type="confirmation_case",
            resource_id=43,
            title="函证审批",
        )
        assert wf.version == 0
        assert wf.status == APPROVAL_STATUS_IN_PROGRESS

        approver = _user(2, role=ROLE_ASSISTANT)
        wf2 = await ApprovalEngine.decide(
            session,
            workflow_id=wf.id,
            actor=approver,
            action="approve",
            expected_version=0,
        )
        assert wf2.version == 1
        assert wf2.current_step == 2  # 推进到下一步
        assert wf2.status == APPROVAL_STATUS_IN_PROGRESS

    @pytest.mark.asyncio
    async def test_decide_no_version_backward_compat(self, session: AsyncSession):
        """不传 expected_version = 退化为读后即改, 兼容老调用."""
        from app.services.auth.approval import ApprovalEngine

        u = _user(1)
        wf = await ApprovalEngine.create_workflow(
            session, initiator=u, resource_type="x", resource_id=1, title="t"
        )
        approver = _user(2, role=ROLE_ASSISTANT)
        wf2 = await ApprovalEngine.decide(
            session,
            workflow_id=wf.id,
            actor=approver,
            action="approve",
            # expected_version 缺省
        )
        assert wf2.version == 1

    @pytest.mark.asyncio
    async def test_withdraw_wrong_version_raises_conflict(self, session: AsyncSession):
        from app.services.auth.approval import ApprovalConflict, ApprovalEngine

        u = _user(1)
        wf = await ApprovalEngine.create_workflow(
            session, initiator=u, resource_type="x", resource_id=1, title="t"
        )
        with pytest.raises(ApprovalConflict):
            await ApprovalEngine.withdraw(
                session, workflow_id=wf.id, actor=u, expected_version=123
            )

    @pytest.mark.asyncio
    async def test_withdraw_correct_version_succeeds(self, session: AsyncSession):
        from app.services.auth.approval import ApprovalEngine
        from app.models.db.auth import APPROVAL_STATUS_WITHDRAWN

        u = _user(1)
        wf = await ApprovalEngine.create_workflow(
            session, initiator=u, resource_type="x", resource_id=1, title="t"
        )
        wf2 = await ApprovalEngine.withdraw(
            session, workflow_id=wf.id, actor=u, expected_version=0
        )
        assert wf2.status == APPROVAL_STATUS_WITHDRAWN
        assert wf2.version == 1

    @pytest.mark.asyncio
    async def test_self_approval_blocked_by_default(self, session: AsyncSession):
        """发起人不能审批自己发起的请求 (除非显式 allow_self_approval=True)."""
        from app.services.auth.approval import ApprovalEngine, InvalidApprovalAction

        u = _user(1)
        wf = await ApprovalEngine.create_workflow(
            session, initiator=u, resource_type="x", resource_id=1, title="t"
        )
        with pytest.raises(InvalidApprovalAction):
            await ApprovalEngine.decide(
                session,
                workflow_id=wf.id,
                actor=u,
                action="approve",
                expected_version=0,
            )

    @pytest.mark.asyncio
    async def test_self_approval_allowed_with_flag(self, session: AsyncSession):
        """显式 allow_self_approval=True 时可放行 (极少场景: 5 级签字一个人顶 5 关)."""
        from app.services.auth.approval import ApprovalEngine

        u = _user(1, role=ROLE_SIGNING_PARTNER)
        wf = await ApprovalEngine.create_workflow(
            session, initiator=u, resource_type="x", resource_id=1, title="t"
        )
        # 显式放行后不抛异常
        wf2 = await ApprovalEngine.decide(
            session,
            workflow_id=wf.id,
            actor=u,
            action="approve",
            expected_version=0,
            allow_self_approval=True,
        )
        assert wf2.status in {"in_progress", "approved"}


# ============================================================
#  4) 审计轨迹归档
# ============================================================


class TestAuditLogArchive:
    """audit_log_stats + rotate_audit_logs (dry-run / 真删) + ensure_archive_table."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, session: AsyncSession):
        from app.services.auth.archive import audit_log_stats

        stats = await audit_log_stats(session)
        assert stats["total"] == 0
        assert stats["earliest"] is None
        assert stats["latest"] is None
        assert stats["by_firm"] == []

    @pytest.mark.asyncio
    async def test_stats_with_data(self, session: AsyncSession):
        from app.services.auth.archive import audit_log_stats

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 插 3 条
        for i in range(3):
            session.add(
                AuditLog(
                    user_id=1,
                    user_display="u",
                    action="create",
                    firm_id=1,
                    created_at=now - timedelta(days=i),
                )
            )
        # 插 1 条 firm=2
        session.add(
            AuditLog(
                user_id=2,
                user_display="u2",
                action="update",
                firm_id=2,
                created_at=now,
            )
        )
        await session.commit()

        stats = await audit_log_stats(session)
        assert stats["total"] == 4
        assert stats["earliest"] is not None
        assert stats["latest"] is not None
        # by_firm 应包含 firm=1 (3 行) 和 firm=2 (1 行)
        by_firm_map = {x["firm_id"]: x["count"] for x in stats["by_firm"]}
        assert by_firm_map.get(1) == 3
        assert by_firm_map.get(2) == 1

    @pytest.mark.asyncio
    async def test_rotate_dry_run_no_delete(self, session: AsyncSession):
        from app.services.auth.archive import rotate_audit_logs

        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
        for i in range(5):
            session.add(
                AuditLog(user_id=1, action="create", created_at=old - timedelta(hours=i))
            )
        await session.commit()

        result = await rotate_audit_logs(session, months=6, confirm=False)
        assert result["dry_run"] is True
        assert result["to_archive"] == 5
        assert result["archived"] == 0
        assert result["deleted"] == 0
        # 实际行数不变
        from sqlalchemy import func, select

        cnt = (await session.execute(select(func.count(AuditLog.id)))).scalar_one()
        assert cnt == 5

    @pytest.mark.asyncio
    async def test_rotate_confirm_actually_moves(self, session: AsyncSession):
        from sqlalchemy import func, select

        from app.services.auth.archive import (
            ensure_archive_table,
            rotate_audit_logs,
        )

        await ensure_archive_table(session)

        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
        for i in range(3):
            session.add(
                AuditLog(user_id=1, action="create", created_at=old - timedelta(hours=i))
            )
        # 留 1 条新的不该动
        new_ts = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(AuditLog(user_id=1, action="create", created_at=new_ts))
        await session.commit()

        result = await rotate_audit_logs(
            session, months=6, confirm=True, batch_size=10
        )
        assert result["dry_run"] is False
        assert result["archived"] == 3
        assert result["deleted"] == 3

        # 原表只剩 1 条 (新那条)
        cnt_main = (await session.execute(select(func.count(AuditLog.id)))).scalar_one()
        assert cnt_main == 1

        # 直接 raw SQL 验证
        from sqlalchemy import text

        cnt_arch = (
            await session.execute(
                text("SELECT COUNT(*) FROM audit_logs_archive")
            )
        ).scalar_one()
        assert cnt_arch == 3

    @pytest.mark.asyncio
    async def test_rotate_no_old_rows(self, session: AsyncSession):
        from app.services.auth.archive import rotate_audit_logs

        new_ts = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(AuditLog(user_id=1, action="create", created_at=new_ts))
        await session.commit()

        result = await rotate_audit_logs(session, months=6, confirm=True)
        assert result["to_archive"] == 0
        assert result["archived"] == 0
        assert result["deleted"] == 0
        assert result["dry_run"] is False

    @pytest.mark.asyncio
    async def test_ensure_archive_table_idempotent(self, session: AsyncSession):
        from app.services.auth.archive import ensure_archive_table

        await ensure_archive_table(session)
        await ensure_archive_table(session)  # 第二次不应抛


# ============================================================
#  5) DeepSeek 关联方推断 (mock client)
# ============================================================


class TestRelatedPartyAIInferer:
    """RelatedPartyAIInferer.infer: JSON 解析, confidence 过滤, 批处理, 降级."""

    def _make_client(self, payloads: List[dict]) -> MagicMock:
        """构造一个 mock DeepSeekClient, 每次 chat_json 返 payloads[i]."""
        client = MagicMock()
        client.is_configured = True

        async def chat_json(*args, **kwargs):
            if not payloads:
                return {"candidates": []}
            return payloads.pop(0)

        client.chat_json.side_effect = chat_json
        return client

    @pytest.mark.asyncio
    async def test_unconfigured_raises_deepseek_error(self):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer
        from app.services.sales_ledger.deepseek_client import DeepSeekError

        client = MagicMock()
        client.is_configured = False
        inferer = RelatedPartyAIInferer(client)
        with pytest.raises(DeepSeekError):
            await inferer.infer(
                db=MagicMock(), project_id=1, max_candidates=10
            )

    @pytest.mark.asyncio
    async def test_filters_low_confidence(self, session: AsyncSession):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer
        from app.models.db_models import SalesRecord

        session.add(_project(1, company_name="本公司"))
        session.add(
            SalesRecord(
                project_id=1,
                customer_name="北京XX科技有限公司",
                contract_no="C1",
                product_code="P1",
                product_name="PN",
                revenue_amount=100.0,
                source="manual",
            )
        )
        await session.commit()

        client = self._make_client(
            [
                {
                    "candidates": [
                        {
                            "name": "北京XX科技",
                            "raw_names": ["北京XX科技有限公司"],
                            "reason": "客户名与实控人姓名高度相似",
                            "confidence": 0.92,
                            "evidence_type": "name_similar",
                        },
                        {
                            "name": "上海噪声数据",
                            "raw_names": [],
                            "reason": "弱信号",
                            "confidence": 0.2,  # < 0.3 应被过滤
                            "evidence_type": "other",
                        },
                    ],
                    "scan_summary": "命中 1 个高置信候选",
                }
            ]
        )
        inferer = RelatedPartyAIInferer(client)
        result = await inferer.infer(session, project_id=1, max_candidates=10)

        # 只剩 1 个高置信
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.confidence >= 0.3
        assert c.source == "ai_inferred"
        assert c.party_type == "other"
        # evidence 应包含 reason + evidence_type
        joined = " ".join(c.evidence)
        assert "AI 判断" in joined
        assert "name_similar" in joined

    @pytest.mark.asyncio
    async def test_dedup_within_run(self, session: AsyncSession):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer
        from app.models.db_models import SalesRecord

        session.add(_project(1, company_name="本公司"))
        session.add(
            SalesRecord(
                project_id=1,
                customer_name="客户A",
                contract_no="C1",
                product_code="P1",
                product_name="PN",
                revenue_amount=1.0,
                source="manual",
            )
        )
        await session.commit()

        client = self._make_client(
            [
                {
                    "candidates": [
                        {
                            "name": "客户A",
                            "reason": "d1",
                            "confidence": 0.8,
                            "evidence_type": "other",
                        },
                        {
                            "name": "客户A",  # 同名重复
                            "reason": "d2",
                            "confidence": 0.7,
                            "evidence_type": "other",
                        },
                    ],
                    "scan_summary": "重复",
                }
            ]
        )
        inferer = RelatedPartyAIInferer(client)
        result = await inferer.infer(session, project_id=1, max_candidates=10)
        # 去重后只剩 1
        assert len(result.candidates) == 1

    @pytest.mark.asyncio
    async def test_dedup_with_existing(self, session: AsyncSession):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer
        from app.models.db.related_parties import RelatedParty, RP_TYPE_OTHER
        from app.models.db_models import SalesRecord

        session.add(_project(1, company_name="本公司"))
        session.add(
            SalesRecord(
                project_id=1,
                customer_name="已知关联方",
                contract_no="C1",
                product_code="P1",
                product_name="PN",
                revenue_amount=1.0,
                source="manual",
            )
        )
        # 已存在
        session.add(
            RelatedParty(
                project_id=1,
                name="已知关联方",
                party_type=RP_TYPE_OTHER,
                is_confirmed=True,
            )
        )
        await session.commit()

        client = self._make_client(
            [
                {
                    "candidates": [
                        {
                            "name": "已知关联方",
                            "reason": "应被去重",
                            "confidence": 0.9,
                            "evidence_type": "other",
                        },
                    ],
                    "scan_summary": "0 个新",
                }
            ]
        )
        inferer = RelatedPartyAIInferer(client)
        result = await inferer.infer(
            session,
            project_id=1,
            max_candidates=10,
            existing_names={"已知关联方"},
        )
        assert len(result.candidates) == 0

    @pytest.mark.asyncio
    async def test_project_not_found(self, session: AsyncSession):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer

        client = self._make_client([])
        inferer = RelatedPartyAIInferer(client)
        result = await inferer.infer(session, project_id=999)
        assert result.candidates == []
        assert "项目不存在" in result.scan_summary

    @pytest.mark.asyncio
    async def test_empty_data_returns_no_scan(self, session: AsyncSession):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer

        session.add(_project(1, company_name="X"))
        await session.commit()

        client = self._make_client([])
        inferer = RelatedPartyAIInferer(client)
        result = await inferer.infer(session, project_id=1)
        assert result.candidates == []
        assert "无法" in result.scan_summary

    @pytest.mark.asyncio
    async def test_max_candidates_caps(self, session: AsyncSession):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer
        from app.models.db_models import SalesRecord

        session.add(_project(1, company_name="X"))
        for i in range(5):
            session.add(
                SalesRecord(
                    project_id=1,
                    customer_name=f"客户{i}",
                    contract_no=f"C{i}",
                    product_code=f"P{i}",
                    product_name="PN",
                    revenue_amount=1.0,
                    source="manual",
                )
            )
        await session.commit()

        cands = [
            {
                "name": f"命中{i}",
                "reason": "x",
                "confidence": 0.7,
                "evidence_type": "other",
            }
            for i in range(10)
        ]
        client = self._make_client([{"candidates": cands, "scan_summary": "s"}])
        inferer = RelatedPartyAIInferer(client)
        result = await inferer.infer(session, project_id=1, max_candidates=3)
        assert len(result.candidates) <= 3

    @pytest.mark.asyncio
    async def test_confidence_clamped(self, session: AsyncSession):
        from app.services.related_parties.ai_inferer import RelatedPartyAIInferer
        from app.models.db_models import SalesRecord

        session.add(_project(1, company_name="X"))
        session.add(
            SalesRecord(
                project_id=1,
                customer_name="C",
                contract_no="C1",
                product_code="P1",
                product_name="PN",
                revenue_amount=1.0,
                source="manual",
            )
        )
        await session.commit()

        client = self._make_client(
            [
                {
                    "candidates": [
                        {
                            "name": "越界值",
                            "reason": "模型返回 1.5",
                            "confidence": 1.5,  # 应当被 clamp 到 0.95
                            "evidence_type": "other",
                        }
                    ],
                    "scan_summary": "s",
                }
            ]
        )
        inferer = RelatedPartyAIInferer(client)
        result = await inferer.infer(session, project_id=1)
        assert len(result.candidates) == 1
        assert result.candidates[0].confidence <= 0.95


# ============================================================
#  6) Word 富格式渲染 (run-aware placeholder 替换)
# ============================================================


def _make_docx_with_paragraphs(paragraphs: List[str]) -> bytes:
    """构造一个 word/document.xml 含多个段落的 docx.

    paragraphs 中的每个 string 是一段完整的段落文本, 整段放在单个 <w:t> 里
    (单 run 场景, 走最简单的 path).
    """
    body = "".join(
        f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>"
        for p in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def _make_docx_split_runs(text_segments: List[List[str]]) -> bytes:
    """构造一个 word/document.xml, 每个段落里 placeholder 被 Word 拆到多个 <w:r> run.

    text_segments: List[List[str]] — 外层每个 list = 一段, 内层 list 的元素
    会被分到不同的 <w:r><w:t> 里. 模拟 Word 拆开 placeholder 的实际表现.
    """
    paragraphs_xml = []
    for para_segs in text_segments:
        runs = "".join(
            f"<w:r><w:t xml:space=\"preserve\">{s}</w:t></w:r>" for s in para_segs
        )
        paragraphs_xml.append(f"<w:p>{runs}</w:p>")
    body = "".join(paragraphs_xml)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def _docx_extract_text(docx_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


class TestReportTemplateRunAware:
    """Word XML run-level placeholder 替换 — Pack A.2 P0 修复."""

    def test_single_run_placeholder_replaced(self):
        from app.services.report_template import render_docx

        tmpl = _make_docx_with_paragraphs(["客户名称: ${cust_name}"])
        out = render_docx(tmpl, {"cust_name": "ACME"})
        text = _docx_extract_text(out)
        assert "${cust_name}" not in text
        assert "ACME" in text

    def test_placeholder_split_across_runs(self):
        """核心 case: placeholder 被 Word 拆到 3 个 run ($ {cust_ name})."""
        from app.services.report_template import render_docx

        tmpl = _make_docx_split_runs(
            [
                [
                    "客户名称: $",
                    "{cust_",
                    "name}",
                    " (后续文字)",
                ]
            ]
        )
        out = render_docx(tmpl, {"cust_name": "ACME"})
        text = _docx_extract_text(out)
        assert "${cust_name}" not in text
        assert "ACME" in text
        assert "后续文字" in text  # 后续文字必须保留

    def test_placeholder_split_5_runs(self):
        from app.services.report_template import render_docx

        tmpl = _make_docx_split_runs(
            [
                [
                    "甲: $",
                    "{",
                    "x",
                    "}",
                    " 乙:",
                ]
            ]
        )
        out = render_docx(tmpl, {"x": "X-VAL"})
        text = _docx_extract_text(out)
        assert "${x}" not in text
        assert "X-VAL" in text
        assert "乙:" in text

    def test_multiple_placeholders_in_paragraph(self):
        from app.services.report_template import render_docx

        tmpl = _make_docx_with_paragraphs(
            ["甲方 ${a} 与乙方 ${b} 签订合同 ${contract_no}"]
        )
        out = render_docx(
            tmpl,
            {"a": "AA", "b": "BB", "contract_no": "C-2026-001"},
        )
        text = _docx_extract_text(out)
        for k in ("${a}", "${b}", "${contract_no}"):
            assert k not in text
        assert "AA" in text
        assert "BB" in text
        assert "C-2026-001" in text

    def test_missing_placeholder_replaced_with_marker(self):
        from app.services.report_template import render_docx

        tmpl = _make_docx_with_paragraphs(["hi ${missing}"])
        out = render_docx(tmpl, {})  # 不传 missing
        text = _docx_extract_text(out)
        assert "${missing}" not in text
        # 服务端默认 marker 格式: [未填:<name>] (无空格)
        assert "[未填:missing]" in text

    def test_strict_mode_raises_on_missing(self):
        from app.services.report_template import render_docx

        tmpl = _make_docx_with_paragraphs(["hi ${missing}"])
        with pytest.raises(Exception):
            render_docx(tmpl, {}, strict=True)

    def test_format_preserved_via_first_run(self):
        """run 替换时第一个 run 的 <w:rPr> 不应丢失 (Pack A.2 关键)."""
        from app.services.report_template import render_docx

        # 构造有 rPr 的 docx: 第一个 run 带 <w:rPr> 加粗
        body = (
            '<w:p>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">客户: $</w:t></w:r>'
            '<w:r><w:t xml:space="preserve">{cust_name}</w:t></w:r>'
            "</w:p>"
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>"
        ).encode("utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
            zf.writestr("word/document.xml", xml)
        tmpl = buf.getvalue()

        out = render_docx(tmpl, {"cust_name": "ACME"})
        text = _docx_extract_text(out)
        # 第一个 run 的 <w:rPr><w:b/> 应保留 (ET 序列化可能写成 <w:b />)
        assert "<w:b/>" in text or "<w:b />" in text
        # 内容应替换
        assert "ACME" in text
        assert "${cust_name}" not in text

    def test_nested_field_path(self):
        """${section.field} 嵌套路径."""
        from app.services.report_template import render_docx

        tmpl = _make_docx_with_paragraphs(
            ["项目: ${project.name}, 客户: ${customer.full_name}"]
        )
        out = render_docx(
            tmpl,
            {"project": {"name": "P1"}, "customer": {"full_name": "C1"}},
        )
        text = _docx_extract_text(out)
        assert "P1" in text
        assert "C1" in text

    def test_no_placeholder_unchanged(self):
        from app.services.report_template import render_docx

        tmpl = _make_docx_with_paragraphs(["无占位符的段落"])
        out = render_docx(tmpl, {"any": "X"})
        text = _docx_extract_text(out)
        assert "无占位符的段落" in text
        assert "X" not in text  # 没占位符不写

    def test_garbage_xml_falls_back_to_regex(self):
        """XML 损坏时回退到正则替换 (不抛)."""
        from app.services.report_template import render_docx

        # 构造损坏的 word/document.xml
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            zf.writestr(
                "word/document.xml",
                b"<<< not valid xml >>> ${cust_name} <<<",
            )
        tmpl = buf.getvalue()
        # 不 strict 时不抛, 用正则兜底
        out = render_docx(tmpl, {"cust_name": "ACME"})
        text = _docx_extract_text(out)
        # 正则能命中, 应替换
        assert "ACME" in text
