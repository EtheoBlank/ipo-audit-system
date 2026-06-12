"""报告模板服务 (Pack A — Roadmap Phase 20).

事务所自定义品牌报告渲染器:
  - 上传 .docx / .xlsx 模板, 内嵌 ``${placeholder}`` 占位符
  - 解析 placeholder 列表 (前端预览, 提示用户应注入哪些 context key)
  - 渲染 (context dict → 替换占位符 → 输出 bytes)

降级路径:
  - python-docx 不可用时仍能 list 模板, 但 render 抛错
  - 用户传 context 中缺 placeholder 时, 默认替换为 ``[未填: name]`` 而非报错
  - 严格模式可通过 ``strict=True`` 抛错
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.report_template import (
    REPORT_FORMAT_DOCX,
    REPORT_FORMAT_XLSX,
    ReportRenderHistory,
    ReportTemplate,
)

logger = logging.getLogger(__name__)


# placeholder 语法: ${name} 或 ${section.field}
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.\-]*)\}")


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class TemplateAnalysis:
    placeholders: List[str]
    duplicates: List[str]
    unknown_tags: List[str]
    is_valid: bool
    suggested_context_keys: Dict[str, str]


def _extract_docx_text(template_bytes: bytes) -> str:
    """从 .docx 提取所有可见文本 (用于扫 placeholder)."""
    try:
        with zipfile.ZipFile(io.BytesIO(template_bytes)) as zf:
            text_chunks: List[str] = []
            for name in zf.namelist():
                if not (name.startswith("word/") and name.endswith(".xml")):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                # 简化: 去 XML tag 取纯文本
                stripped = re.sub(r"<[^>]+>", " ", content)
                text_chunks.append(stripped)
            return " ".join(text_chunks)
    except zipfile.BadZipFile:
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 docx 失败: %s", exc)
        return ""


def _extract_xlsx_text(template_bytes: bytes) -> str:
    """从 .xlsx 提取 sharedStrings + sheet XML 文本."""
    try:
        with zipfile.ZipFile(io.BytesIO(template_bytes)) as zf:
            text_chunks: List[str] = []
            for name in zf.namelist():
                if not (
                    name.startswith("xl/sharedStrings.xml")
                    or name.startswith("xl/worksheets/")
                ):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                stripped = re.sub(r"<[^>]+>", " ", content)
                text_chunks.append(stripped)
            return " ".join(text_chunks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 xlsx 失败: %s", exc)
        return ""


def analyze_template(template_bytes: bytes, output_format: str) -> TemplateAnalysis:
    """探测模板里的 placeholder, 给前端预览用."""
    if output_format == REPORT_FORMAT_DOCX:
        text = _extract_docx_text(template_bytes)
    elif output_format == REPORT_FORMAT_XLSX:
        text = _extract_xlsx_text(template_bytes)
    else:
        text = template_bytes.decode("utf-8", errors="ignore")

    matches = _PLACEHOLDER_RE.findall(text or "")
    placeholders = sorted(set(matches))
    duplicates = sorted({n for n in matches if matches.count(n) > 1})

    # 常见 placeholder 提示 → context key 建议
    suggested = {
        "company_name": "公司名称 (str)",
        "fiscal_year": "会计年度 (int)",
        "report_date": "报告日期 (YYYY-MM-DD)",
        "firm_name": "事务所名称 (str)",
        "signing_partner": "签字合伙人 (str)",
        "project_name": "项目名称 (str)",
        "total_assets": "资产总计 (float)",
        "net_profit": "净利润 (float)",
    }
    suggestions_hit = {k: v for k, v in suggested.items() if k in placeholders}

    return TemplateAnalysis(
        placeholders=placeholders,
        duplicates=duplicates,
        unknown_tags=[],
        is_valid=bool(placeholders) or True,  # 允许无 placeholder 的纯模板
        suggested_context_keys=suggestions_hit,
    )


def _flatten_context(ctx: Dict[str, Any], parent: str = "") -> Dict[str, str]:
    """嵌套 dict 拍平成 ``a.b.c`` -> 值."""
    out: Dict[str, str] = {}
    for k, v in (ctx or {}).items():
        key = f"{parent}.{k}" if parent else str(k)
        if isinstance(v, dict):
            out.update(_flatten_context(v, key))
        elif v is None:
            out[key] = ""
        else:
            out[key] = str(v)
    return out


def _render_placeholder_in_text(
    text: str, flat_ctx: Dict[str, str], strict: bool = False
) -> str:
    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name in flat_ctx:
            return flat_ctx[name]
        if strict:
            raise KeyError(f"模板需要 ${{{name}}}, 但 context 未提供")
        return f"[未填:{name}]"

    return _PLACEHOLDER_RE.sub(_sub, text)


def render_docx(
    template_bytes: bytes,
    context: Dict[str, Any],
    *,
    strict: bool = False,
) -> bytes:
    """渲染 .docx — 直接改 zip 里 word/*.xml 的文本."""
    flat = _flatten_context(context or {})
    try:
        in_buf = io.BytesIO(template_bytes)
        out_buf = io.BytesIO()
        with zipfile.ZipFile(in_buf, "r") as zin, zipfile.ZipFile(
            out_buf, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                    try:
                        text = data.decode("utf-8")
                        if "${" in text:
                            text = _render_placeholder_in_text(text, flat, strict=strict)
                        data = text.encode("utf-8")
                    except Exception as exc:  # noqa: BLE001
                        if strict:
                            raise
                        logger.warning("渲染 %s 时跳过: %s", item.filename, exc)
                zout.writestr(item, data)
        return out_buf.getvalue()
    except zipfile.BadZipFile as exc:
        raise ValueError("模板文件不是合法的 docx (zip 损坏)") from exc


def render_xlsx(
    template_bytes: bytes,
    context: Dict[str, Any],
    *,
    strict: bool = False,
) -> bytes:
    """渲染 .xlsx — 改 sharedStrings + sheets XML."""
    flat = _flatten_context(context or {})
    try:
        in_buf = io.BytesIO(template_bytes)
        out_buf = io.BytesIO()
        with zipfile.ZipFile(in_buf, "r") as zin, zipfile.ZipFile(
            out_buf, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename in {"xl/sharedStrings.xml"} or item.filename.startswith(
                    "xl/worksheets/"
                ):
                    try:
                        text = data.decode("utf-8")
                        if "${" in text:
                            text = _render_placeholder_in_text(text, flat, strict=strict)
                        data = text.encode("utf-8")
                    except Exception as exc:  # noqa: BLE001
                        if strict:
                            raise
                        logger.warning("渲染 %s 时跳过: %s", item.filename, exc)
                zout.writestr(item, data)
        return out_buf.getvalue()
    except zipfile.BadZipFile as exc:
        raise ValueError("模板文件不是合法的 xlsx (zip 损坏)") from exc


class ReportTemplateService:
    """高层 CRUD + 渲染编排."""

    @staticmethod
    async def list_templates(
        db: AsyncSession,
        *,
        firm_id: Optional[int] = None,
        report_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[int, List[ReportTemplate]]:
        from sqlalchemy import func as _func

        conds = []
        if firm_id is not None:
            conds.append(ReportTemplate.firm_id == firm_id)
        if report_type:
            conds.append(ReportTemplate.report_type == report_type)
        if is_active is not None:
            conds.append(ReportTemplate.is_active == is_active)
        where = and_(*conds) if conds else None
        count_stmt = select(_func.count(ReportTemplate.id))
        if where is not None:
            count_stmt = count_stmt.where(where)
        total = int((await db.execute(count_stmt)).scalar_one() or 0)

        stmt = select(ReportTemplate)
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.order_by(desc(ReportTemplate.updated_at)).offset(
            max(0, int(skip))
        ).limit(max(1, min(500, int(limit))))
        items = list((await db.execute(stmt)).scalars().all())
        return total, items

    @staticmethod
    async def get(db: AsyncSession, template_id: int) -> Optional[ReportTemplate]:
        return (
            await db.execute(select(ReportTemplate).where(ReportTemplate.id == template_id))
        ).scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        template_code: str,
        template_name: str,
        report_type: str,
        output_format: str,
        template_bytes: bytes,
        template_filename: str,
        version: str = "v1",
        description: Optional[str] = None,
        firm_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
        created_by_display: Optional[str] = None,
    ) -> ReportTemplate:
        analysis = analyze_template(template_bytes, output_format)
        sha = hashlib.sha256(template_bytes).hexdigest()
        tpl = ReportTemplate(
            firm_id=firm_id,
            template_code=template_code,
            template_name=template_name,
            report_type=report_type,
            version=version,
            output_format=output_format,
            description=description,
            placeholder_schema=",".join(analysis.placeholders) if analysis.placeholders else None,
            template_bytes=template_bytes,
            template_filename=template_filename,
            template_size=len(template_bytes),
            template_sha256=sha,
            is_active=True,
            is_builtin=False,
            created_by_user_id=created_by_user_id,
            created_by_display=created_by_display,
            created_at=_utcnow_naive(),
            updated_at=_utcnow_naive(),
        )
        db.add(tpl)
        await db.commit()
        await db.refresh(tpl)
        return tpl

    @staticmethod
    async def update(
        db: AsyncSession,
        *,
        template_id: int,
        template_name: Optional[str] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[ReportTemplate]:
        tpl = await ReportTemplateService.get(db, template_id)
        if tpl is None:
            return None
        if template_name is not None:
            tpl.template_name = template_name
        if description is not None:
            tpl.description = description
        if is_active is not None:
            tpl.is_active = is_active
        tpl.updated_at = _utcnow_naive()
        await db.commit()
        await db.refresh(tpl)
        return tpl

    @staticmethod
    async def delete(db: AsyncSession, template_id: int) -> bool:
        tpl = await ReportTemplateService.get(db, template_id)
        if tpl is None:
            return False
        await db.delete(tpl)
        await db.commit()
        return True

    @staticmethod
    async def render(
        db: AsyncSession,
        *,
        template_id: int,
        context: Dict[str, Any],
        project_id: Optional[int] = None,
        output_filename: Optional[str] = None,
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
        strict: bool = False,
    ) -> Tuple[bytes, str, ReportRenderHistory]:
        tpl = await ReportTemplateService.get(db, template_id)
        if tpl is None:
            raise ValueError(f"模板 id={template_id} 不存在")
        if not tpl.is_active:
            raise ValueError(f"模板 id={template_id} 已停用")
        try:
            if tpl.output_format == REPORT_FORMAT_DOCX:
                rendered = render_docx(tpl.template_bytes, context, strict=strict)
                ext = ".docx"
            elif tpl.output_format == REPORT_FORMAT_XLSX:
                rendered = render_xlsx(tpl.template_bytes, context, strict=strict)
                ext = ".xlsx"
            else:
                raise ValueError(f"暂不支持 output_format={tpl.output_format}")
        except Exception as exc:  # noqa: BLE001
            history = ReportRenderHistory(
                template_id=tpl.id,
                project_id=project_id,
                output_filename=output_filename or f"render_failed{datetime.now().strftime('%Y%m%d%H%M%S')}",
                output_size=0,
                success=False,
                error_msg=str(exc)[:2000],
                rendered_by_user_id=user_id,
                rendered_by_display=user_display,
                created_at=_utcnow_naive(),
            )
            db.add(history)
            await db.commit()
            raise

        out_name = output_filename or f"{tpl.template_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        if not out_name.endswith(ext):
            out_name = out_name + ext

        history = ReportRenderHistory(
            template_id=tpl.id,
            project_id=project_id,
            output_filename=out_name,
            output_size=len(rendered),
            success=True,
            context_snapshot=str(context)[:4000] if context else None,
            rendered_by_user_id=user_id,
            rendered_by_display=user_display,
            created_at=_utcnow_naive(),
        )
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return rendered, out_name, history


__all__ = [
    "TemplateAnalysis",
    "analyze_template",
    "render_docx",
    "render_xlsx",
    "ReportTemplateService",
]
