"""法规抓取器测试 — P0 测试空白 (2026-06-19).

app/services/regulation_scraper.py 597 行 0 测试覆盖.
覆盖:
- RegulationItem dataclass: 必填字段 / compute_hash / 自动 content_hash
- item_to_dict: None 字段被剔除
- 5 个 Adapter 入口存在且类型正确
- 内容指纹稳定性: 同样元数据 → 同 hash
- Unicode / 中文 title 不影响 hash
"""
from __future__ import annotations

import pytest

from app.services.regulation_scraper import (
    BaseRegulationAdapter,
    CSRCAdapter,
    MOFAdapter,
    PBOCAdapter,
    RegulationItem,
    RegulationScraperService,
    SAFEAdapter,
    STAAdapter,
    item_to_dict,
)


# ============================================================
# RegulationItem — dataclass + hash
# ============================================================


class TestRegulationItem:
    """P0 业务正确性 — 抓取条目落库前的契约."""

    def test_minimal_required_fields(self):
        item = RegulationItem(
            source="CSRC",
            title="测试公告",
            full_text="内容",
        )
        assert item.source == "CSRC"
        assert item.title == "测试公告"
        assert item.is_effective is True  # 默认
        assert item.content_hash != ""  # 自动计算

    def test_content_hash_auto_computed(self):
        item = RegulationItem(source="MOF", title="测试", full_text="x")
        # __post_init__ 自动算 hash
        assert item.content_hash is not None
        assert len(item.content_hash) == 64  # SHA-256 hex

    def test_hash_stable_for_same_metadata(self):
        # 同样 source+title+document_no+publish_date → 同样 hash
        a = RegulationItem(
            source="CSRC", title="T", document_no="[2024]1", publish_date="2024-01-01"
        )
        b = RegulationItem(
            source="CSRC", title="T", document_no="[2024]1", publish_date="2024-01-01"
        )
        assert a.content_hash == b.content_hash

    def test_hash_differs_when_metadata_differs(self):
        a = RegulationItem(source="CSRC", title="T1")
        b = RegulationItem(source="CSRC", title="T2")
        assert a.content_hash != b.content_hash

    def test_hash_unicode_safe(self):
        # 中文 title / 长 unicode 不影响 hash 计算
        item = RegulationItem(
            source="MOF",
            title="关于全面推进我国管理会计体系建设的指导意见",
            document_no="财会[2014]27号",
            publish_date="2014-12-31",
        )
        assert len(item.content_hash) == 64
        # 包含中文的标题不应抛错
        assert item.content_hash

    def test_hash_ignores_full_text(self):
        # 故意设计: hash 只看 source+title+document_no+publish_date
        # 因为正文易因网页改版变动, 用元信息更稳定
        a = RegulationItem(source="CSRC", title="T", full_text="正文A")
        b = RegulationItem(source="CSRC", title="T", full_text="完全不同的正文B")
        assert a.content_hash == b.content_hash


# ============================================================
# item_to_dict — ORM 入库 dict 转换
# ============================================================


class TestItemToDict:
    """P0 数据入库 — None 字段必须剔除 (数据库列可能 NOT NULL)."""

    def test_minimal_dict(self):
        item = RegulationItem(source="CSRC", title="测试", full_text="正文")
        d = item_to_dict(item)
        assert d["source"] == "CSRC"
        assert d["title"] == "测试"
        assert d["full_text"] == "正文"
        assert d["content_hash"]  # hash 必填

    def test_none_fields_stripped(self):
        # 不传的可选字段 (issuing_authority / category / document_no 等)
        # 应从 dict 中剔除, 避免 ORM NOT NULL 列收到 None
        item = RegulationItem(source="MOF", title="t", full_text="x")
        d = item_to_dict(item)
        # 这些可选字段没传 → 不应在 dict 里
        assert "issuing_authority" not in d
        assert "category" not in d
        assert "document_no" not in d
        assert "publish_date" not in d
        assert "effective_date" not in d

    def test_set_fields_included(self):
        item = RegulationItem(
            source="STA",
            title="t",
            full_text="x",
            document_no="税总函[2024]100号",
            publish_date="2024-06-01",
            category="通知",
        )
        d = item_to_dict(item)
        assert d["document_no"] == "税总函[2024]100号"
        assert d["publish_date"] == "2024-06-01"
        assert d["category"] == "通知"


# ============================================================
# 5 个 Adapter 实例化 + BaseRegulationAdapter
# ============================================================


class TestAdapters:
    """P0 抓取入口 — 5 个 Adapter 必须能实例化."""

    @pytest.mark.parametrize(
        "adapter_cls",
        [CSRCAdapter, MOFAdapter, STAAdapter, SAFEAdapter, PBOCAdapter],
    )
    def test_adapter_instantiation(self, adapter_cls):
        # 5 个 adapter 都需要 _HttpClient, 这里传 None mock 即可 (不实际请求)
        adapter = adapter_cls(http=None)  # type: ignore[arg-type]
        assert isinstance(adapter, BaseRegulationAdapter)

    def test_scraper_service_instantiation(self):
        svc = RegulationScraperService()
        assert svc is not None