"""JWT encode / decode — python-jose 兜底到内置 hmac+base64.

jose 不可用时 (HF Space 极端情况) 走纯 stdlib 实现, HS256 + 显式
header / payload / signature, 兼容标准 JWT 格式。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class JWTError(Exception):
    """Token 解析 / 校验失败."""


# 优先 python-jose
_jose_jwt = None


class _JoseErrorSentinel(Exception):
    """python-jose 不可用时的占位异常类型 — 仅用于 except 分支结构.

    之前用 ``_JoseError = Exception`` 会把"任何异常"当成 JWT 错误吞掉, 真实的编程 bug
    (TypeError 等) 会被掩盖. 用独立占位类型避免这个问题.
    """


_JoseError = _JoseErrorSentinel
try:  # pragma: no cover
    from jose import jwt as _jose_jwt  # type: ignore
    from jose import JWTError as _JoseError  # type: ignore
except Exception as exc:  # noqa: BLE001
    logger.warning("python-jose 不可用, JWT 走 stdlib 兜底: %s", exc)
    _jose_jwt = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _stdlib_encode(payload: Dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    h_b = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p_b = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()
    )
    signing_input = f"{h_b}.{p_b}".encode("ascii")
    sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h_b}.{p_b}.{_b64url_encode(sig)}"


def _stdlib_decode(token: str) -> Dict[str, Any]:
    try:
        h_b, p_b, s_b = token.split(".")
    except ValueError as exc:
        raise JWTError("token 格式无效") from exc
    signing_input = f"{h_b}.{p_b}".encode("ascii")
    expected_sig = hmac.new(
        settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    try:
        actual_sig = _b64url_decode(s_b)
    except Exception as exc:
        raise JWTError("签名段非法") from exc
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise JWTError("签名不匹配")
    try:
        payload = json.loads(_b64url_decode(p_b))
    except Exception as exc:
        raise JWTError("payload 不是合法 JSON") from exc
    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_dt = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        except Exception as exc:
            raise JWTError("exp 字段格式错") from exc
        if exp_dt <= _utcnow():
            raise JWTError("token 已过期")
    # 校验 iss — 必须存在且匹配, 防止别的部署/环境的 token 拿来重放
    # 即使 JWT_SECRET 不一致, 越权场景排除) (P0 安全: 防 token 重放)
    iss = payload.get("iss")
    if iss != "ipo-audit-system":
        raise JWTError(f"issuer 不匹配或缺失: {iss!r}")
    return payload


def _build_payload(
    *,
    sub: str,
    token_type: str,
    expires_minutes: int,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = _utcnow()
    payload: Dict[str, Any] = {
        "sub": sub,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "iss": "ipo-audit-system",
    }
    if extra:
        payload.update(extra)
    return payload


def create_access_token(
    user_id: int,
    username: str,
    role: str,
    *,
    firm_id: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    base_extra = {
        "username": username,
        "role": role,
        "firm_id": firm_id,
    }
    if extra:
        base_extra.update(extra)
    payload = _build_payload(
        sub=str(user_id),
        token_type="access",
        expires_minutes=settings.JWT_ACCESS_EXPIRE_MINUTES,
        extra=base_extra,
    )
    if _jose_jwt is not None:
        try:
            return _jose_jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        except Exception as exc:  # noqa: BLE001
            logger.exception("jose 编码失败, 走 stdlib: %s", exc)
    return _stdlib_encode(payload)


def create_refresh_token(user_id: int, username: str) -> str:
    payload = _build_payload(
        sub=str(user_id),
        token_type="refresh",
        expires_minutes=settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 60,
        extra={"username": username},
    )
    if _jose_jwt is not None:
        try:
            return _jose_jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        except Exception as exc:  # noqa: BLE001
            logger.exception("jose 编码失败, 走 stdlib: %s", exc)
    return _stdlib_encode(payload)


def decode_token(token: str) -> Dict[str, Any]:
    """解码并校验 token. 失败抛 ``JWTError``."""
    if not token:
        raise JWTError("token 为空")
    # P0 安全: 强制算法白名单, 防止运维把 JWT_ALGORITHM 误设为 'none' 后
    # 攻击者用 alg=none + 空签名伪造 token 通过校验。
    _SAFE_ALGS = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512"}
    alg = settings.JWT_ALGORITHM
    if alg not in _SAFE_ALGS:
        raise JWTError(f"非法的 JWT 算法配置: {alg!r}")
    if _jose_jwt is not None:
        try:
            return _jose_jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[alg],
                options={"require": ["exp", "iat", "sub", "iss"]},
                issuer="ipo-audit-system",
            )
        except _JoseError as exc:
            raise JWTError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("jose 解码失败, 尝试 stdlib: %s", exc)
    return _stdlib_decode(token)
