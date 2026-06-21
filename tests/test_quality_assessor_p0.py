"""MeetingQualityAssessor 主路径单测 (Round 30 P0 补测).

重点:
  - 分数边界 (0-100, 越界 → 截断)
  - 空文本降级
  - AI 失败兜底
  - 评分维度: 完整性 / 准确性 / 时效性 (通过 strength/weakness 列表覆盖)

不依赖真实 API — DeepSeek 用 AsyncMock, 直接断言结果结构 + 数值范围.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.team_management.quality_assessor import (
    MeetingQualityAssessor,
    MeetingQualityContext,
    MeetingQualityResult,
    _fallback_assessment,
)


# ----------------------------------------------------------------------
#  Helper
# ----------------------------------------------------------------------


def _make_context(**overrides) -> MeetingQualityContext:
    defaults = dict(
        meeting_title="IPO 项目阶段评审会",
        meeting_type="阶段评审",
        content="讨论收入确认与关联交易, 形成决议: 由张三在 2025-03-01 前完成函证.",
        decisions=[
            {"topic": "收入确认", "owner": "张三", "due": "2025-03-01"},
            {"topic": "关联交易披露", "owner": "李四", "due": "2025-03-15"},
        ],
        action_items=[
            {"action": "完成银行函证", "owner": "王五", "deadline": "2025-03-10"},
        ],
        attendees=["张三", "李四", "王五", "赵六"],
    )
    defaults.update(overrides)
    return MeetingQualityContext(**defaults)


# ----------------------------------------------------------------------
#  测试 1: 分数合法范围 (0-100)
# ----------------------------------------------------------------------


async def test_score_in_valid_range_0_100(monkeypatch):
    """AI 返回 score=87.5 → 仍在 0-100 范围."""
    assessor = MeetingQualityAssessor(
        deepseek=MagicMock(is_configured=True, chat_json=AsyncMock(
            return_value={
                "quality_score": 87.5,
                "strengths": ["决策明确", "行动项有 owner"],
                "weaknesses": ["未涉及风险点"],
                "suggestions": ["增加风险讨论"],
            }
        ))
    )
    ctx = _make_context()
    result = await assessor.assess(ctx)

    assert isinstance(result, MeetingQualityResult)
    assert 0.0 <= result.quality_score <= 100.0
    assert result.quality_score == 87.5
    assert result.ai_enabled is True
    assert result.ai_raw == {
        "quality_score": 87.5,
        "strengths": ["决策明确", "行动项有 owner"],
        "weaknesses": ["未涉及风险点"],
        "suggestions": ["增加风险讨论"],
    }
    assert result.strengths == ["决策明确", "行动项有 owner"]
    assert result.weaknesses == ["未涉及风险点"]
    assert result.suggestions == ["增加风险讨论"]


# ----------------------------------------------------------------------
#  测试 2: 越界分数被截断到 [0, 100]
# ----------------------------------------------------------------------


async def test_score_out_of_range_clamped_or_raised():
    """AI 返回 150 → max(0, min(100, ...)) 截断为 100. 负分同理截断为 0."""
    # (a) 高位截断
    assessor_high = MeetingQualityAssessor(
        deepseek=MagicMock(is_configured=True, chat_json=AsyncMock(
            return_value={"quality_score": 150, "strengths": [], "weaknesses": [], "suggestions": []}
        ))
    )
    res_high = await assessor_high.assess(_make_context())
    assert res_high.quality_score == 100.0

    # (b) 负分截断
    assessor_neg = MeetingQualityAssessor(
        deepseek=MagicMock(is_configured=True, chat_json=AsyncMock(
            return_value={"quality_score": -50, "strengths": [], "weaknesses": [], "suggestions": []}
        ))
    )
    res_neg = await assessor_neg.assess(_make_context())
    assert res_neg.quality_score == 0.0

    # (c) score 字段类型错误 → _parse_ai_result 内部 try/except 转 0.0
    assessor_bad = MeetingQualityAssessor(
        deepseek=MagicMock(is_configured=True, chat_json=AsyncMock(
            return_value={"quality_score": "abc", "strengths": [], "weaknesses": [], "suggestions": []}
        ))
    )
    res_bad = await assessor_bad.assess(_make_context())
    assert res_bad.quality_score == 0.0


# ----------------------------------------------------------------------
#  测试 3: 空文本 / 全空字段降级到 fallback
# ----------------------------------------------------------------------


def test_empty_meeting_notes_returns_default_score(monkeypatch):
    """空 content / 空 decisions / 空 attendees → fallback 评估, score 在合法范围."""
    # 关掉 AI 走 fallback
    empty_ctx = MeetingQualityContext(
        meeting_title="空会议",
        meeting_type="周会",
        content="",  # 空内容
        decisions=[],
        action_items=[],
        attendees=[],
    )
    result = _fallback_assessment(empty_ctx)
    assert 0.0 <= result.quality_score <= 100.0
    # fallback 必填 weaknesses 应非空 (至少一条规则化的弱点)
    assert isinstance(result.weaknesses, list)
    assert len(result.weaknesses) >= 1
    # fallback 标记 ai_enabled=False
    assert result.ai_enabled is False
    # 空决策 → 必出 "未提炼决策事项" 类弱点
    assert any("决策" in w for w in result.weaknesses)

    # (b) 仅 content 非空但 < 300 → 触发 "纪要过于简短"
    short_ctx = MeetingQualityContext(
        meeting_title="短纪要",
        meeting_type="周会",
        content="讨论了一下" * 10,  # ~50 字符
        decisions=[],
        action_items=[],
        attendees=["A"],
    )
    short_res = _fallback_assessment(short_ctx)
    assert any("简短" in w or "遗漏" in w for w in short_res.weaknesses)


# ----------------------------------------------------------------------
#  测试 4: AI 失败兜底到 fallback
# ----------------------------------------------------------------------


async def test_ai_failure_returns_fallback_score(monkeypatch):
    """DeepSeek 抛 DeepSeekError → 兜底为 fallback 评估."""
    from app.services.sales_ledger.deepseek_client import DeepSeekError

    # (a) DeepSeekError
    mock_ds = MagicMock(is_configured=True, chat_json=AsyncMock(
        side_effect=DeepSeekError("API timeout")
    ))
    assessor = MeetingQualityAssessor(deepseek=mock_ds)
    ctx = _make_context()
    result = await assessor.assess(ctx)
    assert result.ai_enabled is False
    assert 0.0 <= result.quality_score <= 100.0
    assert isinstance(result.strengths, list)

    # (b) 通用 Exception
    mock_ds2 = MagicMock(is_configured=True, chat_json=AsyncMock(
        side_effect=RuntimeError("network dead")
    ))
    assessor2 = MeetingQualityAssessor(deepseek=mock_ds2)
    result2 = await assessor2.assess(ctx)
    assert result2.ai_enabled is False

    # (c) is_configured=False → 直接 fallback (不调 chat_json)
    mock_ds3 = MagicMock(is_configured=False, chat_json=AsyncMock(
        side_effect=AssertionError("should not be called")
    ))
    assessor3 = MeetingQualityAssessor(deepseek=mock_ds3)
    result3 = await assessor3.assess(ctx)
    assert result3.ai_enabled is False
    mock_ds3.chat_json.assert_not_called()


# ----------------------------------------------------------------------
#  测试 5: 评分维度 — 完整性 / 准确性 / 时效性
# ----------------------------------------------------------------------


async def test_score_dimensions_complete_accuracy_timeliness():
    """覆盖 system prompt 列出的 4 维度:
      1) 内容完整性 (content_len ≥ 800 → +10,  < 300 → -10 + weakness)
      2) 决策清晰度 (有 owner / due)
      3) 行动项质量 (有 owner / deadline)
      4) 跟进机制 (attendees ≥ 3)
    """
    # (a) 完整 + 清晰 + 有跟进 → 应得 ≥ 60 分
    rich_ctx = MeetingQualityContext(
        meeting_title="IPO 阶段评审",
        meeting_type="评审",
        content=("讨论内容详实, " * 100),  # ~600 chars
        decisions=[
            {"topic": f"D{i}", "owner": "张三", "due": "2025-03-01"}
            for i in range(3)
        ],
        action_items=[
            {"action": f"A{i}", "owner": "李四", "deadline": "2025-03-10"}
            for i in range(2)
        ],
        attendees=["A", "B", "C", "D"],
    )
    rich = _fallback_assessment(rich_ctx)
    assert rich.quality_score >= 60.0
    # strength 至少提到决策数 + 行动项数 + 与会人
    assert any("决策" in s for s in rich.strengths)
    assert any("行动项" in s for s in rich.strengths)
    assert any("与会人" in s or "参与" in s for s in rich.strengths)

    # (b) 内容空 + 无决策无行动 + 无与会人 → 应低于 50 分 (base 50, -10 short -10 低质)
    poor_ctx = MeetingQualityContext(
        meeting_title="空会议",
        meeting_type="周会",
        content="稍后再说, 下次再讨论",
        decisions=[],
        action_items=[],
        attendees=[],
    )
    poor = _fallback_assessment(poor_ctx)
    assert poor.quality_score < 50.0
    assert any("简短" in w or "遗漏" in w for w in poor.weaknesses)
    assert any("决策" in w for w in poor.weaknesses)
    assert any("行动" in w for w in poor.weaknesses)
    assert any("与会人" in w or "追溯" in w for w in poor.weaknesses)

    # (c) 含模糊措辞 ("稍后再说") → 触发额外扣分
    vague_ctx = MeetingQualityContext(
        meeting_title="模糊纪要",
        meeting_type="周会",
        content=("这个月底前完成. 稍后再说细节. 下次再讨论. " * 30),
        decisions=[],
        action_items=[],
        attendees=["A", "B"],
    )
    vague = _fallback_assessment(vague_ctx)
    assert any("稍后" in w or "模糊" in w or "不够明确" in w for w in vague.weaknesses)
    # 模糊措辞扣 10 分; 满分 <= 100, 至少验证分数在合法范围
    assert 0.0 <= vague.quality_score <= 100.0
    # 对比 rich: vague 应 ≤ rich (模糊纪要不优于完整纪要)
    assert vague.quality_score <= rich.quality_score

    # (d) 决策缺 owner → 扣分 + weakness
    no_owner_ctx = MeetingQualityContext(
        meeting_title="缺 owner",
        meeting_type="周会",
        content="讨论内容丰富, " * 50,
        decisions=[{"topic": "D1"}, {"topic": "D2", "owner": "有owner的人"}],
        action_items=[],
        attendees=["A", "B", "C"],
    )
    no_owner = _fallback_assessment(no_owner_ctx)
    assert any("owner" in w for w in no_owner.weaknesses)

    # (e) AI 返回 strengths/weaknesses/suggestions 为非 list → _parse_ai_result 截断为 []
    assessor = MeetingQualityAssessor(
        deepseek=MagicMock(is_configured=True, chat_json=AsyncMock(
            return_value={
                "quality_score": 75,
                "strengths": "不是 list",
                "weaknesses": None,
                "suggestions": ["有效"],
            }
        ))
    )
    parsed = await assessor.assess(_make_context())
    assert parsed.strengths == []  # 错误类型 → []
    assert parsed.weaknesses == []  # None → []
    assert parsed.suggestions == ["有效"]
    assert parsed.quality_score == 75.0
