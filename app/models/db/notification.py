"""Generic Notification ORM (Pack A).

替代 ``SentimentNotification`` 的"概念扩展"版 — 用于:
  - 卡点升级
  - 函证到期
  - 审批待办 / 已批准 / 被拒绝
  - 长期资产发生额审定恒等式不平
  - 关联方识别新候选 (Pack B)
  - 招股书勾稽差异 (Pack D)
  - 反馈意见 SLA 临期 (Pack D)

``SentimentNotification`` 老表保留 (兼容旧数据), 但前端的全局红点和
所有新模块统一走本表。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


__all__ = [
    "Notification",
    # 严重度
    "NOTIF_SEVERITY_INFO",
    "NOTIF_SEVERITY_NOTICE",
    "NOTIF_SEVERITY_WARN",
    "NOTIF_SEVERITY_CRITICAL",
    "ALL_NOTIF_SEVERITIES",
    # 模块
    "NOTIF_MODULE_AUTH",
    "NOTIF_MODULE_APPROVAL",
    "NOTIF_MODULE_CONFIRMATION",
    "NOTIF_MODULE_INVENTORY",
    "NOTIF_MODULE_BLOCKER",
    "NOTIF_MODULE_ACCOUNT_AUDIT",
    "NOTIF_MODULE_RELATED_PARTY",
    "NOTIF_MODULE_PROSPECTUS",
    "NOTIF_MODULE_FEEDBACK",
    "NOTIF_MODULE_SENTIMENT",
    "NOTIF_MODULE_SYSTEM",
]


def _utcnow() -> datetime:
    """与 db_models 一致 — naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# === 严重度 (与现有 SentimentEvent 对齐, 便于前端统一渲染) ===
NOTIF_SEVERITY_INFO = "info"
NOTIF_SEVERITY_NOTICE = "notice"
NOTIF_SEVERITY_WARN = "warn"
NOTIF_SEVERITY_CRITICAL = "critical"
ALL_NOTIF_SEVERITIES = [
    NOTIF_SEVERITY_INFO,
    NOTIF_SEVERITY_NOTICE,
    NOTIF_SEVERITY_WARN,
    NOTIF_SEVERITY_CRITICAL,
]


# === 模块标签 (用于分类筛选 + 全局红点分模块计数) ===
NOTIF_MODULE_AUTH = "auth"
NOTIF_MODULE_APPROVAL = "approval"
NOTIF_MODULE_CONFIRMATION = "confirmation"
NOTIF_MODULE_INVENTORY = "inventory"
NOTIF_MODULE_BLOCKER = "blocker"
NOTIF_MODULE_ACCOUNT_AUDIT = "account_audit"
NOTIF_MODULE_RELATED_PARTY = "related_party"
NOTIF_MODULE_PROSPECTUS = "prospectus"
NOTIF_MODULE_FEEDBACK = "feedback"
NOTIF_MODULE_SENTIMENT = "sentiment"
NOTIF_MODULE_SYSTEM = "system"


class Notification(Base):
    """通用通知 — 推送到指定用户 / 项目, 或广播 (user_id = NULL)."""
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notif_user_unread", "user_id", "is_read"),
        Index("ix_notif_module_severity", "module", "severity"),
        Index("ix_notif_project_module", "project_id", "module"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 接收者. user_id NULL = 广播给所有用户 (按 module 过滤)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    # 业务上下文
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )

    module: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # 具体事件类型, 如 confirmation.locked
    severity: Mapped[str] = mapped_column(
        String(20), default=NOTIF_SEVERITY_INFO, nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 前端跳转 (anchor 或 path)

    # 资源指针 (可空, 通知未必关联到具体资源)
    resource_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 字符串, 给前端额外渲染

    # 状态
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )
