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
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import SentimentQuarterlyReport

logger = logging.getLogger(__name__)


# 季报关键数据 schema — 审计师必须填的核心字段
REQUIRED_FIELDS: list[str] = [
    "revenue",            # 营业收入 (元)
    "net_profit",         # 净利润 (元)
    "non_recurring_pnl",  # 扣非净利润 (元)
    "gross_margin",       # 毛利率 (%, 0-100)
    "yoy_revenue",        # 营收同比 (%, 正负)
    "yoy_net_profit",     # 净利同比 (%, 正负)
    "total_assets",       # 期末总资产 (元)
    "operating_cash_flow",# 经营活动现金流净额 (元)
]


@dataclass
class FinancialInput:
    """季报关键数据 (内存表示)."""
    data: dict = field(default_factory=dict)
    source: str = "manual"             # manual / uploaded_pdf / uploaded_excel
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    note: Optional[str] = None

    def is_complete(self) -> bool:
        return all(f in self.data and self.data[f] is not None for f in REQUIRED_FIELDS)

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
    if not verified_by:
        return False, "必须填写 verified_by (审计师签名)"

    if not fin.is_complete():
        missing = [f for f in REQUIRED_FIELDS if f not in fin.data or fin.data[f] is None]
        return False, f"必填字段缺失: {missing}"

    fin.verified_by = verified_by
    fin.verified_at = FinancialInput.now_iso()
    report.financial_input_json = fin.to_json()
    report.financial_input_source = fin.source
    report.financial_input_verified_by = verified_by
    report.financial_input_verified_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("save_financial_input: report=%s verified_by=%s", report.id, verified_by)
    return True, ""
