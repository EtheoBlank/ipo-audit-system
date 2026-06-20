"""Password hashing — bcrypt 直连 + pbkdf2 降级.

为什么不用 passlib:
  passlib 1.7.x 与 bcrypt >= 4.1 不兼容 (bcrypt 4.1 删了 __about__.__version__).
  这里直接用 bcrypt 库; 失败时降到 pbkdf2-sha256 (OWASP 2023 推荐 600k 轮).

bcrypt 限制:
  - 密码最多 72 字节 (bcrypt 协议硬约束). 超过会被截断, 这里手动截断 + 提示.
  - 不支持 NUL 字节 — 拒绝含 NUL 的密码, 避免下游截断绕过校验.

Round 35 P1: 增加弱密码黑名单 (``WEAK_PASSWORDS``) + ``is_weak_password`` 校验函数,
供 ``app.services.auth.service`` 在 change_password / reset_password 前调用.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import FrozenSet, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


# bcrypt 优先, 失败降级 pbkdf2
_bcrypt_mod = None
_bcrypt_error: Optional[str] = None
try:  # pragma: no cover — 环境差异性 import
    import bcrypt as _bcrypt

    # 简单烟雾测试 (passlib 之前在这里栽跟头, 我们自己也测一下)
    _smoke = _bcrypt.hashpw(b"x", _bcrypt.gensalt(rounds=4))
    _ = _bcrypt.checkpw(b"x", _smoke)
    _bcrypt_mod = _bcrypt
except Exception as exc:  # noqa: BLE001
    _bcrypt_error = repr(exc)
    logger.warning("bcrypt 不可用, 将降级到 pbkdf2-sha256: %s", exc)
    _bcrypt_mod = None


# bcrypt 哈希前缀 (识别老 passlib 写入的哈希)
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_FALLBACK_PREFIX = "pbkdf2_sha256$"
_FALLBACK_ITER = 600_000  # OWASP 2023 推荐 ≥ 600_000
_BCRYPT_MAX_BYTES = 72    # bcrypt 协议硬约束


# ============================================================
#  Round 35 P1 — 弱密码黑名单 (30 条)
# ============================================================
# 选词策略: OWASP Top 25 弱密码 (2023) + 中文拼音常见 + 数字序列 + 行业相关.
# 存储为不区分大小写的 frozenset (查 O(1)).
WEAK_PASSWORDS: FrozenSet[str] = frozenset({
    # 英文类
    "password", "password1", "password123", "pwd", "pwd123",
    "12345678", "123456789", "1234567890", "qwerty", "qwerty123",
    "abc123", "admin", "admin123", "root", "root123",
    "welcome", "welcome1", "letmein", "monkey", "dragon",
    "iloveyou", "sunshine", "princess", "football",
    "baseball", "superman", "batman",
    # 数字序列
    "00000000", "11111111", "11223344", "123321",
    # 中文拼音 / 业务
    "woaini", "nihao", "woaini1314", "qwer1234",
})

assert len(WEAK_PASSWORDS) >= 30, (
    f"WEAK_PASSWORDS 应至少 30 条, 当前 {len(WEAK_PASSWORDS)}"
)


def is_weak_password(password: str) -> bool:
    """检查密码是否在弱密码黑名单. 不抛, 仅 True/False.

    比较时统一小写 + 去前后空格, 避免 ``Password1 `` 这种 bypass.
    """
    if not password:
        return True  # 空串视为弱 (调用方再判长度)
    return password.strip().lower() in WEAK_PASSWORDS


def _normalize_for_bcrypt(password: str) -> bytes:
    """bcrypt 入参归一化: encode utf-8 + 截断到 72 字节 + 拒绝 NUL."""
    if not password:
        raise ValueError("password 不能为空")
    raw = password.encode("utf-8")
    if b"\x00" in raw:
        # bcrypt 不支持 NUL — 早 reject, 避免下游截断绕过校验
        raise ValueError("password 不能包含 NUL 字节")
    if len(raw) > _BCRYPT_MAX_BYTES:
        raw = raw[:_BCRYPT_MAX_BYTES]
    return raw


def _fallback_hash(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _FALLBACK_ITER)
    return f"{_FALLBACK_PREFIX}{_FALLBACK_ITER}${salt.hex()}${derived.hex()}"


def _fallback_verify(password: str, hashed: str) -> bool:
    try:
        _, iter_str, salt_hex, hash_hex = hashed.split("$", 3)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iter_str))
        return hmac.compare_digest(derived, expected)
    except Exception as exc:  # noqa: BLE001
        # P0 (round 32): 之前静默 return False, 任何异常都吞掉, 运维看不到
        # 现在记日志, 便于排查 "用户密码校验莫名失败"
        logger.exception("密码校验失败 (fallback pbkdf2): %s", exc)
        return False


def hash_password(password: str) -> str:
    """生成密码哈希. 永不抛 — 降级到 pbkdf2. 直连 bcrypt, 跳过 passlib."""
    if not password:
        raise ValueError("password 不能为空")
    if _bcrypt_mod is not None:
        try:
            raw = _normalize_for_bcrypt(password)
            rounds = max(4, min(settings.BCRYPT_ROUNDS, 15))  # bcrypt rounds 边界保护
            salt = _bcrypt_mod.gensalt(rounds=rounds)
            return _bcrypt_mod.hashpw(raw, salt).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            logger.exception("bcrypt 哈希失败, 降级到 pbkdf2: %s", exc)
    return _fallback_hash(password)


def verify_password(password: str, hashed: Optional[str]) -> bool:
    """校验密码. 失败 / 哈希格式异常一律 False, 不抛. 支持 bcrypt / pbkdf2 两种格式."""
    if not password or not hashed:
        return False
    # 1) pbkdf2 格式
    if hashed.startswith(_FALLBACK_PREFIX):
        return _fallback_verify(password, hashed)
    # 2) bcrypt 格式 (本进程写的 + 老 passlib 写的)
    if any(hashed.startswith(p) for p in _BCRYPT_PREFIXES):
        if _bcrypt_mod is None:
            logger.warning("bcrypt 哈希但 bcrypt 模块不可用, 校验失败")
            return False
        try:
            raw = _normalize_for_bcrypt(password)
            return _bcrypt_mod.checkpw(raw, hashed.encode("ascii"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bcrypt 校验异常: %s", exc)
            return False
    # 3) 未知格式
    logger.warning("未知密码哈希格式 (前 12 字符): %s", hashed[:12])
    return False
