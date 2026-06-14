"""API 层公共样板 helper。

把散布在多个 API 文件里的 "按主键查 ORM, 不存在则 raise 404" 重复代码
统一抽到这里。原来每个文件一个 ``_get_project_or_404`` (5 行) × 8 个文件 = 40 行
样板代码, 现在收敛成一个 ``get_project_or_404`` (一行调用)。

新模块复用规范:
    from app.api._helpers import get_project_or_404, get_or_404

``get_or_404`` 是通用版, 可以查任意带 ``id`` 主键的 ORM 模型;
``get_project_or_404`` 是它的 ``Project`` 特化便捷别名。
"""

from __future__ import annotations

from typing import Type, TypeVar

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import Project

T = TypeVar("T")


async def get_or_404(
    db: AsyncSession,
    model: Type[T],
    pk: int,
    *,
    label: str = "记录",
) -> T:
    """按主键查 ORM 模型, 不存在则 raise HTTPException 404。

    参数:
        db:      异步数据库 session
        model:   任意带 ``id`` 主键字段的 SQLAlchemy ORM 模型
        pk:      主键值
        label:   错误消息里的对象名 (默认 "记录")

    返回:
        查到的 ORM 实例 (类型与 ``model`` 一致)

    抛出:
        HTTPException(404, f"{label} {pk} 不存在")  当记录不存在时
    """
    obj = await db.get(model, pk)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label} {pk} 不存在")
    return obj


async def get_project_or_404(db: AsyncSession, project_id: int) -> Project:
    """按 ID 查 Project, 不存在则 404。

    8 个老 API 文件曾各自维护同名 ``_get_project_or_404``, 现在统一走这里。
    """
    return await get_or_404(db, Project, project_id, label="项目")
