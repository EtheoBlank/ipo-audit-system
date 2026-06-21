"""字段映射引擎。

把模板中 `workpaper:<dataset>.<path>` 形式的字段从项目数据中抽取并填值。

设计要点：
- 注册表模式：内置一组常用 dataset 的解析器（project / account_balance /
  ar_ledger / ap_ledger / confirmation / trial_balance / sales_ledger / revenue_contract），
  外部可通过 `register()` 扩展
- 解析器接口统一：``ResolverCallable = Callable[[DataPath, WorkpaperDataContext], object]``
- 解析失败不抛异常，返回 ``None``，由后续问答引擎兜底
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional

import pandas as pd

from app.services.comprehensive.schemas import FillResult, TemplateField

logger = logging.getLogger(__name__)


# ----------------------------- 数据上下文 -----------------------------


@dataclass
class WorkpaperDataContext:
    """综合底稿自动填充所需的全部基础数据。"""

    project: Any = None  # app.models.db_models.Project ORM 对象
    account_balances: Optional[pd.DataFrame] = None  # 科目余额表
    chronological: Optional[pd.DataFrame] = None  # 序时账
    confirmation_cases: Optional[list[Any]] = None  # ConfirmationCase 列表
    sales_ledger: Optional[pd.DataFrame] = None  # 销售清单
    trial_balance: Optional[pd.DataFrame] = None  # 试算平衡
    contracts: Optional[list[Any]] = None  # 合同文档
    inventory: Optional[pd.DataFrame] = None  # 存货明细

    # 自由扩展字段（防止新增 dataset 时改 dataclass）
    extra: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        """按 dataset 名取数据，未知数据集返回 None。"""
        if hasattr(self, name):
            return getattr(self, name)
        return self.extra.get(name)


# ----------------------------- 数据路径 -----------------------------


@dataclass(frozen=True)
class DataPath:
    """``workpaper:dataset.path.to.value`` 解析后的路径对象。"""

    dataset: str
    parts: tuple[str, ...]

    @property
    def leaf(self) -> str:
        return self.parts[-1] if self.parts else ""

    def __str__(self) -> str:
        return f"{self.dataset}." + ".".join(self.parts)


def parse_workpaper_source(source: str) -> Optional[DataPath]:
    """解析形如 ``workpaper:ar_ledger.total_ending`` 的 source 字符串。

    失败返回 None（说明这个 source 不是 workpaper 类型）。
    """
    if not source.startswith("workpaper:"):
        return None
    body = source[len("workpaper:") :]
    if not body:
        return None
    parts = body.split(".")
    return DataPath(dataset=parts[0], parts=tuple(parts[1:]))


# ----------------------------- 解析器类型 -----------------------------

ResolverCallable = Callable[[DataPath, WorkpaperDataContext], Any]


class MappingError(Exception):
    """字段映射失败。"""


class FieldMapper:
    """字段映射引擎。"""

    def __init__(self):
        self._resolvers: dict[str, ResolverCallable] = {}
        self._register_builtin()

    # ---------- 注册管理 ----------

    def register(self, dataset: str, resolver: ResolverCallable) -> None:
        """注册某个 dataset 的解析器。"""
        self._resolvers[dataset] = resolver
        logger.debug("注册 workpaper 解析器: %s", dataset)

    # ---------- 公共 API ----------

    def map_field(
        self,
        field_def: TemplateField,
        ctx: WorkpaperDataContext,
    ) -> FillResult:
        """解析单个字段的 workpaper source 并填值。"""
        path = parse_workpaper_source(field_def.source)
        if path is None:
            return FillResult(
                field_id=field_def.field_id,
                value=None,
                source_used=f"workpaper:unrecognized({field_def.source})",
                confidence=0.0,
                citation="source 格式非法",
            )

        resolver = self._resolvers.get(path.dataset)
        if resolver is None:
            return FillResult(
                field_id=field_def.field_id,
                value=None,
                source_used=f"workpaper:{path.dataset}",
                confidence=0.0,
                citation=f"未知数据集 '{path.dataset}'，请注册解析器",
            )

        try:
            raw = resolver(path, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("字段 '%s' 解析失败: %s", field_def.field_id, exc)
            return FillResult(
                field_id=field_def.field_id,
                value=None,
                source_used=f"workpaper:{path}",
                confidence=0.0,
                citation=f"解析异常: {exc}",
            )

        value = self._coerce(raw, field_def.type)
        if value is None:
            return FillResult(
                field_id=field_def.field_id,
                value=None,
                source_used=f"workpaper:{path}",
                confidence=0.0,
                citation="数据源返回空值，将由其他引擎/问答补全",
            )

        return FillResult(
            field_id=field_def.field_id,
            value=value,
            source_used=f"workpaper:{path}",
            confidence=0.95,
            citation=f"基础底稿 / {path.dataset}",
        )

    def map_all(
        self,
        fields: list[TemplateField],
        ctx: WorkpaperDataContext,
    ) -> list[FillResult]:
        """批量解析所有 workpaper 类型字段。"""
        results = []
        for f in fields:
            if not f.source.startswith("workpaper:"):
                continue
            results.append(self.map_field(f, ctx))
        return results

    # ---------- 类型适配 ----------

    @staticmethod
    def _coerce(value: Any, target_type: str) -> Any:
        """把原始值适配到模板字段的目标类型。"""
        if value is None:
            return None
        try:
            if target_type == "number":
                if isinstance(value, (int, float)):
                    return float(value)
                return float(str(value).replace(",", ""))
            if target_type == "percent":
                if isinstance(value, (int, float)):
                    return float(value)
                s = str(value).rstrip("%").strip()
                return float(s)
            if target_type == "date":
                if isinstance(value, date):
                    return value.isoformat()
                return str(value)
            if target_type == "boolean":
                if isinstance(value, bool):
                    return value
                return str(value).strip().lower() in ("true", "1", "yes", "y", "是")
            if target_type in ("text", "text_long"):
                return str(value).strip()
            if target_type == "choice":
                return str(value).strip()
        except (ValueError, TypeError):
            return None
        return value

    # ---------- 内置解析器 ----------

    def _register_builtin(self) -> None:
        self.register("project", _resolve_project)
        self.register("account_balance", _resolve_account_balance)
        self.register("ar_ledger", _resolve_ar_ledger)
        self.register("ap_ledger", _resolve_ap_ledger)
        self.register("inventory", _resolve_inventory)
        self.register("confirmation", _resolve_confirmation)
        self.register("trial_balance", _resolve_trial_balance)
        self.register("sales_ledger", _resolve_sales_ledger)
        self.register("revenue_contract", _resolve_revenue_contract)


# ============================================================
# 内置解析器实现
# ============================================================


def _resolve_project(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """从 Project ORM 取字段。"""
    if ctx.project is None:
        return None
    leaf = path.leaf

    # 派生字段优先
    if leaf == "audit_period" and ctx.project.fiscal_year:
        fy = ctx.project.fiscal_year
        # ALG-02 (round32, 2026-06-20): 优先使用 ORM 显式 fiscal_year_start/end 字段
        # (覆盖非自然年项目, 如 FY2024 = 2023-07-01~2024-06-30);
        # 字段缺失时才 fallback 到自然年 (01-01~12-31).
        fy_start = getattr(ctx.project, "fiscal_year_start", None)
        fy_end = getattr(ctx.project, "fiscal_year_end", None)
        if fy_start and fy_end:
            return f"{fy_start}~{fy_end}"
        if fy_start and not fy_end:
            # 仅给 start 时, end 按 start + 365 天推算
            try:
                end_dt = pd.to_datetime(fy_start) + pd.Timedelta(days=365)
                return f"{fy_start}~{end_dt.strftime('%Y-%m-%d')}"
            except Exception:
                logger.warning(
                    "audit_period: fiscal_year_start=%s 无法解析, fallback 自然年", fy_start,
                )
        elif fy_end and not fy_start:
            try:
                start_dt = pd.to_datetime(fy_end) - pd.Timedelta(days=365)
                return f"{start_dt.strftime('%Y-%m-%d')}~{fy_end}"
            except Exception:
                logger.warning(
                    "audit_period: fiscal_year_end=%s 无法解析, fallback 自然年", fy_end,
                )
        # 自然年兜底 (旧逻辑) + is_assumption 警告
        logger.warning(
            "audit_period: project.fiscal_year_start/end 未设置, 假设自然年 %d-01-01~%d-12-31 "
            "(is_assumption=True). 非自然年项目请显式设置 fiscal_year_start/end.",
            fy, fy,
        )
        return f"{fy}-01-01~{fy}-12-31"

    # 优先用 ORM 字段名（小写），其次用一些常用别名
    aliases = {
        "company_name": "company_name",
        "name": "name",
        "industry": "industry",
        "fiscal_year": "fiscal_year",
        "status": "status",
    }
    attr = aliases.get(leaf, leaf)
    if not hasattr(ctx.project, attr):
        return None
    return getattr(ctx.project, attr)


def _resolve_account_balance(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """科目余额表聚合。支持的 path 示例：
    - account_balance.1122.ending_balance   （按科目编码取期末余额）
    - account_balance.1122.*                （整行）
    - account_balance.total_debit           （全表借方合计）
    """
    df = ctx.account_balances
    if df is None or df.empty:
        return None

    if not path.parts:
        return None

    head = path.parts[0]
    # 数字开头 → 视为科目编码
    if head.isdigit():
        if len(path.parts) == 1:
            return df[df["account_code"] == head]
        col = path.parts[1]
        if col not in df.columns:
            return None
        rows = df[df["account_code"] == head]
        if rows.empty:
            return None
        return rows.iloc[0][col]
    # 聚合函数 total_X → 列 X 或 X_amount
    if head.startswith("total_"):
        base = head.removeprefix("total_")
        col = base if base in df.columns else f"{base}_amount"
        if col in df.columns:
            return float(df[col].sum())
    return None


def _resolve_ar_ledger(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """应收账款明细（科目 1122）。"""
    return _resolve_ledger(path, ctx, account_prefix="1122")


def _resolve_ap_ledger(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """应付账款明细（科目 2202）。"""
    return _resolve_ledger(path, ctx, account_prefix="2202")


def _resolve_inventory(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """存货明细（科目 1401/1403/1405）。"""
    df = ctx.inventory if ctx.inventory is not None else ctx.account_balances
    if df is None or df.empty:
        return None
    if not path.parts:
        return None
    head = path.parts[0]
    if head in ("ending", "ending_balance"):
        return float(
            df[df["account_code"].str.startswith(("1401", "1403", "1405"))]["ending_balance"].sum()
        )
    return None


def _resolve_ledger(path: DataPath, ctx: WorkpaperDataContext, account_prefix: str) -> Any:
    """通用明细账解析。"""
    df = ctx.account_balances
    if df is None or df.empty:
        return None
    if not path.parts:
        return None

    # path.parts[0] 期望为聚合名（total_ending / total_beginning / turnover_days / count）
    fn = path.parts[0]
    sub = df[df["account_code"].str.startswith(account_prefix)]
    if sub.empty:
        return None
    if fn == "total_ending":
        return float(sub["ending_balance"].sum())
    if fn == "total_beginning":
        return float(sub["beginning_balance"].sum())
    if fn == "total_debit":
        return float(sub["debit_amount"].sum())
    if fn == "total_credit":
        return float(sub["credit_amount"].sum())
    if fn == "count":
        return int(len(sub))
    if fn == "turnover_days":
        # 周转天数 = period_days × 平均应收余额 / 赊销收入净额
        # 优先使用 credit_sales（赊销收入），缺省时回退到 revenue（营业收入），
        # 两种口径都通过 ctx.extra 注入，使用方需在 hint 中说明
        # ALG-03 (round32, 2026-06-20): 不再硬编码 365, 改用 ctx.extra.period_days
        # (或顶层 ctx.period_days), 兼容季报/中期/非自然年; 缺省时 fallback 365.
        revenue = ctx.extra.get("credit_sales") or ctx.extra.get("revenue")
        if not revenue or revenue == 0:
            return None
        avg = (sub["ending_balance"].sum() + sub["beginning_balance"].sum()) / 2
        if avg == 0:
            return None
        period_days = ctx.extra.get("period_days") or getattr(ctx, "period_days", None) or 365
        try:
            period_days = float(period_days)
        except (TypeError, ValueError):
            period_days = 365.0
        return round(period_days * avg / float(revenue), 2)
    return None


def _resolve_confirmation(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """函证结果聚合。

    提供三类口径（按审计准则第 1312 号 / ISA 505）：
      - coverage       发函金额 / 函证样本余额（衡量抽样充分性）
      - response_rate  已回函金额 / 发函金额（衡量回函充分性）
      - agreement_rate 已相符金额 / 已回函金额（衡量金额相符性）
    """
    cases = ctx.confirmation_cases or []
    if not cases:
        return None
    if not path.parts:
        return None
    fn = path.parts[0]
    if fn == "count":
        return len(cases)
    if fn == "coverage":
        # 分子：发函金额（sent_amount）；分母：函证样本余额
        sent_amount = sum(
            getattr(c, "sent_amount", 0) or 0
            for c in cases
            if getattr(c, "status", "") not in ("cancelled",)
        )
        sample_balance = sum(getattr(c, "sample_balance", 0) or 0 for c in cases)
        if sample_balance == 0:
            return None
        return round(sent_amount / sample_balance, 4)
    if fn == "response_rate":
        sent = sum(getattr(c, "sent_amount", 0) or 0 for c in cases)
        replied = sum(
            getattr(c, "confirmed_amount", 0) or 0
            for c in cases
            if getattr(c, "status", "") in ("confirmed", "replied", "agreed", "disputed")
        )
        if sent == 0:
            return None
        return round(replied / sent, 4)
    if fn == "agreement_rate":
        replied = sum(
            getattr(c, "confirmed_amount", 0) or 0
            for c in cases
            if getattr(c, "status", "") in ("confirmed", "replied", "agreed", "disputed")
        )
        agreed = sum(
            getattr(c, "confirmed_amount", 0) or 0
            for c in cases
            if getattr(c, "status", "") in ("confirmed", "agreed")
        )
        if replied == 0:
            return None
        return round(agreed / replied, 4)
    if fn == "agreed":
        return sum(1 for c in cases if getattr(c, "status", "") in ("confirmed", "agreed"))
    if fn == "disputed":
        return sum(1 for c in cases if getattr(c, "status", "") in ("disputed", "disagree"))
    if fn == "no_reply":
        return sum(1 for c in cases if getattr(c, "status", "") in ("no_reply", "pending"))
    return None


def _resolve_trial_balance(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """试算平衡表。"""
    df = ctx.trial_balance
    if df is None or df.empty:
        return None
    if not path.parts:
        return None
    head = path.parts[0]
    if head in df.columns:
        return float(df[head].sum()) if df[head].dtype != "O" else df[head].iloc[0]
    return None


def _resolve_sales_ledger(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """销售清单。"""
    df = ctx.sales_ledger
    if df is None or df.empty:
        return None
    if not path.parts:
        return None
    head = path.parts[0]
    if head == "total_revenue":
        return float(df["amount"].sum()) if "amount" in df.columns else None
    if head == "customer_count":
        return int(df["customer"].nunique()) if "customer" in df.columns else None
    if head == "top_customer_share":
        if "customer" not in df.columns or "amount" not in df.columns:
            return None
        total = df["amount"].sum()
        if total == 0:
            return None
        top = df.groupby("customer")["amount"].sum().max()
        return round(float(top) / float(total), 4)
    return None


def _resolve_revenue_contract(path: DataPath, ctx: WorkpaperDataContext) -> Any:
    """收入合同分析结果。"""
    contracts = ctx.contracts or []
    if not contracts:
        return None
    if not path.parts:
        return None
    fn = path.parts[0]
    if fn == "count":
        return len(contracts)
    if fn == "high_risk_count":
        return sum(1 for c in contracts if getattr(c, "risk_level", "") == "高")
    return None
