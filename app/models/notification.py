"""Pydantic schemas for Notification module."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.models.db.notification import (
    NOTIF_SEVERITY_INFO,
    ALL_NOTIF_SEVERITIES,
)


class NotificationCreate(BaseModel):
    user_id: Optional[int] = None
    project_id: Optional[int] = None
    module: str = Field(..., min_length=1, max_length=40)
    type: str = Field(..., min_length=1, max_length=80)
    severity: str = Field(default=NOTIF_SEVERITY_INFO)
    title: str = Field(..., min_length=1, max_length=300)
    body: Optional[str] = None
    link: Optional[str] = Field(None, max_length=500)
    resource_type: Optional[str] = Field(None, max_length=80)
    resource_id: Optional[str] = Field(None, max_length=80)
    payload: Optional[str] = None

    @field_validator("severity")
    @classmethod
    def _severity_known(cls, v: str) -> str:
        if v not in ALL_NOTIF_SEVERITIES:
            raise ValueError(f"severity 必须是 {ALL_NOTIF_SEVERITIES} 之一")
        return v


class NotificationResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    project_id: Optional[int] = None
    module: str
    type: str
    severity: str
    title: str
    body: Optional[str] = None
    link: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    payload: Optional[str] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    total: int
    unread: int
    items: List[NotificationResponse]


class NotificationUnreadCountResponse(BaseModel):
    total_unread: int
    by_module: dict  # {module_name: count}
    by_severity: dict  # {severity: count}


class NotificationMarkReadRequest(BaseModel):
    ids: Optional[List[int]] = None
    module: Optional[str] = None
    mark_all: bool = False
