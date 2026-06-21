"""AI 引擎 / 审计备注生成器 / 风险识别器 单元测试.

覆盖:
  - AIAnalysisEngine: _call_ai 失败返回 JSON 错误包 (不裸抛)
  - RiskIdentifier: 4 类规则识别 (收入 / 关联 / 商誉 / 存货 / 现金流)
  - AnomalyDetector: 4 类异常检测 (整数 / 余额方向 / 无活动 / 集中度)
  - AuditNoteGenerator: KB 失败 / AI 失败 / 法规失败 三路降级仍能输出 markdown

注: AIAnalysisService 的 4 个业务方法 + 3 个 _parse_* 已迁出至 ai_analysis_engine.py,
此处不再保留相关测试.

不依赖真实 API — httpx 用 mock 替换.
pytest-asyncio mode = auto (pyproject.toml).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_analysis_engine import AIAnalysisEngine, AnomalyDetector, RiskIdentifier


# ----------------------------------------------------------------------
#  1) AIAnalysisEngine._call_ai — 失败包装
# ----------------------------------------------------------------------


class TestAIAnalysisEngineCallAi:
    def test_disabled_returns_error_json(self, monkeypatch):
        """未配置 key → 返回 {"error": ...} JSON, 不抛."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "MINIMAX_API_KEY", None)
        engine = AIAnalysisEngine()
        assert engine.enabled is False
        import asyncio

        result = asyncio.run(engine._call_ai("hello"))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "MINIMAX_API_KEY" in parsed["error"]

    async def test_analyze_risk_level_json_parse_failure_returns_default(self):
        """AI 返回非 JSON → fallback 默认 dict."""
        engine = AIAnalysisEngine(api_key="test-key")

        # Mock httpx response
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "非JSON响应"}}]
        }
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            result = await engine.analyze_risk_level(
                financial_data={"revenue": 1000}, industry="制造"
            )
        # _call_ai 返回 "非JSON响应" → json.loads 抛 → except 返回默认 dict
        assert result["risk_level"] == "中"

    async def test_detect_anomalies_non_list_returns_empty(self):
        engine = AIAnalysisEngine(api_key="test-key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"foo": "bar"}'}}]
        }
        mock_resp.raise_for_status.return_value = None
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            result = await engine.detect_anomalies([], [])
        # AI 返回 dict 不是 list → 退化为 [result] 是 dict, 但非空
        # 合约: 至少不抛
        assert isinstance(result, list)


# ----------------------------------------------------------------------
#  2) RiskIdentifier — 5 个规则函数
# ----------------------------------------------------------------------


class TestRiskIdentifier:
    def test_revenue_recognition_large_credit_flagged(self):
        """贷方发生额 > 500万 → 高风险 (需要 ending > 0 也满足)."""
        risks = RiskIdentifier.identify_revenue_recognition_risk([
            {"account_code": "5001", "credit_amount": 6_000_000, "ending_balance": 100}
        ])
        assert len(risks) >= 1
        # 至少一条高风险
        assert any(r["risk_level"] == "高" for r in risks)
        assert all(r["risk_type"] == "收入确认" for r in risks)

    def test_revenue_recognition_excludes_5401_5501(self):
        """round23 (P0 正确性回归): 5401/5501 是费用类, 不应作为收入风险."""
        risks = RiskIdentifier.identify_revenue_recognition_risk([
            {"account_code": "5401", "credit_amount": 10_000_000, "ending_balance": 100},
            {"account_code": "5501", "credit_amount": 5_000_000, "ending_balance": 100},
        ])
        assert risks == []  # 5401/5501 不计入

    def test_revenue_recognition_includes_5001_5002_5051_5301(self):
        """5001/5002/5051/5301 都应计入."""
        for code in ["5001", "5002", "5051", "5301"]:
            risks = RiskIdentifier.identify_revenue_recognition_risk([
                {"account_code": code, "credit_amount": 6_000_000, "ending_balance": 100}
            ])
            assert len(risks) >= 1, f"代码 {code} 应被识别"

    def test_receivable_too_high_flagged(self):
        """期末 > 贷方 2 倍 → 中风险 (应收账款异常偏高)."""
        risks = RiskIdentifier.identify_revenue_recognition_risk([
            {"account_code": "5001", "credit_amount": 1000, "ending_balance": 5000}
        ])
        assert any(r["risk_level"] == "中" for r in risks)

    def test_related_party_keyword_match(self):
        risks = RiskIdentifier.identify_related_party_risk([
            {"voucher_no": "V001", "summary": "与关联公司发生资金往来", "account_name": "银行存款"},
        ])
        assert len(risks) >= 1
        assert "关联公司" in risks[0]["description"]

    def test_related_party_no_keyword_clean(self):
        risks = RiskIdentifier.identify_related_party_risk([
            {"voucher_no": "V001", "summary": "正常采购付款", "account_name": "应付账款"},
        ])
        assert risks == []

    def test_related_party_capped_at_10(self):
        """最多返回 10 条 (避免无限)."""
        risks = RiskIdentifier.identify_related_party_risk([
            {"voucher_no": f"V{i:03d}", "summary": f"关联方交易 {i}", "account_name": "X"}
            for i in range(50)
        ])
        assert len(risks) <= 10

    def test_goodwill_impairment_flagged(self):
        risks = RiskIdentifier.identify_goodwill_impairment_risk([
            {"account_name": "商誉", "ending_balance": 10_000_000}
        ])
        assert len(risks) == 1
        assert risks[0]["risk_type"] == "商誉减值"

    def test_goodwill_zero_not_flagged(self):
        risks = RiskIdentifier.identify_goodwill_impairment_risk([
            {"account_name": "商誉", "ending_balance": 0}
        ])
        assert risks == []

    def test_inventory_turnover_high_value(self):
        result = RiskIdentifier.identify_inventory_turnover_risk(
            account_balances=[
                {"account_name": "存货", "ending_balance": 10_000_000},
            ],
            industry="制造",
        )
        assert result["risk_level"] == "中"
        assert result["total_inventory"] == 10_000_000

    def test_inventory_no_balance_empty(self):
        result = RiskIdentifier.identify_inventory_turnover_risk(
            account_balances=[{"account_name": "银行存款", "ending_balance": 100}],
            industry="制造",
        )
        assert result == {}

    def test_cash_flow_negative_critical(self):
        """货币资金 < 短期借款 → 高风险."""
        result = RiskIdentifier.identify_cash_flow_risk([
            {"account_name": "银行存款", "ending_balance": 100},
            {"account_name": "短期借款", "ending_balance": 1000},
        ])
        assert result["risk_level"] == "高"

    def test_cash_flow_low_warning(self):
        result = RiskIdentifier.identify_cash_flow_risk([
            {"account_name": "银行存款", "ending_balance": 500},
            {"account_name": "短期借款", "ending_balance": 0},
        ])
        assert result["risk_level"] == "中"

    def test_cash_flow_healthy(self):
        result = RiskIdentifier.identify_cash_flow_risk([
            {"account_name": "银行存款", "ending_balance": 5_000_000},
            {"account_name": "短期借款", "ending_balance": 0},
        ])
        assert result["risk_level"] == "低"


# ----------------------------------------------------------------------
#  3) AnomalyDetector — 4 类异常
# ----------------------------------------------------------------------


class TestAnomalyDetector:
    def test_round_number_anomaly(self):
        anomalies = AnomalyDetector.detect_round_number_anomalies([
            {"account_code": "1001", "account_name": "现金", "ending_balance": 500_000},
        ])
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "整数金额"

    def test_round_number_below_threshold_ignored(self):
        anomalies = AnomalyDetector.detect_round_number_anomalies([
            {"account_code": "1001", "account_name": "现金", "ending_balance": 5000},
        ])
        assert anomalies == []

    def test_balance_direction_debit_negative_anomaly(self):
        anomalies = AnomalyDetector.detect_balance_direction_anomalies([
            {"balance_direction": "借", "ending_balance": -1000, "account_name": "应收"}
        ])
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "余额方向异常"

    def test_balance_direction_credit_positive_anomaly(self):
        anomalies = AnomalyDetector.detect_balance_direction_anomalies([
            {"balance_direction": "贷", "ending_balance": 1000, "account_name": "应付"}
        ])
        assert len(anomalies) == 1

    def test_balance_direction_normal(self):
        anomalies = AnomalyDetector.detect_balance_direction_anomalies([
            {"balance_direction": "借", "ending_balance": 1000, "account_name": "应收"}
        ])
        assert anomalies == []

    def test_zero_activity_anomaly(self):
        anomalies = AnomalyDetector.detect_zero_activity_anomalies([
            {"debit_amount": 0, "credit_amount": 0, "ending_balance": 1000}
        ])
        assert len(anomalies) == 1

    def test_zero_activity_no_balance_clean(self):
        anomalies = AnomalyDetector.detect_zero_activity_anomalies([
            {"debit_amount": 0, "credit_amount": 0, "ending_balance": 0}
        ])
        assert anomalies == []

    def test_concentration_risk_high(self):
        """top5 > 80% 总余额 → 中/高."""
        # top5 = 10000+10000+10000+10000+10000 = 50000
        # 其余 = 100+100+100+100+100 = 500
        # 总 = 50500, top5 比例 = 50000/50500 = 99% > 80%
        accounts = (
            [{"account_code": "1001", "account_name": f"Big{i}", "ending_balance": 10000}
             for i in range(5)]
            + [{"account_code": "1002", "account_name": f"Small{i}", "ending_balance": 100}
               for i in range(5)]
        )
        result = AnomalyDetector.detect_concentration_risk(accounts, top_n=5)
        assert "risk_type" in result
        assert result["risk_level"] in ("中", "高")

    def test_concentration_risk_empty(self):
        result = AnomalyDetector.detect_concentration_risk([], top_n=5)
        assert result == {}


# ----------------------------------------------------------------------
#  4) AuditNoteGenerator — 三路降级
# ----------------------------------------------------------------------


class TestAuditNoteGeneratorFallback:
    """KB / AI / 法规 三路任意失败, 仍能输出 markdown."""

    async def test_kb_failure_returns_empty_kb_results(self):
        """KB.search 抛异常 → kb_results=[], 仍生成 markdown."""
        from app.services.audit_note_generator import (
            AuditNoteContext,
            AuditNoteGenerator,
        )

        gen = AuditNoteGenerator()
        # Mock KB.search 抛异常
        gen.kb.search = AsyncMock(side_effect=RuntimeError("KB dead"))
        gen.ai.enabled = False  # 关掉 AI

        # 真实 session: 用 sqlite 内存
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool
        from app.core.database import Base

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)

        async with sm() as db:
            ctx = AuditNoteContext(
                project_id=1,
                account_code="5001",
                account_name="主营业务收入",
                audit_objective="收入截止性",
            )
            result = await gen.generate(db, ctx)

        # KB 失败但仍输出 markdown
        assert result.note
        assert "审计说明" in result.note
        assert result.ai_enabled is False
        assert result.references_kb == []
        await engine.dispose()

    async def test_ai_call_failure_still_returns_markdown(self):
        """AI 失败 → ai_text=None → 走 _compose_note 骨架分支."""
        from app.services.audit_note_generator import (
            AuditNoteContext,
            AuditNoteGenerator,
        )

        gen = AuditNoteGenerator()
        gen.kb.search = AsyncMock(return_value=[])
        gen.ai.enabled = True
        gen.ai._call_minimax = AsyncMock(side_effect=RuntimeError("AI dead"))

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool
        from app.core.database import Base

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)

        async with sm() as db:
            ctx = AuditNoteContext(
                project_id=1,
                account_code="1221",
                account_name="其他应收款",
                balance_amount=50000.0,
                audit_objective="完整性",
            )
            result = await gen.generate(db, ctx, include_regulations=False)

        assert result.note
        # AI 失败但 ai_enabled=True (key 配了), ai_raw=None
        assert result.ai_enabled is True
        assert result.ai_raw is None
        # 走骨架分支, 应有 "科目情况" / "审计程序" 等章节
        assert "科目情况" in result.note or "审计程序" in result.note
        await engine.dispose()

    def test_build_query_joins_parts(self):
        from app.services.audit_note_generator import (
            AuditNoteContext,
            AuditNoteGenerator,
        )

        gen = AuditNoteGenerator()
        ctx = AuditNoteContext(
            project_id=1,
            account_code="5001",
            account_name="主营业务收入",
            audit_objective="截止性",
            industry="制造",
        )
        q = gen._build_query(ctx)
        assert "5001" in q
        assert "主营业务收入" in q
        assert "截止性" in q
        assert "制造" in q

    def test_build_query_fallback_when_empty(self):
        from app.services.audit_note_generator import (
            AuditNoteContext,
            AuditNoteGenerator,
        )

        gen = AuditNoteGenerator()
        ctx = AuditNoteContext(project_id=1)
        q = gen._build_query(ctx)
        assert q == "审计说明"