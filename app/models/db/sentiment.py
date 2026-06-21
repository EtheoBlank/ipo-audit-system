"""Sentiment (舆情跟踪) ORM namespace — 主体/信源/事件/简报/季报/通知.

本文件是**逻辑分组 / re-export 容器**, 不再重复定义 ORM 类.
所有类仍由 ``app.models.db_models`` 统一定义, 本模块只把它们按"舆情域"汇总.

包含 8 张表 + 状态机常量 + 异常类:
  - ``SentimentSubject`` / ``SentimentSource`` / ``SentimentEvent``
  - ``SentimentDailyBriefing`` / ``SentimentDailyBriefingRevision``
  - ``SentimentQuarterlyReport`` / ``SentimentQuarterlyReportRevision``
  - ``SentimentNotification``
  - 异常: ``SentimentError`` / ``PaidSourceMissingKey`` / ``NoLlmConfigured`` /
         ``IllegalStateTransition`` / ``VerificationFailed``

调用约定 (推荐):
  >>> from app.models.db_models import SentimentEvent  # 老式, 仍兼容
  >>> from app.models.db.sentiment import SentimentEvent  # 新式, 语义化
  >>> from app.models.db.sentiment import NoLlmConfigured  # 异常类
"""

from app.models.db_models import (  # noqa: F401  re-export
    # ORM
    SentimentSubject,
    SentimentSource,
    SentimentEvent,
    SentimentDailyBriefing,
    SentimentDailyBriefingRevision,
    SentimentQuarterlyReport,
    SentimentQuarterlyReportRevision,
    SentimentNotification,
    # 异常类
    SentimentError,
    PaidSourceMissingKey,
    NoLlmConfigured,
    IllegalStateTransition,
    VerificationFailed,
    # 常量 — 严重度
    SENTIMENT_SEVERITY_INFO,
    SENTIMENT_SEVERITY_NOTICE,
    SENTIMENT_SEVERITY_WARN,
    SENTIMENT_SEVERITY_CRITICAL,
    SENTIMENT_SEVERITY_LABELS,
    # 常量 — 来源类型
    SENTIMENT_SOURCE_REGULATOR,
    SENTIMENT_SOURCE_NEWS,
    SENTIMENT_SOURCE_ANNOUNCE,
    SENTIMENT_SOURCE_RSS,
    SENTIMENT_SOURCE_PAID_API,
    SENTIMENT_SOURCE_MANUAL,
    SENTIMENT_SOURCE_OTHER,
    SENTIMENT_SOURCE_TYPE_LABELS,
    # 常量 — 事件状态
    SENTIMENT_EVENT_STATUS_UNREAD,
    SENTIMENT_EVENT_STATUS_READ,
    SENTIMENT_EVENT_STATUS_IGNORED,
    SENTIMENT_EVENT_STATUS_ATTACHED,
    SENTIMENT_EVENT_STATUS_LABELS,
    # 常量 — 信源状态
    SENTIMENT_SOURCE_STATUS_SUCCESS,
    SENTIMENT_SOURCE_STATUS_PARTIAL,
    SENTIMENT_SOURCE_STATUS_FAILED,
    SENTIMENT_SOURCE_STATUS_SKIPPED,
    SENTIMENT_SOURCE_STATUS_DISABLED,
    SENTIMENT_SOURCE_STATUS_LABELS,
    # 常量 — 文档状态机
    SENTIMENT_DOC_STATUS_DRAFT,
    SENTIMENT_DOC_STATUS_REVIEW,
    SENTIMENT_DOC_STATUS_APPROVED,
    SENTIMENT_DOC_STATUS_REJECTED,
    SENTIMENT_DOC_STATUS_FROZEN,
    SENTIMENT_DOC_STATUS_LABELS,
    SENTIMENT_DOC_STATUS_TRANSITIONS,
    # 常量 — 期间类型
    SENTIMENT_PERIOD_TYPE_Q1,
    SENTIMENT_PERIOD_TYPE_H1,
    SENTIMENT_PERIOD_TYPE_Q3,
    SENTIMENT_PERIOD_TYPE_ANNUAL,
    SENTIMENT_PERIOD_TYPE_LABELS,
    # 常量 — 通知类型
    SENTIMENT_NOTIFY_NEW_EVENT,
    SENTIMENT_NOTIFY_BRIEFING_READY,
    SENTIMENT_NOTIFY_BRIEFING_REJECTED,
    SENTIMENT_NOTIFY_REPORT_READY,
    SENTIMENT_NOTIFY_REPORT_APPROVED,
    SENTIMENT_NOTIFY_REPORT_REJECTED,
    SENTIMENT_NOTIFY_SCAN_FAILED,
    SENTIMENT_NOTIFY_TYPE_LABELS,
)


__all__ = [
    "SentimentSubject",
    "SentimentSource",
    "SentimentEvent",
    "SentimentDailyBriefing",
    "SentimentDailyBriefingRevision",
    "SentimentQuarterlyReport",
    "SentimentQuarterlyReportRevision",
    "SentimentNotification",
    "SentimentError",
    "PaidSourceMissingKey",
    "NoLlmConfigured",
    "IllegalStateTransition",
    "VerificationFailed",
    "SENTIMENT_SEVERITY_INFO",
    "SENTIMENT_SEVERITY_NOTICE",
    "SENTIMENT_SEVERITY_WARN",
    "SENTIMENT_SEVERITY_CRITICAL",
    "SENTIMENT_SEVERITY_LABELS",
    "SENTIMENT_SOURCE_REGULATOR",
    "SENTIMENT_SOURCE_NEWS",
    "SENTIMENT_SOURCE_ANNOUNCE",
    "SENTIMENT_SOURCE_RSS",
    "SENTIMENT_SOURCE_PAID_API",
    "SENTIMENT_SOURCE_MANUAL",
    "SENTIMENT_SOURCE_OTHER",
    "SENTIMENT_SOURCE_TYPE_LABELS",
    "SENTIMENT_EVENT_STATUS_UNREAD",
    "SENTIMENT_EVENT_STATUS_READ",
    "SENTIMENT_EVENT_STATUS_IGNORED",
    "SENTIMENT_EVENT_STATUS_ATTACHED",
    "SENTIMENT_EVENT_STATUS_LABELS",
    "SENTIMENT_SOURCE_STATUS_SUCCESS",
    "SENTIMENT_SOURCE_STATUS_PARTIAL",
    "SENTIMENT_SOURCE_STATUS_FAILED",
    "SENTIMENT_SOURCE_STATUS_SKIPPED",
    "SENTIMENT_SOURCE_STATUS_DISABLED",
    "SENTIMENT_SOURCE_STATUS_LABELS",
    "SENTIMENT_DOC_STATUS_DRAFT",
    "SENTIMENT_DOC_STATUS_REVIEW",
    "SENTIMENT_DOC_STATUS_APPROVED",
    "SENTIMENT_DOC_STATUS_REJECTED",
    "SENTIMENT_DOC_STATUS_FROZEN",
    "SENTIMENT_DOC_STATUS_LABELS",
    "SENTIMENT_DOC_STATUS_TRANSITIONS",
    "SENTIMENT_PERIOD_TYPE_Q1",
    "SENTIMENT_PERIOD_TYPE_H1",
    "SENTIMENT_PERIOD_TYPE_Q3",
    "SENTIMENT_PERIOD_TYPE_ANNUAL",
    "SENTIMENT_PERIOD_TYPE_LABELS",
    "SENTIMENT_NOTIFY_NEW_EVENT",
    "SENTIMENT_NOTIFY_BRIEFING_READY",
    "SENTIMENT_NOTIFY_BRIEFING_REJECTED",
    "SENTIMENT_NOTIFY_REPORT_READY",
    "SENTIMENT_NOTIFY_REPORT_APPROVED",
    "SENTIMENT_NOTIFY_REPORT_REJECTED",
    "SENTIMENT_NOTIFY_SCAN_FAILED",
    "SENTIMENT_NOTIFY_TYPE_LABELS",
]
