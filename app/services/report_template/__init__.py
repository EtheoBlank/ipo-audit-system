"""报告模板服务 (Pack A — Roadmap Phase 20).

事务所自定义品牌报告渲染器:
  - 上传 .docx / .xlsx 模板, 内嵌 ``${placeholder}`` 占位符
  - 解析 placeholder 列表 (前端预览, 提示用户应注入哪些 context key)
  - 渲染 (context dict → 替换占位符 → 输出 bytes)

降级路径:
  - python-docx 不可用时仍能 list 模板, 但 render 抛错
  - 用户传 context 中缺 placeholder 时, 默认替换为 ``[未填: name]`` 而非报错
  - 严格模式可通过 ``strict=True`` 抛错

Pack A.2 — Word 富格式优化 (P0):
  - 老版用正则在 XML 上一刀切, 当 placeholder 被 Word 拆到多 ``<w:r>`` run 时
    (例: ``${cust_name}`` 在 word 里被切成 ``$ {cust_ name}`` 三个 run, 通常因为
    用户拼写、自动更正、复制粘贴), 正则匹配不到 → 占位符被原样写入报告.
  - 新版走 XML run-level 合并: 把同一段落里被切散的 ``${...}`` 拼回去, 整段
    替换写回第一个 run, 其余 run 的 ``<w:t>`` 清空 — 保留第一个 run 的格式
    (字体/字号/加粗/颜色/下划线), 避免格式被吞.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import xml.etree.ElementTree as ET
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

# Word XML 命名空间 — 处理 docx 时统一注册避免 etree 自己 mangle
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NSMAP = {"w": _W_NS}
ET.register_namespace("w", _W_NS)


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
                    # round 36 P1: 之前静默 continue, 损坏的 word xml 偷掉, 模板预览少字
                    logger.exception("report_template: docx 内 %s 解码失败, 跳过", name)
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
                    name.startswith("xl/sharedStrings.xml") or name.startswith("xl/worksheets/")
                ):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    # round 36 P1: 之前静默 continue, 损坏的 sheet xml 偷掉, 模板预览少字
                    logger.exception("report_template: xlsx 内 %s 解码失败, 跳过", name)
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
        is_valid=True,  # 占位符为空也算合法 — 允许纯静态模板
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
    text: str,
    flat_ctx: Dict[str, str],
    strict: bool = False,
    _depth: int = 0,
    _expanding: Optional[set] = None,
) -> str:
    """递归替换 ``${name}`` placeholder.

    round 31 修嵌套占位符死循环:
      - ``_MAX_DEPTH`` 限制递归层数, 防止 ``${a.b.c}`` 这种 dot-paths 在
        错误实现下无限展开 (例: 把 "${a.b.c}" 解释为 "展开 a 后再展开 b.c")
      - ``_expanding`` set 检测循环: 模板/数据本身定义 a -> b -> a 时
        单次 sub 是 OK 的, 但若实现支持递归展开会爆栈. 这里选择"不展开",
        防止坏数据拖垮服务
    """
    # round 31: 嵌套占位符死循环保护 — 50 层已远超任何合法模板
    _MAX_DEPTH = 50
    if _depth >= _MAX_DEPTH:
        logger.warning("placeholder 嵌套深度超过 %s, 提前停止展开", _MAX_DEPTH)
        return text
    if _expanding is None:
        _expanding = set()

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        # round 31: cycle 检测 — 若该 placeholder 正在展开链上, 保留原文 + warning
        if name in _expanding:
            logger.warning("检测到循环占位符 $%s, 保留原文", name)
            return match.group(0)
        if name in flat_ctx:
            value = flat_ctx[name]
            # round 31: 嵌套保护 — 若替换后的值本身含 ${...}, 且未在展开链,
            # 递归展开一次 (允许 ${a} -> "项目 ${b}" 这种合法嵌套)
            if "${" in value:
                _expanding.add(name)
                try:
                    value = _render_placeholder_in_text(
                        value, flat_ctx, strict=strict,
                        _depth=_depth + 1, _expanding=_expanding,
                    )
                finally:
                    _expanding.discard(name)
            return value
        if strict:
            raise KeyError(f"模板需要 ${{{name}}}, 但 context 未提供")
        return f"[未填:{name}]"

    return _PLACEHOLDER_RE.sub(_sub, text)


# ============================================================
# Word run-level 渲染 — 保留富格式 (Pack A.2 P0 修复)
# ============================================================


def _qn(tag: str) -> str:
    """带命名空间的 etree tag, 例 _qn('p') == '{http://...}p'."""
    return f"{{{_W_NS}}}{tag}"


def _iter_text_blocks(root: ET.Element):
    """遍历 word XML 中所有"文本聚合块" — 段落 (w:p) 或表格 cell 内 (w:tc).

    每个块内的 w:t 元素需要合并扫描 placeholder, 因为 placeholder 可能
    跨多个 run (w:r) 但绝不会跨段落/单元格.
    """
    # 收集 p (段落) 和 tc (表格单元格里的段落由 tc 包) — 同时返回, 顺序无所谓
    for p in root.iter(_qn("p")):
        yield p


def _collect_text_segments(block: ET.Element) -> Tuple[List[ET.Element], str]:
    """收集块内所有 ``<w:t>`` 元素 + 拼接后的整段文本.

    返回 (t_elements, full_text). full_text 与遍历顺序 1:1 对齐,
    可以用 offset 反查每个字符落在哪个 t 元素.
    """
    t_elements: List[ET.Element] = []
    parts: List[str] = []
    for t in block.iter(_qn("t")):
        t_elements.append(t)
        parts.append(t.text or "")
    return t_elements, "".join(parts)


def _replace_in_block(block: ET.Element, flat_ctx: Dict[str, str], strict: bool = False) -> bool:
    """在一个段落/单元格里替换所有 ``${...}`` placeholder.

    算法:
      1) 拼接块内所有 w:t 文本 → full_text
      2) 用正则在 full_text 上找 placeholder span
      3) 对每个 span (倒序处理, 避免位移影响后续 span):
         - 找到 span 起始 char 所在的 t_element 与位移 offset_in_t
         - 找到 span 结束 char 所在的 t_element 与位移
         - 把"起始 t"的文本改为 ``起始前缀 + 替换值``
         - 把中间到结束 t 的文本清空 (起始 t 不变, 因为已经写了完整替换);
           结束 t 保留"结束后缀"(防止把同段后面无关字符吞掉)
      4) 因为是倒序处理, 多个 placeholder 不会互相干扰

    保留富格式: 起始 t 保留, 中间/结束 t 的 w:r run 属性 (rPr) 不动,
    只清空 w:t 文本 — Word 看到空 run 视作空字符串.

    Returns: True 如果发生了任何替换 (供调用方决定是否重写整段 XML).
    """
    t_elements, full_text = _collect_text_segments(block)
    if not full_text or "${" not in full_text:
        return False

    matches = list(_PLACEHOLDER_RE.finditer(full_text))
    if not matches:
        return False

    # 预计算: 每个字符的 (t_idx, char_offset_in_t)
    char_map: List[Tuple[int, int]] = []
    for ti, t in enumerate(t_elements):
        text = t.text or ""
        for ci in range(len(text)):
            char_map.append((ti, ci))

    changed = False
    # 倒序处理 — 改前面的 span 时不影响后面 span 的位置参考
    for m in reversed(matches):
        name = m.group(1)
        if name in flat_ctx:
            replacement = flat_ctx[name]
        elif strict:
            raise KeyError(f"模板需要 ${{{name}}}, 但 context 未提供")
        else:
            replacement = f"[未填:{name}]"

        start, end = m.start(), m.end()
        if start >= len(char_map) or end > len(char_map):
            # 防御 — 不应该发生 (matches 是基于 full_text 算的)
            continue
        start_ti, start_ci = char_map[start]
        # end 是开区间 → 取 end-1 所在 t, 然后 +1 算偏移
        end_ti, end_ci_inclusive = char_map[end - 1]
        end_ci = end_ci_inclusive + 1

        start_t = t_elements[start_ti]
        start_text_full = start_t.text or ""
        prefix = start_text_full[:start_ci]

        if start_ti == end_ti:
            # placeholder 完整落在同一个 t — 最简单情况
            suffix = start_text_full[end_ci:]
            start_t.text = prefix + replacement + suffix
        else:
            # 跨多个 t — 起始 t 写 [prefix + replacement], 末尾 t 写 [suffix],
            # 中间所有 t 清空 (保留 run 属性)
            start_t.text = prefix + replacement
            for mid_ti in range(start_ti + 1, end_ti):
                t_elements[mid_ti].text = ""
            end_t = t_elements[end_ti]
            end_text_full = end_t.text or ""
            end_t.text = end_text_full[end_ci:]

            # P0 W3: xml:space=preserve — 否则 Word 会把首尾空格去掉
            for t in (start_t, end_t):
                if t.text and (t.text.startswith(" ") or t.text.endswith(" ")):
                    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

        # 替换值首尾如果有空格, 给起始 t 加 xml:space=preserve
        if replacement and (replacement.startswith(" ") or replacement.endswith(" ")):
            start_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

        changed = True

    return changed


def _render_docx_xml_blob(xml_bytes: bytes, flat_ctx: Dict[str, str], strict: bool) -> bytes:
    """对单个 word/*.xml 文件做 run-level 替换. 失败回退到正则替换.

    注意: 早退判断**不能**用 ``b"${" in xml_bytes`` — 当 placeholder 被 Word
    拆到多个 ``<w:r>`` run 时 (``$ {cust_ name}``), ``${`` 不会在原始字节流中
    相邻出现, 会被错误地判为"无 placeholder" 跳过 — 正是这个 bug 促使本函数
    重写为 XML 段落级合并. 这里改用更宽松的判断: 存在 ``$`` **和** ``{`` 才
    解析 (round 31: 同时存在才能构成 ``${`` placeholder; 但 round 14 P1-11
    已知漏判 — 改用 ``b"$" in xml_bytes and b"{" in xml_bytes``, 比 ``or``
    更精确, 避免对纯 ``$`` 货币符 (例: ``100$``) 错误触发 ET 解析).
    """
    # round 31: 早退条件既查 $ 也查 { — 必须同时存在才可能是 placeholder
    # (round 14 P1-11 已知: 单 ``$`` 出现在 "100$ 美元" 时应跳过 ET 解析)
    if b"$" not in xml_bytes and b"{" not in xml_bytes:
        return xml_bytes  # 既无 $ 也无 {, 必不可能含 placeholder
    # round 31: 防 "$" 与 "{" 分属 XML 不同位置 (例: 货币 $ + 大括号元素),
    # 强制两个标记都出现才走 ET 解析 — 避免无 placeholder 模板走 ET 浪费
    if not (b"$" in xml_bytes and b"{" in xml_bytes):
        return xml_bytes

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.warning("XML 解析失败, 回退正则: %s", exc)
        try:
            text = xml_bytes.decode("utf-8")
            text = _render_placeholder_in_text(text, flat_ctx, strict=strict)
            return text.encode("utf-8")
        except Exception:  # noqa: BLE001
            # round 36 P1: 之前静默回退原 xml — 模板占位符没替换也不知道
            logger.exception(
                "report_template: XML 二次正则回退失败, 返回原 bytes (strict=%s)", strict
            )
            if strict:
                raise
            return xml_bytes

    changed = False
    for block in _iter_text_blocks(root):
        if _replace_in_block(block, flat_ctx, strict=strict):
            changed = True

    if not changed:
        return xml_bytes

    # 注意: ET 序列化默认不加 XML 声明 — Word 需要; 用 tostring(xml_declaration=True)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render_docx(
    template_bytes: bytes,
    context: Dict[str, Any],
    *,
    strict: bool = False,
) -> bytes:
    """渲染 .docx — XML 段落级 run-aware 替换, 保留富格式.

    对每个 word/*.xml:
      - 走 _render_docx_xml_blob (XML 解析 + 段落级 run 合并)
      - 解析失败时回退到正则 (老行为, 兼容损坏模板)
    其他文件 (settings.xml, theme/, media/...) 原样复制.
    """
    flat = _flatten_context(context or {})
    try:
        in_buf = io.BytesIO(template_bytes)
        out_buf = io.BytesIO()
        with (
            zipfile.ZipFile(in_buf, "r") as zin,
            zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout,
        ):
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                    try:
                        data = _render_docx_xml_blob(data, flat, strict=strict)
                    except Exception as exc:  # noqa: BLE001
                        if strict:
                            raise
                        logger.warning("渲染 %s 时跳过 (走原内容): %s", item.filename, exc)
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
        with (
            zipfile.ZipFile(in_buf, "r") as zin,
            zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout,
        ):
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
        stmt = (
            stmt.order_by(desc(ReportTemplate.updated_at))
            .offset(max(0, int(skip)))
            .limit(max(1, min(500, int(limit))))
        )
        items = list((await db.execute(stmt)).scalars().all())
        return total, items

    @staticmethod
    async def get(
        db: AsyncSession,
        template_id: int,
        firm_id: Optional[int] = None,
    ) -> Optional[ReportTemplate]:
        stmt = select(ReportTemplate).where(ReportTemplate.id == template_id)
        if firm_id is not None:
            stmt = stmt.where(ReportTemplate.firm_id == firm_id)
        return (await db.execute(stmt)).scalar_one_or_none()

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
        firm_id: Optional[int] = None,
        template_id: int,
        template_name: Optional[str] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[ReportTemplate]:
        tpl = await ReportTemplateService.get(db, template_id, firm_id=firm_id)
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
    async def delete(db: AsyncSession, template_id: int, firm_id: Optional[int] = None) -> bool:
        tpl = await ReportTemplateService.get(db, template_id, firm_id=firm_id)
        if tpl is None:
            return False
        await db.delete(tpl)
        await db.commit()
        return True

    @staticmethod
    async def render(
        db: AsyncSession,
        *,
        firm_id: Optional[int] = None,
        template_id: int,
        context: Dict[str, Any],
        project_id: Optional[int] = None,
        output_filename: Optional[str] = None,
        user_id: Optional[int] = None,
        user_display: Optional[str] = None,
        strict: bool = False,
    ) -> Tuple[bytes, str, ReportRenderHistory]:
        tpl = await ReportTemplateService.get(db, template_id, firm_id=firm_id)
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
                output_filename=output_filename
                or f"render_failed{datetime.now().strftime('%Y%m%d%H%M%S')}",
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

        out_name = (
            output_filename
            or f"{tpl.template_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        )
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
