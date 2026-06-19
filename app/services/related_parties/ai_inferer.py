"""关联方 AI 推断 (Pack B.2 — DeepSeek 增强).

规则识别 (RelatedPartyDetector) 之外的"AI 兜底":
  - 输入: 客户/供应商主数据 + 已知股东 / 实控人 / 董监高 名单
  - DeepSeek 用法律 + 实务知识判断潜在关联关系:
      * 公司名形似 (兄弟公司常用近似名)
      * 注册地址 / 联系电话 / 联系人重复
      * 实控人 / 法人 / 股东重合
      * 主营业务雷同 (同业竞争)
  - 输出: 候选 list, 每个候选带 reasoning + confidence
  - 全部走"候选, 待 confirm"流程, 不直接落 RelatedParty 表

降级路径:
  - DeepSeek 未配置 → 抛 DeepSeekError 让 API 层 503
  - 模型 JSON 解析失败 → 返回空候选 + warning, 不抛
  - 输入候选超过 200 个 → 分批 (每批 50), 防止 prompt 过长

调用方:
  RelatedPartyDetector.run(req with enable_ai_inference=True) → 自动叠加 AI 通道
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.related_parties import (
    RP_SOURCE_AI,
    RP_TYPE_OTHER,
    RelatedParty,
)
from app.models.db_models import (
    ChronologicalAccount,
    Project,
    SalesRecord,
)
from app.models.related_parties import DetectorCandidate
from app.services.sales_ledger.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """你是一位资深的 IPO 审计师, 专精识别"潜在关联方". 中国《企业会计准则第36号——关联方披露》定义:

1) 一方控制、共同控制另一方或对另一方施加重大影响
2) 两方或两方以上同受一方控制、共同控制或重大影响
3) 主要投资者个人、关键管理人员或与其关系密切的家庭成员
4) 上述主要投资者个人或关键管理人员直接控制的企业

你需要从用户提供的"客户/供应商清单 + 已知关联方/股东/董监高列表"中, 找出潜在的、被刻意隐藏的关联方. 重点关注:

  - 公司名与已知关联方 / 股东 / 董监高名字高度相似 (兄弟公司常起近似名)
  - 公司名包含已知股东 / 实控人姓名拼音 / 首字母 / 谐音
  - 同名 / 同字号公司在客户和供应商表都出现 ("两头通吃")
  - 客户名带"咨询""服务""贸易""科技"等空壳常见后缀, 且交易金额异常 (你看不到金额, 需要审计师后续验证)

输出必须是 JSON, 严格遵守 schema:
{
  "candidates": [
    {
      "name": "(去除常见公司后缀的规整名)",
      "raw_names": ["原始全名 1", "原始全名 2 (如果同时是供应商)"],
      "reason": "判断依据 (中文, 1-2 句)",
      "confidence": 0.0-1.0,
      "evidence_type": "name_similar | shareholder_link | both_customer_and_supplier | shell_company_pattern | other"
    }
  ],
  "scan_summary": "本轮扫描总结 (中文, 1 句话)"
}

confidence:
  - 0.9+ 几乎确认 (名字直接撞同名股东 / 兄弟公司名)
  - 0.6-0.8 中等怀疑 (名字相似 + 间接证据)
  - 0.3-0.5 弱怀疑 (仅一项轻微证据), 建议人工二次复核
  - <0.3 不要输出 (噪音)

如果一个都找不到, 返回 "candidates": [] 也合法. 不要编造 evidence_type 之外的值."""


@dataclass
class AIInferenceResult:
    candidates: List[DetectorCandidate]
    scan_summary: str
    raw_payload: dict


class RelatedPartyAIInferer:
    """AI 推断关联方 — 兜底通道, 在规则识别之上叠加."""

    BATCH_SIZE = 50

    def __init__(self, client: DeepSeekClient):
        self.client = client

    @property
    def is_configured(self) -> bool:
        return bool(self.client and self.client.is_configured)

    async def infer(
        self,
        db: AsyncSession,
        project_id: int,
        *,
        max_candidates: int = 30,
        existing_names: Optional[set[str]] = None,
    ) -> AIInferenceResult:
        """对项目下的客户/供应商/已知股东做 AI 推断.

        Args:
            max_candidates: 限制返回的候选数 (防止 token 爆炸)
            existing_names: 已经在 RelatedParty 表里登记过的关联方名 (用于去重)

        Returns:
            AIInferenceResult: 候选列表 + 扫描总结
        """
        if not self.is_configured:
            raise DeepSeekError("DEEPSEEK_API_KEY 未配置, 无法启用 AI 推断")

        # 1) 拉项目基础信息
        proj = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if proj is None:
            return AIInferenceResult(candidates=[], scan_summary="项目不存在", raw_payload={})

        # 2) 拉客户名 (从 SalesRecord 聚合)
        cust_rows = list(
            (
                await db.execute(
                    select(SalesRecord.customer_name, func.count(SalesRecord.id))
                    .where(SalesRecord.project_id == project_id)
                    .group_by(SalesRecord.customer_name)
                )
            ).all()
        )
        customers = [{"name": r[0], "count": int(r[1])} for r in cust_rows if r[0]]

        # 3) 拉供应商 (P0 修复: 用 account_code 准则前缀白名单替代 like('%应付%') 模糊匹配)
        #    老实现用 account_name LIKE '%应付%', 会误伤:
        #      - 应付职工薪酬 (2211)   → 含 "应付" 但不是供应商
        #      - 应付福利费     (2241) → 含 "应付" 但不是供应商
        #      - 预付账款       (1122) → 不含 "应付" 但其实是预付供应商款
        #      - 预提费用       (2241) → 含 "应付" 边缘, 不是供应商
        #    准则规定的"供应商应付/预付"科目编码:
        #      1122 预付账款                 — pre-paid supplier
        #      2202 应付账款                 — supplier payable
        #      2203 预收账款                 — 客户预付, 不算供应商, 不在白名单
        #      2241 其他应付款               — 多为费用/押金, 不算供应商
        #    兜底: account_name 仍带 "职工/薪酬/福利" 关键字 → 直接跳过, 防止少数科目编码异常的项目
        sup_rows = list(
            (
                await db.execute(
                    select(
                        ChronologicalAccount.auxiliary_accounting,
                        func.count(ChronologicalAccount.id),
                    )
                    .where(
                        ChronologicalAccount.project_id == project_id,
                        ChronologicalAccount.auxiliary_accounting.isnot(None),
                        # P0 修复: 4 位/多位科目编码前缀白名单
                        # 1122 预付账款 | 2202 应付账款 | 2201 应付票据 (供应商票据)
                        # 2241 预提费用 已故意从白名单移除 — 不是供应商
                        # 2203 预收账款 已故意从白名单移除 — 是客户预付, 不是供应商
                        (
                            ChronologicalAccount.account_code.like("1122%")
                            | ChronologicalAccount.account_code.like("2201%")
                            | ChronologicalAccount.account_code.like("2202%")
                        ),
                    )
                    .group_by(ChronologicalAccount.auxiliary_accounting)
                )
            ).all()
        )
        suppliers = []
        for r in sup_rows:
            name = r[0]
            if not name:
                continue
            # P0 兜底: 即使 account_code 异常, account_name 关键词二次过滤
            # raw 来自 auxiliary_accounting, 此处不能再取 account_name — 用名字本身体检
            if "职工" in name or "薪酬" in name or "福利" in name:
                continue
            suppliers.append({"name": name, "count": int(r[1])})

        # 4) 已知关联方 (供 LLM 参考)
        existing_rps = list(
            (
                await db.execute(
                    select(RelatedParty.name, RelatedParty.party_type).where(
                        RelatedParty.project_id == project_id
                    )
                )
            ).all()
        )
        known = [{"name": n, "type": t} for n, t in existing_rps if n]

        if not customers and not suppliers and not known:
            return AIInferenceResult(
                candidates=[],
                scan_summary="项目无客户 / 供应商 / 已知关联方数据, 无法 AI 推断.",
                raw_payload={},
            )

        # 5) 分批 (单批最多 50 个客户) — 避免 prompt 过长
        candidates_all: List[DetectorCandidate] = []
        scan_notes: List[str] = []
        existing_lower = {n.lower() for n in (existing_names or set())}
        seen_in_run: set[str] = set()

        # 简化: 把客户和供应商合并, 一次扔给 LLM (单批 BATCH_SIZE)
        merged_parties = [{"role": "customer", **c} for c in customers] + [
            {"role": "supplier", **s} for s in suppliers
        ]
        for start in range(0, len(merged_parties), self.BATCH_SIZE):
            batch = merged_parties[start : start + self.BATCH_SIZE]
            try:
                payload = await self._call_llm(proj, known, batch)
            except DeepSeekError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("AI 推断单批失败 (batch start=%s): %s", start, exc)
                continue

            for cand in (payload.get("candidates") or [])[:max_candidates]:
                name = (cand.get("name") or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key in existing_lower or key in seen_in_run:
                    continue
                seen_in_run.add(key)
                try:
                    conf = float(cand.get("confidence", 0.5))
                except (TypeError, ValueError):
                    conf = 0.5
                if conf < 0.3:
                    continue  # 噪音
                evidence = [
                    f"AI 判断: {cand.get('reason', '')}",
                    f"证据类型: {cand.get('evidence_type', 'other')}",
                ]
                raw_names = cand.get("raw_names") or []
                if raw_names:
                    evidence.append(f"原始命名: {', '.join(str(n) for n in raw_names[:5])}")
                candidates_all.append(
                    DetectorCandidate(
                        name=name,
                        party_kind="entity",
                        party_type=RP_TYPE_OTHER,
                        source=RP_SOURCE_AI,
                        confidence=max(0.3, min(0.95, conf)),
                        evidence=evidence,
                    )
                )
                if len(candidates_all) >= max_candidates:
                    break

            summary_text = (payload.get("scan_summary") or "").strip()
            if summary_text:
                scan_notes.append(summary_text)
            if len(candidates_all) >= max_candidates:
                break

        return AIInferenceResult(
            candidates=candidates_all,
            scan_summary=" / ".join(scan_notes)
            if scan_notes
            else f"AI 扫描 {len(merged_parties)} 个交易对手, 命中 {len(candidates_all)} 个候选.",
            raw_payload={
                "customers": len(customers),
                "suppliers": len(suppliers),
                "known": len(known),
            },
        )

    async def _call_llm(
        self,
        proj: Project,
        known: List[dict],
        batch_parties: List[dict],
    ) -> dict:
        """单次 LLM 调用. 失败抛, 调用方决定怎么吞."""
        user_msg = json.dumps(
            {
                "company_name": getattr(proj, "company_name", "(未知)"),
                "industry": getattr(proj, "industry", "(未知)"),
                "fiscal_year": getattr(proj, "fiscal_year", None),
                "known_related_parties": known,
                "parties_to_screen": batch_parties,
            },
            ensure_ascii=False,
            default=str,
        )
        return await self.client.chat_json(
            system=_SYSTEM_PROMPT,
            user=user_msg,
            temperature=0.1,
            max_tokens=3500,
        )


__all__ = [
    "AIInferenceResult",
    "RelatedPartyAIInferer",
]
