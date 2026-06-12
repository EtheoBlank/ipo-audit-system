"""Auth service package — JWT / password / RBAC / audit log / approval workflow.

模块组织:
  - password.py       — bcrypt 哈希 / 校验
  - jwt.py            — access / refresh token 编码 / 解码
  - rbac.py           — 角色级别比较 + 权限检查
  - audit_log.py      — 写审计轨迹
  - approval.py       — 五级签字流引擎
  - bootstrap.py      — 启动时创建默认事务所 / 管理员
  - service.py        — 高层编排 (login / logout / change-password / etc.)
  - dependencies.py   — FastAPI Depends 注入 (get_current_user / require_role / require_permission)

所有模块共用 ``app.core.database`` 的 AsyncSession.
"""

from app.services.auth.password import hash_password, verify_password
from app.services.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_token,
    JWTError,
)
from app.services.auth.rbac import (
    role_at_least,
    has_permission,
    check_permission,
    AuthorizationError,
)
from app.services.auth.audit_log import record_audit_log, query_audit_logs
from app.services.auth.approval import (
    ApprovalEngine,
    DEFAULT_FIVE_LEVEL_FLOW,
    InvalidApprovalAction,
)
from app.services.auth.service import (
    authenticate,
    login,
    refresh_access_token,
    change_password,
    AuthenticationError,
    AccountLockedError,
)
from app.services.auth.bootstrap import bootstrap_auth
from app.services.auth.dependencies import (
    get_current_user,
    get_current_user_optional,
    require_role,
    require_permission,
)

__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "JWTError",
    "role_at_least",
    "has_permission",
    "check_permission",
    "AuthorizationError",
    "record_audit_log",
    "query_audit_logs",
    "ApprovalEngine",
    "DEFAULT_FIVE_LEVEL_FLOW",
    "InvalidApprovalAction",
    "authenticate",
    "login",
    "refresh_access_token",
    "change_password",
    "AuthenticationError",
    "AccountLockedError",
    "bootstrap_auth",
    "get_current_user",
    "get_current_user_optional",
    "require_role",
    "require_permission",
]
