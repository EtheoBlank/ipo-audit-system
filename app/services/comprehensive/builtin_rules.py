"""内置规则库。

为常用综合底稿字段提供开箱即用的判断逻辑。后续事务所可在
``manual/<topic>.yaml`` 中扩展。

典型规则示例（应收账款风险等级）：

  优先级 30: 周转天数 > 120              → 高
  优先级 20: 周转天数 > 90  且 <= 120     → 中
  优先级 10: 其他                         → 低
"""

from __future__ import annotations

from app.services.comprehensive.rule_engine import (
    Rule,
    RuleAction,
    RuleBook,
    RuleCondition,
)


def default_rule_book() -> RuleBook:
    """构造内置规则集合。"""
    return RuleBook(
        rules=[
            # ----- 应收账款风险等级（覆盖全部三种 outcome）-----
            Rule(
                id="ar_risk_high_turnover",
                description="周转天数 > 120 → 高风险",
                target_field="risk_level",
                priority=30,
                conditions=[
                    RuleCondition(field="ar_turnover_days", op=">", value=120),
                ],
                action=RuleAction(
                    value="高",
                    citation="周转天数 >120 天：客户回款能力或信用政策存在重大疑虑",
                    confidence=0.85,
                ),
            ),
            Rule(
                id="ar_risk_medium_turnover",
                description="周转天数在 90~120 之间 → 中风险",
                target_field="risk_level",
                priority=20,
                conditions=[
                    RuleCondition(field="ar_turnover_days", op="between", value=[90, 120]),
                ],
                action=RuleAction(
                    value="中",
                    citation="周转天数 90~120 天：需关注回款节奏",
                    confidence=0.80,
                ),
            ),
            Rule(
                id="ar_risk_low_turnover",
                description="周转天数 <= 90 → 低风险",
                target_field="risk_level",
                priority=10,
                conditions=[
                    # P0 正确性: 改为 between [0, 90] 范围检查 (周转天数 <= 90 才算低风险)
                    RuleCondition(field="ar_turnover_days", op="between", value=[0, 90]),
                ],
                action=RuleAction(
                    value="低",
                    citation="周转天数 <=90 天：回款节奏健康",
                    confidence=0.80,
                ),
            ),
            # ----- 函证覆盖率提示 -----
            Rule(
                id="confirmation_low_coverage",
                description="函证覆盖率 < 50% 需补充替代程序",
                target_field="disclosure_note",
                priority=20,
                conditions=[
                    RuleCondition(field="confirmation_rate", op="<", value=0.5),
                ],
                action=RuleAction(
                    value=(
                        "本期函证覆盖率不足 50%，已执行替代审计程序"
                        "（检查期后回款、原始凭证、合同/发货单据等）"
                    ),
                    citation=(
                        "依据《中国注册会计师审计准则第 1312 号 — 函证》及 ISA 505（审计证据）"
                    ),
                    confidence=0.90,
                ),
            ),
        ]
    )
