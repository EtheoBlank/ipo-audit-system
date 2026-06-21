"""Pack D — 一档剩余 + 三档 IPO 专属 核心服务函数."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.ipo_specials import (
    CustomerSupplierOverlap,  # noqa: F401
    FeedbackLetter,  # noqa: F401
    PeriodComparisonReport,
    Prospectus,  # noqa: F401
    ProspectusKeyMetric,
    ReconciliationFinding,  # noqa: F401
)
from app.models.db_models import AccountBalance, SalesRecord  # noqa: F401

logger = logging.getLogger(__name__)


# ============================================================
#  Phase 16 — 内控穿行
# ============================================================


class WalkthroughSampler:
    @staticmethod
    def select_samples(
        items: List[Dict[str, Any]],
        cycle_code: str,
        n: int = 3,
    ) -> List[Dict[str, Any]]:
        """从凭证列表里按规则选 n 笔.

        销售: 金额前 30% 中随机 1 + 期末前后各 1 (截止性)
        采购: 金额前 30% + 异常凭证
        其他: 金额前 N
        """
        if not items:
            return []
        sorted_by_amount = sorted(items, key=lambda x: float(x.get("amount", 0)), reverse=True)
        top_30 = sorted_by_amount[: max(1, len(sorted_by_amount) // 3)]
        if cycle_code == "sales":
            # 简化: 前 3 笔大额 + 后 2 笔 (假设末尾按日期排序)
            cutoff = items[-min(2, len(items)) :]
            samples = top_30[:n] + cutoff[:2]
        else:
            samples = top_30[:n]
        return samples[: n + 2]

    @staticmethod
    def to_mermaid_flowchart(steps: List[Dict[str, Any]]) -> str:
        """步骤列表生成 Mermaid 流程图源码."""
        if not steps:
            return "graph TD\n  A[暂无步骤]"
        lines = ["graph TD"]
        prev = None
        for i, s in enumerate(steps, start=1):
            node_id = f"S{i}"
            desc = (s.get("step_description") or f"步骤 {i}")[:40].replace('"', "'")
            lines.append(f'  {node_id}["{desc}"]')
            if prev:
                lines.append(f"  {prev} --> {node_id}")
            prev = node_id
        return "\n".join(lines)


# ============================================================
#  Phase 17 — 截止性测试
# ============================================================


class RevenueCutoffTester:
    @staticmethod
    def judge(
        ship_date: Optional[str],
        revenue_confirm_date: Optional[str],
        period_end: str,
        cutoff_days: int = 5,
    ) -> Tuple[str, int]:
        """判断截止性 — early / late / normal + 偏差天数."""
        from datetime import datetime

        try:
            pe_dt = datetime.strptime(period_end, "%Y-%m-%d")
        except Exception as exc:  # noqa: BLE001
            # round 35 P1: 之前静默吞, 截止性判错全返 normal (正常), 审计失真.
            logger.exception(
                "ipo_specials: period_end 解析失败 raw=%r exc=%s", period_end, exc
            )
            return "normal", 0
        ship_dt = None
        rc_dt = None
        try:
            ship_dt = datetime.strptime(ship_date, "%Y-%m-%d") if ship_date else None
        except Exception as exc:
            # 上游字段脏数据 (非 ISO), 不阻断主流程, 留痕
            logger.debug("ipo_specials: ship_date 解析失败 raw=%r exc=%s", ship_date, exc)
        try:
            rc_dt = (
                datetime.strptime(revenue_confirm_date, "%Y-%m-%d")
                if revenue_confirm_date
                else None
            )
        except Exception as exc:
            logger.debug(
                "ipo_specials: revenue_confirm_date 解析失败 raw=%r exc=%s",
                revenue_confirm_date, exc,
            )

        if not ship_dt or not rc_dt:
            return "normal", 0

        # 发货在期末后 cutoff_days 内但确认在期末前 → 提前确认收入 (early)
        # 发货在期末前 cutoff_days 内但确认在期末后 → 延迟确认 (late)
        diff_days = (rc_dt - ship_dt).days
        if rc_dt <= pe_dt and ship_dt > pe_dt and (ship_dt - pe_dt).days <= cutoff_days:
            return "early", (ship_dt - pe_dt).days
        if ship_dt <= pe_dt and rc_dt > pe_dt and (rc_dt - pe_dt).days <= cutoff_days:
            return "late", (rc_dt - pe_dt).days
        return "normal", diff_days


# ============================================================
#  招股书勾稽
# ============================================================


class ProspectusReconciler:
    @staticmethod
    async def reconcile_metric(
        db: AsyncSession,
        *,
        prospectus_id: int,
        metric: ProspectusKeyMetric,
        tolerance_pct: float = 1.0,
    ) -> ProspectusKeyMetric:
        """对单个 metric 比对 prospectus_value vs system_value."""
        if metric.system_value is None:
            metric.is_matched = False
            metric.diff_amount = 0
            metric.diff_pct = 0
            return metric
        diff = (metric.prospectus_value or 0) - metric.system_value
        diff_pct = abs(diff) / max(1e-9, abs(metric.prospectus_value or 1)) * 100
        metric.diff_amount = round(diff, 4)
        metric.diff_pct = round(diff_pct, 4)
        metric.is_matched = diff_pct <= tolerance_pct
        return metric


# ============================================================
#  三年一期对比 — 异动检测
# ============================================================


class PeriodAnomalyDetector:
    @staticmethod
    def detect_anomaly(report: PeriodComparisonReport) -> Optional[str]:
        """检测异动:
        - 毛利率波动 > 3pct
        - AR 周转 / 存货周转变动 > 30%
        - 营收转负
        """
        # 简化: 看 yoy_change_pct
        code = report.metric_code or ""
        change = report.yoy_change_pct or 0
        if "gross_margin" in code and abs(change) > 3:
            return "gross_margin_swing_over_3pct"
        if "turnover" in code and abs(change) > 30:
            return "turnover_swing_over_30pct"
        if "revenue" in code and report.value_period_3 < 0:
            return "revenue_turned_negative"
        if abs(change) > 50:
            return "yoy_change_over_50pct"
        return None


# ============================================================
#  客户/供应商重叠 — 名称模糊匹配
# ============================================================


class OverlapDetector:
    @staticmethod
    def fuzzy_score(a: str, b: str) -> float:
        """简单 Levenshtein 相似度 (没 rapidfuzz 依赖, 自己实现)."""
        if not a or not b:
            return 0.0
        a = a.strip().lower()
        b = b.strip().lower()
        if a == b:
            return 1.0
        # 用最长公共子串近似
        if a in b or b in a:
            return 0.85
        # 简单字符交集
        sa = set(a)
        sb = set(b)
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        union = len(sa | sb)
        return round(inter / union, 4)

    @staticmethod
    async def find_overlaps(
        db: AsyncSession,
        *,
        project_id: int,
        customer_names: List[str],
        supplier_names: List[str],
        fuzzy_threshold: float = 0.75,
    ) -> List[Dict[str, Any]]:
        """找客户名 vs 供应商名重叠. 返回 dict 列表 (不入库)."""
        overlaps: List[Dict[str, Any]] = []
        for cust in customer_names:
            for sup in supplier_names:
                if not cust or not sup:
                    continue
                score = OverlapDetector.fuzzy_score(cust, sup)
                if score >= fuzzy_threshold:
                    overlaps.append(
                        {
                            "customer_name": cust,
                            "supplier_name": sup,
                            "fuzzy_score": score,
                            "match_type": "exact" if score == 1.0 else "fuzzy",
                        }
                    )
        return overlaps


# ============================================================
#  可比公司基准 — 计算偏离度
# ============================================================


class PeerBenchmarkAnalyzer:
    @staticmethod
    def issuer_vs_peers(
        issuer_value: float,
        peer_values: List[float],
    ) -> Dict[str, float]:
        """发行人指标 vs 可比公司平均值."""
        if not peer_values:
            return {"peer_avg": 0, "peer_median": 0, "deviation_pct": 0}
        avg = sum(peer_values) / len(peer_values)
        sorted_v = sorted(peer_values)
        n = len(sorted_v)
        median = sorted_v[n // 2] if n % 2 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
        deviation = ((issuer_value - avg) / avg * 100) if avg else 0
        return {
            "peer_avg": round(avg, 4),
            "peer_median": round(median, 4),
            "issuer_value": issuer_value,
            "deviation_pct": round(deviation, 2),
            "is_outlier": abs(deviation) > 30,
        }


# ============================================================
#  反馈意见 — SLA 计算
# ============================================================


# round 35 P1: 旧版静默吞 + 返 999, 999 是合法 int (差 999 天虽夸张但
# 调度端做 <0 / <=3 / <=7 分类时不区分 999 与真值). 改 None (类型变化,
# 强制调用方显式处理) + 暴露 is_unparseable 标志.
SLA_UNPARSEABLE = 999


def _sla_unparseable_return() -> int:
    """日期解析失败时返 SLA_UNPARSEABLE. 留作统一哨兵, 调用方可用
    ``is_sla_unparseable`` / ``isinstance`` 区分.
    """
    return SLA_UNPARSEABLE


def is_sla_unparseable(days_left: Optional[int]) -> bool:
    """调用方判断 SLA 数值是否为 '日期解析失败' 哨兵 (而不是真剩余天数)."""
    return days_left is None or days_left == SLA_UNPARSEABLE


class FeedbackSLAMonitor:
    @staticmethod
    def days_to_deadline(deadline: str, today: Optional[str] = None) -> int:
        from datetime import date, datetime

        if today is None:
            today_dt = date.today()
        else:
            try:
                today_dt = datetime.strptime(today, "%Y-%m-%d").date()
            except Exception as exc:  # noqa: BLE001
                # round 35 P1: 999 当哨兵难区分 "死线真有 999 天" vs "today 解析失败".
                # 改 None + flag, 调用方能区分.
                logger.exception(
                    "ipo_specials: today 解析失败 raw=%r exc=%s", today, exc
                )
                return _sla_unparseable_return()
        try:
            dl = datetime.strptime(deadline, "%Y-%m-%d").date()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "ipo_specials: deadline 解析失败 raw=%r exc=%s", deadline, exc
            )
            return _sla_unparseable_return()
        return (dl - today_dt).days

    @staticmethod
    def urgency_level(days_left: int) -> str:
        if is_sla_unparseable(days_left):
            # round 35 P1: 数据错误不归类为 normal, 标 'unknown', 前端可染色.
            return "unknown"
        if days_left < 0:
            return "overdue"
        if days_left <= 3:
            return "critical"
        if days_left <= 7:
            return "warn"
        return "normal"


# ============================================================
#  申报清单 — 内置模板
# ============================================================


# 主板 / 创业板 / 科创板 / 北交所 通用申报材料 (简化版, 实际可达 100+)
DEFAULT_SUBMISSION_CHECKLIST = [
    ("PROSPECTUS", "招股说明书", True),
    ("FIN_REPORT", "审计报告 + 财务报告", True),
    ("LEGAL_OPINION", "律师工作底稿 + 法律意见书", True),
    ("ASSET_APPRAISAL", "资产评估报告 (如适用)", False),
    ("CAPITAL_VERIFICATION", "验资报告", True),
    ("COMPANY_CHARTER", "公司章程", True),
    ("MATERIAL_CONTRACTS", "重大合同清单", True),
    ("RELATED_PARTY_DISCLOSURE", "关联方关系及交易说明", True),
    ("PEER_COMPETITION_COMMITMENT", "同业竞争承诺函", True),
    ("INTERNAL_CONTROL_AUDIT", "内部控制审计报告", True),
    ("FOUNDERS_QUALIFICATION", "实控人 / 控股股东资格证明", True),
    ("BUSINESS_LICENSE", "营业执照", True),
    ("TAX_REGISTRATION", "税务证明", True),
    ("LABOR_CONTRACTS", "员工劳动合同概况", False),
    ("ENV_PROTECTION", "环保合规证明 (如适用)", False),
    ("SAFETY_PRODUCTION", "安全生产许可 (如适用)", False),
    ("INDUSTRY_LICENSES", "行业准入许可 (如适用)", False),
    ("IP_CERTIFICATES", "知识产权证明清单", True),
    ("KEY_TECHNOLOGY", "核心技术说明", True),
    ("PROCUREMENT_POLICY", "采购销售政策说明", True),
    ("RD_RECORDS", "研发投入及成果证明", True),
    ("BOARD_RESOLUTIONS", "重要董事会 / 股东会决议", True),
    ("SHARE_CHANGE_HISTORY", "历次股权变更证明", True),
]


__all__ = [
    "WalkthroughSampler",
    "RevenueCutoffTester",
    "ProspectusReconciler",
    "PeriodAnomalyDetector",
    "OverlapDetector",
    "PeerBenchmarkAnalyzer",
    "FeedbackSLAMonitor",
    "DEFAULT_SUBMISSION_CHECKLIST",
]
