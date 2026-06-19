"""长期资产发生额审定服务 (Pack A — 用户特别要求).

提供:
  - 判断科目是否长期资产 (考虑项目级覆盖)
  - 从序时账初始化审定记录 (account_movement_audits)
  - 单笔审定 / 批量审定 / 争议标记
  - 单科目汇总 (用于底稿恒等式校验)
  - 项目级总览 (跨科目)
  - Excel 导出审定明细
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple  # noqa: F401

from sqlalchemy import and_, asc, delete, desc, func, or_, select  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.account_audit import (
    AccountAuditOverview,
    AccountAuditSummary,
    MovementAuditBulkItem,
)
from app.models.db.account_audit import (
    DEFAULT_LONG_TERM_ASSET_PREFIXES,
    MOVEMENT_AUDIT_STATUS_AUDITED,
    MOVEMENT_AUDIT_STATUS_DISPUTED,
    MOVEMENT_AUDIT_STATUS_PENDING,
    MOVEMENT_AUDIT_STATUS_SKIPPED,
    MOVEMENT_DIRECTION_CREDIT,
    MOVEMENT_DIRECTION_DEBIT,
    AccountMovementAudit,
    LongTermAssetScopeOverride,
)
from app.models.db_models import AccountBalance, ChronologicalAccount, Project  # noqa: F401

logger = logging.getLogger(__name__)

_EPS = 0.01  # 1 分钱视为相等


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_csv_prefixes(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    return parts


def _global_extra_includes() -> List[str]:
    return _parse_csv_prefixes(settings.LONG_TERM_ASSET_EXTRA_INCLUDES)


def _global_extra_excludes() -> List[str]:
    return _parse_csv_prefixes(settings.LONG_TERM_ASSET_EXTRA_EXCLUDES)


async def get_effective_prefixes(
    db: AsyncSession,
    project_id: Optional[int],
) -> List[str]:
    """计算项目实际生效的长期资产科目前缀集合."""
    base = set(DEFAULT_LONG_TERM_ASSET_PREFIXES) | set(_global_extra_includes())
    base -= set(_global_extra_excludes())

    if project_id is None:
        return sorted(base)

    rows = list(
        (
            await db.execute(
                select(LongTermAssetScopeOverride).where(
                    LongTermAssetScopeOverride.project_id == project_id
                )
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        if r.action == "include":
            base.add(r.account_prefix)
        elif r.action == "exclude":
            base.discard(r.account_prefix)
    return sorted(base)


def is_long_term_asset_account(account_code: str, prefixes: Sequence[str]) -> bool:
    """快速判定 — 是否以任一前缀开头. ``prefixes`` 调用方应预先从 ``get_effective_prefixes`` 取一次."""
    if not account_code or not prefixes:
        return False
    code = account_code.strip()
    for p in prefixes:
        if code.startswith(p):
            return True
    return False


class AccountAuditService:
    """长期资产发生额审定服务."""

    # === 范围覆盖 ===
    @staticmethod
    async def list_scope_overrides(
        db: AsyncSession, project_id: int
    ) -> List[LongTermAssetScopeOverride]:
        return list(
            (
                await db.execute(
                    select(LongTermAssetScopeOverride)
                    .where(LongTermAssetScopeOverride.project_id == project_id)
                    .order_by(asc(LongTermAssetScopeOverride.account_prefix))
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def add_scope_override(
        db: AsyncSession,
        *,
        project_id: int,
        account_prefix: str,
        action: str,
        reason: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> LongTermAssetScopeOverride:
        if action not in {"include", "exclude"}:
            raise ValueError("action 必须为 include / exclude")
        # upsert by (project_id, account_prefix)
        existing = (
            await db.execute(
                select(LongTermAssetScopeOverride).where(
                    LongTermAssetScopeOverride.project_id == project_id,
                    LongTermAssetScopeOverride.account_prefix == account_prefix,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.action = action
            existing.reason = reason
            existing.created_by_user_id = created_by_user_id
            await db.commit()
            await db.refresh(existing)
            return existing
        ov = LongTermAssetScopeOverride(
            project_id=project_id,
            account_prefix=account_prefix,
            action=action,
            reason=reason,
            created_by_user_id=created_by_user_id,
            created_at=_utcnow_naive(),
        )
        db.add(ov)
        await db.commit()
        await db.refresh(ov)
        return ov

    @staticmethod
    async def remove_scope_override(db: AsyncSession, *, project_id: int, override_id: int) -> bool:
        ov = (
            await db.execute(
                select(LongTermAssetScopeOverride).where(
                    LongTermAssetScopeOverride.id == override_id,
                    LongTermAssetScopeOverride.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if ov is None:
            return False
        await db.delete(ov)
        await db.commit()
        return True

    # === 初始化 (从序时账抽长期资产发生额) ===
    @staticmethod
    async def initialize_from_chronological(
        db: AsyncSession,
        *,
        project_id: int,
        period_end: str,
        prefixes: Optional[List[str]] = None,
        replace_pending: bool = True,
    ) -> Dict[str, int]:
        """读 ``ChronologicalAccount`` 把所有命中长期资产前缀的凭证行写入
        ``account_movement_audits``, audited_amount = book_amount, status=pending.

        - ``replace_pending=True`` 时, 同 period_end 下的 ``status=pending`` 行先删后建
          (不动已审定 / 争议 / 跳过的行, 防止覆盖审计师工作)
        - 凭证行的复合唯一键: (project_id, account_code, voucher_no, voucher_line_no, direction)
          重复时 skip (因为唯一约束会拒)
        """
        if prefixes is None:
            prefixes = await get_effective_prefixes(db, project_id)
        if not prefixes:
            return {"scanned": 0, "inserted": 0, "skipped": 0, "deleted_pending": 0}

        # 拉所有序时账行 (只筛长期资产前缀)
        or_clauses = [ChronologicalAccount.account_code.like(p + "%") for p in prefixes]
        stmt = select(ChronologicalAccount).where(
            ChronologicalAccount.project_id == project_id,
            or_(*or_clauses) if len(or_clauses) > 1 else or_clauses[0],
        )
        rows = list((await db.execute(stmt)).scalars().all())

        deleted_pending = 0
        if replace_pending:
            del_stmt = delete(AccountMovementAudit).where(
                AccountMovementAudit.project_id == project_id,
                AccountMovementAudit.period_end == period_end,
                AccountMovementAudit.status == MOVEMENT_AUDIT_STATUS_PENDING,
            )
            res = await db.execute(del_stmt)
            deleted_pending = int(res.rowcount or 0)

        # 查询已有键, 避免唯一约束冲突
        existing_keys = set()
        if rows:
            ex_stmt = select(
                AccountMovementAudit.account_code,
                AccountMovementAudit.voucher_no,
                AccountMovementAudit.voucher_line_no,
                AccountMovementAudit.direction,
            ).where(
                AccountMovementAudit.project_id == project_id,
                AccountMovementAudit.period_end == period_end,
            )
            for r in (await db.execute(ex_stmt)).all():
                existing_keys.add(tuple(r))

        inserted = 0
        skipped = 0
        for r in rows:
            # 默认每行只有一个方向(借或贷)非零; 都非零时分两条记录
            for direction, amount in (
                (MOVEMENT_DIRECTION_DEBIT, float(r.debit_amount or 0)),
                (MOVEMENT_DIRECTION_CREDIT, float(r.credit_amount or 0)),
            ):
                if not amount or not math.isfinite(amount):
                    continue
                # 同凭证多行同方向时用 line_no 区分
                base_line = 1
                # 用 (voucher_no, account_code, direction) 在已有键里看占用情况
                while True:
                    key = (r.account_code, r.voucher_no, base_line, direction)
                    if key not in existing_keys:
                        break
                    base_line += 1
                existing_keys.add(key)

                db.add(
                    AccountMovementAudit(
                        project_id=project_id,
                        account_code=r.account_code,
                        account_name=r.account_name,
                        period_end=period_end,
                        voucher_date=r.voucher_date,
                        voucher_no=r.voucher_no,
                        voucher_line_no=base_line,
                        direction=direction,
                        summary=r.summary,
                        counter_account=None,  # 序时账模型无对方科目字段
                        auxiliary_accounting=r.auxiliary_accounting,
                        book_amount=amount,
                        audited_amount=amount,
                        adjustment_amount=0.0,
                        status=MOVEMENT_AUDIT_STATUS_PENDING,
                        created_at=_utcnow_naive(),
                        updated_at=_utcnow_naive(),
                    )
                )
                inserted += 1

        try:
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("initialize_from_chronological 提交失败: %s", exc)
            raise

        return {
            "scanned": len(rows),
            "inserted": inserted,
            "skipped": skipped,
            "deleted_pending": deleted_pending,
        }

    # === 单笔审定 ===
    @staticmethod
    async def audit_row(
        db: AsyncSession,
        *,
        movement_id: int,
        audited_amount: float,
        adjustment_reason: Optional[str] = None,
        working_paper_ref: Optional[str] = None,
        note: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
    ) -> AccountMovementAudit:
        row = (
            await db.execute(
                select(AccountMovementAudit).where(AccountMovementAudit.id == movement_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"movement_id={movement_id} 不存在")
        if not math.isfinite(audited_amount):
            raise ValueError("audited_amount 必须是有限数")
        row.audited_amount = float(audited_amount)
        row.adjustment_amount = float(audited_amount) - float(row.book_amount or 0)
        row.adjustment_reason = adjustment_reason
        row.working_paper_ref = working_paper_ref
        row.note = note
        row.status = status or MOVEMENT_AUDIT_STATUS_AUDITED
        row.audited_by_user_id = user_id
        row.audited_by_display = user_display
        row.audited_at = _utcnow_naive()
        row.updated_at = _utcnow_naive()
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def dispute_row(
        db: AsyncSession,
        *,
        movement_id: int,
        reason: str,
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
    ) -> AccountMovementAudit:
        row = (
            await db.execute(
                select(AccountMovementAudit).where(AccountMovementAudit.id == movement_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"movement_id={movement_id} 不存在")
        row.status = MOVEMENT_AUDIT_STATUS_DISPUTED
        row.note = (
            row.note or ""
        ) + f"\n[争议 {_utcnow_naive().isoformat()} by {user_display or user_id}] {reason}"
        row.audited_by_user_id = user_id
        row.audited_by_display = user_display
        row.audited_at = _utcnow_naive()
        row.updated_at = _utcnow_naive()
        await db.commit()
        await db.refresh(row)
        return row

    # === 批量审定 ===
    @staticmethod
    async def bulk_audit(
        db: AsyncSession,
        *,
        project_id: int,
        period_end: str,
        items: List[MovementAuditBulkItem],
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
    ) -> Dict[str, Any]:
        # round 28 P0-6: partial commit 模式 — 失败行不影响已成功的行
        # 旧版: 单行失败 → commit 整体回滚 → 整个批量白做
        # 新版: 逐行 try/except + 单行 commit; 失败行 rollback 单独 + 收集 error
        if not items:
            return {"matched": 0, "updated": 0, "not_found": 0, "errors": []}
        # 拉本期所有审定行 dict by 复合键
        rows = list(
            (
                await db.execute(
                    select(AccountMovementAudit).where(
                        AccountMovementAudit.project_id == project_id,
                        AccountMovementAudit.period_end == period_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        index: Dict[Tuple[str, str, int, str], AccountMovementAudit] = {
            (r.account_code, r.voucher_no, r.voucher_line_no, r.direction): r for r in rows
        }
        matched = 0
        updated = 0
        not_found = 0
        errors: List[str] = []
        for idx, item in enumerate(items):
            try:
                key = (item.account_code, item.voucher_no, item.voucher_line_no, item.direction)
                row = index.get(key)
                if row is None:
                    not_found += 1
                    continue
                matched += 1
                row.audited_amount = float(item.audited_amount)
                row.adjustment_amount = float(item.audited_amount) - float(row.book_amount or 0)
                row.adjustment_reason = item.adjustment_reason
                row.working_paper_ref = item.working_paper_ref
                row.note = item.note
                row.status = MOVEMENT_AUDIT_STATUS_AUDITED
                row.audited_by_user_id = user_id
                row.audited_by_display = user_display
                row.audited_at = _utcnow_naive()
                row.updated_at = _utcnow_naive()
                # partial commit: 单行 commit, 失败不影响其他行
                await db.commit()
                updated += 1
            except Exception as exc:  # noqa: BLE001
                # 失败行单独 rollback, 不影响已成功行
                try:
                    await db.rollback()
                except Exception:
                    logger.exception("bulk_audit: 单行 rollback 失败 idx=%d", idx)
                errors.append(f"行 {idx}/{item.account_code}/{item.voucher_no}: {exc}")
        return {"matched": matched, "updated": updated, "not_found": not_found, "errors": errors}

    # === 查询 ===
    @staticmethod
    async def list_movements(
        db: AsyncSession,
        *,
        project_id: int,
        account_code: Optional[str] = None,
        period_end: Optional[str] = None,
        direction: Optional[str] = None,
        status: Optional[str] = None,
        voucher_no: Optional[str] = None,
        keyword: Optional[str] = None,
        skip: int = 0,
        limit: int = 200,
    ) -> Dict[str, Any]:
        conds = [AccountMovementAudit.project_id == project_id]
        if account_code:
            conds.append(AccountMovementAudit.account_code == account_code)
        if period_end:
            conds.append(AccountMovementAudit.period_end == period_end)
        if direction:
            conds.append(AccountMovementAudit.direction == direction)
        if status:
            conds.append(AccountMovementAudit.status == status)
        if voucher_no:
            conds.append(AccountMovementAudit.voucher_no == voucher_no)
        if keyword:
            # P0 第 2 轮修复 — 转义 LIKE 通配符 + 限长 200 防全表扫描 DoS (与 audit_log 同源)
            from app.services.auth.audit_log import _escape_like

            kw = keyword[:200]
            like = f"%{_escape_like(kw)}%"
            conds.append(
                or_(
                    AccountMovementAudit.summary.ilike(like, escape="\\"),
                    AccountMovementAudit.account_name.ilike(like, escape="\\"),
                    AccountMovementAudit.adjustment_reason.ilike(like, escape="\\"),
                )
            )
        where = and_(*conds)
        total = int(
            (
                await db.execute(select(func.count(AccountMovementAudit.id)).where(where))
            ).scalar_one()
            or 0
        )
        stmt = (
            select(AccountMovementAudit)
            .where(where)
            .order_by(
                asc(AccountMovementAudit.account_code),
                asc(AccountMovementAudit.voucher_date),
                asc(AccountMovementAudit.voucher_no),
                asc(AccountMovementAudit.voucher_line_no),
            )
            .offset(max(0, int(skip)))
            .limit(max(1, min(1000, int(limit))))
        )
        items = list((await db.execute(stmt)).scalars().all())
        return {"total": total, "items": items}

    # === 汇总 ===
    @staticmethod
    async def account_summary(
        db: AsyncSession,
        *,
        project_id: int,
        account_code: str,
        period_end: str,
        prefixes: Optional[List[str]] = None,
    ) -> AccountAuditSummary:
        if prefixes is None:
            prefixes = await get_effective_prefixes(db, project_id)

        # 期初 / 期末 / 账面发生额 → AccountBalance
        bal = (
            await db.execute(
                select(AccountBalance).where(
                    AccountBalance.project_id == project_id,
                    AccountBalance.account_code == account_code,
                )
            )
        ).scalar_one_or_none()
        beg = float(bal.beginning_balance or 0) if bal else 0.0
        end_book = float(bal.ending_balance or 0) if bal else 0.0
        debit_book_total_bal = float(bal.debit_amount or 0) if bal else 0.0
        credit_book_total_bal = float(bal.credit_amount or 0) if bal else 0.0
        account_name = bal.account_name if bal else account_code

        # 本期发生额审定汇总
        rows = list(
            (
                await db.execute(
                    select(AccountMovementAudit).where(
                        AccountMovementAudit.project_id == project_id,
                        AccountMovementAudit.account_code == account_code,
                        AccountMovementAudit.period_end == period_end,
                    )
                )
            )
            .scalars()
            .all()
        )

        debit_book = 0.0
        debit_audited = 0.0
        credit_book = 0.0
        credit_audited = 0.0
        d_pending = d_audited = d_disputed = 0
        c_pending = c_audited = c_disputed = 0

        for r in rows:
            if r.status == MOVEMENT_AUDIT_STATUS_SKIPPED:
                continue
            if r.direction == MOVEMENT_DIRECTION_DEBIT:
                debit_book += float(r.book_amount or 0)
                debit_audited += float(r.audited_amount or 0)
                if r.status == MOVEMENT_AUDIT_STATUS_PENDING:
                    d_pending += 1
                elif r.status == MOVEMENT_AUDIT_STATUS_AUDITED:
                    d_audited += 1
                elif r.status == MOVEMENT_AUDIT_STATUS_DISPUTED:
                    d_disputed += 1
            elif r.direction == MOVEMENT_DIRECTION_CREDIT:
                credit_book += float(r.book_amount or 0)
                credit_audited += float(r.audited_amount or 0)
                if r.status == MOVEMENT_AUDIT_STATUS_PENDING:
                    c_pending += 1
                elif r.status == MOVEMENT_AUDIT_STATUS_AUDITED:
                    c_audited += 1
                elif r.status == MOVEMENT_AUDIT_STATUS_DISPUTED:
                    c_disputed += 1

        # 如果没有 movement_audit 行 (从未初始化), 直接用 AccountBalance 的发生额作为账面 + 审定
        if not rows and (debit_book_total_bal or credit_book_total_bal):
            debit_book = debit_book_total_bal
            debit_audited = debit_book_total_bal
            credit_book = credit_book_total_bal
            credit_audited = credit_book_total_bal

        # 期末审定 = 期初(暂时不区分审定) + 借审定 - 贷审定 (按借方科目); 贷方科目反过来。
        # 简化处理: 用账面方向判断, AccountBalance.balance_direction
        is_debit_account = bal and bal.balance_direction == "借"

        # 期初/期末 审定值: 当前没有专门的"余额审定"表, 假设审定值 = 账面 (后续可接 ChinaGAAP 期初/期末审定模块)
        beg_audited = beg
        end_audited = end_book  # 默认值; 真正的恒等式校验从下面计算

        # 恒等式校验
        # 借方科目(资产/费用): ending = beg + debit - credit → beg + debit - credit - ending = 0
        # 贷方科目(负债/权益/收入): ending = beg + credit - debit → beg + credit - debit - ending = 0
        if is_debit_account:
            identity_book = beg + debit_book - credit_book - end_book
            identity_audited = beg_audited + debit_audited - credit_audited - end_audited
        else:
            identity_book = beg + credit_book - debit_book - end_book
            identity_audited = beg_audited + credit_audited - debit_audited - end_audited

        is_balanced = abs(identity_audited) < _EPS
        is_lta = is_long_term_asset_account(account_code, prefixes)

        return AccountAuditSummary(
            project_id=project_id,
            account_code=account_code,
            account_name=account_name,
            period_end=period_end,
            is_long_term_asset=is_lta,
            beginning_balance_book=beg,
            beginning_balance_audited=beg_audited,
            beginning_balance_adjustment=beg_audited - beg,
            debit_book_total=debit_book,
            debit_audited_total=debit_audited,
            debit_adjustment_total=debit_audited - debit_book,
            debit_pending_count=d_pending,
            debit_audited_count=d_audited,
            debit_disputed_count=d_disputed,
            debit_total_count=d_pending + d_audited + d_disputed,
            credit_book_total=credit_book,
            credit_audited_total=credit_audited,
            credit_adjustment_total=credit_audited - credit_book,
            credit_pending_count=c_pending,
            credit_audited_count=c_audited,
            credit_disputed_count=c_disputed,
            credit_total_count=c_pending + c_audited + c_disputed,
            ending_balance_book=end_book,
            ending_balance_audited=end_audited,
            ending_balance_adjustment=end_audited - end_book,
            identity_check_book=identity_book,
            identity_check_audited=identity_audited,
            is_balanced=is_balanced,
        )

    @staticmethod
    async def project_overview(
        db: AsyncSession,
        *,
        project_id: int,
        period_end: str,
    ) -> AccountAuditOverview:
        prefixes = await get_effective_prefixes(db, project_id)

        # 找出本项目下所有长期资产科目 (来自 AccountBalance — 真实账套数据)
        balances = list(
            (
                await db.execute(
                    select(AccountBalance).where(AccountBalance.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )

        accounts: List[AccountAuditSummary] = []
        fully_audited = with_pending = with_dispute = unbalanced = 0

        # 预过滤: 仅长期资产科目才需要详查
        target_codes = [
            bal.account_code
            for bal in balances
            if is_long_term_asset_account(bal.account_code, prefixes)
        ]
        # 并发查每个科目的 summary — 大幅减少 N+1 串行延迟
        # 注: account_summary 内部复用同一 db session, 走同一连接串行化,
        #     但能减少逻辑分支, 后续可改成 batch SQL
        summaries = await asyncio.gather(
            *[
                AccountAuditService.account_summary(
                    db,
                    project_id=project_id,
                    account_code=code,
                    period_end=period_end,
                    prefixes=prefixes,
                )
                for code in target_codes
            ],
            return_exceptions=True,
        )
        for summary in summaries:
            if isinstance(summary, Exception) or summary is None:
                # 单个科目失败不应让整个概览崩 — 记日志后跳过
                logger.warning("account_summary 失败: %s", summary)
                continue
            accounts.append(summary)
            total_movements = summary.debit_total_count + summary.credit_total_count
            pending = summary.debit_pending_count + summary.credit_pending_count
            disputed = summary.debit_disputed_count + summary.credit_disputed_count
            if total_movements > 0 and pending == 0 and disputed == 0:
                fully_audited += 1
            if pending > 0:
                with_pending += 1
            if disputed > 0:
                with_dispute += 1
            if not summary.is_balanced:
                unbalanced += 1

        return AccountAuditOverview(
            project_id=project_id,
            period_end=period_end,
            total_accounts=len(accounts),
            accounts_fully_audited=fully_audited,
            accounts_with_pending=with_pending,
            accounts_with_dispute=with_dispute,
            accounts_unbalanced=unbalanced,
            accounts=accounts,
        )


__all__ = [
    "AccountAuditService",
    "get_effective_prefixes",
    "is_long_term_asset_account",
]
