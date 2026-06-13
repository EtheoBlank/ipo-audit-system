"""报告模板自定义化 ORM (Pack A — Roadmap Phase 20).

事务所品牌定制: 用户上传 Word / Excel 模板, 系统按 placeholder 填充。

与 ``FirmTemplate`` (综合底稿模板) 的区别:
  - ``FirmTemplate`` 是综合底稿模板 (Excel + 字段映射), 走 fill_engine
    流水线
  - ``ReportTemplate`` 是审计报告 / 管理建议书 / 穿行报告 等纯文档输出模板,
    走 docxtpl 简单变量替换
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


__all__ = [
    "ReportTemplate",
    "ReportRenderHistory",
    # 模板类型
    "REPORT_TYPE_AUDIT_REPORT",
    "REPORT_TYPE_MANAGEMENT_LETTER",
    "REPORT_TYPE_WALKTHROUGH",
    "REPORT_TYPE_SENTIMENT_BRIEFING",
    "REPORT_TYPE_RELATED_PARTY",
    "REPORT_TYPE_COMPREHENSIVE",
    "REPORT_TYPE_CUSTOM",
    "ALL_REPORT_TYPES",
    # 输出格式
    "REPORT_FORMAT_DOCX",
    "REPORT_FORMAT_XLSX",
    "REPORT_FORMAT_PDF",
]


def _utcnow() -> datetime:
    """与 db_models 一致 — naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# === 模板类型 ===
REPORT_TYPE_AUDIT_REPORT = "audit_report"  # 审计报告
REPORT_TYPE_MANAGEMENT_LETTER = "management_letter"  # 管理建议书
REPORT_TYPE_WALKTHROUGH = "walkthrough_report"  # 内控穿行报告
REPORT_TYPE_SENTIMENT_BRIEFING = "sentiment_briefing"  # 舆情简报
REPORT_TYPE_RELATED_PARTY = "related_party_report"  # 关联方专项报告 (Pack B)
REPORT_TYPE_COMPREHENSIVE = "comprehensive_report"  # 综合报告
REPORT_TYPE_CUSTOM = "custom"  # 自定义

ALL_REPORT_TYPES = [
    REPORT_TYPE_AUDIT_REPORT,
    REPORT_TYPE_MANAGEMENT_LETTER,
    REPORT_TYPE_WALKTHROUGH,
    REPORT_TYPE_SENTIMENT_BRIEFING,
    REPORT_TYPE_RELATED_PARTY,
    REPORT_TYPE_COMPREHENSIVE,
    REPORT_TYPE_CUSTOM,
]


# === 输出格式 ===
REPORT_FORMAT_DOCX = "docx"
REPORT_FORMAT_XLSX = "xlsx"
REPORT_FORMAT_PDF = "pdf"


class ReportTemplate(Base):
    """报告模板 (事务所定制). 一行 = 一个版本."""

    __tablename__ = "report_templates"
    __table_args__ = (
        UniqueConstraint("firm_id", "template_code", "version", name="uq_report_template_version"),
        Index("ix_report_template_lookup", "firm_id", "report_type", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    firm_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("auth_firms.id"), nullable=True, index=True
    )

    template_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    template_name: Mapped[str] = mapped_column(String(200), nullable=False)
    report_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    output_format: Mapped[str] = mapped_column(
        String(10), default=REPORT_FORMAT_DOCX, nullable=False
    )

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    placeholder_schema: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 字符串

    # 模板文件内容 — 直接存 bytes (Word / Excel), 不依赖外部文件系统
    template_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    template_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    template_size: Mapped[int] = mapped_column(Integer, nullable=False)
    template_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ReportRenderHistory(Base):
    """报告渲染历史 — 谁在何时用哪个模板渲染了哪个项目的什么内容."""

    __tablename__ = "report_render_history"
    __table_args__ = (
        Index("ix_render_history_template", "template_id", "created_at"),
        Index("ix_render_history_project", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("report_templates.id"), nullable=False, index=True
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=True, index=True
    )

    output_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    output_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 写盘可选

    context_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    rendered_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rendered_by_display: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )
