"""Database configuration and session management."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


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
    """Get database session — auto-rollback on exception so a failing route
    doesn't leak half-applied writes into the next request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables (idempotent — safe to call multiple times).

    关键: 在 create_all 之前必须显式 import 所有 ORM 模块, 否则 Base.metadata 为空,
    不会有任何表被建 (尤其新加表后忘记 import 会导致数据丢失).
    """
    # 显式 import 所有 ORM 模型 — SQLAlchemy 靠 import-time 注册
    import app.models.db_models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)