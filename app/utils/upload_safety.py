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


def sanitize_filename(name: Optional[str], default: str = "upload") -> str:
    """Return a path-safe basename (no separators, no NUL, length <= 240)."""
    if not name:
        return default
    base = Path(name).name        # strip any directory parts
    base = base.replace("\x00", "_")
    base = _FILENAME_KEEP.sub("_", base)
    base = base.strip("._") or default
    return base[:240]


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
    """In-place neutralise DDE prefixes in selected string columns."""
    cols = list(columns) if columns is not None else [c for c in df.columns if df[c].dtype == object]
    for c in cols:
        if c in df.columns:
            df[c] = df[c].apply(neutralize_formula)
    return df
