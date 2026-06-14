"""Pack A — Notification 模块单元测试.

覆盖:
  - schemas 字段约束 (severity 必须有效)
  - 模块常量完备性
"""
from __future__ import annotations

import pytest

from app.models.notification import (
    NotificationCreate,
    NotificationMarkReadRequest,
)
from app.models.db.notification import (
    ALL_NOTIF_SEVERITIES,
    NOTIF_MODULE_ACCOUNT_AUDIT,
    NOTIF_MODULE_APPROVAL,
    NOTIF_MODULE_AUTH,
    NOTIF_MODULE_SENTIMENT,
    NOTIF_MODULE_SYSTEM,
    NOTIF_SEVERITY_CRITICAL,
    NOTIF_SEVERITY_INFO,
    NOTIF_SEVERITY_NOTICE,
    NOTIF_SEVERITY_WARN,
)


class TestNotificationSchemas:
    def test_normal_create(self):
        n = NotificationCreate(
            module="account_audit",
            type="account_audit.unbalanced",
            severity=NOTIF_SEVERITY_WARN,
            title="测试通知",
        )
        assert n.module == "account_audit"
        assert n.severity == "warn"

    def test_invalid_severity_rejected(self):
        with pytest.raises(Exception):
            NotificationCreate(
                module="a", type="b", severity="bogus_sev", title="x"
            )

    def test_severity_constants_all_present(self):
        assert NOTIF_SEVERITY_INFO in ALL_NOTIF_SEVERITIES
        assert NOTIF_SEVERITY_NOTICE in ALL_NOTIF_SEVERITIES
        assert NOTIF_SEVERITY_WARN in ALL_NOTIF_SEVERITIES
        assert NOTIF_SEVERITY_CRITICAL in ALL_NOTIF_SEVERITIES
        assert len(ALL_NOTIF_SEVERITIES) == 4

    def test_module_constants_unique(self):
        modules = [
            NOTIF_MODULE_AUTH,
            NOTIF_MODULE_APPROVAL,
            NOTIF_MODULE_ACCOUNT_AUDIT,
            NOTIF_MODULE_SENTIMENT,
            NOTIF_MODULE_SYSTEM,
        ]
        assert len(set(modules)) == len(modules)


class TestMarkReadRequest:
    def test_mark_by_ids(self):
        r = NotificationMarkReadRequest(ids=[1, 2, 3])
        assert r.ids == [1, 2, 3]
        assert not r.mark_all

    def test_mark_all(self):
        r = NotificationMarkReadRequest(mark_all=True)
        assert r.mark_all

    def test_mark_by_module(self):
        r = NotificationMarkReadRequest(module="account_audit")
        assert r.module == "account_audit"
