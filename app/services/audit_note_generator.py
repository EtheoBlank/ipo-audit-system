"""审计说明生成器 (Audit Note Generator).

把 "底稿上下文 + 知识库相似案例 + (可选) 法规摘录" 喂给 AI 模型，输出可直接贴到
Excel 备注列 / Word 报告里的审计说明。

设计点：
  - 即便 AI 不可用，也会把检索到的 KB / 法规摘录拼成 markdown 返回 — 至少不空。
  - 所有外部依赖都做了"温和降级"：KB 没书 / AI key 没配 都不会 500。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import Regulation
from app.services.ai_analysis import AIAnalysisService
from app.services.knowledge_base import KnowledgeBaseService
from app.services.knowledge_base.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

# P0-15 (2026-06-19, round30): markdown 注入防护. KB 检索 / 法规摘录 / AI 返回
# 直接拼进审计说明 markdown, 前端或 docx 渲染时 ![alt](javascript:...) 可执行 XSS.
# 在 _compose_note 末尾脱敏, 不影响其他业务逻辑.
_SANITIZE_RULES: list[tuple[str, str, int]] = [
    # 1) 图片链接 javascript: / data:text/html
    (r"!\[([^\]]*)\]\(\s*javascript:[^)]*\)", r"!\1()(已脱敏)", re.IGNORECASE),
    (r"!\[([^\]]*)\]\(\s*data:text/html[^)]*\)", r"!\1()(已脱敏)", re.IGNORECASE),
    # 2) 普通链接 javascript: / data:text/html
    (r"\[([^\]]+)\]\(\s*javascript:[^)]*\)", r"\1(已脱敏)", re.IGNORECASE),
    (r"\[([^\]]+)\]\(\s*data:text/html[^)]*\)", r"\1(已脱敏)", re.IGNORECASE),
    # 3) <script>...</script> 整段
    (r"<script[^>]*>.*?</script>", "(script 标签已脱敏)", re.IGNORECASE | re.DOTALL),
    # 4) 内联 on*= 事件
    (r'\bon\w+\s*=\s*"[^"]*"', "", re.IGNORECASE),
    (r"\bon\w+\s*=\s*'[^']*'", "", re.IGNORECASE),
]


def _sanitize_markdown(md: Optional[str]) -> Optional[str]:
    """P0-15: 审计说明 markdown 渲染前脱敏, 防 KB 注入 XSS."""
    if not md:
        return md
    for pattern, repl, flags in _SANITIZE_RULES:
        md = re.sub(pattern, repl, md, flags=flags)
    return md


@dataclass
class AuditNoteContext:
    """生成一条审计说明需要的上下文."""

    project_id: int
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    balance_amount: Optional[float] = None
    industry: Optional[str] = None
    risk_description: Optional[str] = None
    audit_objective: Optional[str] = None  # 例如"收入截止性"、"存货跌价"
    extra_facts: Optional[dict] = None  # 任意额外字段
    # P0 IDOR 修复 (round 32, 2026-06-20): 显式 firm_id, 用于 KB 检索多所隔离.
    # 调用方传了 → 直接用; 没传 → 从 project 反查.
    firm_id: Optional[int] = None


@dataclass
class AuditNoteResult:
    """生成结果。"""

    note: str  # 主输出 — Markdown
    references_kb: List[dict] = field(default_factory=list)
    references_regulations: List[dict] = field(default_factory=list)
    ai_enabled: bool = False
    ai_raw: Optional[str] = None


# ----------------------------------------------------------------------
# 主类
# ----------------------------------------------------------------------


class AuditNoteGenerator:
    """组合 KB / 法规 / AI 三件套生成审计说明。"""

    def __init__(self) -> None:
        self.kb = KnowledgeBaseService()
        self.ai = AIAnalysisService()

    # —————————————————————————————————————————————————————————

    async def generate(
        self,
        db: AsyncSession,
        ctx: AuditNoteContext,
        *,
        kb_top_k: int = 4,
        kb_category: Optional[str] = None,
        include_regulations: bool = True,
        firm_id: Optional[int] = None,  # P0 IDOR (round 32): 显式传值覆盖 ctx/project 反查
    ) -> AuditNoteResult:
        """生成审计说明 — 主入口。"""
        query_text = self._build_query(ctx)

        # P0 IDOR (round 32, 2026-06-20): 显式 firm_id 优先; 否则从 ctx 拿;
        # 都没有 → 从 project 反查. 防跨 firm 检索知识库泄密.
        if firm_id is None:
            firm_id = ctx.firm_id
        if firm_id is None and ctx.project_id:
            try:
                from app.models.db_models import Project
                from sqlalchemy import select as _select
                proj = (
                    await db.execute(
                        _select(Project.firm_id).where(Project.id == ctx.project_id)
                    )
                ).scalar_one_or_none()
                firm_id = proj
            except Exception:  # noqa: BLE001
                logger.exception("project -> firm_id 反查失败, KB 检索跳过 firm 过滤")

        # 1) 知识库检索
        try:
            kb_results = await self.kb.search(
                db,
                query=query_text,
                top_k=kb_top_k,
                category=kb_category,
                project_id=ctx.project_id,
                firm_id=firm_id,  # P0 IDOR: 强制传 firm_id, 防跨所泄 KB
                context=(
                    f"account_code={ctx.account_code or ''};objective={ctx.audit_objective or ''}"
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("KB 检索失败 — 跳过")
            kb_results = []

        # 2) 法规检索
        regulation_hits: list[Regulation] = []
        if include_regulations:
            regulation_hits = await self._search_regulations(db, ctx)

        # 3) 拼上下文 → AI
        prompt = self._build_prompt(ctx, kb_results, regulation_hits)
        ai_text: Optional[str] = None
        if self.ai.enabled:
            try:
                ai_text = await self.ai._call_minimax(prompt, self._system_prompt())
            except Exception:  # noqa: BLE001
                logger.exception("AI 调用失败 — 退回纯检索结果")

        # 4) 汇总输出
        note_md = self._compose_note(ctx, kb_results, regulation_hits, ai_text)
        return AuditNoteResult(
            note=note_md,
            references_kb=[
                {
                    "book_id": r.book_id,
                    "book_title": r.book_title,
                    "chapter": r.chapter,
                    "section": r.section,
                    "page": r.page,
                    "score": r.score,
                }
                for r in kb_results
            ],
            references_regulations=[
                {
                    "id": r.id,
                    "title": r.title,
                    "document_no": r.document_no,
                    "source": r.source,
                    "publish_date": r.publish_date,
                }
                for r in regulation_hits
            ],
            ai_enabled=self.ai.enabled,
            ai_raw=ai_text,
        )

    # —————————————————————————————————————————————————————————
    # 内部
    # —————————————————————————————————————————————————————————

    def _build_query(self, ctx: AuditNoteContext) -> str:
        parts: list[str] = []
        if ctx.account_code or ctx.account_name:
            parts.append(f"科目 {ctx.account_code or ''} {ctx.account_name or ''}".strip())
        if ctx.audit_objective:
            parts.append(f"审计目标 {ctx.audit_objective}")
        if ctx.risk_description:
            parts.append(ctx.risk_description)
        if ctx.industry:
            parts.append(f"行业 {ctx.industry}")
        return " ".join(p for p in parts if p).strip() or "审计说明"

    async def _search_regulations(
        self, db: AsyncSession, ctx: AuditNoteContext
    ) -> list[Regulation]:
        """根据科目/目标拼关键词 → SQL LIKE 检索 3-5 条法规。"""
        keywords = []
        if ctx.account_name:
            keywords.append(ctx.account_name)
        if ctx.audit_objective:
            keywords.append(ctx.audit_objective)
        keywords = [k for k in keywords if k]
        if not keywords:
            return []

        # P0 性能修复 (2026-06-19): 旧版对 Regulation.full_text (Text 无索引) 做 LIKE
        # 10K+ 条规 × 5 kw × 3 列 = 150 LIKE-on-Text → 全表扫, 单次 10s+
        # 新版: 只查 title/keywords (短字段, LIKE 快), full_text 留给 Python 后置过滤
        from sqlalchemy import or_

        # 截断关键词防超长 + LIKE 通配符转义
        from app.services.auth.audit_log import _escape_like

        kw_capped = [k[:30] for k in keywords[:8]]
        clauses = []
        for kw in kw_capped:
            like = f"%{_escape_like(kw)}%"
            clauses.append(Regulation.title.like(like, escape="\\"))
            clauses.append(Regulation.keywords.like(like, escape="\\"))
        stmt = (
            select(Regulation)
            .where(or_(*clauses))
            .order_by(Regulation.publish_date.desc().nullslast())
            .limit(20)  # 多取一些供 Python 评分
        )
        try:
            rows = list((await db.execute(stmt)).scalars().all())
        except Exception:  # noqa: BLE001
            logger.exception("法规检索失败")
            return []

        # 后置 full_text 二次过滤 (Python 内存级, 不走 DB)
        def _hit(r: Regulation) -> bool:
            ft = r.full_text or ""
            return any(kw in ft for kw in kw_capped)

        rows = [r for r in rows if _hit(r)]
        return rows[:5]

    def _system_prompt(self) -> str:
        return (
            "你是一名资深 IPO 审计经理。请基于给定的科目上下文、知识库中检索到的"
            "相似实务案例、以及最新法规摘要，撰写一段简洁、专业、可以直接放在"
            "审计底稿/审计说明里的中文文字。要求：\n"
            "1) 先描述科目情况；\n"
            "2) 引用相似案例的处理方式 (注明书名/章节)；\n"
            "3) 引用对应法规依据 (注明文号/条款)；\n"
            "4) 给出本期的审计程序与结论建议；\n"
            "字数 200-450 字，禁止虚构条款编号或案例。"
        )

    def _build_prompt(
        self,
        ctx: AuditNoteContext,
        kb: List[RetrievedChunk],
        regs: list[Regulation],
    ) -> str:
        kb_blocks = []
        for i, r in enumerate(kb, 1):
            loc = " / ".join(filter(None, [r.book_title, r.chapter, r.section]))
            kb_blocks.append(f"[案例{i}] (出处: {loc}, 相似度 {r.score:.2f})\n{r.content[:600]}")
        kb_block = "\n\n".join(kb_blocks) or "(知识库未命中)"

        reg_blocks = []
        for i, r in enumerate(regs, 1):
            head = f"《{r.title}》"
            if r.document_no:
                head += f"({r.document_no})"
            if r.publish_date:
                head += f"  发布日期 {r.publish_date}"
            text = (r.full_text or r.summary or "")[:400]
            reg_blocks.append(f"[法规{i}] {head}\n{text}")
        reg_block = "\n\n".join(reg_blocks) or "(法规库未命中)"

        ctx_block = json.dumps(
            {
                "account_code": ctx.account_code,
                "account_name": ctx.account_name,
                "balance_amount": ctx.balance_amount,
                "industry": ctx.industry,
                "audit_objective": ctx.audit_objective,
                "risk_description": ctx.risk_description,
                "extra_facts": ctx.extra_facts,
            },
            ensure_ascii=False,
            indent=2,
        )

        return (
            f"### 底稿上下文\n{ctx_block}\n\n"
            f"### 相似实务案例 (来自用户上传知识库)\n{kb_block}\n\n"
            f"### 相关法规依据\n{reg_block}\n\n"
            "请按 system 中的要求撰写审计说明。"
        )

    def _compose_note(
        self,
        ctx: AuditNoteContext,
        kb: List[RetrievedChunk],
        regs: list[Regulation],
        ai_text: Optional[str],
    ) -> str:
        """无论 AI 是否生成成功，都给出可读 markdown。"""
        lines: list[str] = []
        title = "审计说明"
        if ctx.account_code or ctx.account_name:
            title += f" — {ctx.account_code or ''} {ctx.account_name or ''}".rstrip()
        lines.append(f"## {title}")

        if ai_text and ai_text.strip():
            lines.append(ai_text.strip())
        else:
            # 无 AI 时给一个结构化骨架
            lines.append("### 一、科目情况")
            lines.append(
                f"{ctx.account_name or '该科目'} 余额 "
                f"{ctx.balance_amount if ctx.balance_amount is not None else '—'}，"
                f"{ctx.risk_description or '审计目标：' + (ctx.audit_objective or '余额完整性与准确性')}"
            )
            lines.append("### 二、参考案例")
            if kb:
                for i, r in enumerate(kb[:3], 1):
                    loc = " / ".join(filter(None, [r.book_title, r.chapter, r.section]))
                    lines.append(f"{i}. **出处**：{loc}")
                    lines.append(f"   > {r.content[:200]}…")
            else:
                lines.append("- (知识库未命中相似案例)")
            lines.append("### 三、法规依据")
            if regs:
                for r in regs[:3]:
                    head = f"《{r.title}》"
                    if r.document_no:
                        head += f"（{r.document_no}）"
                    lines.append(f"- {head}")
            else:
                lines.append("- (法规库未命中)")
            lines.append("### 四、建议执行的审计程序")
            lines.append("- 复核期末余额构成；")
            lines.append("- 抽样检查原始凭证；")
            lines.append("- 实施替代程序 / 函证；")
            lines.append("- 关注与同行业相似科目的处理差异。")

        return _sanitize_markdown("\n\n".join(lines))


# 全局单例 — 与 KnowledgeBaseService 一致
audit_note_generator = AuditNoteGenerator()
