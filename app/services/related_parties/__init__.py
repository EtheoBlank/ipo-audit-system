"""关联方专项服务 (Pack B).

5 个核心子能力, 全部走 Pack A 降级三件套:
  - detector       — 多通道识别 (序时账扫描 + 客户供应商重叠 + 招股书对比 + AI 兜底)
  - transaction    — 关联交易 CRUD + 公允性测试
  - capital_occupation — 资金占用穿行 (银行+其他应收应付)
  - peer_competition — 同业竞争评分 (主业关键词重合度)
  - disclosure_checker — 招股书披露 diff
  - report_generator — 专项报告 docx
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import and_, desc, func, or_, select  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.related_parties import (
    DISCLOSURE_GAP_CRITICAL,
    DISCLOSURE_GAP_OK,  # noqa: F401
    DISCLOSURE_GAP_REVIEW,
    PeerCompetitionAssessment,
    ProspectusDisclosureGap,
    RP_SOURCE_AI,  # noqa: F401
    RP_SOURCE_CHRONO_SCAN,
    RP_SOURCE_CUSTOMER_OVERLAP,
    RP_TYPE_OTHER,
    RelatedParty,
    RelatedPartyCapitalOccupation,  # noqa: F401
    RelatedPartyRelation,  # noqa: F401
    RelatedPartyTransaction,
)
from app.models.db_models import ChronologicalAccount, SalesRecord
from app.models.related_parties import (
    DetectorCandidate,
    DetectorRunRequest,
    DetectorRunResponse,
    DisclosureCheckResponse,
    DisclosureGapResponse,
    FairnessCheckRequest,
    FairnessCheckResponse,
    RelatedPartyCreate,  # noqa: F401
    RelatedPartyResponse,  # noqa: F401
)

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# === 序时账摘要中常见关联方关键词 (中文+英文) ===
_RELATED_PARTY_KEYWORDS_CN = [
    "关联方",
    "关联公司",
    "关联企业",
    "兄弟公司",
    "母公司",
    "子公司",
    "实际控制人",
    "实控人",
    "一致行动人",
    "控股股东",
    "董事",
    "监事",
    "高级管理人员",
    "股东",
    "配偶",
    "亲属",
    "近亲属",
]


def _normalize_name(name: str) -> str:
    """公司名归一化 — 去空白 / 全半角 / 常见后缀."""
    if not name:
        return ""
    n = name.strip()
    n = re.sub(r"\s+", "", n)
    n = re.sub(r"[（(].*?[)）]", "", n)
    # 按长度倒序匹配, 防止 "股份有限公司" 被 "有限公司" 先吃掉一半
    for suffix in ("股份有限公司", "有限责任公司", "(有限合伙)", "有限公司", "公司"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
            break
    return n


# ============================================================
#  Detector (识别引擎)
# ============================================================


class RelatedPartyDetector:
    """多通道识别 + AI 兜底."""

    @staticmethod
    async def run(
        db: AsyncSession,
        req: DetectorRunRequest,
        *,
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
    ) -> DetectorRunResponse:
        scanned_vouchers = 0
        scanned_customers = 0
        scanned_suppliers = 0
        candidates: List[DetectorCandidate] = []
        seen_names: Set[str] = set()

        # 已有关联方
        existing = list(
            (
                await db.execute(
                    select(RelatedParty.name).where(RelatedParty.project_id == req.project_id)
                )
            )
            .scalars()
            .all()
        )
        existing_norm = {_normalize_name(n) for n in existing}

        keywords = list(_RELATED_PARTY_KEYWORDS_CN)
        if req.keywords_extra:
            keywords.extend(req.keywords_extra)

        # 通道 1: 序时账摘要扫描
        if req.enable_chrono_scan:
            chrono_stmt = select(ChronologicalAccount).where(
                ChronologicalAccount.project_id == req.project_id,
                ChronologicalAccount.summary.isnot(None),
            )
            chrono_rows = list((await db.execute(chrono_stmt)).scalars().all())
            scanned_vouchers = len(chrono_rows)
            for row in chrono_rows:
                summary = row.summary or ""
                # 命中关键词时, 把摘要 + 对方科目 / 辅助核算 一并提为候选
                hits = [k for k in keywords if k in summary]
                if hits:
                    aux = row.auxiliary_accounting or ""
                    candidate_name = aux.strip() if aux else summary[:50]
                    candidate_name = _normalize_name(candidate_name)
                    if (
                        candidate_name
                        and candidate_name not in existing_norm
                        and candidate_name not in seen_names
                    ):
                        seen_names.add(candidate_name)
                        candidates.append(
                            DetectorCandidate(
                                name=candidate_name,
                                party_kind="entity",
                                party_type=RP_TYPE_OTHER,
                                source=RP_SOURCE_CHRONO_SCAN,
                                confidence=0.5 + 0.1 * min(len(hits), 3),
                                evidence=[
                                    f"凭证 {row.voucher_no} 摘要含关键词: {','.join(hits)}",
                                    f"原文: {summary[:120]}",
                                ],
                            )
                        )

        # 通道 2: 客户/供应商交叉重叠 — 同一名称在客户和供应商主数据都出现 = 高度可疑
        if req.enable_customer_overlap:
            # 从 SalesRecord 抽客户名 + 数量
            customer_stmt = (
                select(SalesRecord.customer_name, func.count(SalesRecord.id))
                .where(SalesRecord.project_id == req.project_id)
                .group_by(SalesRecord.customer_name)
            )
            customer_rows = list((await db.execute(customer_stmt)).all())
            scanned_customers = len(customer_rows)
            customer_names = {
                _normalize_name(r[0]): (r[0], int(r[1])) for r in customer_rows if r[0]
            }

            # 供应商主数据 — 这里项目可能没单独的供应商表, 暂从序时账"供应商相关"科目摘要抽
            # MVP: 跳过供应商扫描, 等 Pack C 应付循环来补
            scanned_suppliers = 0

            # 任何客户名同时出现在两个表的, 标记为候选 (这里 supplier 缺, 暂用关键词二次筛)
            for norm, (orig, cnt) in customer_names.items():
                if norm and norm in existing_norm:
                    continue
                # 命中关联方关键词 (如 "兄弟", "母公司") 的客户名
                if any(k in orig for k in keywords):
                    if norm not in seen_names:
                        seen_names.add(norm)
                        candidates.append(
                            DetectorCandidate(
                                name=norm,
                                party_kind="entity",
                                party_type=RP_TYPE_OTHER,
                                source=RP_SOURCE_CUSTOMER_OVERLAP,
                                confidence=0.6,
                                evidence=[
                                    f"客户名含关联方关键词, 出现 {cnt} 笔销售记录",
                                ],
                            )
                        )

        # 通道 3 (Pack B.2): DeepSeek AI 推断 — 兜底通道
        ai_enabled = False
        notes_extras: List[str] = []
        if getattr(req, "enable_ai_inference", False):
            try:
                from app.core.config import settings
                from app.services.related_parties.ai_inferer import RelatedPartyAIInferer
                from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

                ds_client = DeepSeekClient(
                    api_key=settings.DEEPSEEK_API_KEY,
                    base_url=settings.DEEPSEEK_API_BASE,
                    model=settings.DEEPSEEK_MODEL,
                )
                inferer = RelatedPartyAIInferer(ds_client)
                if not inferer.is_configured:
                    notes_extras.append("AI 推断已请求但 DEEPSEEK_API_KEY 未配置, 跳过.")
                else:
                    ai_result = await inferer.infer(
                        db,
                        req.project_id,
                        max_candidates=getattr(req, "ai_max_candidates", 30),
                        existing_names=existing_norm | seen_names,
                    )
                    ai_enabled = True
                    notes_extras.append(f"AI 推断: {ai_result.scan_summary}")
                    for c in ai_result.candidates:
                        key = _normalize_name(c.name)
                        if not key or key in existing_norm or key in seen_names:
                            continue
                        seen_names.add(key)
                        # AI 候选 name 也走归一化, 与规则候选去重一致
                        c.name = key
                        candidates.append(c)
            except DeepSeekError as exc:
                logger.warning("AI 推断失败: %s", exc)
                notes_extras.append(f"AI 推断失败: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("AI 推断异常 (已吞, 不影响规则识别): %s", exc)
                notes_extras.append(f"AI 推断异常: {exc}")

        base_note = "识别完成。所有候选均需人工 confirm 后才会落库为正式关联方 (RelatedParty.is_confirmed=true)。"
        if not ai_enabled and not getattr(req, "enable_ai_inference", False):
            base_note += (
                " AI 推断未启用 — 设 enable_ai_inference=true + 配置 DEEPSEEK_API_KEY 启用."
            )
        notes = base_note + ("\n" + " | ".join(notes_extras) if notes_extras else "")

        return DetectorRunResponse(
            scanned_vouchers=scanned_vouchers,
            scanned_customers=scanned_customers,
            scanned_suppliers=scanned_suppliers,
            new_candidates=len(candidates),
            candidates=candidates,
            ai_enabled=ai_enabled,
            notes=notes,
        )

    @staticmethod
    async def confirm_candidate(
        db: AsyncSession,
        *,
        project_id: int,
        candidate: DetectorCandidate,
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
    ) -> RelatedParty:
        """把候选人确认为正式关联方."""
        rp = RelatedParty(
            project_id=project_id,
            name=candidate.name,
            party_kind=candidate.party_kind,
            party_type=candidate.party_type,
            source=candidate.source,
            confidence=candidate.confidence,
            is_confirmed=True,
            relation_chain="\n".join(candidate.evidence) if candidate.evidence else None,
            created_by_user_id=user_id,
            created_by_display=user_display,
            created_at=_utcnow_naive(),
            updated_at=_utcnow_naive(),
        )
        db.add(rp)
        await db.commit()
        await db.refresh(rp)
        return rp


# ============================================================
#  Transaction Analyzer
# ============================================================


class TransactionAnalyzer:
    @staticmethod
    async def check_fairness(
        db: AsyncSession,
        req: FairnessCheckRequest,
        *,
        project_id: int,
    ) -> FairnessCheckResponse:
        """简单公允性测试 — 按同类型交易的中位数对比.

        每笔关联交易 (例如销售) 与同期同产品的非关联方交易做加权均价对比,
        偏离度 > ±10% → not_fair; 否则 fair. MVP 用同 transaction_type
        的所有交易的均价作为 baseline.
        """
        conds = [RelatedPartyTransaction.project_id == project_id]
        if req.transaction_ids:
            conds.append(RelatedPartyTransaction.id.in_(req.transaction_ids))
        if req.party_id:
            conds.append(RelatedPartyTransaction.party_id == req.party_id)
        if req.period_end:
            conds.append(RelatedPartyTransaction.period_end == req.period_end)

        rows = list(
            (await db.execute(select(RelatedPartyTransaction).where(and_(*conds)))).scalars().all()
        )
        if not rows:
            return FairnessCheckResponse(notes="没有匹配的关联交易记录")

        # 按 transaction_type 分组算均价
        by_type: Dict[str, List[float]] = {}
        for r in rows:
            by_type.setdefault(r.transaction_type, []).append(float(r.amount or 0))
        type_avg = {t: (sum(amts) / len(amts) if amts else 0) for t, amts in by_type.items()}

        assessed = fair = not_fair = pending = 0
        scores: List[float] = []
        for r in rows:
            baseline = type_avg.get(r.transaction_type, 0)
            if baseline <= 0:
                pending += 1
                continue
            assessed += 1
            deviation = abs(float(r.amount or 0) - baseline) / baseline
            # 偏离 0 = 100 分, 偏离 ±10% = 80, ±50% = 0
            score = max(0.0, 100.0 - deviation * 200)
            r.fairness_score = round(score, 2)
            r.similar_market_price = round(baseline, 2)
            is_fair_now = deviation <= 0.10
            r.is_fair = is_fair_now
            r.fairness_note = f"同类交易均价 {baseline:.2f}, 偏离 {deviation * 100:.2f}%"
            r.updated_at = _utcnow_naive()
            if is_fair_now:
                fair += 1
            else:
                not_fair += 1
            scores.append(score)

        await db.commit()
        return FairnessCheckResponse(
            assessed=assessed,
            fair=fair,
            not_fair=not_fair,
            pending=pending,
            avg_score=round(sum(scores) / len(scores), 2) if scores else 0.0,
            notes=f"baseline = 同 transaction_type 的均价 (按 {len(by_type)} 类分组)",
        )


# ============================================================
#  Capital Occupation
# ============================================================


class CapitalOccupationService:
    @staticmethod
    async def compute_max_occupation(
        db: AsyncSession,
        *,
        project_id: int,
        party_id: int,
        period_start: str,
        period_end: str,
    ) -> Dict[str, Any]:
        """从其他应收/应付科目按月汇总, 计算期间内最大占用余额."""
        # 简化: 从 ChronologicalAccount 抽对方=关联方的所有凭证, 累积余额
        rp = (
            await db.execute(select(RelatedParty).where(RelatedParty.id == party_id))
        ).scalar_one_or_none()
        if rp is None:
            return {"max_amount": 0, "max_date": None, "ending_balance": 0}

        like = f"%{rp.name}%"
        rows = list(
            (
                await db.execute(
                    select(ChronologicalAccount)
                    .where(
                        ChronologicalAccount.project_id == project_id,
                        ChronologicalAccount.voucher_date >= period_start,
                        ChronologicalAccount.voucher_date <= period_end,
                        or_(
                            ChronologicalAccount.summary.ilike(like),
                            ChronologicalAccount.auxiliary_accounting.ilike(like),
                        ),
                    )
                    .order_by(ChronologicalAccount.voucher_date)
                )
            )
            .scalars()
            .all()
        )

        balance = 0.0
        max_amount = 0.0
        max_date: Optional[str] = None
        for r in rows:
            balance += float(r.debit_amount or 0) - float(r.credit_amount or 0)
            if abs(balance) > max_amount:
                max_amount = abs(balance)
                max_date = r.voucher_date

        return {
            "max_amount": round(max_amount, 2),
            "max_date": max_date,
            "ending_balance": round(balance, 2),
            "voucher_count": len(rows),
        }


# ============================================================
#  Peer Competition
# ============================================================


class PeerCompetitionService:
    @staticmethod
    def overlap_score(
        issuer_keywords: List[str],
        peer_business_scope: Optional[str],
    ) -> float:
        """主业关键词重合度评分 — 简单 Jaccard 相似度."""
        if not issuer_keywords or not peer_business_scope:
            return 0.0
        peer_text = peer_business_scope.lower()
        hits = sum(1 for k in issuer_keywords if k.lower() in peer_text)
        return round(100.0 * hits / max(1, len(issuer_keywords)), 2)

    @staticmethod
    def risk_level_for_score(score: float) -> str:
        if score >= 70:
            return "critical"
        if score >= 40:
            return "high"
        if score >= 15:
            return "medium"
        return "low"

    @staticmethod
    async def assess(
        db: AsyncSession,
        *,
        project_id: int,
        party_id: int,
        issuer_keywords: List[str],
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
    ) -> PeerCompetitionAssessment:
        rp = (
            await db.execute(select(RelatedParty).where(RelatedParty.id == party_id))
        ).scalar_one_or_none()
        if rp is None:
            raise ValueError(f"关联方 {party_id} 不存在")

        score = PeerCompetitionService.overlap_score(issuer_keywords, rp.business_scope)
        risk = PeerCompetitionService.risk_level_for_score(score)
        matched = [
            k
            for k in issuer_keywords
            if rp.business_scope and k.lower() in rp.business_scope.lower()
        ]

        # upsert
        existing = (
            await db.execute(
                select(PeerCompetitionAssessment).where(
                    PeerCompetitionAssessment.project_id == project_id,
                    PeerCompetitionAssessment.party_id == party_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = PeerCompetitionAssessment(
                project_id=project_id,
                party_id=party_id,
                created_at=_utcnow_naive(),
            )
            db.add(existing)
        existing.overlap_score = score
        existing.overlap_keywords = ",".join(matched)
        existing.risk_level = risk
        existing.assessed_by_user_id = user_id
        existing.assessed_by_display = user_display
        existing.assessed_at = _utcnow_naive()
        await db.commit()
        await db.refresh(existing)
        return existing


# ============================================================
#  Disclosure Checker
# ============================================================


class DisclosureChecker:
    @staticmethod
    async def diff(
        db: AsyncSession,
        *,
        project_id: int,
        prospectus_party_names: List[str],
    ) -> DisclosureCheckResponse:
        """对比系统 RelatedParty (已 confirmed) vs 招股书披露清单."""
        sys_rows = list(
            (
                await db.execute(
                    select(RelatedParty).where(
                        RelatedParty.project_id == project_id,
                        RelatedParty.is_confirmed == True,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        sys_norm = {_normalize_name(r.name): r for r in sys_rows}
        prospectus_norm = {_normalize_name(n): n for n in prospectus_party_names if n}

        # 清旧 gap 重建 (幂等)
        old = list(
            (
                await db.execute(
                    select(ProspectusDisclosureGap).where(
                        ProspectusDisclosureGap.project_id == project_id,
                        ProspectusDisclosureGap.resolved == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        for o in old:
            await db.delete(o)

        system_only: List[DisclosureGapResponse] = []
        prospectus_only: List[DisclosureGapResponse] = []
        matched = 0

        # 系统有 + 招股书没有 = critical
        for norm, rp in sys_norm.items():
            if norm in prospectus_norm:
                matched += 1
                continue
            # 算这个关联方的交易数和金额
            tx_stmt = select(
                func.count(RelatedPartyTransaction.id),
                func.coalesce(func.sum(RelatedPartyTransaction.amount), 0),
            ).where(RelatedPartyTransaction.party_id == rp.id)
            cnt, amt = (await db.execute(tx_stmt)).one()
            gap = ProspectusDisclosureGap(
                project_id=project_id,
                party_id=rp.id,
                gap_status=DISCLOSURE_GAP_CRITICAL,
                party_name=rp.name,
                in_system=True,
                in_prospectus=False,
                transaction_count=int(cnt or 0),
                total_amount=float(amt or 0),
                suggested_action=(
                    "招股书未披露此关联方, 请补充至 '关联方及关联交易' 章节;"
                    "若交易金额重大需说明定价依据 + 必要性"
                ),
                created_at=_utcnow_naive(),
            )
            db.add(gap)
            await db.flush()
            system_only.append(DisclosureGapResponse.model_validate(gap))

        # 招股书有 + 系统没有 = review (可能误报或系统漏识别)
        for norm, original in prospectus_norm.items():
            if norm in sys_norm:
                continue
            gap = ProspectusDisclosureGap(
                project_id=project_id,
                party_id=None,
                gap_status=DISCLOSURE_GAP_REVIEW,
                party_name=original,
                in_system=False,
                in_prospectus=True,
                transaction_count=0,
                total_amount=0.0,
                suggested_action=(
                    "招股书披露但系统未识别 — 可能系统漏检, 或招股书披露过宽。请人工复核"
                ),
                created_at=_utcnow_naive(),
            )
            db.add(gap)
            await db.flush()
            prospectus_only.append(DisclosureGapResponse.model_validate(gap))

        await db.commit()
        return DisclosureCheckResponse(
            system_only=system_only,
            prospectus_only=prospectus_only,
            matched=matched,
            total_critical=len(system_only),
            total_review=len(prospectus_only),
        )


__all__ = [
    "RelatedPartyDetector",
    "TransactionAnalyzer",
    "CapitalOccupationService",
    "PeerCompetitionService",
    "DisclosureChecker",
    "RelatedPartyAIInferer",
    "AIInferenceResult",
]


# 延迟引入避免循环 (ai_inferer 反过来 import RelatedParty ORM 没问题, 这里只是导出)
from app.services.related_parties.ai_inferer import (  # noqa: E402
    AIInferenceResult,
    RelatedPartyAIInferer,
)
