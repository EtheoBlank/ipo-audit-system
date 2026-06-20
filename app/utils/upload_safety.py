"""Safe file-upload helpers — shared by inventory / sales-ledger / contracts.

Enforces:
- Max upload size (streamed, no full-buffer read until verified)
- Filename sanitisation (strip path separators, control chars, NUL)
- Extension whitelist
- Resolved-path containment check against UPLOAD_DIR
- CSV/XLSX cell-formula injection neutralisation (DDE prefixes)
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Iterable, Optional

from fastapi import HTTPException, UploadFile

from app.core.config import settings

# Cells beginning with any of these are interpreted as formulas by Excel/Numbers
# (CSV injection / DDE attack).  Prepend a single quote to neutralise.
_DDE_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# Allow only the most common alphanumerics / dash / underscore / dot / CJK
# in saved filenames.  Anything else becomes "_".
_FILENAME_KEEP = re.compile(r"[^\w.\-一-龥]+")

# round 31 P1-5: 文件 magic bytes 白名单 — 防止 evil.xlsx.exe / evil.pdf.js
# 这种"双扩展名绕过"攻击. 扩展名易伪造, 文件头 magic bytes 难伪造.
# CSV/TXT 等纯文本格式无固定 magic, 跳过.
_MAGIC_BYTES: dict[str, Optional[bytes]] = {
    ".xlsx": b"PK\x03\x04",          # ZIP (OOXML 本质)
    ".xls":  b"\xD0\xCF\x11\xE0",   # OLE2 Compound Document
    ".docx": b"PK\x03\x04",          # ZIP
    ".doc":  b"\xD0\xCF\x11\xE0",   # OLE2
    ".pdf":  b"%PDF",                # PDF signature
    ".png":  b"\x89PNG\r\n\x1a\n",   # PNG signature
    ".jpg":  b"\xff\xd8\xff",        # JPEG SOI marker
    ".jpeg": b"\xff\xd8\xff",        # JPEG SOI marker
    ".csv":  None,                   # 纯文本, 不查
    ".txt":  None,                   # 纯文本, 不查
}


def sanitize_filename(name: Optional[str], default: str = "upload") -> str:
    """Return a path-safe basename (no separators, no NUL, length <= 240)."""
    if not name:
        return default
    base = Path(name).name  # strip any directory parts
    base = base.replace("\x00", "_")
    base = _FILENAME_KEEP.sub("_", base)
    base = base.strip("._") or default
    return base[:240]


def check_magic_bytes(content: bytes, ext: str) -> bool:
    """round 31 P1-5: 验证 content 头部与 ext 期望一致.

    防止 ``evil.xlsx.exe`` / ``evil.pdf.js`` 等"双扩展名绕过" — 仅校验
    文件名扩展名易被构造, 文件头 magic bytes 难伪造. CSV/TXT 等纯文本格式
    无固定 magic, 跳过检查 (返 True).

    Args:
        content: 文件原始字节 (至少前 4-8 字节, 短文件也算)
        ext: 文件扩展名, 带或不带 ``.`` 都可 (例: ``".xlsx"`` / ``"xlsx"``)

    Returns:
        True 表示通过 (无 magic 检查要求 / 内容与扩展名匹配);
        False 表示拒绝 (内容与扩展名不符, 可能为伪造).
    """
    norm = (ext or "").lower().strip()
    if norm and not norm.startswith("."):
        norm = "." + norm
    expected = _MAGIC_BYTES.get(norm)
    if expected is None:
        # 未在白名单 (csv / txt / 未知扩展) — 跳过检查
        return True
    # content 短于 magic 长度: 文件过小, 拒绝
    if len(content) < len(expected):
        return False
    return content[: len(expected)] == expected


async def read_upload_capped(
    file: UploadFile,
    *,
    max_bytes: int = settings.MAX_UPLOAD_SIZE,
    allowed_exts: Optional[Iterable[str]] = None,
) -> tuple[bytes, str, str]:
    """Stream the upload into memory with a hard cap.

    Returns (content, safe_basename, lower_suffix).
    Raises HTTPException 413 if too big, 400 if extension blocked.
    """
    safe_name = sanitize_filename(file.filename, default="upload")
    suffix = Path(safe_name).suffix.lower()
    if allowed_exts is not None:
        allowed = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in allowed_exts}
        if suffix not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型 {suffix or '(无后缀)'}，仅允许：{sorted(allowed)}",
            )

    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB at a time
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过上限 {max_bytes // 1024 // 1024} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks), safe_name, suffix


def unique_save_path(base_dir: Path, safe_name: str) -> Path:
    """Build a non-clobbering save path INSIDE base_dir (no path traversal).

    Returns ``base_dir / "<ts>_<uuid8>_<safe_name>"``.  Caller must have
    already created ``base_dir``.
    """
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uniq = uuid.uuid4().hex[:8]
    target = base_dir / f"{ts}_{uniq}_{safe_name}"
    base_resolved = base_dir.resolve()
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="非法的文件路径（疑似路径穿越）",
        ) from exc
    return target


def neutralize_formula(value: object) -> object:
    """Prepend a single quote when a string starts with a DDE prefix.

    Use this on every user-supplied string before persisting it to a DataFrame
    that will later be exported back to Excel/CSV.
    """
    if isinstance(value, str) and value and value[0] in _DDE_PREFIXES:
        return "'" + value
    return value


def neutralize_dataframe_strings(df, columns: Optional[Iterable[str]] = None):
    """In-place neutralise DDE prefixes in selected string columns.

    round23 修复: pandas 2.x 默认 StringDtype (kind='O') 与 numpy object dtype
    不严格相等, 旧版 `df[c].dtype == object` 会漏掉 StringDtype 列,
    导致中性化静默失效. 改用 kind in ('O','U','S') 同时覆盖两种 string 表示.
    """
    def _is_string_col(c: str) -> bool:
        if c not in df.columns:
            return False
        kind = df[c].dtype.kind
        return kind in ("O", "U", "S")  # object / unicode / bytes

    cols = list(columns) if columns is not None else [c for c in df.columns if _is_string_col(c)]
    for c in cols:
        if c in df.columns:
            df[c] = df[c].apply(neutralize_formula)
    return df
