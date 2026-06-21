"""Database configuration and session management."""

import logging

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


# 关键: 在模块加载时 (而不是 lifespan 中) 显式 import 所有 ORM 模型,
# 确保 Base.metadata 在任何 lifespan/请求之前就注册了所有表。
# 原因: HF Space 用 Docker 时, 我们走 --no-install-project 模式 (源码从 /app 通过
# PYTHONPATH 加载), 第一次 uvicorn 启动时 `import app.api.projects` 会触发
# `app.models.db_models` 加载, 但有些启动顺序下 (比如 app.api.* 在 lifespan
# 之后才被 import) 会导致 Base.metadata 为空, create_all 不会建任何表,
# 后续 SELECT 报 "no such table"。
import app.models.db_models  # noqa: F401, E402


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Get database session — AsyncSession.__aexit__ auto-rollbacks uncommitted tx."""
    async with AsyncSessionLocal() as session:
        yield session


# ============================================================
#  Schema drift 自愈 (Round 30 P0)
# ============================================================
# 项目无 Alembic — 历史通过 SQLAlchemy create_all + 加列 DDL 演进出
# Base.metadata. 生产部署时若 Base.metadata 多了一列但 DB 缺, 任何 INSERT/SELECT
# 都会爆 OperationalError. 这里提供轻量级"启动时自检 + 自动 ALTER"机制:
#   1. create_all(checkfirst=True) 兜底缺表
#   2. PRAGMA / Inspector 拿 DB 实际列, 对比 ORM 期望, 缺则 ADD COLUMN
# 兼容 SQLite (生产默认) 与 Postgres (移植成本仅 SQL 片段).


def _sqlite_column_type(col) -> str:
    """把 SQLAlchemy Column 对象翻译成 SQLite ALTER TABLE 兼容的 DDL 片段.

    SQLite ADD COLUMN 只接受: NULL / NOT NULL / DEFAULT / PRIMARY KEY / UNIQUE /
    REFERENCES / COLLATE / GENERATED. 不接受 enum/array/jsonb/postgres 方言.
    我们的列类型大多是 Integer/String/Text/DateTime/Boolean/Float — 全部兼容.
    """
    col_type = col.type
    py_type = type(col_type).__name__
    if py_type in ("Integer",):
        return "INTEGER"
    if py_type in ("String", "Text"):
        if py_type == "String" and getattr(col_type, "length", None):
            return f"VARCHAR({col_type.length})"
        return "TEXT"
    if py_type in ("Float", "Numeric", "Decimal"):
        return "FLOAT"
    if py_type in ("Boolean",):
        return "BOOLEAN"
    if py_type in ("DateTime", "Date", "Time"):
        return "DATETIME"
    if py_type in ("JSON",):
        return "TEXT"
    return "TEXT"


def _ensure_column(sync_conn, table_name: str, col) -> None:
    """单列 ADD COLUMN (幂等). 失败不抛, 留 trace 给后续诊断.

    接收同步连接 — 因为 SQLAlchemy Inspector 是 sync API, 在 async conn 里
    我们用 conn.run_sync(fn) 把它跑在 greenlet 里.

    SQLite 限制:
      - ADD COLUMN 不支持 NOT NULL 但已有数据无 default (会爆)
      - callable default (如 default=utc_now) 拿不到静态值 → ADD COLUMN 必然失败
      - 解决方案: callable default 时, DROP NOT NULL (老行补 NULL, 应用层仍强制非空),
        这与 Alembic 早期阶段的 batch_alter_table 思路一致.
    """
    inspector = inspect(sync_conn)
    try:
        existing = {c["name"] for c in inspector.get_columns(table_name)}
    except Exception:  # noqa: BLE001 — 表不在时 sqlite 抛
        return
    if col.name in existing:
        return
    ddl_type = _sqlite_column_type(col)
    has_static_default = False
    if col.default is not None and not col.primary_key:
        default_value = col.default.arg if hasattr(col.default, "arg") else col.default
        if isinstance(default_value, (bool, int, float, str)):
            has_static_default = True
    if has_static_default:
        not_null = " NOT NULL" if not col.nullable else ""
    else:
        # 无静态 default (callable / 无 default) — 老行需要 NULL 兜底, 不加 NOT NULL
        not_null = ""
    default_sql = ""
    if col.default is not None and not col.primary_key:
        default_value = col.default.arg if hasattr(col.default, "arg") else col.default
        if isinstance(default_value, bool):
            default_sql = f" DEFAULT {1 if default_value else 0}"
        elif isinstance(default_value, (int, float)):
            default_sql = f" DEFAULT {default_value}"
        elif isinstance(default_value, str):
            esc = default_value.replace("'", "''")
            default_sql = f" DEFAULT '{esc}'"
    sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {ddl_type}{not_null}{default_sql}'
    try:
        sync_conn.exec_driver_sql(sql)
        logger.warning(
            "schema drift auto-migrated: ADD COLUMN %s.%s (%s%s)",
            table_name, col.name, ddl_type, not_null
        )
    except Exception as exc:  # noqa: BLE001 — 已存在 / 权限不足等
        logger.warning(
            "schema drift ADD COLUMN failed: %s.%s — %s",
            table_name, col.name, exc,
        )


def _sync_auto_migrate_all_columns(sync_conn) -> None:
    """同步版通用 auto-migrate — 通过 conn.run_sync() 在 async 里调用."""
    inspector = inspect(sync_conn)
    try:
        existing_tables = set(inspector.get_table_names())
    except Exception as exc:  # noqa: BLE001
        logger.warning("inspector.get_table_names() 失败: %s — auto-migrate 跳过", exc)
        return

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            # 表缺失由 create_all(checkfirst=True) 负责
            continue
        for col in table.columns:
            _ensure_column(sync_conn, table_name, col)


async def init_db() -> None:
    """Initialize database tables (idempotent — safe to call multiple times).

    关键流程 (按顺序):
      1. create_all(checkfirst=True) 建新表 / 已存在的表跳过
      2. ALTER TABLE ADD COLUMN 补齐漂移列 (DB 有表但缺列的场景)
      3. 通用 _sync_auto_migrate_all_columns 兜底所有未注册的列

    不会丢数据 — SQLite ADD COLUMN 不影响已有行 (默认填 DEFAULT / NULL).
    """
    async with engine.begin() as conn:
        # 1) 建新表
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        # 2) 全部走 run_sync, 因为 Inspector 是 sync API
        await conn.run_sync(_sync_auto_migrate_all_columns)

    logger.info("init_db 完成: create_all + auto_migrate_all_columns")