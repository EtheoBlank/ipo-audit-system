"""SQL LIKE 通配符转义工具.

集中防 LIKE 通配符 (%, _, \\) 在用户输入中被误解释 — 配合 SQLAlchemy 的
``escape="\\\\"`` 使用.

注意: SQLAlchemy 把 Python 字符串里的 ``"\\\\\\\\"`` 当作 SQL 层的单个反斜杠
escape 字符, 所以先 ``\\\\`` → ``\\\\\\\\`` 把用户输入里的反斜杠转义,
再用单反斜杠转义 ``%`` 和 ``_``.
"""

from __future__ import annotations


def escape_like(text: str) -> str:
    """转义 SQL LIKE 通配符 — 防止用户输入 ``%`` / ``_`` / ``\\`` 触发全表扫描.

    :param text: 待转义的文本 (可为 ``None`` — 视作空串)
    :return: 转义后的字符串
    """
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
