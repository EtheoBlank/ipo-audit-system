"""Round 25 P0-7: ai_inferer 供应商来源 — account_code 白名单修复测试.

覆盖:
  - 老实现用 account_name LIKE '%应付%' 模糊匹配, 把"应付职工薪酬"/"应付福利费"
    / "预付账款" 等无关科目一起拉进供应商, 送给 DeepSeek 做关联方推断,
    会产生大量 false positive 候选.
  - 新实现只取 1122/2201/2202 前缀 + account_name "职工/薪酬/福利" 兜底排除.
"""
from __future__ import annotations

import os
from typing import List
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTH_ENABLED", "false")

from app.core.database import Base
from app.models.db_models import ChronologicalAccount, Project


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
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sm() as s:
        yield s
    await engine.dispose()


def _project(pid: int = 1) -> Project:
    return Project(
        id=pid,
        name=f"P{pid}",
        company_name="本公司",
        fiscal_year=2024,
        status="active",
    )


def _chrono(
    *,
    pid: int = 1,
    code: str = "2202",
    name: str = "应付账款",
    aux: str | None = "XX 供应商",
) -> ChronologicalAccount:
    return ChronologicalAccount(
        project_id=pid,
        voucher_date="2024-06-01",
        voucher_no=f"V{pid}-{code}",
        account_code=code,
        account_name=name,
        debit_amount=100.0,
        credit_amount=100.0,
        auxiliary_accounting=aux,
    )


def _make_client(payloads: List[dict]) -> MagicMock:
    """构造 mock DeepSeekClient: 返回空候选 (不关心 AI 输出, 只关心 supplier 名单)."""
    client = MagicMock()
    client.is_configured = True

    async def chat_json(*args, **kwargs):
        if not payloads:
            return {"candidates": []}
        return payloads.pop(0)

    client.chat_json.side_effect = chat_json
    return client


async def _capture_supplier_names(session: AsyncSession, project_id: int) -> list[str]:
    """走 infer() 的 SQL 路径, 提取合并后送给 LLM 的 supplier 名列表."""
    from app.services.related_parties.ai_inferer import RelatedPartyAIInferer

    captured: dict[str, list[str]] = {}

    client = MagicMock()
    client.is_configured = True

    async def chat_json(*args, **kwargs):
        # user_msg 是 JSON 字符串, 解析后取 parties_to_screen 里的 supplier
        import json as _json

        try:
            payload = _json.loads(kwargs.get("user") or args[1] if len(args) > 1 else kwargs["user"])
        except Exception:  # noqa: BLE001
            payload = {}
        suppliers = [
            p.get("name")
            for p in (payload.get("parties_to_screen") or [])
            if p.get("role") == "supplier"
        ]
        captured.setdefault("suppliers", []).extend(suppliers)
        return {"candidates": [], "scan_summary": "noop"}

    client.chat_json.side_effect = chat_json
    inferer = RelatedPartyAIInferer(client)
    await inferer.infer(session, project_id=project_id, max_candidates=5)
    return captured.get("suppliers", [])


# ----------------------------------------------------------------------
#  P0-7 修复测试
# ----------------------------------------------------------------------


class TestSupplierWhitelistP0:
    """P0-7: account_code 白名单 + 职工/薪酬/福利 兜底排除."""

    @pytest.mark.asyncio
    async def test_supplier_query_excludes_employee_payable(self, session: AsyncSession):
        """'应付职工薪酬' (2211) 不应被纳入供应商列表."""
        session.add(_project(1))
        # 真正的供应商
        session.add(
            _chrono(
                code="2202",
                name="应付账款",
                aux="北京钢铁有限公司",
            )
        )
        # 应付职工薪酬 — 必须排除
        session.add(
            _chrono(
                code="2211",
                name="应付职工薪酬",
                aux="应付职工薪酬-工资",
            )
        )
        # 应付福利费 — 必须排除
        session.add(
            _chrono(
                code="2241",
                name="应付福利费",
                aux="应付福利费-部门聚餐",
            )
        )
        await session.commit()

        supplier_names = await _capture_supplier_names(session, project_id=1)

        assert "北京钢铁有限公司" in supplier_names, "真供应商应保留"
        assert "应付职工薪酬-工资" not in supplier_names, "职工薪酬必须被排除"
        assert "应付福利费-部门聚餐" not in supplier_names, "福利费必须被排除"

    @pytest.mark.asyncio
    async def test_supplier_query_excludes_welfare_payable(self, session: AsyncSession):
        """'应付福利费' (2241) 不应被纳入供应商列表 — 2241 已从白名单移除."""
        session.add(_project(1))
        session.add(
            _chrono(
                code="2241",
                name="应付福利费",
                aux="应付福利费",
            )
        )
        await session.commit()

        supplier_names = await _capture_supplier_names(session, project_id=1)
        assert "应付福利费" not in supplier_names

    @pytest.mark.asyncio
    async def test_supplier_query_includes_real_supplier_payable(self, session: AsyncSession):
        """'应付账款 - XX 供应商' (2202) 应正常纳入供应商列表."""
        session.add(_project(1))
        session.add(
            _chrono(
                code="2202",
                name="应付账款",
                aux="上海金属贸易有限公司",
            )
        )
        # 2201 应付票据也是供应商相关, 应纳入
        session.add(
            _chrono(
                code="2201",
                name="应付票据",
                aux="广州机械有限公司",
            )
        )
        # 1122 预付账款是预付供应商款, 应纳入
        session.add(
            _chrono(
                code="1122",
                name="预付账款",
                aux="深圳电子元件供应商",
            )
        )
        await session.commit()

        supplier_names = await _capture_supplier_names(session, project_id=1)
        assert "上海金属贸易有限公司" in supplier_names
        assert "广州机械有限公司" in supplier_names
        assert "深圳电子元件供应商" in supplier_names

    @pytest.mark.asyncio
    async def test_supplier_query_keyword_fallback(self, session: AsyncSession):
        """即便 account_code 异常 (不在白名单前缀), 但若辅核名带 '职工/薪酬',
        也应在关键词兜底路径被排除 — 实际 SQL 已不取该行, 这里直接验证白名单
        的过滤结果集不含职工/薪酬/福利关键字."""
        session.add(_project(1))
        # 真正的供应商
        session.add(
            _chrono(
                code="2202",
                name="应付账款",
                aux="ABC Trade Co.",
            )
        )
        # 即使有些数据源 code 标记成 2241 但 aux 写的是供应商, 也不该进 (2241 已移除)
        session.add(
            _chrono(
                code="2241",
                name="其他应付款",
                aux="DEF Logistics Co.",
            )
        )
        # 2241 另一行 — 名字带职工, 兜底路径触发 (走不到这行因为 SQL 已过滤)
        session.add(
            _chrono(
                code="2211",
                name="应付职工薪酬",
                aux="应付职工薪酬-年终奖",
            )
        )
        await session.commit()

        supplier_names = await _capture_supplier_names(session, project_id=1)
        # 2241 应排除 (已从白名单移除)
        assert "DEF Logistics Co." not in supplier_names, "2241 不在白名单"
        assert "ABC Trade Co." in supplier_names
        # 职工薪酬必排除
        assert not any("职工" in n or "薪酬" in n or "福利" in n for n in supplier_names)

    @pytest.mark.asyncio
    async def test_supplier_query_excludes_pre_received(self, session: AsyncSession):
        """'预收账款' (2203) 是客户预付, 不是供应商 — 必须排除."""
        session.add(_project(1))
        session.add(
            _chrono(
                code="2203",
                name="预收账款",
                aux="北京XX客户预付",
            )
        )
        await session.commit()

        supplier_names = await _capture_supplier_names(session, project_id=1)
        assert "北京XX客户预付" not in supplier_names, "预收账款是客户, 不是供应商"