"""AuditNoteGenerator 主路径单测 (Round 30 P0 补测).

重点:
  - KB 检索失败 → references_kb=[], 仍生成 markdown
  - AI 失败 → ai_text=None, _compose_note 走骨架分支
  - 法规检索失败 → references_regulations=[], 仍生成
  - 正常路径: 5 KB 命中 + 3 法规 + AI 成功 → 完整 markdown
  - _build_query 边界: 空 ctx → "审计说明"
  - _build_prompt 含全部 3 块 (ctx / KB / 法规)
  - _compose_note 骨架格式正确

不依赖真实 API — DB 用 sqlite in-memory, KB / AI 全部 AsyncMock.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_models import Regulation
from app.services.audit_note_generator import (
    AuditNoteContext,
    AuditNoteGenerator,
    AuditNoteResult,
)
from app.services.knowledge_base.retriever import RetrievedChunk


# ----------------------------------------------------------------------
#  通用 fixture — 内存 sqlite + 项目 + 法规
# ----------------------------------------------------------------------


@pytest.fixture
async def db_session():
    """建内存 sqlite + 一个项目 + N 条法规, 返回 (session_maker, project_id)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    from app.models.db_models import Project

    async with sm() as db:
        proj = Project(
            name="P0-Test-审计底稿",
            company_name="测试客户有限公司",
            industry="制造",
            fiscal_year=2024,
        )
        db.add(proj)
        await db.commit()
        await db.refresh(proj)
        pid = proj.id

        # 5 条法规, full_text 都带关键词 "收入" + "主营业务收入"
        for i in range(3):
            db.add(
                Regulation(
                    source="MOF",
                    title=f"《收入准则应用指南》第{i + 1}条",
                    document_no=f"财会[{2020 + i}] 1号",
                    publish_date=f"202{i}-01-01",
                    is_effective=True,
                    full_text=(
                        f"主营业务收入确认应当考虑合同条款, "
                        f"按履约进度确认收入. 法规编号 {i}."
                    ),
                    keywords="收入,主营业务收入,合同",
                )
            )
        await db.commit()

    try:
        yield sm, pid
    finally:
        await engine.dispose()


def _make_kb_chunk(idx: int, score: float = 0.85) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=idx,
        book_id=idx,
        book_title=f"审计案例集{idx}",
        chapter=f"第{idx}章",
        section=f"第{idx}.{idx}节",
        page=idx * 10,
        content=f"案例{idx}: 该公司主营业务收入确认存在截止性问题, 应执行截止性测试...",
        score=score,
        semantic_score=score,
        keyword_score=score,
    )


# ----------------------------------------------------------------------
#  测试 1: KB 检索失败 → 仍生成 markdown
# ----------------------------------------------------------------------


async def test_generate_kb_failure_returns_markdown(db_session):
    sm, pid = db_session
    gen = AuditNoteGenerator()
    gen.kb.search = AsyncMock(side_effect=RuntimeError("KB backend dead"))
    gen.ai.enabled = False

    async with sm() as db:
        ctx = AuditNoteContext(
            project_id=pid,
            account_code="5001",
            account_name="主营业务收入",
            audit_objective="收入截止性",
        )
        result = await gen.generate(db, ctx)

    assert isinstance(result, AuditNoteResult)
    assert result.note  # 非空 markdown
    assert "审计说明" in result.note
    assert result.references_kb == []
    assert result.ai_enabled is False
    assert result.ai_raw is None
    # 骨架分支应出现
    assert "科目情况" in result.note or "审计程序" in result.note


# ----------------------------------------------------------------------
#  测试 2: AI 失败 → 走 _compose_note 骨架分支
# ----------------------------------------------------------------------


async def test_generate_ai_failure_uses_skeleton(db_session):
    sm, pid = db_session
    gen = AuditNoteGenerator()
    # KB 返回 2 条命中
    chunks = [_make_kb_chunk(1), _make_kb_chunk(2)]
    gen.kb.search = AsyncMock(return_value=chunks)
    gen.ai.enabled = True
    gen.ai._call_minimax = AsyncMock(side_effect=RuntimeError("AI down"))

    async with sm() as db:
        ctx = AuditNoteContext(
            project_id=pid,
            account_code="1221",
            account_name="其他应收款",
            balance_amount=50000.0,
            audit_objective="完整性",
            risk_description="存在异常大额挂账",
        )
        result = await gen.generate(db, ctx, include_regulations=False)

    assert result.ai_enabled is True
    assert result.ai_raw is None
    # 骨架应包含: 科目情况 / 参考案例 / 法规依据 / 审计程序
    assert "科目情况" in result.note
    assert "参考案例" in result.note
    assert "审计程序" in result.note
    # KB 引用应仍保留 (2 条)
    assert len(result.references_kb) == 2
    assert result.references_kb[0]["book_title"] == "审计案例集1"


# ----------------------------------------------------------------------
#  测试 3: 法规检索失败 → 仍生成 markdown
# ----------------------------------------------------------------------


async def test_generate_regulation_failure_returns_markdown(db_session, monkeypatch):
    """Regulation 表查询抛异常 → _search_regulations 内部 try/except 兜底为 [].
    主流程 generate 仍应输出 markdown 且 references_regulations=[].
    """
    sm, pid = db_session

    # 让 _search_regulations 内部 SQL execute 抛异常 — 用一个包装 session 拦截 execute
    from sqlalchemy.exc import OperationalError

    class _BoomSession:
        async def execute(self, *a, **kw):
            raise OperationalError("select", {}, Exception("Regulation DB down"))

        async def commit(self):
            pass

        async def flush(self):
            pass

        async def delete(self, *a, **kw):
            pass

        async def add(self, *a, **kw):
            pass

    boom_db = _BoomSession()
    ctx = AuditNoteContext(
        project_id=pid,
        account_code="5001",
        account_name="主营业务收入",
        audit_objective="截止性",
    )
    gen = AuditNoteGenerator()
    gen.kb.search = AsyncMock(return_value=[])
    gen.ai.enabled = False

    # 直接调 _search_regulations, 它内部 try/except 应该兜底成 []
    result_hits = await gen._search_regulations(boom_db, ctx)
    assert result_hits == []  # 内部异常被吞, 不冒泡

    # 验证: 同样 db 上调 generate, 主流程不挂, note 非空
    gen2 = AuditNoteGenerator()
    gen2.kb.search = AsyncMock(return_value=[])
    gen2.ai.enabled = False
    async with sm() as db:
        # 包一层: 把真实 session 的 execute 替换成抛异常
        class _Wrapped:
            def __init__(self, real):
                self._real = real

            async def execute(self, *a, **kw):
                raise OperationalError("select", {}, Exception("Regulation DB down"))

            def __getattr__(self, name):
                return getattr(self._real, name)

        wrapped = _Wrapped(db)
        result = await gen2.generate(wrapped, ctx, include_regulations=True)
    assert result.note
    assert result.references_kb == []
    assert result.references_regulations == []
    assert result.ai_enabled is False


async def test_generate_regulation_disabled_returns_markdown(db_session):
    """include_regulations=False 时完全不查法规表."""
    sm, pid = db_session
    gen = AuditNoteGenerator()
    gen.kb.search = AsyncMock(return_value=[])
    gen.ai.enabled = False

    async with sm() as db:
        ctx = AuditNoteContext(
            project_id=pid,
            account_code="5001",
            account_name="主营业务收入",
        )
        result = await gen.generate(db, ctx, include_regulations=False)

    assert result.note
    assert result.references_regulations == []


# ----------------------------------------------------------------------
#  测试 4: 正常路径 — 5 KB 命中 + 3 法规 + AI 成功
# ----------------------------------------------------------------------


async def test_generate_full_path_with_all_sources(db_session):
    sm, pid = db_session
    gen = AuditNoteGenerator()
    chunks = [_make_kb_chunk(i, score=0.7 + i * 0.05) for i in range(1, 6)]
    gen.kb.search = AsyncMock(return_value=chunks)
    gen.ai.enabled = True
    ai_text = (
        "### 审计说明(AI)\n"
        "1) 主营业务收入本期存在跨期问题, 参考案例 1/2/3;\n"
        "2) 适用《收入准则应用指南》第 1 条;\n"
        "3) 建议执行截止性测试 + 函证程序。"
    )
    gen.ai._call_minimax = AsyncMock(return_value=ai_text)

    async with sm() as db:
        ctx = AuditNoteContext(
            project_id=pid,
            account_code="5001",
            account_name="主营业务收入",
            balance_amount=10_000_000.0,
            industry="制造",
            audit_objective="截止性 + 完整性",
            risk_description="存在跨期确认风险",
        )
        result = await gen.generate(db, ctx, kb_top_k=5)

    # AI 文本应直接进入 note
    assert ai_text in result.note
    assert result.ai_enabled is True
    assert result.ai_raw == ai_text
    # KB 引用 5 条
    assert len(result.references_kb) == 5
    assert result.references_kb[0]["book_title"] == "审计案例集1"
    # 法规引用 ≤3 条 (we seeded 3, all match keywords)
    assert len(result.references_regulations) >= 1
    assert len(result.references_regulations) <= 3
    # 法规引用结构
    if result.references_regulations:
        r = result.references_regulations[0]
        assert "id" in r
        assert "title" in r
        assert "document_no" in r


# ----------------------------------------------------------------------
#  测试 5: _build_query 只填 account_code
# ----------------------------------------------------------------------


def test_build_query_account_code_only():
    gen = AuditNoteGenerator()
    ctx = AuditNoteContext(project_id=1, account_code="5001")
    q = gen._build_query(ctx)
    assert "5001" in q
    assert q.startswith("科目")


# ----------------------------------------------------------------------
#  测试 6: _build_query 含 audit_objective + industry
# ----------------------------------------------------------------------


def test_build_query_with_audit_objective_and_industry():
    gen = AuditNoteGenerator()
    ctx = AuditNoteContext(
        project_id=1,
        account_code="1221",
        account_name="其他应收款",
        audit_objective="完整性",
        industry="制造",
        risk_description="大额挂账",
    )
    q = gen._build_query(ctx)
    assert "1221" in q
    assert "其他应收款" in q
    assert "完整性" in q
    assert "制造" in q
    assert "大额挂账" in q


# ----------------------------------------------------------------------
#  测试 7: _build_query 空 ctx → "审计说明"
# ----------------------------------------------------------------------


def test_build_query_empty_returns_default():
    gen = AuditNoteGenerator()
    ctx = AuditNoteContext(project_id=1)
    assert gen._build_query(ctx) == "审计说明"

    # 只有 account_name 无 code → "科目 其他应收款"
    ctx2 = AuditNoteContext(project_id=1, account_name="其他应收款")
    q2 = gen._build_query(ctx2)
    assert "其他应收款" in q2


# ----------------------------------------------------------------------
#  测试 8: _build_prompt 含全部 3 块
# ----------------------------------------------------------------------


def test_build_prompt_includes_all_sections():
    gen = AuditNoteGenerator()
    chunks = [_make_kb_chunk(1)]
    regs = [
        Regulation(
            id=1,
            source="MOF",
            title="《收入准则应用指南》",
            document_no="财会[2020] 1号",
            publish_date="2020-01-01",
            full_text="主营业务收入按履约进度确认",
            keywords="收入,主营业务收入",
        )
    ]
    ctx = AuditNoteContext(
        project_id=1,
        account_code="5001",
        account_name="主营业务收入",
        balance_amount=10_000_000.0,
        industry="制造",
        audit_objective="截止性",
        risk_description="跨期风险",
        extra_facts={"voucher_count": 1500},
    )
    prompt = gen._build_prompt(ctx, chunks, regs)

    # 三块标题
    assert "### 底稿上下文" in prompt
    assert "### 相似实务案例" in prompt
    assert "### 相关法规依据" in prompt
    # 上下文 JSON 内容
    assert '"account_code": "5001"' in prompt or '"account_code":"5001"' in prompt
    assert '"audit_objective": "截止性"' in prompt or '"audit_objective":"截止性"' in prompt
    assert "extra_facts" in prompt
    # KB 引用 + 法规引用
    assert "[案例1]" in prompt
    assert "审计案例集1" in prompt
    assert "[法规1]" in prompt
    assert "财会[2020] 1号" in prompt

    # 空 KB / 空法规 → 占位
    prompt2 = gen._build_prompt(ctx, [], [])
    assert "(知识库未命中)" in prompt2
    assert "(法规库未命中)" in prompt2


# ----------------------------------------------------------------------
#  测试 9: _compose_note 骨架格式
# ----------------------------------------------------------------------


async def test_compose_note_skeleton_format(db_session):
    """AI 失败时 _compose_note 走骨架, 必须有 4 大块 + 建议执行的审计程序."""
    sm, pid = db_session
    gen = AuditNoteGenerator()
    chunks = [_make_kb_chunk(1), _make_kb_chunk(2), _make_kb_chunk(3)]
    async with sm() as db:
        ctx = AuditNoteContext(
            project_id=pid,
            account_code="5001",
            account_name="主营业务收入",
            balance_amount=8_000_000.0,
            audit_objective="截止性",
            risk_description="跨期确认风险",
        )
        # 1) AI 文本为 None → 走骨架
        md = gen._compose_note(ctx, chunks, [], ai_text=None)

    assert md.startswith("## 审计说明")
    # 四块小标题
    assert "一、科目情况" in md
    assert "二、参考案例" in md
    assert "三、法规依据" in md
    assert "四、建议执行的审计程序" in md
    # KB 案例显示 3 条 (compose 限制 ≤3)
    assert "审计案例集1" in md
    assert "审计案例集2" in md
    assert "审计案例集3" in md
    # 法规为空 → 占位
    assert "(法规库未命中)" in md
    # 建议审计程序
    assert "复核期末余额构成" in md
    assert "函证" in md
    # ai_text 有值时, 骨架被替换为 AI 文本
    md_with_ai = gen._compose_note(ctx, chunks, [], ai_text="这是 AI 输出的说明")
    assert "这是 AI 输出的说明" in md_with_ai
    assert "一、科目情况" not in md_with_ai  # 骨架被跳过
