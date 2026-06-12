"""Password hashing — bcrypt via passlib.

降级路径: 如果 passlib 由于 bcrypt 版本不兼容报错, 退到 hashlib pbkdf2-sha256
保证系统永远可登录 (打印一次告警 + 标记 hash 前缀方便后续迁移).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


# 优先使用 passlib (bcrypt) — 行业标准
_passlib_context = None
try:  # pragma: no cover — 环境差异性 import
    from passlib.context import CryptContext

    _passlib_context = CryptContext(
        schemes=["bcrypt"],
        deprecated="auto",
        bcrypt__rounds=settings.BCRYPT_ROUNDS,
    )
except Exception as exc:  # noqa: BLE001
    logger.warning("passlib/bcrypt 不可用, 将降级到 pbkdf2-sha256: %s", exc)
    _passlib_context = None


_FALLBACK_PREFIX = "pbkdf2_sha256$"
_FALLBACK_ITER = 200_000


def _fallback_hash(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _FALLBACK_ITER
    )
    return f"{_FALLBACK_PREFIX}{_FALLBACK_ITER}${salt.hex()}${derived.hex()}"


def _fallback_verify(password: str, hashed: str) -> bool:
    try:
        _, iter_str, salt_hex, hash_hex = hashed.split("$", 3)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iter_str)
        )
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def hash_password(password: str) -> str:
    """生成密码哈希. 永不抛 — 降级到 pbkdf2."""
    if not password:
        raise ValueError("password 不能为空")
    if _passlib_context is not None:
        try:
            return _passlib_context.hash(password)
        except Exception as exc:  # noqa: BLE001
            logger.exception("bcrypt 哈希失败, 降级到 pbkdf2: %s", exc)
    return _fallback_hash(password)


def verify_password(password: str, hashed: Optional[str]) -> bool:
    """校验密码. 失败 / 哈希格式异常一律 False, 不抛."""
    if not password or not hashed:
        return False
    if hashed.startswith(_FALLBACK_PREFIX):
        return _fallback_verify(password, hashed)
    if _passlib_context is not None:
        try:
            return _passlib_context.verify(password, hashed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bcrypt 校验异常: %s", exc)
            return False
    # 既不是 fallback 也没有 passlib, 退化校验
    return False
