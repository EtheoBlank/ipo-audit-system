"""Importer for 收发存 (Inventory Movement) Excel files.

Auto-detects column names from金蝶 / 用友 / SAP / 手工模板. The parser is
intentionally permissive: missing columns are filled with zero rather than
hard-erroring, because real ERP exports often omit batch info or unit price.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from app.utils.upload_safety import check_magic_bytes, neutralize_dataframe_strings

logger = logging.getLogger(__name__)


class InventoryImportError(ValueError):
    """Raised when an inventory file cannot be parsed."""


# 一次导入的最大行数；防 zip-bomb / 千万行 OOM
MAX_IMPORT_ROWS = 1_000_000


# ---- column synonym table ----------------------------------------------
# Each standard field name maps to a list of synonyms (lower-cased, stripped).
COLUMN_SYNONYMS: dict[str, list[str]] = {
    "material_code": [
        "物料编码",
        "物料编号",
        "存货编码",
        "存货编号",
        "产品编码",
        "产品编号",
        "商品编码",
        "商品编号",
        "料号",
        "sku",
        "material code",
        "item code",
        "material no",
        "item no",
        "matnr",
    ],
    "material_name": [
        "物料名称",
        "存货名称",
        "产品名称",
        "商品名称",
        "品名",
        "material name",
        "item name",
        "description",
    ],
    "category": [
        "类别",
        "存货类别",
        "物料类别",
        "分类",
        "存货分类",
        "物料分类",
        "category",
        "item category",
    ],
    "spec": ["规格", "型号", "规格型号", "specification", "spec"],
    "unit": ["单位", "计量单位", "uom", "unit"],
    "warehouse": ["仓库", "仓库名称", "库位", "warehouse", "storage location", "lgort"],
    "batch_no": ["批次", "批号", "批次号", "lot", "batch", "charg"],
    "inbound_date": ["入库日期", "入库时间", "首次入库日期", "首次入库", "inbound date", "in date"],
    "opening_qty": ["期初数量", "期初结存数量", "期初库存数量", "opening qty", "begin qty"],
    "opening_amount": [
        "期初金额",
        "期初结存金额",
        "期初库存金额",
        "opening amount",
        "begin amount",
    ],
    "inbound_qty": [
        "本期入库数量",
        "入库数量",
        "收入数量",
        "本期收入数量",
        "in qty",
        "inbound qty",
    ],
    "inbound_amount": [
        "本期入库金额",
        "入库金额",
        "收入金额",
        "本期收入金额",
        "in amount",
        "inbound amount",
    ],
    "outbound_qty": [
        "本期出库数量",
        "出库数量",
        "发出数量",
        "本期发出数量",
        "out qty",
        "outbound qty",
    ],
    "outbound_amount": [
        "本期出库金额",
        "出库金额",
        "发出金额",
        "本期发出金额",
        "out amount",
        "outbound amount",
    ],
    "ending_qty": [
        "期末数量",
        "期末结存数量",
        "期末库存数量",
        "ending qty",
        "end qty",
        "closing qty",
    ],
    "ending_amount": [
        "期末金额",
        "期末结存金额",
        "期末库存金额",
        "ending amount",
        "end amount",
        "closing amount",
    ],
    "unit_cost": ["期末单价", "加权平均单价", "单价", "成本单价", "unit cost", "unit price"],
}


# ---- helpers -----------------------------------------------------------


def _norm(s: Any) -> str:
    return str(s or "").strip().lower().replace(" ", "")


def _build_header_map(columns: list[str]) -> dict[str, str]:
    """Return {original_col -> standard_field}. Unmapped cols are dropped."""
    out: dict[str, str] = {}
    norm_cols = {_norm(c): c for c in columns}
    for std_field, synonyms in COLUMN_SYNONYMS.items():
        for syn in synonyms:
            key = _norm(syn)
            if key in norm_cols:
                out[norm_cols[key]] = std_field
                break
    return out


def _coerce_num(v: Any) -> float:
    """row-wise 单值数字清洗 (回退用, 已被向量化版本替代).

    round 31 P1-1: ``normalize`` 走 ``pd.to_numeric`` 向量化以提速 10x.
    本函数保留以备单值场景 (例: 单元测试 / 外部脚本) 调用.
    """
    if v is None or pd.isna(v):
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("¥", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _coerce_date(v: Any) -> Optional[pd.Timestamp]:
    if v is None or pd.isna(v) or str(v).strip() == "":
        return None
    try:
        result = pd.to_datetime(v, errors="coerce")
        # round 28 P1-9: NaT 视为解析失败, 返 None (调用方应通过 _DATE_FAIL_HOLDER 记录)
        if result is None or pd.isna(result):
            return None
        return result
    except Exception:
        return None


# round 28 P1-9: 日期解析失败行收集器 (thread-local 单例, 由 parse_bytes() 注入)
# 避免在 pd.DataFrame.apply() 闭包中再传一堆参数。
_DATE_FAIL_HOLDER: list[tuple[int, str]] = []


def get_date_parse_failures() -> list[tuple[int, str]]:
    """取当前 parse_bytes 调用过程中失败的日期行 — (row_idx, raw_value) 元组列表."""
    return list(_DATE_FAIL_HOLDER)


def reset_date_parse_failures() -> None:
    _DATE_FAIL_HOLDER.clear()


# ---- main --------------------------------------------------------------


class InventoryImporter:
    """Parse a 收发存 Excel/CSV file into a normalized DataFrame."""

    REQUIRED = ("material_code", "material_name")

    @classmethod
    def parse_bytes(cls, content: bytes, filename: str) -> pd.DataFrame:
        ext = Path(filename).suffix.lower()
        # round 31 P1-5 防 evil.xlsx.exe 绕过扩展名校验 — 文件头 magic bytes 校验
        if not check_magic_bytes(content, ext):
            raise InventoryImportError(
                f"文件内容与扩展名 {ext or '(无)'} 不匹配, 疑似伪造或损坏文件"
            )
        # round 28 P1-9: 进入解析前重置失败收集器
        reset_date_parse_failures()
        try:
            if ext in (".xlsx", ".xls"):
                # nrows=MAX+1 → 后续可检测是否超限，且 openpyxl 不会一次读到 GB 级
                raw = pd.read_excel(io.BytesIO(content), dtype=str, nrows=MAX_IMPORT_ROWS + 1)
            elif ext == ".csv":
                raw = pd.read_csv(io.BytesIO(content), dtype=str, nrows=MAX_IMPORT_ROWS + 1)
            else:
                raise InventoryImportError(f"不支持的文件类型: {ext}，请上传 .xlsx/.xls/.csv")
        except InventoryImportError:
            raise
        except (ValueError, KeyError, OSError) as exc:
            raise InventoryImportError(f"读取文件失败: {exc}") from exc

        if len(raw) > MAX_IMPORT_ROWS:
            raise InventoryImportError(f"文件行数超过 {MAX_IMPORT_ROWS:,} 行上限，请分批上传")

        return cls.normalize(raw)

    @classmethod
    def normalize(cls, raw: pd.DataFrame) -> pd.DataFrame:
        """Map columns and coerce types. Returns the normalized DataFrame."""
        if raw.empty:
            raise InventoryImportError("文件为空")

        # The header row may not be the first row (ERP often prints title/date).
        # Try (a) pandas-inferred header (i=-1), and (b) the first 5 rows of the body;
        # pick whichever yields the most mapped columns.
        best_map: dict[str, str] = {}
        best_header_row: int = -1  # -1 means use raw.columns as-is
        # First try the columns pandas auto-detected
        mapping = _build_header_map(list(raw.columns.astype(str)))
        if mapping:
            best_map = mapping
            best_header_row = -1
        # Then try the first 5 rows
        for i in range(min(5, len(raw))):
            cols = [str(c) for c in raw.iloc[i].tolist()]
            mapping = _build_header_map(cols)
            if len(mapping) > len(best_map):
                best_map = mapping
                best_header_row = i

        if not best_map:
            raise InventoryImportError(
                "无法识别表头。请确认表头包含 物料编码 / 物料名称 / 期末数量 / 期末金额 等字段"
            )

        if best_header_row >= 0:
            new_cols = [str(c) for c in raw.iloc[best_header_row].tolist()]
            df = raw.iloc[best_header_row + 1 :].copy()
            df.columns = new_cols
        else:
            df = raw.copy()
            df.columns = [str(c) for c in df.columns]

        # Re-derive mapping against the actual column names we just set
        mapping = _build_header_map(list(df.columns))
        df = df.rename(columns=mapping)

        # Required columns
        for required in cls.REQUIRED:
            if required not in df.columns:
                raise InventoryImportError(f"缺少必需列: {required}（物料编码/名称）")

        # Drop rows with empty material_code
        df = df[df["material_code"].astype(str).str.strip() != ""].copy()
        df = df[df["material_code"].astype(str).str.lower() != "nan"].copy()

        # Coerce numeric columns
        num_cols = [
            "opening_qty",
            "opening_amount",
            "inbound_qty",
            "inbound_amount",
            "outbound_qty",
            "outbound_amount",
            "ending_qty",
            "ending_amount",
            "unit_cost",
        ]
        for c in num_cols:
            if c not in df.columns:
                df[c] = 0.0
            else:
                # round 31 P1-1 向量化: ``df[c].apply(_coerce_num)`` 是 row-wise
                # Python callback, 1M 行 ~30s. ``pd.to_numeric(..., errors='coerce')``
                # 走 C 路径 + 统一 NaN 兜底, 同规模 <3s. 字段值可能含 ``,`` / ``¥``
                # / 前后空格, 先 ``str.replace`` 清洗再向量化解析 — 注意 pandas 的
                # ``str.replace`` 也走 C 路径, 整体仍远快于 apply.
                series = df[c].astype(str).str.replace(",", "", regex=False).str.replace("¥", "", regex=False).str.strip()
                df[c] = pd.to_numeric(series, errors="coerce").fillna(0)

        # Coerce date — round 28 P1-9: 解析失败不能静默, 把行号+原值记到 _DATE_FAIL_HOLDER
        if "inbound_date" in df.columns:
            raw_dates = df["inbound_date"]
            # 保留原值用于溯源
            raw_raw = raw_dates.astype(str).tolist()
            df["inbound_date"] = raw_dates.apply(_coerce_date)
            # 扫描失败行: 原值非空但解析后 NaT
            for idx, (parsed, raw_val) in enumerate(zip(df["inbound_date"], raw_raw)):
                raw_str = str(raw_val).strip() if raw_val is not None else ""
                if raw_str and raw_str.lower() != "nan" and (parsed is None or pd.isna(parsed)):
                    _DATE_FAIL_HOLDER.append((idx, raw_str))
        else:
            df["inbound_date"] = None

        # Optional string columns -> empty string
        for c in ("category", "spec", "unit", "warehouse", "batch_no"):
            if c not in df.columns:
                df[c] = ""
            else:
                df[c] = df[c].fillna("").astype(str).str.strip()

        # 防 CSV/Excel 公式注入 (=cmd|'/c calc'!A1 等) — 对所有字符串列做 DDE 前缀清洗
        neutralize_dataframe_strings(
            df,
            columns=[
                "material_code",
                "material_name",
                "category",
                "spec",
                "unit",
                "warehouse",
                "batch_no",
            ],
        )

        # Derive unit_cost when only ending_qty/amount given
        mask = (df["unit_cost"] == 0) & (df["ending_qty"] > 0)
        df.loc[mask, "unit_cost"] = df.loc[mask, "ending_amount"] / df.loc[mask, "ending_qty"]

        # Re-derive ending_amount when only qty + unit_cost given
        mask2 = (df["ending_amount"] == 0) & (df["ending_qty"] > 0) & (df["unit_cost"] > 0)
        df.loc[mask2, "ending_amount"] = df.loc[mask2, "ending_qty"] * df.loc[mask2, "unit_cost"]

        df = df.reset_index(drop=True)
        return df
