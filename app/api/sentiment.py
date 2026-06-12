"""舆情跟踪 API 路由 (prefix=/api/sentiment, tags=["舆情跟踪"]).

端点:
    主体:
      GET    /subjects                            列出某项目别名
      POST   /subjects                            新增别名
      PUT    /subjects/{id}                       修改
      DELETE /subjects/{id}                       软删 (is_active=False)
    信源:
      GET    /sources                             列出所有信源
      PUT    /sources/{id}                        启停
    红点:
      GET    /notifications/unread                未读通知
      POST   /notifications/{id}/read             标已读
      POST   /notifications/read-all              全部已读
    事件:
      GET    /events                              列表
      GET    /events/{id}                         详情
      POST   /events/{id}/ignore                  忽略
      POST   /events/import                       手工录入
    简报:
      GET    /briefings                           列表
      GET    /briefings/{id}                      详情
      POST   /briefings/generate                  立即生成
      POST   /briefings/{id}/submit               提交审阅
      POST   /briefings/{id}/approve              批准
      POST   /briefings/{id}/reject               驳回
      POST   /briefings/{id}/revise               修订 (新建版本)
      GET    /briefings/{id}/download             下载 .docx
      GET    /briefings/{id}/verify               重核验
    季度报告:
      GET    /reports                             列表
      POST   /reports                             创建任务
      POST   /reports/{id}/financials             上传季报数据
      POST   /reports/{id}/generate               触发生成
      POST   /reports/{id}/submit                 提交审阅
      POST   /reports/{id}/approve                批准
      POST   /reports/{id}/reject                 驳回
      GET    /reports/{id}/download               下载 .docx
      GET    /reports/{id}/verify                 重核验
    调度:
      GET    /scheduler/status                    调度器状态
      POST   /scheduler/start                     启动
      POST   /scheduler/stop                      停止
      POST   /scheduler/scan/now                  立即扫描
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import (
    IllegalStateTransition,
    NoLlmConfigured,
    Project,
    SENTIMENT_DOC_STATUS_APPROVED,
    SENTIMENT_DOC_STATUS_DRAFT,
    SENTIMENT_DOC_STATUS_FROZEN,
    SENTIMENT_DOC_STATUS_LABELS,
    SENTIMENT_DOC_STATUS_REJECTED,
    SENTIMENT_DOC_STATUS_REVIEW,
    SENTIMENT_DOC_STATUS_TRANSITIONS,
    SENTIMENT_EVENT_STATUS_ATTACHED,
    SENTIMENT_EVENT_STATUS_IGNORED,
    SENTIMENT_EVENT_STATUS_READ,
    SENTIMENT_NOTIFY_BRIEFING_REJECTED,
    SENTIMENT_NOTIFY_BRIEFING_READY,
    SENTIMENT_NOTIFY_NEW_EVENT,
    SENTIMENT_NOTIFY_REPORT_APPROVED,
    SENTIMENT_NOTIFY_REPORT_READY,
    SENTIMENT_NOTIFY_REPORT_REJECTED,
    SENTIMENT_PERIOD_TYPE_LABELS,
    SENTIMENT_SEVERITY_LABELS,
    SENTIMENT_SEVERITY_NOTICE,
    SENTIMENT_SEVERITY_WARN,
    SENTIMENT_SEVERITY_CRITICAL,
    SentimentDailyBriefing,
    SentimentDailyBriefingRevision,
    SentimentEvent,
    SentimentNotification,
    SentimentQuarterlyReport,
    SentimentQuarterlyReportRevision,
    SentimentSource,
    SentimentSubject,
)
from app.models.sentiment import (
    SentimentBriefingGenerateRequest,
    SentimentBriefingRejectRequest,
    SentimentBriefingResponse,
    SentimentBriefingReviewRequest,
    SentimentBriefingReviseRequest,
    SentimentEventImport,
    SentimentEventResponse,
    SentimentNotificationResponse,
    SentimentQuarterlyCreateRequest,
    SentimentQuarterlyFinancialInput,
    SentimentQuarterlyRejectRequest,
    SentimentQuarterlyReportResponse,
    SentimentQuarterlyReviewRequest,
    SentimentScanRequest,
    SentimentSourceResponse,
    SentimentSourceToggle,
    SentimentSubjectCreate,
    SentimentSubjectResponse,
    SentimentSubjectUpdate,
)
from app.services.sentiment.briefing.detector import detect
from app.services.sentiment.briefing.generator import BriefingGenerator
from app.services.sentiment.briefing.verifier import BriefingVerifier
from app.services.sentiment.briefing.word_exporter import BriefingWordExporter
from app.services.sentiment.dedup import compute_content_hash
from app.services.sentiment.notifier import mark_all_read, mark_read
from app.services.sentiment.quarterly.aggregator import aggregate_window, lock_references
from app.services.sentiment.quarterly.financial_input import (
    REQUIRED_FIELDS,
    FinancialInput,
    save_financial_input,
)
from app.services.sentiment.quarterly.generator import QuarterlyReportGenerator
from app.services.sentiment.quarterly.trigger import (
    QuarterlyPeriodSpec,
    create_or_get_report,
)
from app.services.sentiment.quarterly.verifier import QuarterlyVerifier
from app.services.sentiment.quarterly.word_exporter import QuarterlyReportWordExporter
from app.services.sentiment.scheduler import (
    get_scheduler,
    scan_now,
    start_scheduler,
    stop_scheduler,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sentiment", tags=["舆情跟踪"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_transition(current: str, target: str) -> None:
    """状态机流转合法性."""
    allowed = SENTIMENT_DOC_STATUS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"非法状态流转: {current} -> {target}; 允许: {sorted(allowed)}",
        )


async def _get_or_404(db: AsyncSession, model, pk: int, label: str = "对象"):
    obj = await db.get(model, pk)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{label} (id={pk}) 不存在")
    return obj


# ============================================================
#  SentimentSubject — 搜索别名
# ============================================================


@router.get("/subjects", response_model=list[SentimentSubjectResponse])
async def list_subjects(
    project_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(SentimentSubject).where(SentimentSubject.project_id == project_id)
    )
    return res.scalars().all()


@router.post("/subjects", response_model=SentimentSubjectResponse, status_code=201)
async def create_subject(
    body: SentimentSubjectCreate, db: AsyncSession = Depends(get_db),
):
    # 校验 project 存在
    await _get_or_404(db, Project, body.project_id, "项目")
    sub = SentimentSubject(**body.model_dump())
    db.add(sub)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="该别名已存在")
    await db.refresh(sub)
    return sub


@router.put("/subjects/{subject_id}", response_model=SentimentSubjectResponse)
async def update_subject(
    subject_id: int, body: SentimentSubjectUpdate, db: AsyncSession = Depends(get_db),
):
    sub = await _get_or_404(db, SentimentSubject, subject_id, "别名")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(sub, k, v)
    await db.commit()
    await db.refresh(sub)
    return sub


@router.delete("/subjects/{subject_id}", status_code=204)
async def delete_subject(
    subject_id: int, db: AsyncSession = Depends(get_db),
):
    sub = await _get_or_404(db, SentimentSubject, subject_id, "别名")
    sub.is_active = False
    await db.commit()


# ============================================================
#  SentimentSource — 信源
# ============================================================


@router.get("/sources", response_model=list[SentimentSourceResponse])
async def list_sources(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SentimentSource).order_by(SentimentSource.id))
    return res.scalars().all()


@router.put("/sources/{source_id}", response_model=SentimentSourceResponse)
async def toggle_source(
    source_id: int, body: SentimentSourceToggle, db: AsyncSession = Depends(get_db),
):
    src = await _get_or_404(db, SentimentSource, source_id, "信源")
    src.is_enabled = body.is_enabled
    await db.commit()
    await db.refresh(src)
    return src


# ============================================================
#  SentimentNotification — 红点
# ============================================================


@router.get("/notifications/unread")
async def list_unread(
    project_id: Optional[int] = None,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """返回未读通知 + 总数 (供前端红点)."""
    from sqlalchemy import func, update

    q = select(SentimentNotification).where(SentimentNotification.is_read == False)  # noqa: E712
    if project_id is not None:
        q = q.where(SentimentNotification.project_id == project_id)
    q = q.order_by(SentimentNotification.created_at.desc()).limit(limit)
    res = await db.execute(q)
    items = res.scalars().all()

    cnt_q = select(func.count(SentimentNotification.id)).where(
        SentimentNotification.is_read == False,  # noqa: E712
    )
    if project_id is not None:
        cnt_q = cnt_q.where(SentimentNotification.project_id == project_id)
    cnt_res = await db.execute(cnt_q)
    count = int(cnt_res.scalar() or 0)

    return {
        "count": count,
        "items": [SentimentNotificationResponse.model_validate(n) for n in items],
    }


@router.post("/notifications/{notification_id}/read")
async def read_one(notification_id: int, db: AsyncSession = Depends(get_db)):
    ok = await mark_read(db, notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="通知不存在或已读")
    await db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
async def read_all(project_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    n = await mark_all_read(db, project_id=project_id)
    await db.commit()
    return {"ok": True, "count": n}


# ============================================================
#  SentimentEvent — 事件
# ============================================================


@router.get("/events", response_model=list[SentimentEventResponse])
async def list_events(
    project_id: Optional[int] = None,
    severity: Optional[str] = None,
    review_status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(SentimentEvent)
    if project_id is not None:
        q = q.where(SentimentEvent.project_id == project_id)
    if severity:
        q = q.where(SentimentEvent.severity == severity)
    if review_status:
        q = q.where(SentimentEvent.review_status == review_status)
    if date_from:
        q = q.where(SentimentEvent.publish_date >= date_from)
    if date_to:
        q = q.where(SentimentEvent.publish_date <= date_to)
    q = q.order_by(SentimentEvent.publish_date.desc(), SentimentEvent.id.desc())
    q = q.offset((page - 1) * size).limit(size)
    res = await db.execute(q)
    return res.scalars().all()


@router.get("/events/{event_id}", response_model=SentimentEventResponse)
async def get_event(event_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(db, SentimentEvent, event_id, "事件")


@router.post("/events/{event_id}/ignore", response_model=SentimentEventResponse)
async def ignore_event(event_id: int, db: AsyncSession = Depends(get_db)):
    ev = await _get_or_404(db, SentimentEvent, event_id, "事件")
    ev.review_status = SENTIMENT_EVENT_STATUS_IGNORED
    await db.commit()
    await db.refresh(ev)
    return ev


@router.post("/events/import", response_model=SentimentEventResponse, status_code=201)
async def import_event(body: SentimentEventImport, db: AsyncSession = Depends(get_db)):
    await _get_or_404(db, Project, body.project_id, "项目")
    ch = compute_content_hash(
        source_code="manual",
        title=body.title,
        url=body.url,
        publish_date=body.publish_date,
    )
    # 查重
    res = await db.execute(select(SentimentEvent).where(SentimentEvent.content_hash == ch))
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该事件已存在 (content_hash 冲突)")
    ev = SentimentEvent(
        project_id=body.project_id,
        source_code="manual",
        event_kind=body.event_kind or "manual",
        severity=body.severity,
        title=body.title,
        url=body.url,
        publisher=body.publisher or "手工录入",
        publish_date=body.publish_date,
        content_text=body.content_text or "",
        content_hash=ch,
        review_status="unread",
    )
    db.add(ev)
    try:
        await db.commit()
    except IntegrityError:
        # 并发: 另一个请求已 insert 同 hash
        await db.rollback()
        raise HTTPException(status_code=400, detail="该事件已存在 (content_hash 冲突, 并发)")
    await db.refresh(ev)
    return ev


# ============================================================
#  SentimentDailyBriefing — 简报
# ============================================================


@router.get("/briefings", response_model=list[SentimentBriefingResponse])
async def list_briefings(
    project_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(SentimentDailyBriefing)
    if project_id is not None:
        q = q.where(SentimentDailyBriefing.project_id == project_id)
    if date_from:
        q = q.where(SentimentDailyBriefing.briefing_date >= date_from)
    if date_to:
        q = q.where(SentimentDailyBriefing.briefing_date <= date_to)
    if status:
        q = q.where(SentimentDailyBriefing.status == status)
    q = q.order_by(SentimentDailyBriefing.briefing_date.desc())
    res = await db.execute(q)
    return res.scalars().all()


@router.get("/briefings/{briefing_id}", response_model=SentimentBriefingResponse)
async def get_briefing(briefing_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")


@router.post("/briefings/generate", response_model=SentimentBriefingResponse)
async def generate_briefing(
    body: SentimentBriefingGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await _get_or_404(db, Project, body.project_id, "项目")
    briefing_date = body.briefing_date or _utcnow().strftime("%Y-%m-%d")

    # 幂等检查 (除非 force=True)
    if not body.force:
        detection = await detect(db, project.id, briefing_date)
        if not detection.should_generate:
            if detection.existing_briefing_id:
                br = await db.get(SentimentDailyBriefing, detection.existing_briefing_id)
                return br
            raise HTTPException(
                status_code=400,
                detail=f"不生成简报: {detection.reason} (事件数={detection.event_count})",
            )

    # 拉事件
    from app.services.sentiment.briefing.detector import BriefingDetector
    bd = BriefingDetector()
    # 用 detector 的窗口拉事件
    event_count = await bd._count_relevant_events(db, project.id, briefing_date)
    if event_count == 0:
        raise HTTPException(status_code=400, detail="窗口内无事件, 不生成")

    res = await db.execute(
        select(SentimentEvent).where(
            SentimentEvent.project_id == project.id,
            SentimentEvent.publish_date == briefing_date,
            SentimentEvent.review_status != SENTIMENT_EVENT_STATUS_IGNORED,
        ).order_by(SentimentEvent.severity.desc())
    )
    events = res.scalars().all()
    events_dict = [
        {
            "id": e.id, "title": e.title, "content_text": e.content_text,
            "publisher": e.publisher, "publish_date": e.publish_date,
            "severity": e.severity, "url": e.url,
        }
        for e in events
    ]

    # 4 轮 LLM
    try:
        gen = BriefingGenerator()
        content = await gen.generate(
            company_name=project.company_name,
            project_id=project.id,
            briefing_date=briefing_date,
            events=events_dict,
        )
    except NoLlmConfigured as exc:
        # 配置错: 没有可用 LLM (或 key 是占位符)
        raise HTTPException(status_code=503, detail=f"LLM 未配置: {exc}")
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        # 上游错: LLM 服务挂了 / 超时
        raise HTTPException(status_code=502, detail=f"LLM 上游不可达: {exc}")
    except Exception as exc:
        logger.exception("简报生成失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"简报生成失败: {exc}")

    # Verifier
    v = BriefingVerifier()
    report = v.verify(
        content.markdown,
        events_dict,
        safe_fact_event_ids=content.safe_fact_event_ids,
        key_facts=content.extraction.key_facts,  # LLM F2 修复: 校验 quote 是否在原文中
    )

    # 落库 (找现有 brief 或新建 — 唯一约束兜底并发)
    res2 = await db.execute(
        select(SentimentDailyBriefing).where(
            SentimentDailyBriefing.project_id == project.id,
            SentimentDailyBriefing.briefing_date == briefing_date,
        )
    )
    brief = res2.scalar_one_or_none()
    if brief is None:
        brief = SentimentDailyBriefing(
            project_id=project.id,
            briefing_date=briefing_date,
            title=f"{project.company_name} {briefing_date} 舆情简报",
        )
        db.add(brief)
        try:
            await db.flush()  # 立即触发唯一约束
        except IntegrityError:
            # 并发: 另一个 force=True 请求先到, 已创建 brief
            await db.rollback()
            res3 = await db.execute(
                select(SentimentDailyBriefing).where(
                    SentimentDailyBriefing.project_id == project.id,
                    SentimentDailyBriefing.briefing_date == briefing_date,
                )
            )
            brief = res3.scalar_one()
    brief.ai_summary = content.markdown
    brief.event_snapshot_json = json.dumps(content.event_snapshot, ensure_ascii=False)
    brief.risk_assessment_json = json.dumps(content.extraction.severity_breakdown, ensure_ascii=False)
    brief.audit_verification_json = json.dumps(report.to_dict(), ensure_ascii=False)
    brief.verification_failed = (not report.passed)
    brief.verification_message = "; ".join(f"[{i.issue_type}] {i.detail}" for i in report.issues) or None
    brief.event_count = len(events_dict)
    brief.status = SENTIMENT_DOC_STATUS_DRAFT

    # 导出 Word
    try:
        exporter = BriefingWordExporter()
        path, sha256 = exporter.export(
            project.id, briefing_date, project.company_name, content.markdown,
        )
        brief.word_report_path = str(path)
        brief.word_report_sha256 = sha256
    except Exception as exc:
        logger.warning("Word 导出失败: %s", exc)

    try:
        await db.commit()
    except IntegrityError:
        # 并发兜底: 唯一约束最后一道防线
        await db.rollback()
        raise HTTPException(status_code=409, detail="并发冲突: 该日期简报已被其他请求创建")
    await db.refresh(brief)

    # 通知
    from app.services.sentiment.notifier import create_notification
    await create_notification(
        db,
        notification_type=SENTIMENT_NOTIFY_BRIEFING_READY,
        title=f"简报已生成: {project.company_name} {briefing_date}",
        body=f"事件数={brief.event_count}, 校验失败={brief.verification_failed}",
        project_id=project.id,
        link_url=f"/sentiment?project_id={project.id}&briefing_id={brief.id}",
    )
    await db.commit()
    return brief


@router.post("/briefings/{briefing_id}/submit", response_model=SentimentBriefingResponse)
async def submit_briefing(
    briefing_id: int,
    body: SentimentBriefingReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    brief = await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")
    if brief.is_locked:
        raise HTTPException(status_code=400, detail="简报已锁定, 不可修改状态")
    if brief.verification_failed:
        raise HTTPException(
            status_code=400,
            detail=f"简报校验未通过, 禁止进入审阅流: {brief.verification_message}",
        )
    _validate_transition(brief.status, SENTIMENT_DOC_STATUS_REVIEW)
    brief.status = SENTIMENT_DOC_STATUS_REVIEW
    brief.submitted_at = _utcnow()
    brief.submitted_by = body.reviewer
    await db.commit()
    await db.refresh(brief)
    return brief


@router.post("/briefings/{briefing_id}/approve", response_model=SentimentBriefingResponse)
async def approve_briefing(
    briefing_id: int,
    body: SentimentBriefingReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    brief = await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")
    if brief.is_locked:
        raise HTTPException(status_code=400, detail="简报已锁定, 不可修改状态")
    # 状态机: review -> approved (校验) -> frozen (立即)
    _validate_transition(brief.status, SENTIMENT_DOC_STATUS_APPROVED)
    _validate_transition(SENTIMENT_DOC_STATUS_APPROVED, SENTIMENT_DOC_STATUS_FROZEN)
    brief.reviewed_at = _utcnow()
    brief.reviewed_by = body.reviewer
    brief.review_comment = body.comment
    # 一次性跳到 FROZEN, 中间态不写库, 与 approve_report 风格对齐
    brief.status = SENTIMENT_DOC_STATUS_FROZEN
    brief.is_locked = True
    brief.locked_at = _utcnow()
    brief.locked_by = body.reviewer
    brief.lock_reason = f"已批准 by {body.reviewer}"
    await db.commit()
    await db.refresh(brief)
    return brief


@router.post("/briefings/{briefing_id}/reject", response_model=SentimentBriefingResponse)
async def reject_briefing(
    briefing_id: int,
    body: SentimentBriefingRejectRequest,
    db: AsyncSession = Depends(get_db),
):
    brief = await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")
    if brief.is_locked:
        raise HTTPException(status_code=400, detail="简报已锁定, 不可修改状态")
    _validate_transition(brief.status, SENTIMENT_DOC_STATUS_REJECTED)
    brief.status = SENTIMENT_DOC_STATUS_REJECTED
    brief.reviewed_at = _utcnow()
    brief.reviewed_by = body.reviewer
    brief.review_comment = body.comment
    await db.commit()
    await db.refresh(brief)
    # 通知提交人
    from app.services.sentiment.notifier import create_notification
    await create_notification(
        db,
        notification_type=SENTIMENT_NOTIFY_BRIEFING_REJECTED,
        title=f"简报已驳回: {brief.title}",
        body=body.comment,
        project_id=brief.project_id,
    )
    await db.commit()
    return brief


@router.post("/briefings/{briefing_id}/recall", response_model=SentimentBriefingResponse)
async def recall_briefing(
    briefing_id: int,
    body: SentimentBriefingReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """审阅人撤回 (review -> draft). 与 /revise 不同: /revise 是基于 locked 创建新版本."""
    brief = await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")
    if brief.is_locked:
        raise HTTPException(status_code=400, detail="简报已锁定, 不可撤回")
    _validate_transition(brief.status, SENTIMENT_DOC_STATUS_DRAFT)
    brief.status = SENTIMENT_DOC_STATUS_DRAFT
    brief.submitted_at = None
    brief.submitted_by = None
    brief.reviewed_at = _utcnow()
    brief.reviewed_by = body.reviewer
    brief.review_comment = (body.comment or "") + " [撤回]"
    await db.commit()
    await db.refresh(brief)
    return brief


@router.post("/briefings/{briefing_id}/revise", response_model=SentimentBriefingResponse)
async def revise_briefing(
    briefing_id: int,
    body: SentimentBriefingReviseRequest,
    db: AsyncSession = Depends(get_db),
):
    """基于现有简报创建一份新版本 (draft). 老版本冻结在 revision 表."""
    brief = await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")
    if not brief.is_locked:
        raise HTTPException(status_code=400, detail="仅已锁定的简报可修订 (基于旧版新建)")

    # 旧版快照入 revision
    rev = SentimentDailyBriefingRevision(
        briefing_id=brief.id,
        version_no=1,  # 简化: 每份 brief 维护自己的版本号
        snapshot_json=json.dumps({
            "ai_summary": brief.ai_summary,
            "event_snapshot_json": brief.event_snapshot_json,
            "audit_verification_json": brief.audit_verification_json,
            "word_report_path": brief.word_report_path,
            "word_report_sha256": brief.word_report_sha256,
        }, ensure_ascii=False),
        change_note=body.change_note,
        changed_by=body.reviser,
    )
    db.add(rev)

    # 解除锁定 + 重置 status → draft
    brief.is_locked = False
    brief.locked_at = None
    brief.locked_by = None
    brief.lock_reason = None
    brief.status = SENTIMENT_DOC_STATUS_DRAFT
    brief.verification_failed = False
    brief.verification_message = None
    await db.commit()
    await db.refresh(brief)
    return brief


@router.get("/briefings/{briefing_id}/download")
async def download_briefing(briefing_id: int, db: AsyncSession = Depends(get_db)):
    brief = await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")
    if not brief.word_report_path or not Path(brief.word_report_path).exists():
        raise HTTPException(status_code=404, detail="Word 文档不存在")
    # SHA-256 校验
    if brief.word_report_sha256:
        h = hashlib.sha256()
        with open(brief.word_report_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        if h.hexdigest() != brief.word_report_sha256:
            raise HTTPException(status_code=409, detail="文件 SHA-256 与记录不一致, 拒绝下载")
    with open(brief.word_report_path, "rb") as f:
        data = f.read()
    fname = Path(brief.word_report_path).name
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/briefings/{briefing_id}/verify")
async def reverify_briefing(briefing_id: int, db: AsyncSession = Depends(get_db)):
    brief = await _get_or_404(db, SentimentDailyBriefing, briefing_id, "简报")
    if not brief.ai_summary:
        raise HTTPException(status_code=400, detail="简报无 ai_summary, 无法核验")
    # 拉对应事件
    res = await db.execute(
        select(SentimentEvent).where(
            SentimentEvent.project_id == brief.project_id,
            SentimentEvent.publish_date == brief.briefing_date,
        )
    )
    events = res.scalars().all()
    events_dict = [
        {"id": e.id, "title": e.title, "content_text": e.content_text, "publisher": e.publisher, "publish_date": e.publish_date}
        for e in events
    ]
    v = BriefingVerifier()
    report = v.verify(brief.ai_summary, events_dict)
    brief.audit_verification_json = json.dumps(report.to_dict(), ensure_ascii=False)
    brief.verification_failed = (not report.passed)
    brief.verification_message = "; ".join(f"[{i.issue_type}] {i.detail}" for i in report.issues) or None
    await db.commit()
    return report.to_dict()


# ============================================================
#  SentimentQuarterlyReport — 季度跟踪报告
# ============================================================


@router.get("/reports", response_model=list[SentimentQuarterlyReportResponse])
async def list_reports(
    project_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(SentimentQuarterlyReport)
    if project_id is not None:
        q = q.where(SentimentQuarterlyReport.project_id == project_id)
    q = q.order_by(SentimentQuarterlyReport.fiscal_year.desc(), SentimentQuarterlyReport.period_type)
    res = await db.execute(q)
    return res.scalars().all()


@router.get("/reports/{report_id}", response_model=SentimentQuarterlyReportResponse)
async def get_report(report_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")


@router.post("/reports", response_model=SentimentQuarterlyReportResponse, status_code=201)
async def create_report(
    body: SentimentQuarterlyCreateRequest, db: AsyncSession = Depends(get_db),
):
    if body.period_type not in SENTIMENT_PERIOD_TYPE_LABELS:
        raise HTTPException(status_code=400, detail=f"period_type 必须是 Q1/H1/Q3/ANNUAL")
    await _get_or_404(db, Project, body.project_id, "项目")
    rep = await create_or_get_report(
        db, body.project_id, body.period_type, body.fiscal_year,
        trigger_type=body.trigger_type,
    )
    return rep


@router.post("/reports/{report_id}/financials", response_model=SentimentQuarterlyReportResponse)
async def upload_financials(
    report_id: int,
    body: SentimentQuarterlyFinancialInput,
    db: AsyncSession = Depends(get_db),
):
    rep = await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")
    if rep.is_locked:
        raise HTTPException(status_code=400, detail="报告已锁定, 不可修改")

    fin = FinancialInput(
        data={
            "revenue": body.revenue,
            "net_profit": body.net_profit,
            "non_recurring_pnl": body.non_recurring_pnl,
            "gross_margin": body.gross_margin,
            "yoy_revenue": body.yoy_revenue,
            "yoy_net_profit": body.yoy_net_profit,
            "total_assets": body.total_assets,
            "operating_cash_flow": body.operating_cash_flow,
        },
        source="manual",
        note=body.note,
    )
    ok, err = await save_financial_input(db, rep, fin, verified_by=body.verified_by)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    await db.refresh(rep)
    return rep


@router.post("/reports/{report_id}/generate", response_model=SentimentQuarterlyReportResponse)
async def generate_report(report_id: int, db: AsyncSession = Depends(get_db)):
    rep = await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")
    if rep.is_locked:
        raise HTTPException(status_code=400, detail="报告已锁定, 不可重新生成")
    if not rep.financial_input_json:
        raise HTTPException(status_code=400, detail="请先通过 /financials 上传季报数据")

    project = await _get_or_404(db, Project, rep.project_id, "项目")
    fin = FinancialInput.from_json(rep.financial_input_json)

    # 聚合窗口
    briefings_orm, events_orm = await aggregate_window(db, rep)
    await lock_references(db, rep, briefings_orm, events_orm)

    briefings = [
        {
            "id": b.id,
            "briefing_date": b.briefing_date,
            "ai_summary": b.ai_summary or "",
            "audit_verification_json": b.audit_verification_json or "",
        }
        for b in briefings_orm
    ]
    events = [
        {
            "id": e.id, "title": e.title, "content_text": e.content_text,
            "severity": e.severity, "publish_date": e.publish_date, "url": e.url,
        }
        for e in events_orm
    ]

    # 4 轮 LLM
    try:
        gen = QuarterlyReportGenerator()
        content = await gen.generate(
            company_name=project.company_name,
            project_id=project.id,
            fiscal_year=rep.fiscal_year,
            period_type=rep.period_type,
            period_end=rep.period_end,
            financial_input=fin,
            briefings=briefings,
            events=events,
        )
    except NoLlmConfigured as exc:
        raise HTTPException(status_code=503, detail=f"LLM 未配置: {exc}")
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        raise HTTPException(status_code=502, detail=f"LLM 上游不可达: {exc}")
    except Exception as exc:
        logger.exception("季度报告生成失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"季度报告生成失败: {exc}")

    # 双数据源对账
    v = QuarterlyVerifier()
    verify = v.verify(content.markdown, fin.data, events, briefings)

    rep.ai_report_md = content.markdown
    rep.ai_report_verification_json = json.dumps(verify.to_dict(), ensure_ascii=False)
    rep.verification_failed = (not verify.passed)
    rep.verification_message = f"consistency_errors={verify.error_count}"
    rep.amount_snapshot = json.dumps(fin.data, ensure_ascii=False)
    rep.status = SENTIMENT_DOC_STATUS_DRAFT

    # Word 导出
    try:
        exp = QuarterlyReportWordExporter()
        path, sha256 = exp.export(
            rep.project_id, rep.period_type, rep.fiscal_year,
            project.company_name, content.markdown,
        )
        rep.word_report_path = str(path)
        rep.word_report_sha256 = sha256
    except Exception as exc:
        logger.warning("Word 导出失败: %s", exc)

    await db.commit()
    await db.refresh(rep)

    # 通知
    from app.services.sentiment.notifier import create_notification
    await create_notification(
        db,
        notification_type=SENTIMENT_NOTIFY_REPORT_READY,
        title=f"季度报告已生成: {rep.title}",
        body=f"校验失败={rep.verification_failed}",
        project_id=rep.project_id,
    )
    await db.commit()
    return rep


@router.post("/reports/{report_id}/submit", response_model=SentimentQuarterlyReportResponse)
async def submit_report(
    report_id: int, body: SentimentQuarterlyReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    rep = await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")
    if rep.is_locked:
        raise HTTPException(status_code=400, detail="报告已锁定")
    if rep.verification_failed:
        raise HTTPException(status_code=400, detail=f"校验未通过: {rep.verification_message}")
    _validate_transition(rep.status, SENTIMENT_DOC_STATUS_REVIEW)
    rep.status = SENTIMENT_DOC_STATUS_REVIEW
    rep.submitted_at = _utcnow()
    rep.submitted_by = body.reviewer
    await db.commit()
    await db.refresh(rep)
    return rep


@router.post("/reports/{report_id}/approve", response_model=SentimentQuarterlyReportResponse)
async def approve_report(
    report_id: int, body: SentimentQuarterlyReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    rep = await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")
    if rep.is_locked:
        raise HTTPException(status_code=400, detail="报告已锁定")
    _validate_transition(rep.status, SENTIMENT_DOC_STATUS_APPROVED)
    rep.status = SENTIMENT_DOC_STATUS_FROZEN  # approved → frozen
    rep.reviewed_at = _utcnow()
    rep.reviewed_by = body.reviewer
    rep.review_comment = body.comment
    rep.content_snapshot = rep.ai_report_md
    rep.is_locked = True
    rep.locked_at = _utcnow()
    rep.locked_by = body.reviewer
    rep.lock_reason = f"已批准 by {body.reviewer}"
    await db.commit()
    await db.refresh(rep)

    from app.services.sentiment.notifier import create_notification
    await create_notification(
        db,
        notification_type=SENTIMENT_NOTIFY_REPORT_APPROVED,
        title=f"季度报告已批准: {rep.title}",
        project_id=rep.project_id,
    )
    await db.commit()
    return rep


@router.post("/reports/{report_id}/reject", response_model=SentimentQuarterlyReportResponse)
async def reject_report(
    report_id: int, body: SentimentQuarterlyRejectRequest,
    db: AsyncSession = Depends(get_db),
):
    rep = await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")
    if rep.is_locked:
        raise HTTPException(status_code=400, detail="报告已锁定")
    _validate_transition(rep.status, SENTIMENT_DOC_STATUS_REJECTED)
    rep.status = SENTIMENT_DOC_STATUS_REJECTED
    rep.reviewed_at = _utcnow()
    rep.reviewed_by = body.reviewer
    rep.review_comment = body.comment
    await db.commit()
    await db.refresh(rep)

    from app.services.sentiment.notifier import create_notification
    await create_notification(
        db,
        notification_type=SENTIMENT_NOTIFY_REPORT_REJECTED,
        title=f"季度报告已驳回: {rep.title}",
        body=body.comment,
        project_id=rep.project_id,
    )
    await db.commit()
    return rep


@router.get("/reports/{report_id}/download")
async def download_report(report_id: int, db: AsyncSession = Depends(get_db)):
    rep = await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")
    if not rep.word_report_path or not Path(rep.word_report_path).exists():
        raise HTTPException(status_code=404, detail="Word 文档不存在")
    if rep.word_report_sha256:
        h = hashlib.sha256()
        with open(rep.word_report_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        if h.hexdigest() != rep.word_report_sha256:
            raise HTTPException(status_code=409, detail="文件 SHA-256 不一致")
    with open(rep.word_report_path, "rb") as f:
        data = f.read()
    fname = Path(rep.word_report_path).name
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/reports/{report_id}/verify")
async def reverify_report(report_id: int, db: AsyncSession = Depends(get_db)):
    rep = await _get_or_404(db, SentimentQuarterlyReport, report_id, "季度报告")
    if not rep.ai_report_md or not rep.financial_input_json:
        raise HTTPException(status_code=400, detail="报告无内容或无财务数据, 无法核验")
    fin = FinancialInput.from_json(rep.financial_input_json)
    res = await db.execute(
        select(SentimentEvent).where(
            SentimentEvent.project_id == rep.project_id,
            SentimentEvent.publish_date >= rep.daily_briefing_window_start,
            SentimentEvent.publish_date <= rep.daily_briefing_window_end,
        )
    )
    events = [
        {"id": e.id, "title": e.title, "content_text": e.content_text, "severity": e.severity, "publish_date": e.publish_date}
        for e in res.scalars().all()
    ]
    res2 = await db.execute(
        select(SentimentDailyBriefing).where(
            SentimentDailyBriefing.project_id == rep.project_id,
            SentimentDailyBriefing.briefing_date >= rep.daily_briefing_window_start,
            SentimentDailyBriefing.briefing_date <= rep.daily_briefing_window_end,
        )
    )
    briefings = [
        {"id": b.id, "ai_summary": b.ai_summary or "", "audit_verification_json": b.audit_verification_json or ""}
        for b in res2.scalars().all()
    ]
    v = QuarterlyVerifier()
    report = v.verify(rep.ai_report_md, fin.data, events, briefings)
    rep.ai_report_verification_json = json.dumps(report.to_dict(), ensure_ascii=False)
    rep.verification_failed = (not report.passed)
    rep.verification_message = f"consistency_errors={report.error_count}"
    await db.commit()
    return report.to_dict()


# ============================================================
#  调度器控制
# ============================================================


@router.get("/scheduler/status")
async def scheduler_status():
    s = get_scheduler()
    if s is None or not s.running:
        return {"running": False, "jobs": []}
    out = []
    for job in s.get_jobs():
        out.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "max_instances": job.max_instances,
            "coalesce": job.coalesce,
        })
    return {"running": True, "jobs": out}


@router.post("/scheduler/start")
async def sched_start():
    await start_scheduler()
    return {"ok": True}


@router.post("/scheduler/stop")
async def sched_stop():
    await stop_scheduler()
    return {"ok": True}


@router.post("/scheduler/scan/now")
async def sched_scan_now(body: Optional[SentimentScanRequest] = None):
    pid = body.project_id if body else None
    result = await scan_now(pid)
    return result
