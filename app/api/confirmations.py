"""Confirmation (函证) API routes — 银行/客户/供应商/其他往来询证函管理。

Endpoints (all under /api/confirmations):

  - 基础信息 -----------------------------------------------------
    GET    /subjects                              函证涉及的全部科目清单 + 银行模板字段
    GET    /cases                                 列出某项目的所有案卷
    POST   /cases                                 创建新案卷（一份统计表）
    GET    /cases/{case_id}                       案卷详情 + 函证对象列表
    POST   /cases/{case_id}/lock                  锁定案卷（确定发函后调用）
    POST   /cases/{case_id}/unlock                解锁（仅未发出函时允许）

  - 统计表生成 -------------------------------------------------
    POST   /cases/{case_id}/generate              从账套自动生成函证对象
    GET    /cases/{case_id}/items                 列出函证对象
    POST   /items/{item_id}/update                修改单个函证对象（金额/科目/函证项等）

  - 发函 -------------------------------------------------------
    POST   /items/{item_id}/send                  生成并发送一封询证函（锁定）
    GET    /letters/{letter_id}                   发函详情
    GET    /letters/{letter_id}/download          下载生成的 docx/pdf
    POST   /letters/{letter_id}/void              作废发函
    POST   /letters/{letter_id}/remind            催办（增加催办次数）

  - 回函 -------------------------------------------------------
    POST   /letters/{letter_id}/response          手工录入回函
    POST   /letters/{letter_id}/photos            上传回函照片 → OCR + AI 解析 → 回填
    GET    /letters/{letter_id}/response          查看回函详情

  - 汇总 / 导出 -----------------------------------------------
    GET    /cases/{case_id}/summary               函证汇总统计
    GET    /cases/{case_id}/export                导出多 Sheet Excel 工作簿
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._helpers import get_or_404, get_project_or_404
from app.core.config import settings
from app.core.database import get_db
from app.models.confirmation import (
    BANK_CONFIRMATION_TEMPLATE_FIELDS,
    CONFIRMATION_SUBJECTS,
    ConfirmationCaseCreateRequest,
    ConfirmationCaseResponse,
    ConfirmationItemResponse,
    ConfirmationLetterResponse,
    ConfirmationResponseInput,
    ConfirmationSummaryResponse,
    GenerateStatsRequest,
    GenerateStatsResponse,
    LockCaseRequest,
    SendLetterRequest,
    SubjectCatalogueResponse,
    SubjectInfo,
)
from app.models.db_models import (
    ConfirmationCase,
    ConfirmationItem,
    ConfirmationLetter,
    ConfirmationResponse,
    ConfirmationResponsePhoto,
    ITEM_STATUS_CONFIRMED,
    ITEM_STATUS_DRAFT,
    ITEM_STATUS_LABELS,
    ITEM_STATUS_MISMATCH,
    ITEM_STATUS_NO_REPLY,
    ITEM_STATUS_PARTIAL,
    ITEM_STATUS_REJECTED,
    ITEM_STATUS_RESPONDED,
    ITEM_STATUS_SENT,
    ITEM_STATUS_VOIDED,
    PARTY_TYPE_BANK,
    PARTY_TYPE_LABELS,
    RESPONSE_MATCH,
    RESPONSE_PARTIAL,
    RESPONSE_MISMATCH,
    RESPONSE_REJECT,
    RESPONSE_STATUS_LABELS,
    RESPONSE_UNCLEAR,
)
from app.services.confirmation import (
    ConfirmationExporter,
    ConfirmationLetterGenerator,
    ConfirmationResponseProcessor,
    ConfirmationStatsBuilder,
    LetterGenerationError,
    ResponseParseError,
)
from app.models.db.auth import User
from app.services.auth import get_current_user, get_current_user_optional
from app.services.sales_ledger.deepseek_client import DeepSeekClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/confirmations", tags=["函证管理"])


# ---- helpers ---------------------------------------------------------


def _deepseek_client() -> DeepSeekClient:
    return DeepSeekClient(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_API_BASE,
        model=settings.DEEPSEEK_MODEL,
    )


async def _get_case_or_404(db: AsyncSession, case_id: int) -> ConfirmationCase:
    return await get_or_404(db, ConfirmationCase, case_id, label="案卷")


async def _get_item_or_404(db: AsyncSession, item_id: int) -> ConfirmationItem:
    return await get_or_404(db, ConfirmationItem, item_id, label="函证对象")


async def _get_letter_or_404(db: AsyncSession, letter_id: int) -> ConfirmationLetter:
    return await get_or_404(db, ConfirmationLetter, letter_id, label="发函记录")


def _letter_generator() -> ConfirmationLetterGenerator:
    return ConfirmationLetterGenerator(output_dir=settings.OUTPUT_DIR / "confirmations")


def _response_processor() -> ConfirmationResponseProcessor:
    return ConfirmationResponseProcessor(
        output_dir=settings.OUTPUT_DIR / "confirmation_responses",
        client=_deepseek_client(),
    )


def _letter_no(letter_type: str, case_id: int, item_id: int, sent_date: datetime, seq: int) -> str:
    prefix = PARTY_TYPE_LETTER_PREFIX.get(letter_type, "LET")
    return f"{prefix}-{case_id:04d}-{item_id:05d}-{sent_date.strftime('%Y%m%d')}-{seq:02d}"


# party_type -> letter_no 前缀 (新增 party_type 时强制更新)
PARTY_TYPE_LETTER_PREFIX: dict[str, str] = {
    PARTY_TYPE_BANK: "BNK",
    "customer": "CUS",
    "supplier": "SUP",
    "other_recv": "ORX",
    "other_pay": "OPY",
    "loan": "LON",
    "investment": "INV",
    "regulator": "REG",
    "litigation": "LIT",
    "other": "OTH",
}


# ============================================================
# 1. 函证涉及科目清单
# ============================================================


@router.get("/subjects", response_model=SubjectCatalogueResponse)
async def get_subjects(
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """返回函证涉及的全部科目清单 + 银行模板字段 + 状态/类型字典。"""
    subjects = [
        SubjectInfo(
            code=s["code"],
            name=s["name"],
            category=s["category"],
            party_type=s["party_type"],
            party_type_label=PARTY_TYPE_LABELS.get(s["party_type"], s["party_type"]),
            default_subjects=s.get("default_subjects", []),
            threshold=s.get("threshold", 0.0),
            is_cash=s.get("is_cash", False),
            is_required=s.get("is_required", False),
            note=s.get("note"),
        )
        for s in CONFIRMATION_SUBJECTS
    ]
    return SubjectCatalogueResponse(
        subjects=subjects,
        bank_template_fields=BANK_CONFIRMATION_TEMPLATE_FIELDS,
        party_types=PARTY_TYPE_LABELS,
        response_status_labels=RESPONSE_STATUS_LABELS,
        item_status_labels=ITEM_STATUS_LABELS,
    )


# ============================================================
# 2. 案卷 (Case)
# ============================================================


@router.get("/cases", response_model=list[ConfirmationCaseResponse])
async def list_cases(
    project_id: int = Query(..., description="项目 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await get_project_or_404(db, project_id)
    res = await db.execute(
        select(ConfirmationCase)
        .where(ConfirmationCase.project_id == project_id)
        .order_by(ConfirmationCase.created_at.desc())
    )
    return list(res.scalars().all())


@router.post("/cases", response_model=ConfirmationCaseResponse)
async def create_case(
    req: ConfirmationCaseCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await get_project_or_404(db, req.project_id)
    case = ConfirmationCase(
        project_id=req.project_id,
        case_name=req.case_name,
        period_end=req.period_end.isoformat(),
        fiscal_year=req.fiscal_year,
        generated_by=req.generated_by,
        notes=req.notes,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return case


@router.get("/cases/{case_id}", response_model=ConfirmationCaseResponse)
async def get_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    case = await _get_case_or_404(db, case_id)
    return case


@router.post("/cases/{case_id}/lock", response_model=ConfirmationCaseResponse)
async def lock_case(
    case_id: int,
    req: LockCaseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """锁定案卷 — 一旦确定发函,锁定后统计表与发函日期不再变化。

    锁定语义:
      1) is_locked=True 后,PUT /items/{id} 只允许改备注类字段(contact_person/contact_info)
      2) subject_matters / book_balance / total_confirm_amount 等关键字段不可改
      3) send_letter 时固化 subject_matters_snapshot / book_balance_snapshot,
         后续 update_item 不影响已发函追溯
    """
    case = await _get_case_or_404(db, case_id)
    if case.is_locked:
        raise HTTPException(status_code=400, detail="案卷已锁定,不可重复锁定")

    # 一次拉取所有 item, 避免重复 SELECT
    res = await db.execute(select(ConfirmationItem).where(ConfirmationItem.case_id == case_id))
    items = list(res.scalars().all())
    if not items:
        raise HTTPException(status_code=400, detail="案卷下没有函证对象,无法锁定")

    # 检查重发: 已发函且未作废的 item 不允许再进入确认态
    active_letters = (
        (
            await db.execute(
                select(ConfirmationLetter).where(
                    ConfirmationLetter.case_id == case_id,
                    ConfirmationLetter.letter_status == "sent",
                )
            )
        )
        .scalars()
        .all()
    )
    if active_letters:
        raise HTTPException(
            status_code=400,
            detail=f"案卷下已有 {len(active_letters)} 封已发函,不能从已发函状态再次锁定。",
        )

    case.is_locked = True
    case.locked_at = datetime.now(timezone.utc)
    case.locked_by = req.locked_by
    case.lock_reason = req.lock_reason
    # 将所有 draft item 推进到 confirmed
    for it in items:
        if it.status == ITEM_STATUS_DRAFT:
            it.status = ITEM_STATUS_CONFIRMED
    await db.commit()
    await db.refresh(case)
    return case


@router.post("/cases/{case_id}/unlock", response_model=ConfirmationCaseResponse)
async def unlock_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """解锁 — 仅当案卷下无任何已发函(letter_status='sent')且无回函时才允许。

    同时清理 item.sent_letter_id / response_id 残留引用(例如作废 letter 后的悬空指针)。
    """
    case = await _get_case_or_404(db, case_id)

    # 严格检查: 任何已发函 (sent) 或回函 (response) 都不允许解锁
    active_letters = (
        (
            await db.execute(
                select(ConfirmationLetter).where(
                    ConfirmationLetter.case_id == case_id,
                    ConfirmationLetter.letter_status == "sent",
                )
            )
        )
        .scalars()
        .all()
    )
    if active_letters:
        raise HTTPException(
            status_code=400,
            detail=f"案卷下已有 {len(active_letters)} 封已发函,无法解锁。请作废旧发函或新建案卷。",
        )

    # 任何 response (回函) 都不允许解锁
    response_count = (
        await db.execute(
            select(func.count(ConfirmationResponse.id))
            .join(ConfirmationLetter, ConfirmationResponse.letter_id == ConfirmationLetter.id)
            .where(ConfirmationLetter.case_id == case_id)
        )
    ).scalar() or 0
    if response_count:
        raise HTTPException(
            status_code=400,
            detail=f"案卷下已有 {response_count} 条回函,无法解锁。",
        )

    case.is_locked = False
    case.locked_at = None
    case.locked_by = None
    case.lock_reason = None
    # 清理 item 的悬空引用 + 退回 confirmed → draft
    res = await db.execute(select(ConfirmationItem).where(ConfirmationItem.case_id == case_id))
    for it in res.scalars().all():
        if it.status == ITEM_STATUS_CONFIRMED:
            it.status = ITEM_STATUS_DRAFT
        it.sent_letter_id = None
        it.response_id = None
        it.subject_matters_snapshot = None
        it.total_confirm_amount_snapshot = None
        it.book_balance_snapshot = None
    await db.commit()
    await db.refresh(case)
    return case


# ============================================================
# 3. 统计表生成
# ============================================================


@router.post("/cases/{case_id}/generate", response_model=GenerateStatsResponse)
async def generate_stats(
    case_id: int,
    req: GenerateStatsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从账套自动生成函证对象清单。"""
    case = await _get_case_or_404(db, case_id)

    builder = ConfirmationStatsBuilder(db)
    try:
        result = await builder.generate(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 拉回持久化后的 ORM 对象
    res = await db.execute(
        select(ConfirmationItem)
        .where(ConfirmationItem.case_id == case_id)
        .order_by(ConfirmationItem.party_type, ConfirmationItem.book_balance.desc())
    )
    items_orm = list(res.scalars().all())
    item_responses = [ConfirmationItemResponse.from_orm_item(o) for o in items_orm]

    return GenerateStatsResponse(
        case_id=case_id,
        selected_count=result["selected_count"],
        total_amount=result["total_amount"],
        by_party_type=result["by_party_type"],
        items=item_responses,
    )


@router.get("/cases/{case_id}/items", response_model=list[ConfirmationItemResponse])
async def list_items(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    res = await db.execute(
        select(ConfirmationItem)
        .where(ConfirmationItem.case_id == case_id)
        .order_by(ConfirmationItem.party_type, ConfirmationItem.book_balance.desc())
    )
    return [ConfirmationItemResponse.from_orm_item(o) for o in res.scalars().all()]


# 锁定后允许修改的字段白名单 (P0 修复: subject_matters 锁定后不可改, 否则会破坏已发函追溯)
ITEM_LOCKED_ALLOWED_FIELDS = {"contact_person", "contact_info", "selection_reason"}

# 未锁定时允许修改的字段 (高风险字段 party_name / party_id / account_code 等任何时候都不允许改)
ITEM_UNLOCKED_ALLOWED_FIELDS = ITEM_LOCKED_ALLOWED_FIELDS | {
    "subject_matters",
    "importance",
    "account_code",
    "account_name",
    "book_balance",
    "total_confirm_amount",
    "book_balance_date",
}


@router.put("/items/{item_id}")
async def update_item(
    item_id: int,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手工修改函证对象。

    锁定语义:
      - 锁定后: 仅 contact_person / contact_info / selection_reason 可改
      - 未锁定: 上述 + subject_matters / amount / account 等可改
      - party_name / party_id / sent_letter_id / response_id / version / status 任何时候都不可改
    """
    item = await _get_item_or_404(db, item_id)

    res = await db.execute(select(ConfirmationCase).where(ConfirmationCase.id == item.case_id))
    case = res.scalar_one_or_none()
    allowed = (
        ITEM_LOCKED_ALLOWED_FIELDS if (case and case.is_locked) else ITEM_UNLOCKED_ALLOWED_FIELDS
    )

    # 拒绝白名单外的字段
    rejected = [k for k in payload if k not in allowed]
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=f"案卷{'已锁定' if (case and case.is_locked) else '未锁定'}时以下字段不可改: {rejected}",
        )
    for k, v in payload.items():
        if k == "subject_matters":
            item.subject_matters = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
        else:
            setattr(item, k, v)
    item.version = (item.version or 0) + 1
    await db.commit()
    await db.refresh(item)
    return ConfirmationItemResponse.from_orm_item(item).model_dump()


# ============================================================
# 4. 发函
# ============================================================


@router.post("/items/{item_id}/send", response_model=ConfirmationLetterResponse)
async def send_letter(
    item_id: int,
    req: SendLetterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成询证函并锁定发函记录。

    P0 修复:
      1) 状态机收紧: 仅 draft / confirmed / no_reply 状态可发函, partial / responded / rejected
         必须先作废旧 letter
      2) letter_no 加 seq 后缀 (同 item 作废后重发不会撞唯一约束)
      3) 发函时固化 subject_matters_snapshot / book_balance_snapshot / total_confirm_amount_snapshot
      4) try/except 兜底: 失败时回滚 item 状态 (避免脏数据)
      5) 乐观锁: item.version 自增
    """
    item = await _get_item_or_404(db, item_id)
    res = await db.execute(select(ConfirmationCase).where(ConfirmationCase.id == item.case_id))
    case = res.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="案卷不存在")
    if not case.is_locked:
        raise HTTPException(status_code=400, detail="案卷尚未锁定,无法发函。请先『确定发函』。")
    # 状态机: 收到回函后(partial/responded/rejected/mismatch)不能再发函, 必须先作废旧 letter
    if item.status in (
        ITEM_STATUS_SENT,
        ITEM_STATUS_RESPONDED,
        ITEM_STATUS_PARTIAL,
        ITEM_STATUS_MISMATCH,
        ITEM_STATUS_REJECTED,
        ITEM_STATUS_VOIDED,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"函证对象当前状态为 {item.status},不可发函。"
            f"若要重新发函,请先作废旧发函 (POST /letters/{{id}}/void)。",
        )

    # 1) 选模板
    template_id = req.template_id
    if template_id == "standard":
        template_id = {
            PARTY_TYPE_BANK: "bank_official",
            "customer": "customer_std",
            "supplier": "supplier_std",
            "other_recv": "other_std",
            "other_pay": "other_std",
            "loan": "bank_official",
            "investment": "other_std",
            "regulator": "other_std",
            "litigation": "other_std",
            "other": "other_std",
        }.get(item.party_type, "other_std")

    # 2) 渲染文本
    sent_dt = datetime.combine(req.sent_date, datetime.min.time())
    sent_date_str = sent_dt.strftime("%Y-%m-%d")
    balance_date = item.book_balance_date or case.period_end
    period = f"{case.fiscal_year}年度"
    period_start = f"{case.fiscal_year}-01-01"

    subject_matters = []
    try:
        subject_matters = json.loads(item.subject_matters or "[]")
    except Exception:
        pass

    gen = _letter_generator()
    try:
        path, content_text, actual_format = gen.generate(
            template_id,
            company_name=case.project.company_name if case.project else "（公司）",
            period=period,
            period_start=period_start,
            balance_date=balance_date,
            sent_date=sent_date_str,
            recipient=req.recipient or item.party_name,
            party_name=item.party_name,
            sent_by=req.sent_by,
            sender_firm=req.sender_firm,
            cpa_firm=req.sender_firm or "××会计师事务所(特殊普通合伙)",
            file_format=req.file_format,
            book_balance=item.book_balance or 0.0,
            current_deposit=item.book_balance if item.party_type == PARTY_TYPE_BANK else 0.0,
            transaction_amount=0.0,
            repayment_amount=0.0,
            direction=(
                "应收账款"
                if item.party_type == "customer"
                else "应付账款"
                if item.party_type == "supplier"
                else "其他往来"
            ),
            transaction_verb=("销售" if item.party_type == "customer" else "采购"),
            repayment_verb=("回款" if item.party_type == "customer" else "付款"),
            unsettled_invoice_count=0,
            unsettled_invoice_amount=0.0,
            nature="其他应收/应付" if item.party_type in ("other_recv", "other_pay") else "",
            period_amount=0.0,
            start_date=period_start,
            end_date=balance_date,
        )
    except LetterGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # 3) 锁定快照 (P0 修复: 固化 subject_matters_snapshot 等字段)
    amount_snapshot = {
        "book_balance": item.book_balance,
        "total_confirm_amount": item.total_confirm_amount,
        "subjects": list(subject_matters),
        "frozen_at": sent_dt.isoformat(),
    }

    # 计算 seq (同 case+item 下的发函序号)
    seq = (
        await db.execute(
            select(func.count(ConfirmationLetter.id)).where(
                ConfirmationLetter.case_id == case.id,
                ConfirmationLetter.item_id == item.id,
            )
        )
    ).scalar() or 0
    seq += 1

    letter = ConfirmationLetter(
        case_id=case.id,
        item_id=item.id,
        letter_no=_letter_no(item.party_type, case.id, item.id, sent_dt, seq),
        letter_type=item.party_type,
        template_id=template_id,
        seq=seq,
        sent_date=sent_dt,
        sent_method=req.sent_method,
        sent_by=req.sent_by,
        sender_firm=req.sender_firm,
        recipient=req.recipient or item.party_name,
        recipient_address=req.recipient_address,
        courier_no=req.courier_no,
        expected_reply_date=(
            datetime.combine(req.expected_reply_date, datetime.min.time())
            if req.expected_reply_date
            else None
        ),
        content_snapshot=content_text,
        amount_snapshot=json.dumps(amount_snapshot, ensure_ascii=False),
        file_path=str(path),
        file_format=actual_format,  # P0 修复: 用实际文件格式, 不是请求格式
        letter_status="sent",
    )
    try:
        db.add(letter)
        await db.flush()
        # 4) 推进 item 状态 + 固化快照
        item.status = ITEM_STATUS_SENT
        item.sent_letter_id = letter.id
        item.subject_matters_snapshot = json.dumps(subject_matters, ensure_ascii=False)
        item.total_confirm_amount_snapshot = item.total_confirm_amount
        item.book_balance_snapshot = item.book_balance
        item.version = (item.version or 0) + 1
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"并发冲突,letter_no 已存在。请重试。({exc.orig})",
        )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        # 清理已生成的 docx/pdf 文件
        try:
            if path and path.exists():
                path.unlink()
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail=f"发函失败: {exc}") from exc
    await db.refresh(letter)
    return letter


@router.get("/letters/{letter_id}", response_model=ConfirmationLetterResponse)
async def get_letter(
    letter_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    letter = await _get_letter_or_404(db, letter_id)
    return letter


@router.get("/letters/{letter_id}/download")
async def download_letter(
    letter_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    letter = await _get_letter_or_404(db, letter_id)
    if not letter.file_path:
        raise HTTPException(status_code=404, detail="发函文件未生成")
    p = Path(letter.file_path)
    # P0 修复: 路径越权校验 — 必须位于 OUTPUT_DIR 内
    try:
        output_dir = settings.OUTPUT_DIR.resolve()
        target = p.resolve()
        if not target.is_relative_to(output_dir):
            raise HTTPException(status_code=403, detail="文件路径越权")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"文件路径无效: {exc}") from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail="发函文件已丢失")
    # RFC 5987 文件名编码 (中文支持)
    import urllib.parse

    encoded_name = urllib.parse.quote(p.name, safe="")
    media_type = (
        "application/pdf"
        if letter.file_format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return StreamingResponse(
        io.BytesIO(target.read_bytes()),
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="letter_{letter_id}.{letter.file_format or "docx"}"; '
                f"filename*=UTF-8''{encoded_name}"
            ),
        },
    )


@router.post("/letters/{letter_id}/void", response_model=ConfirmationLetterResponse)
async def void_letter(
    letter_id: int,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """作废发函。

    P0 修复:
      1) 只有当回函状态为 unclear (或不存在) 时才允许作废
      2) 作废后清理 item.sent_letter_id / response_id 残留引用
      3) 清理已生成的 docx/pdf 文件
      4) 退回 item.status 到 confirmed (允许重新发函, seq 自增不会撞)
    """
    letter = await _get_letter_or_404(db, letter_id)
    if letter.letter_status == "voided":
        raise HTTPException(status_code=400, detail="发函已作废, 不可重复作废")

    res = await db.execute(
        select(ConfirmationResponse).where(ConfirmationResponse.letter_id == letter_id)
    )
    existing_resp = res.scalar_one_or_none()
    if existing_resp and existing_resp.response_status not in (RESPONSE_UNCLEAR,):
        raise HTTPException(
            status_code=400,
            detail=f"已有回函(状态={existing_resp.response_status}),不能作废。",
        )

    letter.letter_status = "voided"
    # 退回 item 状态
    res = await db.execute(select(ConfirmationItem).where(ConfirmationItem.id == letter.item_id))
    item = res.scalar_one_or_none()
    if item:
        item.status = ITEM_STATUS_CONFIRMED
        item.sent_letter_id = None
        item.response_id = None
        item.subject_matters_snapshot = None
        item.total_confirm_amount_snapshot = None
        item.book_balance_snapshot = None
        item.version = (item.version or 0) + 1
    # 清理已生成的发函文件
    if letter.file_path:
        try:
            p = Path(letter.file_path)
            if p.exists():
                p.unlink()
        except Exception:  # noqa: BLE001
            pass
    await db.commit()
    await db.refresh(letter)
    return letter


@router.post("/letters/{letter_id}/remind", response_model=ConfirmationLetterResponse)
async def remind_letter(
    letter_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """催办 — 增加催办次数。

    P0 修复: 必须 letter_status='sent' AND item.status 在 (SENT, NO_REPLY) 之一。
    收到回函(partial/responded/reject)后催办无意义。
    """
    letter = await _get_letter_or_404(db, letter_id)
    if letter.letter_status != "sent":
        raise HTTPException(status_code=400, detail="发函状态非 'sent', 不可催办")
    # 检查 item 状态
    res = await db.execute(select(ConfirmationItem).where(ConfirmationItem.id == letter.item_id))
    item = res.scalar_one_or_none()
    if not item or item.status not in (ITEM_STATUS_SENT, ITEM_STATUS_NO_REPLY):
        raise HTTPException(
            status_code=400,
            detail=f"函证对象状态为 {item.status if item else 'unknown'},"
            f"不可催办 (已回函/已作废的函证不需要催办)。",
        )
    letter.reminder_count = (letter.reminder_count or 0) + 1
    letter.last_reminded_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(letter)
    return letter


# ============================================================
# 5. 回函
# ============================================================


@router.post("/letters/{letter_id}/response")
async def submit_response(
    letter_id: int,
    req: ConfirmationResponseInput,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手工录入回函。

    P0 修复:
      1) 同一 letter 仅允许一条 response (unique=True), 多次提交会覆写并 version+=1
      2) 合并冗余的 if/else 差异计算
      3) 增加 IntegrityError 兜底 (并发创建 response 时)
      4) mismatch 状态映射到 ITEM_STATUS_MISMATCH (新增), 不再 fallback 到 PARTIAL
    """
    letter = await _get_letter_or_404(db, letter_id)
    if letter.letter_status != "sent":
        raise HTTPException(status_code=400, detail="发函尚未发出,不能录入回函")

    # 找到 / 创建 response
    res = await db.execute(
        select(ConfirmationResponse).where(ConfirmationResponse.letter_id == letter_id)
    )
    resp = res.scalar_one_or_none()
    if resp is None:
        try:
            resp = ConfirmationResponse(letter_id=letter_id)
            db.add(resp)
            await db.flush()
        except IntegrityError:
            await db.rollback()
            # 并发: 重新 select
            res = await db.execute(
                select(ConfirmationResponse).where(ConfirmationResponse.letter_id == letter_id)
            )
            resp = res.scalar_one_or_none()
            if not resp:
                raise HTTPException(status_code=500, detail="回函并发创建失败, 请重试")

    resp.received_date = (
        datetime.combine(req.received_date, datetime.min.time())
        if req.received_date
        else datetime.now(timezone.utc)
    )
    resp.response_method = req.response_method
    resp.response_status = req.response_status
    resp.amount_confirmed = req.amount_confirmed
    resp.difference_reason = req.difference_reason
    resp.response_summary = req.response_summary
    resp.subjects_detail = (
        json.dumps(req.subjects_detail, ensure_ascii=False) if req.subjects_detail else None
    )
    resp.auditor_note = req.auditor_note
    resp.is_manually_confirmed = True
    resp.confirmed_by = req.confirmed_by or "审计师"
    resp.confirmed_at = datetime.now(timezone.utc)
    resp.version = (resp.version or 0) + 1

    # 计算差异 (P0: 合并冗余 if/else, 单行)
    item = letter.item
    if item:
        book_balance = item.book_balance or 0.0
        resp.amount_difference = round(req.amount_confirmed - book_balance, 2)
        # 推进 item 状态
        if req.response_status == RESPONSE_MATCH:
            item.status = ITEM_STATUS_RESPONDED
        elif req.response_status == RESPONSE_PARTIAL:
            item.status = ITEM_STATUS_PARTIAL
        elif req.response_status == RESPONSE_REJECT:
            item.status = ITEM_STATUS_REJECTED
        elif req.response_status == RESPONSE_MISMATCH:
            item.status = ITEM_STATUS_MISMATCH
        else:  # RESPONSE_UNCLEAR
            item.status = ITEM_STATUS_PARTIAL
        item.response_id = resp.id
        item.version = (item.version or 0) + 1
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"并发冲突: {exc.orig}")
    await db.refresh(resp)
    return {"response_id": resp.id, "status": resp.response_status, "version": resp.version}


@router.post("/letters/{letter_id}/photos")
async def upload_response_photo(
    letter_id: int,
    file: UploadFile = File(...),
    auto_confirm: bool = Form(True, description="AI 解析后是否自动按解析结果回填"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传回函照片 → OCR + AI 解析 → 回填。

    P0 修复:
      1) letter_status 必须为 'sent' (P0: 之前没校验, voided 后还能上传)
      2) 文件大小限制 (settings.MAX_UPLOAD_SIZE, 默认 50MB), 防止 DoS
      3) OCR 失败时清理已落盘文件, 避免磁盘垃圾
      4) 顶层 try/except 兜底, 失败时 db.rollback()
      5) mismatch 状态显式映射到 ITEM_STATUS_MISMATCH
    """
    letter = await _get_letter_or_404(db, letter_id)
    if letter.letter_status != "sent":
        raise HTTPException(
            status_code=400,
            detail=f"发函状态为 {letter.letter_status},不能上传回函。请先发函。",
        )

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大 ({len(content) / 1024 / 1024:.1f}MB),"
            f"上限 {settings.MAX_UPLOAD_SIZE / 1024 / 1024:.0f}MB。",
        )
    processor = _response_processor()

    path = None
    try:
        path, ocr_engine, ocr_text, parsed = await processor.process_upload(
            content,
            file.filename or "response.jpg",
            expected_book_amount=letter.item.book_balance if letter.item else 0.0,
        )
    except ResponseParseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # OCR 失败时清理已落盘文件
        if path and Path(path).exists():
            try:
                Path(path).unlink()
            except Exception:  # noqa: BLE001
                pass
        raise HTTPException(status_code=500, detail=f"OCR 处理失败: {exc}") from exc

    # 找 / 创建 response (并发保护)
    res = await db.execute(
        select(ConfirmationResponse).where(ConfirmationResponse.letter_id == letter_id)
    )
    resp = res.scalar_one_or_none()
    if resp is None:
        try:
            resp = ConfirmationResponse(letter_id=letter_id)
            db.add(resp)
            await db.flush()
        except IntegrityError:
            await db.rollback()
            res = await db.execute(
                select(ConfirmationResponse).where(ConfirmationResponse.letter_id == letter_id)
            )
            resp = res.scalar_one_or_none()
            if not resp:
                raise HTTPException(status_code=500, detail="回函并发创建失败, 请重试")

    photo = ConfirmationResponsePhoto(
        response_id=resp.id,
        filename=file.filename or "response.jpg",
        media_type=file.content_type or "image/jpeg",
        file_path=str(path),
        ocr_engine=ocr_engine,
        ocr_text=ocr_text,
        parsed_data=json.dumps(parsed.ai_extracted, ensure_ascii=False)
        if parsed.ai_extracted
        else None,
        match_status="parsed",
        matched_amount=parsed.amount_confirmed or None,
        matched_subjects=json.dumps(parsed.subjects_detail, ensure_ascii=False)
        if parsed.subjects_detail
        else None,
        processed_at=datetime.now(timezone.utc),
    )
    db.add(photo)

    # 回填 response
    resp.raw_text = ocr_text
    resp.ai_extracted = (
        json.dumps(parsed.ai_extracted, ensure_ascii=False) if parsed.ai_extracted else None
    )
    if auto_confirm:
        resp.response_status = parsed.response_status
        resp.amount_confirmed = parsed.amount_confirmed
        resp.amount_difference = parsed.amount_difference
        resp.difference_reason = parsed.difference_reason
        resp.received_date = parsed.received_date or datetime.now(timezone.utc)
        resp.response_method = parsed.response_method
        resp.subjects_detail = (
            json.dumps(parsed.subjects_detail, ensure_ascii=False)
            if parsed.subjects_detail
            else None
        )
        resp.response_summary = parsed.response_summary
        resp.version = (resp.version or 0) + 1
        # 推进 item 状态 (P0: 显式映射 mismatch)
        item = letter.item
        if item:
            if parsed.response_status == RESPONSE_MATCH:
                item.status = ITEM_STATUS_RESPONDED
            elif parsed.response_status == RESPONSE_PARTIAL:
                item.status = ITEM_STATUS_PARTIAL
            elif parsed.response_status == RESPONSE_REJECT:
                item.status = ITEM_STATUS_REJECTED
            elif parsed.response_status == RESPONSE_MISMATCH:
                item.status = ITEM_STATUS_MISMATCH
            else:
                item.status = ITEM_STATUS_PARTIAL
            item.response_id = resp.id
            item.version = (item.version or 0) + 1
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # 清理已上传文件
        if path and Path(path).exists():
            try:
                Path(path).unlink()
            except Exception:  # noqa: BLE001
                pass
        raise HTTPException(status_code=409, detail=f"并发冲突: {exc.orig}")
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"回填失败: {exc}") from exc

    await db.refresh(photo)
    return {
        "photo_id": photo.id,
        "response_id": resp.id,
        "ocr_engine": ocr_engine,
        "parsed": True,
        "matched_amount": parsed.amount_confirmed,
        "parsed_data": parsed.ai_extracted,
        "message": "回函照片已解析" + ("并自动回填" if auto_confirm else "（待人工确认）"),
    }


@router.get("/letters/{letter_id}/response")
async def get_response(
    letter_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    res = await db.execute(
        select(ConfirmationResponse).where(ConfirmationResponse.letter_id == letter_id)
    )
    resp = res.scalar_one_or_none()
    if not resp:
        return {"response": None}

    # 附照片
    res = await db.execute(
        select(ConfirmationResponsePhoto).where(ConfirmationResponsePhoto.response_id == resp.id)
    )
    photos = [
        {
            "id": p.id,
            "filename": p.filename,
            "ocr_engine": p.ocr_engine,
            "matched_amount": p.matched_amount,
            "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
        }
        for p in res.scalars().all()
    ]

    return {
        "response": {
            "id": resp.id,
            "letter_id": resp.letter_id,
            "received_date": resp.received_date.isoformat() if resp.received_date else None,
            "response_method": resp.response_method,
            "response_status": resp.response_status,
            "response_status_label": RESPONSE_STATUS_LABELS.get(
                resp.response_status, resp.response_status
            ),
            "amount_confirmed": resp.amount_confirmed,
            "amount_difference": resp.amount_difference,
            "difference_reason": resp.difference_reason,
            "response_summary": resp.response_summary,
            "subjects_detail": json.loads(resp.subjects_detail) if resp.subjects_detail else None,
            "is_manually_confirmed": resp.is_manually_confirmed,
            "confirmed_by": resp.confirmed_by,
            "confirmed_at": resp.confirmed_at.isoformat() if resp.confirmed_at else None,
            "auditor_note": resp.auditor_note,
            "photos": photos,
        }
    }


# ============================================================
# 6. 汇总 / 导出
# ============================================================


@router.get("/cases/{case_id}/summary", response_model=ConfirmationSummaryResponse)
async def get_summary(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    case = await _get_case_or_404(db, case_id)

    res = await db.execute(select(ConfirmationItem).where(ConfirmationItem.case_id == case_id))
    items = list(res.scalars().all())
    res = await db.execute(select(ConfirmationLetter).where(ConfirmationLetter.case_id == case_id))
    letters = list(res.scalars().all())
    res = await db.execute(
        select(ConfirmationResponse)
        .join(ConfirmationLetter, ConfirmationResponse.letter_id == ConfirmationLetter.id)
        .where(ConfirmationLetter.case_id == case_id)
    )
    responses = list(res.scalars().all())

    # 状态分布
    status_summary: dict[str, int] = defaultdict(int)
    for it in items:
        status_summary[it.status] += 1
    response_status_summary: dict[str, int] = defaultdict(int)
    for r in responses:
        response_status_summary[r.response_status] += 1

    # 按 party_type
    by_type: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "items": 0,
            "amount": 0.0,
            "sent": 0,
            "responded": 0,
        }
    )
    for it in items:
        d = by_type[it.party_type]
        d["items"] += 1
        d["amount"] += it.book_balance or 0
    for l in letters:
        if l.item:
            by_type[l.item.party_type]["sent"] += 1
    for r in responses:
        if r.letter and r.letter.item:
            by_type[r.letter.item.party_type]["responded"] += 1

    by_type_list = []
    for k, d in by_type.items():
        by_type_list.append(
            {
                "party_type": k,
                "party_type_label": PARTY_TYPE_LABELS.get(k, k),
                "items": d["items"],
                "amount": round(d["amount"], 2),
                "sent": d["sent"],
                "responded": d["responded"],
                "response_rate": round(d["responded"] / d["sent"], 4) if d["sent"] else 0,
            }
        )
    by_type_list.sort(key=lambda x: -x["amount"])

    sent_count = len(letters)
    responded_count = len(responses)
    items_with_diff = sum(1 for r in responses if abs(r.amount_difference or 0) > 0.01)
    total_diff = sum(r.amount_difference or 0 for r in responses)

    # 待办 / 未回函
    pending_items = []
    no_reply_items = []
    by_item_id = {l.item_id: l for l in letters}
    for it in items:
        l = by_item_id.get(it.id)
        if it.status in (ITEM_STATUS_SENT, ITEM_STATUS_NO_REPLY):
            entry = {
                "id": it.id,
                "party_name": it.party_name,
                "party_type": it.party_type,
                "party_type_label": PARTY_TYPE_LABELS.get(it.party_type, it.party_type),
                "book_balance": it.book_balance,
                "sent_date": l.sent_date.isoformat() if l and l.sent_date else None,
                "expected_reply_date": l.expected_reply_date.isoformat()
                if l and l.expected_reply_date
                else None,
                "reminder_count": l.reminder_count if l else 0,
            }
            if it.status == ITEM_STATUS_NO_REPLY:
                no_reply_items.append(entry)
            else:
                pending_items.append(entry)

    total_confirmed = sum(r.amount_confirmed for r in responses)
    return ConfirmationSummaryResponse(
        case_id=case_id,
        case_name=case.case_name,
        period_end=case.period_end,
        is_locked=case.is_locked,
        total_items=len(items),
        total_amount=round(sum(it.book_balance or 0 for it in items), 2),
        total_confirmed=round(total_confirmed, 2),
        total_difference=round(total_diff, 2),
        status_summary=dict(status_summary),
        response_status_summary=dict(response_status_summary),
        by_party_type=by_type_list,
        sent_count=sent_count,
        responded_count=responded_count,
        response_rate=round(responded_count / sent_count, 4) if sent_count else 0.0,
        items_with_difference=items_with_diff,
        total_difference_amount=round(total_diff, 2),
        pending_items=pending_items,
        no_reply_items=no_reply_items,
    )


@router.get("/cases/{case_id}/export")
async def export_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """导出函证工作簿 (多 Sheet Excel)。"""
    case = await _get_case_or_404(db, case_id)

    res = await db.execute(select(ConfirmationItem).where(ConfirmationItem.case_id == case_id))
    items = list(res.scalars().all())
    res = await db.execute(select(ConfirmationLetter).where(ConfirmationLetter.case_id == case_id))
    letters = list(res.scalars().all())
    res = await db.execute(
        select(ConfirmationResponse)
        .join(ConfirmationLetter, ConfirmationResponse.letter_id == ConfirmationLetter.id)
        .where(ConfirmationLetter.case_id == case_id)
    )
    responses = list(res.scalars().all())

    data = ConfirmationExporter.build(items, letters, responses)
    fname = f"函证工作簿_{case.case_name}_{case.period_end}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
