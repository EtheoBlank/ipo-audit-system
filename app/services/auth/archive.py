"""AuditLog 归档与分区辅助.

Pack A 后续优化 — 长期 100w+ 行的运维:

  1) ``rotate_audit_logs(months=6)`` — 把 N 月前的 AuditLog 行**复制**到
     ``audit_logs_archive`` 物理影子表, 然后用 raw DELETE 直接清掉原表
     (绕开 SQLAlchemy event 的 before_delete 拦截 — 只有此函数允许).

  2) ``audit_log_stats(db)`` — 返回总行数 / 最早一行 / 各 firm 行数, 让 DBA
     判断是否到了该 rotate 的时点.

设计原则:
  - 默认安全: 只有显式 ``confirm=True`` 才真删除 (防误调)
  - 影子表结构与原表一致 (CREATE TABLE IF NOT EXISTS 兜底)
  - 调用方必须有 admin 角色 (路由层校验); 本模块不再做权限判断
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.auth import AuditLog

logger = logging.getLogger(__name__)


# 影子表名 — 与原表同 schema, 但允许 INSERT (复制) + DELETE (人工清理)
_ARCHIVE_TABLE = "audit_logs_archive"


# CREATE TABLE 语句 — 与 AuditLog ORM 字段保持一致;
# 不带索引 (归档表只用于偶尔取证, 写多读少, 索引反而拖慢 rotate)
_CREATE_ARCHIVE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_ARCHIVE_TABLE} (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NULL,
    user_display VARCHAR(120) NULL,
    user_role VARCHAR(40) NULL,
    firm_id INTEGER NULL,
    action VARCHAR(40) NOT NULL,
    resource_type VARCHAR(80) NULL,
    resource_id VARCHAR(80) NULL,
    project_id INTEGER NULL,
    method VARCHAR(10) NULL,
    path VARCHAR(500) NULL,
    ip VARCHAR(64) NULL,
    user_agent VARCHAR(500) NULL,
    status_code INTEGER NULL,
    summary VARCHAR(500) NULL,
    payload TEXT NULL,
    error_detail TEXT NULL,
    created_at DATETIME NOT NULL,
    archived_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


async def ensure_archive_table(db: AsyncSession) -> None:
    """确保归档影子表存在 (幂等). 第一次 rotate 前自动调一次."""
    await db.execute(text(_CREATE_ARCHIVE_SQL))
    await db.commit()


async def audit_log_stats(db: AsyncSession) -> Dict[str, Any]:
    """返回 AuditLog 行数 / 时间跨度 / firm 分布 — 给 ops 判断要不要归档."""
    total = int((await db.execute(select(func.count(AuditLog.id)))).scalar_one() or 0)
    earliest = (await db.execute(select(func.min(AuditLog.created_at)))).scalar_one()
    latest = (await db.execute(select(func.max(AuditLog.created_at)))).scalar_one()

    by_firm: list[Dict[str, Any]] = []
    rows = (
        await db.execute(
            select(AuditLog.firm_id, func.count(AuditLog.id))
            .group_by(AuditLog.firm_id)
            .order_by(func.count(AuditLog.id).desc())
            .limit(20)
        )
    ).all()
    for firm_id, cnt in rows:
        by_firm.append({"firm_id": firm_id, "count": int(cnt or 0)})

    return {
        "total": total,
        "earliest": earliest.isoformat() if earliest else None,
        "latest": latest.isoformat() if latest else None,
        "by_firm": by_firm,
    }


async def rotate_audit_logs(
    db: AsyncSession,
    *,
    months: int = 6,
    cutoff: Optional[datetime] = None,
    confirm: bool = False,
    batch_size: int = 5000,
) -> Dict[str, Any]:
    """把 ``cutoff`` (默认 N 月前) 之前的行**复制**到归档表 + 删除原表.

    Args:
        months: 保留近 N 月数据; 之前的归档. cutoff 优先 (cutoff 非 None 时忽略 months).
        cutoff: 显式指定截止时间 (UTC naive). 仅删除 created_at < cutoff 的行.
        confirm: 必须 True 才真正 DELETE; 否则 dry-run 只返回 "会删多少行".
        batch_size: 一次 INSERT … SELECT 的最大行数 (避免长事务锁表).

    Returns:
        ``{"to_archive": N, "archived": N, "deleted": N, "cutoff": "..."}``
        dry-run 时 archived/deleted = 0.

    Notes:
        - DELETE 走 raw SQL (绕开 SQLAlchemy event 的 before_delete 拦截) —
          只有归档场景允许这样做.
        - 函数运行期间不允许其他写 AuditLog — 调用方应在维护窗口跑.
    """
    if cutoff is None:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30 * months)

    # 提前统计候选行数
    to_archive = int(
        (
            await db.execute(select(func.count(AuditLog.id)).where(AuditLog.created_at < cutoff))
        ).scalar_one()
        or 0
    )

    result: Dict[str, Any] = {
        "to_archive": to_archive,
        "archived": 0,
        "deleted": 0,
        "cutoff": cutoff.isoformat(),
        "dry_run": not confirm,
    }
    if to_archive == 0:
        logger.info("rotate_audit_logs: 截止 %s 之前无需归档行", cutoff)
        return result
    if not confirm:
        return result

    await ensure_archive_table(db)

    archived_total = 0
    deleted_total = 0
    cols = (
        "id, user_id, user_display, user_role, firm_id, action, "
        "resource_type, resource_id, project_id, method, path, ip, user_agent, "
        "status_code, summary, payload, error_detail, created_at"
    )

    # 分批: INSERT … SELECT 同事务 DELETE — 失败回滚
    while True:
        # 拉一批 id (按 created_at 升序, 优先归档最老的)
        ids_rows = (
            await db.execute(
                select(AuditLog.id)
                .where(AuditLog.created_at < cutoff)
                .order_by(AuditLog.created_at)
                .limit(batch_size)
            )
        ).all()
        ids = [r[0] for r in ids_rows]
        if not ids:
            break

        # 复制到归档表
        id_csv = ",".join(str(i) for i in ids)
        insert_sql = text(
            f"INSERT INTO {_ARCHIVE_TABLE} ({cols}) "
            f"SELECT {cols} FROM audit_logs WHERE id IN ({id_csv})"
        )
        await db.execute(insert_sql)

        # raw DELETE (绕过 ORM event)
        del_sql = text(f"DELETE FROM audit_logs WHERE id IN ({id_csv})")
        del_res = await db.execute(del_sql)
        await db.commit()

        archived_total += len(ids)
        deleted_total += del_res.rowcount or len(ids)
        logger.info(
            "rotate_audit_logs: 已归档 %d / %d (本批 %d)",
            archived_total,
            to_archive,
            len(ids),
        )

    result["archived"] = archived_total
    result["deleted"] = deleted_total
    return result
