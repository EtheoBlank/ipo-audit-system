"""Background event dispatcher — 简单事件总线 + FastAPI BackgroundTasks 起步.

设计:
  - 同步注册 listener (函数), 触发时按异步任务跑
  - 每个 listener 拿到自己的 AsyncSession (从 AsyncSessionLocal), 不复用 request scope
  - listener 异常被吞掉 + 写日志 (避免一个 listener 挂掉影响其他)
  - **fire-and-forget 时强引用 pending tasks**, 防 asyncio 自动 GC 抢任务

未来可平滑替换为 Celery / Arq, 调用方接口 ``dispatch(event_name, payload)`` 不变.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


# 类型: async def listener(payload: dict) -> None
# listener 内部如果需要 DB, 用 get_session_factory() 自取独立 session
Listener = Callable[[Dict[str, Any]], Awaitable[None]]

_listeners: Dict[str, List[Listener]] = {}

# 强引用 fire-and-forget 任务, 防 asyncio GC. dispatch_background 完成自动 discard.
_pending_tasks: Set[asyncio.Task] = set()


def register_listener(event_name: str, fn: Listener) -> None:
    """注册事件监听器. 重复注册同函数会被去重."""
    if not event_name or not callable(fn):
        return
    bucket = _listeners.setdefault(event_name, [])
    if fn not in bucket:
        bucket.append(fn)
        logger.debug("已注册事件监听器: %s -> %s", event_name, getattr(fn, "__name__", str(fn)))


def unregister_listener(event_name: str, fn: Listener) -> None:
    bucket = _listeners.get(event_name)
    if bucket and fn in bucket:
        bucket.remove(fn)


def list_listeners(event_name: Optional[str] = None) -> Dict[str, List[str]]:
    """诊断用: 列出已注册的事件 + listener 名."""
    if event_name:
        return {
            event_name: [getattr(f, "__name__", str(f)) for f in _listeners.get(event_name, [])]
        }
    return {
        name: [getattr(f, "__name__", str(f)) for f in bucket]
        for name, bucket in _listeners.items()
    }


async def _run_listener_safely(event_name: str, fn: Listener, payload: Dict[str, Any]) -> None:
    try:
        await fn(payload)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "事件 %s 的监听器 %s 抛异常: %s",
            event_name,
            getattr(fn, "__name__", str(fn)),
            exc,
        )


async def dispatch(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """同步分发事件 (并行 await 所有 listener). 用在路由内部时建议套 BackgroundTasks."""
    bucket = _listeners.get(event_name) or []
    if not bucket:
        return
    payload = payload or {}
    await asyncio.gather(
        *(_run_listener_safely(event_name, fn, payload) for fn in bucket),
        return_exceptions=False,
    )


def dispatch_background(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """火并忘 — 在当前 event loop 起一个 task, 调用方不需要 await.

    P0 修复: 用 ``_pending_tasks`` 强引用 task, 防止 asyncio runtime GC 后任务"消失".
    """
    bucket = _listeners.get(event_name) or []
    if not bucket:
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(dispatch(event_name, payload))
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)
    except RuntimeError:
        # 没有 running loop (同步脚本 / 测试) — 退化到同步执行
        try:
            asyncio.run(dispatch(event_name, payload))
        except Exception:  # noqa: BLE001
            logger.exception("dispatch_background 同步退化执行失败")


# 提供给 listener 自取 session 的工厂
def get_session_factory():
    return AsyncSessionLocal


__all__ = [
    "register_listener",
    "unregister_listener",
    "list_listeners",
    "dispatch",
    "dispatch_background",
    "get_session_factory",
]
