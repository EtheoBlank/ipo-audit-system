"""P1 修复 (2026-06-19): 0 假值导致勾稽按钮静默失败.

修复前: if pid: 在 pid=0 (默认值) 时跳过整段, 用户点'勾稽'按钮什么都不发生.
修复后: 改 if pid and pid > 0: + else 分支 st.warning("请先填招股书 ID").
"""
from __future__ import annotations

import re
from pathlib import Path


def _read_frontend_file() -> str:
    p = Path(__file__).parent.parent / "frontend" / "pages_ipo_specials.py"
    return p.read_text(encoding="utf-8")


class TestPidZeroGuard:
    def test_pid_zero_shows_warning(self):
        """前端代码中 'if pid:' 应改为 'if pid and pid > 0:' 防止 0 假值静默跳过."""
        src = _read_frontend_file()
        # 旧的 "if pid:" 应当不存在 (已改为 if pid and pid > 0:)
        assert not re.search(r"^\s*if pid:\s*$", src, flags=re.MULTILINE), (
            "pages_ipo_specials.py 仍存在裸 'if pid:', pid=0 时会静默跳过"
        )
        # 新的 guard 应当存在
        assert "if pid and pid > 0:" in src, (
            "pages_ipo_specials.py 缺 if pid and pid > 0: guard"
        )
        # warning 应当存在
        assert "st.warning" in src, "缺 st.warning 兜底提示"

    def test_item_id_zero_guard(self):
        """清单项 'if item_id:' 同样应改为 'if item_id and item_id > 0:'."""
        src = _read_frontend_file()
        assert not re.search(r"^\s*if item_id:\s*$", src, flags=re.MULTILINE), (
            "pages_ipo_specials.py 仍存在裸 'if item_id:'"
        )
        assert "if item_id and item_id > 0:" in src
