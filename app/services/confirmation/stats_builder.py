"""Confirmation statistics builder — 从账套自动生成函证统计表。

输入: 项目 ID + 报告期截止日
输出: 一组 ConfirmationItem（按 party_type 分类）写入数据库

数据源:
  - AccountBalance        (科目余额表)
  - ChronologicalAccount  (序时账 - 用于按对方科目聚合本期发生额、票据背书)
  - BankStatement         (银行对账单 - 用于识别银行机构与账号)

函证对象识别规则 (默认):
  - 银行存款 / 其他货币资金: 按「银行账号」聚合
  - 应收账款: 按「客户名称/编号」聚合
  - 应付账款 / 预付: 按「供应商名称/编号」聚合
  - 其他应收 / 其他应付: 按「辅助核算」
  - 借款: 按「贷款机构 + 合同号」聚合
  - 长期股权投资: 按「被投资单位」聚合

涉及科目清单与默认函证项见 ``app.models.confirmation.CONFIRMATION_SUBJECTS``。
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.confirmation import (
    CONFIRMATION_SUBJECTS,
    GenerateStatsRequest,
    SubjectSelection,
)
from app.models.db_models import (
    AccountBalance,
    BankStatement,
    ChronologicalAccount,
    ConfirmationCase,
    ConfirmationItem,
    ConfirmationLetter,
    ConfirmationResponse,
    ConfirmationResponsePhoto,
    ITEM_STATUS_DRAFT,
    PARTY_TYPE_BANK,
    PARTY_TYPE_CUSTOMER,
    PARTY_TYPE_INVESTMENT,
    PARTY_TYPE_LOAN,
    PARTY_TYPE_OTHER_PAYABLE,
    PARTY_TYPE_OTHER_RECEIVABLE,
    PARTY_TYPE_SUPPLIER,
)

logger = logging.getLogger(__name__)


# 科目关键字识别
RECEIVABLE_ACCOUNTS = {"1122", "1123", "1221"}      # 应收账款/预付账款/其他应收款
PAYABLE_ACCOUNTS = {"2202", "2203", "2241"}          # 应付账款/预收账款(或合同负债)/其他应付款
# 注意: 2203 预收账款是「客户往来」,由 _select_payables 单独按 party_type=customer 处理,
#       不应与 1122/1221 应收类放在一起 — 修复重复发函 bug.
BANK_ACCOUNTS = {"1002", "1012", "1101"}            # 银行存款/其他货币资金/理财
LOAN_ACCOUNTS = {"2001", "2501", "2101"}            # 短期借款/长期借款/应付债券
INVESTMENT_ACCOUNTS = {"1511", "1512", "1531"}      # 长投/投资性房地产/长期应收款（投资类）


class ConfirmationStatsBuilder:
    """函证统计表生成器。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---- 主流程 --------------------------------------------------------

    async def generate(self, req: GenerateStatsRequest) -> dict[str, Any]:
        """从账套自动生成函证对象清单。

        Returns:
            dict with 选样统计 + 待写库的 ConfirmationItem 列表
        """
        case = await self._get_case(req.case_id)
        if case.is_locked:
            raise ValueError(
                f"案卷已锁定 (locked_at={case.locked_at})，不可重新生成。"
                "请新建一个案卷。"
            )

        # 1) 拉取账套数据
        balances = await self._fetch_balances(case.project_id, req.period_end)
        journals = await self._fetch_journals(case.project_id, req.period_end)
        bank_stmts = await self._fetch_bank_statements(case.project_id, req.period_end)

        # 2) 按对方/银行账号聚合
        bank_groups = self._aggregate_banks(balances, bank_stmts)
        receivable_groups = self._aggregate_receivables(balances, journals)
        payable_groups = self._aggregate_payables(balances, journals)
        loan_groups = self._aggregate_loans(balances, journals)
        investment_groups = self._aggregate_investments(balances, journals)

        # 3) 应用选样规则 → ConfirmationItem (in-memory)
        items: list[SubjectSelection] = []
        items.extend(self._select_banks(bank_groups, req))
        items.extend(self._select_receivables(receivable_groups, req, PARTY_TYPE_CUSTOMER))
        items.extend(self._select_receivables(receivable_groups, req, PARTY_TYPE_OTHER_RECEIVABLE, account_codes={"1221"}))
        items.extend(self._select_payables(payable_groups, req, PARTY_TYPE_SUPPLIER))
        items.extend(self._select_payables(payable_groups, req, PARTY_TYPE_OTHER_PAYABLE, account_codes={"2241"}))
        items.extend(self._select_payables(payable_groups, req, PARTY_TYPE_CUSTOMER, account_codes={"2203"}))  # 合同负债作客户函证
        items.extend(self._select_loans(loan_groups, req))
        items.extend(self._select_investments(investment_groups, req))

        # 4) 用户手工调整覆盖（如果提供）
        if req.selected_items:
            # 用用户的覆盖自动选样（通常在前端预览-调整-确认的流程中用到）
            items = list(req.selected_items)

        # 5) 落库 (P0 修复: clear + insert 在单事务, 失败整体回滚)
        if req.persist:
            try:
                await self._clear_existing_items(case.id)
                await self.db.flush()  # 强制 DELETE 落库, 获取依赖关系
                for sel in items:
                    obj = ConfirmationItem(
                        case_id=case.id,
                        party_type=sel.party_type,
                        party_name=sel.party_name,
                        party_id=sel.party_id,
                        contact_person=sel.contact_person,
                        contact_info=sel.contact_info,
                        account_code=sel.account_code,
                        account_name=sel.account_name,
                        book_balance=sel.book_balance,
                        book_balance_date=sel.book_balance_date,
                        subject_matters=json.dumps(sel.subject_matters, ensure_ascii=False),
                        total_confirm_amount=sel.book_balance,
                        selection_method="auto" if not req.selected_items else "manual",
                        selection_reason=sel.selection_reason,
                        importance=sel.importance,
                        status=ITEM_STATUS_DRAFT,
                    )
                    self.db.add(obj)
                await self.db.flush()
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

        # 6) 汇总
        by_type: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "amount": 0.0})
        for sel in items:
            d = by_type[sel.party_type]
            d["count"] += 1
            d["amount"] += sel.book_balance

        return {
            "case_id": case.id,
            "selected_count": len(items),
            "total_amount": round(sum(s.book_balance for s in items), 2),
            "by_party_type": {k: {"count": v["count"], "amount": round(v["amount"], 2)} for k, v in by_type.items()},
            "items": items,
        }

    # ---- 数据拉取 ------------------------------------------------------

    async def _get_case(self, case_id: int) -> ConfirmationCase:
        res = await self.db.execute(
            select(ConfirmationCase).where(ConfirmationCase.id == case_id)
        )
        case = res.scalar_one_or_none()
        if not case:
            raise ValueError(f"案卷不存在: {case_id}")
        return case

    async def _fetch_balances(self, project_id: int, period_end: Optional[Any]) -> list[AccountBalance]:
        q = select(AccountBalance).where(AccountBalance.project_id == project_id)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def _fetch_journals(self, project_id: int, period_end: Optional[Any]) -> list[ChronologicalAccount]:
        q = select(ChronologicalAccount).where(ChronologicalAccount.project_id == project_id)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def _fetch_bank_statements(self, project_id: int, period_end: Optional[Any]) -> list[BankStatement]:
        q = select(BankStatement).where(BankStatement.project_id == project_id)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def _clear_existing_items(self, case_id: int) -> None:
        """清理案卷下所有 items + 级联清理 letters/responses/photos。

        P0 修复:
          1) 顺序: letters -> responses -> photos -> items (避免外键悬空)
          2) 单事务 (由调用方 commit 控制)
          3) 已被函证过的项目 (有 response) 不允许重生成, 强制新建案卷
        """
        from sqlalchemy import delete

        # 检查: 若任何 item 已有 response, 则不允许重生成 (避免审计痕迹丢失)
        has_response = (await self.db.execute(
            select(func.count(ConfirmationResponse.id))
            .join(ConfirmationLetter, ConfirmationResponse.letter_id == ConfirmationLetter.id)
            .where(ConfirmationLetter.case_id == case_id)
        )).scalar() or 0
        if has_response:
            raise ValueError(
                f"案卷下已有 {has_response} 条回函, 不能重新生成统计表。"
                "请新建案卷以保持审计轨迹完整。"
            )

        # 顺序: responses -> photos -> letters -> items
        # 1) 找到所有 letter ids
        letter_ids = (await self.db.execute(
            select(ConfirmationLetter.id).where(ConfirmationLetter.case_id == case_id)
        )).scalars().all()
        if letter_ids:
            # 2) 删 responses (会级联删 photos via cascade="all, delete-orphan")
            response_ids = (await self.db.execute(
                select(ConfirmationResponse.id).where(ConfirmationResponse.letter_id.in_(letter_ids))
            )).scalars().all()
            if response_ids:
                await self.db.execute(
                    delete(ConfirmationResponsePhoto).where(
                        ConfirmationResponsePhoto.response_id.in_(response_ids)
                    )
                )
                await self.db.execute(
                    delete(ConfirmationResponse).where(ConfirmationResponse.id.in_(response_ids))
                )
            # 3) 删 letters
            await self.db.execute(
                delete(ConfirmationLetter).where(ConfirmationLetter.id.in_(letter_ids))
            )
        # 4) 删 items
        await self.db.execute(
            delete(ConfirmationItem).where(ConfirmationItem.case_id == case_id)
        )

    # ---- 聚合 ---------------------------------------------------------

    @staticmethod
    def _normalize_party_name(name: str) -> str:
        return re.sub(r"\s+", "", (name or "").strip())

    def _aggregate_banks(
        self,
        balances: list[AccountBalance],
        bank_stmts: list[BankStatement],
    ) -> list[SubjectSelection]:
        """聚合: 银行账号 / 银行名称

        P0 修复: book_balance 改为 += 累加, 不用 max.
        同一银行多账户 (1002 活期 + 1012 保证金) 累加.
        """
        by_account: dict[str, dict[str, Any]] = {}

        # 从银行对账单识别
        for stmt in bank_stmts:
            key = (stmt.bank_account or "unknown").strip()
            if not key or key == "unknown":
                continue
            if key not in by_account:
                by_account[key] = {
                    "party_name": "",
                    "party_id": key,
                    "book_balance": 0.0,
                    "account_codes": set(),
                    "account_names": set(),
                }
            entry = by_account[key]
            # 取第一笔有意义的描述做对方名称
            desc = (stmt.description or "").strip()
            if desc and not entry["party_name"]:
                entry["party_name"] = desc[:200]
            # P0 修复: 用末笔余额(更近报告期), 不累加对账单
            stmt_balance = stmt.balance or 0.0
            # 仅在更新到更大的日期时覆盖 (对账单可能乱序)
            stmt_date = getattr(stmt, "statement_date", "") or ""
            cur_date = entry.get("_last_date", "")
            if stmt_date >= cur_date:
                entry["book_balance"] = stmt_balance
                entry["_last_date"] = stmt_date
            entry["account_codes"].add("1002")

        # 从科目余额表（如果没银行对账单，按科目找对方）
        for b in balances:
            if b.account_code not in BANK_ACCOUNTS:
                continue
            aux = (b.auxiliary_accounting or "").strip()
            if not aux:
                continue
            if aux not in by_account:
                by_account[aux] = {
                    "party_name": aux,
                    "party_id": aux,
                    "book_balance": 0.0,
                    "account_codes": set(),
                    "account_names": set(),
                }
            entry = by_account[aux]
            # P0 修复: 累加同一银行多账户, 不用 max
            entry["book_balance"] += abs(b.ending_balance or 0)
            entry["account_codes"].add(b.account_code)
            entry["account_names"].add(b.account_name)

        # 默认 subjects
        bank_subjects = self._get_default_subjects("1002")
        out: list[SubjectSelection] = []
        for key, d in by_account.items():
            out.append(SubjectSelection(
                account_code=", ".join(sorted(d["account_codes"])) or "1002",
                account_name=", ".join(sorted(d["account_names"])) or "银行存款",
                party_type=PARTY_TYPE_BANK,
                party_name=d["party_name"] or key,
                party_id=d["party_id"],
                book_balance=round(d["book_balance"], 2),
                book_balance_date=None,
                subject_matters=bank_subjects,
                importance="A",
                selection_reason="银行询证函 - 必发",
                contact_info=None,
                account_codes=sorted(d["account_codes"]),
            ))
        return out

    def _aggregate_receivables(
        self,
        balances: list[AccountBalance],
        journals: list[ChronologicalAccount],
    ) -> dict[str, dict[str, Any]]:
        """聚合: 应收类（1122/1123/1221）按对方名称

        P0 修复: 移除 2203 (预收账款), 避免与 _aggregate_payables 重复发函.
        """
        return self._aggregate_by_aux(balances, journals, RECEIVABLE_ACCOUNTS)

    def _aggregate_payables(
        self,
        balances: list[AccountBalance],
        journals: list[ChronologicalAccount],
    ) -> dict[str, dict[str, Any]]:
        return self._aggregate_by_aux(balances, journals, PAYABLE_ACCOUNTS)

    def _aggregate_by_aux(
        self,
        balances: list[AccountBalance],
        journals: list[ChronologicalAccount],
        account_codes: set[str],
    ) -> dict[str, dict[str, Any]]:
        """按辅助核算聚合对方 + 本期发生额 + 期末余额。

        P0 修复:
          - AccountBalance 没有 auxiliary_accounting 字段, 只在 journals 中找对方
          - AccountBalance 提供按 account_code 级别的期末余额作为参考
        """
        by_party: dict[str, dict[str, Any]] = {}

        # 1) 从 AccountBalance 拿 account_code -> 期末余额 的映射 (供后续 sum 兜底)
        balance_by_code: dict[str, float] = {}
        for b in balances:
            if b.account_code in account_codes:
                balance_by_code[b.account_code] = (
                    balance_by_code.get(b.account_code, 0.0) + (b.ending_balance or 0)
                )

        # 2) 从序时账按对方聚合
        for j in journals:
            if j.account_code not in account_codes:
                continue
            aux = (j.auxiliary_accounting or "").strip()
            if not aux:
                continue
            key = self._normalize_party_name(aux)
            if key not in by_party:
                by_party[key] = {
                    "party_name": aux,
                    "party_id": aux,
                    "ending_balance": 0.0,
                    "debit": 0.0,
                    "credit": 0.0,
                    "account_codes": set(),
                    "account_names": set(),
                }
            entry = by_party[key]
            entry["debit"] += j.debit_amount or 0
            entry["credit"] += j.credit_amount or 0
            entry["account_codes"].add(j.account_code)
            entry["account_names"].add(j.account_name)
            # ending_balance 用对方级别的余额 (P0: 简化 - 真实场景应从科目余额表的对方辅助核算取)
            # 这里用本期发生额近似 (贷方 - 借方) for 应收/应付
            if j.account_code in ("1122", "1123", "1221", "2202", "2203", "2241"):
                # 应收类余额 = 借方 - 贷方, 应付类余额 = 贷方 - 借方
                if j.account_code in ("1122", "1123", "1221"):
                    entry["ending_balance"] = entry["debit"] - entry["credit"]
                else:
                    entry["ending_balance"] = entry["credit"] - entry["debit"]

        return by_party

    def _aggregate_loans(
        self,
        balances: list[AccountBalance],
        journals: list[ChronologicalAccount],
    ) -> dict[str, dict[str, Any]]:
        by_party: dict[str, dict[str, Any]] = {}
        for b in balances:
            if b.account_code not in LOAN_ACCOUNTS:
                continue
            aux = (b.auxiliary_accounting or "").strip() or b.account_name
            key = self._normalize_party_name(aux)
            if key not in by_party:
                by_party[key] = {
                    "party_name": aux,
                    "party_id": aux,
                    "ending_balance": 0.0,
                    "debit": 0.0,
                    "credit": 0.0,
                    "account_codes": set(),
                    "account_names": set(),
                }
            entry = by_party[key]
            entry["ending_balance"] += b.ending_balance or 0
            entry["account_codes"].add(b.account_code)
            entry["account_names"].add(b.account_name)
        return by_party

    def _aggregate_investments(
        self,
        balances: list[AccountBalance],
        journals: list[ChronologicalAccount],
    ) -> dict[str, dict[str, Any]]:
        by_party: dict[str, dict[str, Any]] = {}
        for b in balances:
            if b.account_code not in INVESTMENT_ACCOUNTS:
                continue
            aux = (b.auxiliary_accounting or "").strip() or b.account_name
            key = self._normalize_party_name(aux)
            if key not in by_party:
                by_party[key] = {
                    "party_name": aux,
                    "party_id": aux,
                    "ending_balance": 0.0,
                    "account_codes": set(),
                    "account_names": set(),
                }
            entry = by_party[key]
            entry["ending_balance"] += b.ending_balance or 0
            entry["account_codes"].add(b.account_code)
            entry["account_names"].add(b.account_name)
        return by_party

    # ---- 选样 ---------------------------------------------------------

    def _select_banks(
        self,
        groups: list[SubjectSelection],
        req: GenerateStatsRequest,
    ) -> list[SubjectSelection]:
        # 银行: 全部发
        return list(groups)

    def _select_receivables(
        self,
        groups: dict[str, dict[str, Any]],
        req: GenerateStatsRequest,
        party_type: str,
        account_codes: Optional[set[str]] = None,
    ) -> list[SubjectSelection]:
        # P0 修复: threshold 按 party_type 显式映射, 不再笼统用 customer_threshold
        if party_type == PARTY_TYPE_CUSTOMER:
            threshold = req.customer_threshold
            default_subjects = self._get_default_subjects("1122")
        elif party_type == PARTY_TYPE_OTHER_RECEIVABLE:
            threshold = req.other_threshold
            default_subjects = self._get_default_subjects("1221")
        else:
            # 2203 (合同负债) 走客户函证, 用 customer_threshold
            threshold = req.customer_threshold
            default_subjects = self._get_default_subjects("2203")

        out: list[SubjectSelection] = []
        rng = random.Random(req.random_seed)
        keys = list(groups.keys())
        for k in keys:
            d = groups[k]
            if account_codes and not (d["account_codes"] & account_codes):
                continue
            bal = d["ending_balance"]
            if abs(bal) < 1e-6 and not req.include_zero_balance:
                continue
            importance = "A" if abs(bal) >= threshold * 5 else ("B" if abs(bal) >= threshold else "C")
            reason = (
                f"金额 {bal:,.2f} ≥ 必发阈值 {threshold:,.0f}"
                if abs(bal) >= threshold
                else f"金额 {bal:,.2f} 抽样补充"
            )
            out.append(SubjectSelection(
                account_code=", ".join(sorted(d["account_codes"])) or "1122",
                account_name=", ".join(sorted(d["account_names"])) or "应收账款",
                party_type=party_type,
                party_name=d["party_name"],
                party_id=d["party_id"],
                book_balance=round(bal, 2),
                book_balance_date=None,
                subject_matters=default_subjects,
                importance=importance,
                selection_reason=reason,
                contact_info=None,
                account_codes=sorted(d["account_codes"]),
            ))

        # 阈值以下随机补充
        below = [g for g in groups.values() if abs(g["ending_balance"]) < threshold]
        if below and req.additional_sample_ratio > 0:
            n = max(1, int(len(below) * req.additional_sample_ratio))
            sampled = rng.sample(below, min(n, len(below)))
            existing_keys = {self._normalize_party_name(o.party_name) for o in out}
            for d in sampled:
                if self._normalize_party_name(d["party_name"]) in existing_keys:
                    continue
                if account_codes and not (d["account_codes"] & account_codes):
                    continue
                bal = d["ending_balance"]
                out.append(SubjectSelection(
                    account_code=", ".join(sorted(d["account_codes"])) or "1122",
                    account_name=", ".join(sorted(d["account_names"])) or "应收账款",
                    party_type=party_type,
                    party_name=d["party_name"],
                    party_id=d["party_id"],
                    book_balance=round(bal, 2),
                    book_balance_date=None,
                    subject_matters=default_subjects,
                    importance="C",
                    selection_reason=f"金额 {bal:,.2f} 阈值以下随机抽样",
                    contact_info=None,
                    account_codes=sorted(d["account_codes"]),
                ))
        return out

    def _select_payables(
        self,
        groups: dict[str, dict[str, Any]],
        req: GenerateStatsRequest,
        party_type: str,
        account_codes: Optional[set[str]] = None,
    ) -> list[SubjectSelection]:
        # P0 修复: threshold 按 party_type 显式映射
        if party_type == PARTY_TYPE_SUPPLIER:
            threshold = req.supplier_threshold
            default_subjects = self._get_default_subjects("2202")
        elif party_type == PARTY_TYPE_OTHER_PAYABLE:
            threshold = req.other_threshold
            default_subjects = self._get_default_subjects("2241")
        elif party_type == PARTY_TYPE_CUSTOMER:
            # 2203 (合同负债) 由 _select_payables 用 customer 处理
            threshold = req.customer_threshold
            default_subjects = self._get_default_subjects("2203")
        else:
            raise ValueError(f"_select_payables 不支持 party_type={party_type}")

        out: list[SubjectSelection] = []
        for k, d in groups.items():
            if account_codes and not (d["account_codes"] & account_codes):
                continue
            bal = d["ending_balance"]
            if abs(bal) < 1e-6 and not req.include_zero_balance:
                continue
            importance = "A" if abs(bal) >= threshold * 5 else ("B" if abs(bal) >= threshold else "C")
            reason = (
                f"金额 {bal:,.2f} ≥ 必发阈值 {threshold:,.0f}"
                if abs(bal) >= threshold
                else f"金额 {bal:,.2f} 抽样补充"
            )
            out.append(SubjectSelection(
                account_code=", ".join(sorted(d["account_codes"])) or "2202",
                account_name=", ".join(sorted(d["account_names"])) or "应付账款",
                party_type=party_type,
                party_name=d["party_name"],
                party_id=d["party_id"],
                book_balance=round(bal, 2),
                book_balance_date=None,
                subject_matters=default_subjects,
                importance=importance,
                selection_reason=reason,
                contact_info=None,
                account_codes=sorted(d["account_codes"]),
            ))
        return out

    def _select_loans(
        self,
        groups: dict[str, dict[str, Any]],
        req: GenerateStatsRequest,
    ) -> list[SubjectSelection]:
        loan_subjects = self._get_default_subjects("1002-loan")
        out: list[SubjectSelection] = []
        for d in groups.values():
            bal = d["ending_balance"]
            if abs(bal) < 1e-6 and not req.include_zero_balance:
                continue
            out.append(SubjectSelection(
                account_code=", ".join(sorted(d["account_codes"])) or "2001",
                account_name=", ".join(sorted(d["account_names"])) or "短期借款",
                party_type=PARTY_TYPE_LOAN,
                party_name=d["party_name"],
                party_id=d["party_id"],
                book_balance=round(bal, 2),
                book_balance_date=None,
                subject_matters=loan_subjects,
                importance="A",
                selection_reason=f"贷款余额 {bal:,.2f}，必发",
                contact_info=None,
                account_codes=sorted(d["account_codes"]),
            ))
        return out

    def _select_investments(
        self,
        groups: dict[str, dict[str, Any]],
        req: GenerateStatsRequest,
    ) -> list[SubjectSelection]:
        inv_subjects = self._get_default_subjects("1511")
        out: list[SubjectSelection] = []
        for d in groups.values():
            bal = d["ending_balance"]
            if abs(bal) < 1e-6 and not req.include_zero_balance:
                continue
            out.append(SubjectSelection(
                account_code=", ".join(sorted(d["account_codes"])) or "1511",
                account_name=", ".join(sorted(d["account_names"])) or "长期股权投资",
                party_type=PARTY_TYPE_INVESTMENT,
                party_name=d["party_name"],
                party_id=d["party_id"],
                book_balance=round(bal, 2),
                book_balance_date=None,
                subject_matters=inv_subjects,
                importance="A",
                selection_reason=f"投资余额 {bal:,.2f}，必发",
                contact_info=None,
                account_codes=sorted(d["account_codes"]),
            ))
        return out

    @staticmethod
    def _get_default_subjects(code: str) -> list[str]:
        for s in CONFIRMATION_SUBJECTS:
            if s["code"] == code:
                return list(s.get("default_subjects", []))
        return []
