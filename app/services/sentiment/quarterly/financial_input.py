"""季报关键数据输入.

支持:
    - 手工录入 (JSON 字段)
    - 上传 PDF/Excel (后续扩展, 此处只接 schema)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from numbers import Real
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import SentimentQuarterlyReport

logger = logging.getLogger(__name__)


# 季报关键数据 schema — 审计师必须填的核心字段
REQUIRED_FIELDS: list[str] = [
    "revenue",  # 营业收入 (元)
    "net_profit",  # 净利润 (元)
    "non_recurring_pnl",  # 扣非净利润 (元)
    "gross_margin",  # 毛利率 (%, 0-100)
    "yoy_revenue",  # 营收同比 (%, 正负)
    "yoy_net_profit",  # 净利同比 (%, 正负)
    "total_assets",  # 期末总资产 (元)
    "operating_cash_flow",  # 经营活动现金流净额 (元)
]


def _is_numeric(value: Any) -> bool:
    """判定 value 是否是合法数值类型.

    Round 35 P1: 旧版 ``is_complete`` 只查 key 存在 + 非 None, 接受 ``str`` "abc"
    / list / dict — 误判完整, save_financial_input 把垃圾落库, verifier 再去匹配
    文本时硬编码 ``isinstance(value, (int, float))`` 跳过, 整条报告静默失配.
    """
    if value is None:
        return False
    if isinstance(value, bool):  # bool 是 int 子类, 必须先排除
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    return False


@dataclass
class FinancialInput:
    """季报关键数据 (内存表示)."""

    data: dict = field(default_factory=dict)
    source: str = "manual"  # manual / uploaded_pdf / uploaded_excel
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    note: Optional[str] = None

    def is_complete(self) -> bool:
        # Round 35 P1: 不仅查 key 存在, 还要查类型是数值. 拒绝 str / list / bool.
        for f in REQUIRED_FIELDS:
            if f not in self.data:
                return False
            if not _is_numeric(self.data[f]):
                return False
        return True

    def invalid_fields(self) -> list[tuple[str, str]]:
        """返回 (field, reason) 列表, 用于审计师快速定位错填字段."""
        out: list[tuple[str, str]] = []
        for f in REQUIRED_FIELDS:
            if f not in self.data:
                out.append((f, "missing"))
                continue
            v = self.data[f]
            if v is None:
                out.append((f, "null"))
                continue
            if not _is_numeric(v):
                out.append((f, f"non_numeric_type={type(v).__name__}"))
        return out

    def to_json(self) -> str:
        return json.dumps(
            {
                "data": self.data,
                "source": self.source,
                "verified_by": self.verified_by,
                "verified_at": self.verified_at,
                "note": self.note,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, s: str) -> "FinancialInput":
        if not s:
            return cls()
        d = json.loads(s)
        return cls(
            data=d.get("data") or {},
            source=d.get("source", "manual"),
            verified_by=d.get("verified_by"),
            verified_at=d.get("verified_at"),
            note=d.get("note"),
        )

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


async def save_financial_input(
    db: AsyncSession,
    report: SentimentQuarterlyReport,
    fin: FinancialInput,
    *,
    verified_by: str,
) -> tuple[bool, str]:
    """保存财务输入. 必须 verify_by + 必填字段全部填才允许.

    Returns:
        (ok, error_message). ok=True 表示保存成功; False 表示未通过校验.
    """
    # ALG-05 (round32, 2026-06-20): 报告已锁定后, 财务输入不应被重写
    # (会破坏审计痕迹 / 与引用快照逻辑一致).
    if getattr(report, "is_locked", False):
        return False, "报告已锁定, 不可重写财务输入"

    if not verified_by:
        return False, "必须填写 verified_by (审计师签名)"

    if not fin.is_complete():
        invalid = fin.invalid_fields()
        # Round 35 P1: 区分 missing/null vs 类型错, 错误消息带 type= 便于定位.
        msgs = [f"{f}({reason})" for f, reason in invalid]
        # 兼容旧测试: 必填字段缺失 (missing/null) 路径, 主消息保留 "缺失" 字眼.
        only_missing_null = all(reason in ("missing", "null") for _f, reason in invalid)
        if only_missing_null:
            return False, f"必填字段缺失: {[f for f, _r in invalid]}"
        return False, f"必填字段不合法: {msgs}"

    fin.verified_by = verified_by
    fin.verified_at = FinancialInput.now_iso()
    report.financial_input_json = fin.to_json()
    report.financial_input_source = fin.source
    report.financial_input_verified_by = verified_by
    report.financial_input_verified_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("save_financial_input: report=%s verified_by=%s", report.id, verified_by)
    return True, ""
