"""Shared test helpers (round 32+).

本包为多租户 / IDOR / 认证 / 数据库的回归测试提供**可复用**的 fixture、工厂、
断言 helper. 任何 test_*.py 都可以 ``from tests._helpers import ...`` 直接使用.

设计原则:
  - **不破坏现有测试**: 新增的 fixture 名避免与已存在的 test-local fixture 冲突
    (例如 ``admin_user`` 在多个 test 文件里有局部定义, 本包不抢).
  - **singleton-by-default**: 通用 helper 走 module-level 单例 (e.g. 角色常量).
  - **不绑数据库 schema 演进**: 用 SQLAlchemy ORM 而不是裸 SQL, 自动 follow 模型变更.
  - **async-first**: 项目主路径全 async, helper 优先暴露 async fixture.

暴露的模块:
  - ``db``: 异步 session / engine / 事务回滚
  - ``auth``: 角色常量、用户工厂、firm 工厂、JWT 签发
  - ``idor``: 跨所断言 / 角色断言 / status_code 断言
  - ``http``: TestClient fixture + auth header 助手
  - ``pagination``: 分页一致性断言 (limit/offset/total)
"""
from tests._helpers import auth, db, http, idor, pagination

__all__ = ["auth", "db", "http", "idor", "pagination"]
