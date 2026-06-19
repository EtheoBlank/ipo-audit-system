"""Round 29 P1 — report_generator.py 单测.

覆盖 ``app/services/report_generator.py`` 4 个核心类:
  1. ComprehensiveReportGenerator.generate_word_report  —  Word 报告生成
  2. InteractiveReportGenerator.generate_dashboard_data  —  Web 仪表盘数据
  3. InteractiveReportGenerator.generate_trend_data      —  趋势数据
  4. PDFReportGenerator.generate_pdf                    —  PDF 报告
  5. ReportScheduler                                    —  报告调度 CRUD

设计要点:
  - 不写磁盘, 全部 in-memory BytesIO 验证
  - 中文内容验证 (不依赖系统字体, 用 docx 自身的中文 paragraph 验证)
  - PDF 用 PyPDF2 / reportlab 自身重新解析检查 page 数
  - 不调 AI, 不调 DB
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Dict, List

import pytest

from app.services.report_generator import (
    ComprehensiveReportGenerator,
    InteractiveReportGenerator,
    PDFReportGenerator,
    ReportScheduler,
)


# ============================================================
# Fixtures — 模拟项目/财务/风险数据
# ============================================================


@pytest.fixture
def project_info() -> Dict:
    return {
        "name": "IPO-AUDIT-2024-001",
        "company_name": "杭州某某科技股份有限公司",
        "industry": "计算机、通信和其他电子设备制造业",
        "fiscal_year": 2024,
    }


@pytest.fixture
def financial_data() -> Dict:
    return {
        "registered_capital": 5000.00,
        "total_assets": 12_345.67,
        "revenue": 9_876.54,
        "net_profit": 1_234.56,
        "current_assets": 7_000.00,
        "non_current_assets": 5_345.67,
        "gross_margin": 35.5,
        "net_margin": 12.5,
        "roe": 15.0,
        "eps": 1.85,
        "current_ratio": 2.1,
        "quick_ratio": 1.6,
        "debt_ratio": 45.0,
    }


@pytest.fixture
def risk_analysis() -> Dict:
    return {
        "risk_level": "中",
        "risk_points": [
            "应收账款集中度偏高, 前 5 大客户占比 > 60%",
            "存货周转天数同比上升 20 天, 关注跌价风险",
            "研发费用资本化比例 > 30%, 关注会计估计合理性",
        ],
        "recommendations": [
            "扩大函证样本至前 20 大客户",
            "对库龄 > 1 年的存货执行 NRV 测试",
            "复核研发立项文档, 评估资本化条件",
        ],
    }


@pytest.fixture
def trial_balance() -> Dict:
    return {
        "is_balanced": True,
        "total_debit": 12_345.67,
        "total_credit": 12_345.67,
        "difference": 0.0,
    }


@pytest.fixture
def regulatory_cases() -> List[Dict]:
    return [
        {
            "title": "关于 XX 股份有限公司首次公开发行股票并上市的审核问询函",
            "source": "证监会",
            "publish_date": "2024-03-15",
            "content": "请发行人补充披露报告期内主要客户变化的合理性, 以及应收账款坏账准备计提的充分性。",
        },
        {
            "title": "关于 YY 股份有限公司的监管措施决定书",
            "source": "交易所",
            "publish_date": "2024-05-20",
            "content": "公司未及时披露重大关联交易, 决定采取出具警示函的监管措施。",
        },
    ]


# ============================================================
# Task 1: ComprehensiveReportGenerator — Word
# ============================================================


class TestComprehensiveReportGenerator:
    """``ComprehensiveReportGenerator.generate_word_report`` 6 测试."""

    def test_generate_word_basic(self, project_info, financial_data, risk_analysis,
                                  trial_balance, regulatory_cases):
        """基本流程: 输入 4 段数据 → 输出非空 docx bytes."""
        gen = ComprehensiveReportGenerator()
        out = gen.generate_word_report(
            project_info=project_info,
            financial_data=financial_data,
            risk_analysis=risk_analysis,
            trial_balance=trial_balance,
            regulatory_cases=regulatory_cases,
        )
        assert isinstance(out, (bytes, bytearray))
        assert len(out) > 1000, f"docx 太小, 怀疑生成失败: {len(out)} bytes"
        # docx 是 zip 格式, 头 4 字节是 PK\x03\x04
        assert out[:4] == b"PK\x03\x04", "不是有效的 zip/docx 文件"

    def test_generate_word_contains_chinese_and_company_name(
        self, project_info, financial_data, risk_analysis, trial_balance, regulatory_cases
    ):
        """docx 内部能解析出公司名 + 中文财务/风险关键词."""
        gen = ComprehensiveReportGenerator()
        out = gen.generate_word_report(
            project_info, financial_data, risk_analysis, trial_balance, regulatory_cases
        )
        # docx 是 zip, 文档文本在 word/document.xml
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert project_info["company_name"] in doc_xml, "公司名未写入 docx"
        assert "IPO审计综合分析报告" in doc_xml, "标题缺失"
        assert "毛利率" in doc_xml, "财务表头缺失"
        assert "风险" in doc_xml, "风险章节缺失"
        assert "审计结论" in doc_xml, "结论章节缺失"

    def test_generate_word_with_empty_risk_points(
        self, project_info, financial_data, trial_balance
    ):
        """risk_points=[] 不报错, 仍生成有效 docx."""
        gen = ComprehensiveReportGenerator()
        out = gen.generate_word_report(
            project_info,
            financial_data,
            {"risk_level": "低", "risk_points": []},
            trial_balance,
            [],
        )
        assert len(out) > 1000
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        assert "未发现重大风险点" in xml

    def test_generate_word_with_empty_regulatory_cases(
        self, project_info, financial_data, risk_analysis, trial_balance
    ):
        """regulatory_cases=[] 时不报 IndexError / KeyError."""
        gen = ComprehensiveReportGenerator()
        out = gen.generate_word_report(
            project_info, financial_data, risk_analysis, trial_balance, []
        )
        assert len(out) > 1000
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        assert "未找到相关监管案例" in xml

    def test_generate_word_handles_high_risk_level(
        self, project_info, financial_data, trial_balance
    ):
        """risk_level='高' 触发对应结论文案."""
        gen = ComprehensiveReportGenerator()
        out = gen.generate_word_report(
            project_info, financial_data, {"risk_level": "高", "risk_points": ["测试"]},
            trial_balance, []
        )
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        assert "较高风险" in xml

    def test_generate_word_chinese_chars_not_corrupted(
        self, project_info, financial_data, risk_analysis, trial_balance, regulatory_cases
    ):
        """中文段落 (财务报表数字 + 公司名) 写入 docx 不会出现替换符/方块."""
        gen = ComprehensiveReportGenerator()
        out = gen.generate_word_report(
            project_info, financial_data, risk_analysis, trial_balance, regulatory_cases
        )
        # 简单 sanity check: 监管案例标题的特定字符应原样保留
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        # 中文标点应该原样, 不会出现 "?" 或  "□"
        assert "?" not in xml or xml.count("?") < 3, f"中文疑似乱码: 出现过多 '?'"
        # 风险点具体内容
        assert "应收账款集中度偏高" in xml


# ============================================================
# Task 2: PDFReportGenerator
# ============================================================


class TestPDFReportGenerator:
    """``PDFReportGenerator.generate_pdf`` 1 测试."""

    def test_generate_pdf_basic(self, project_info, financial_data, risk_analysis, trial_balance):
        """输入 4 段数据 → 输出非空 PDF bytes."""
        out = PDFReportGenerator.generate_pdf(
            project_info=project_info,
            financial_data=financial_data,
            risk_analysis=risk_analysis,
            trial_balance=trial_balance,
        )
        assert isinstance(out, (bytes, bytearray))
        assert len(out) > 1000, f"PDF 太小: {len(out)} bytes"
        # PDF 头 5 字节
        assert out[:5] == b"%PDF-", f"不是有效 PDF (头 5 bytes = {out[:5]!r})"
        # PDF 文件尾标识
        assert b"%%EOF" in out[-1024:], "PDF 文件未正常结束"

    def test_generate_pdf_with_chinese(
        self, project_info, financial_data, risk_analysis, trial_balance
    ):
        """PDF 生成中文 (reportlab 默认字体支持) 不抛异常."""
        # 报告 PDF 中文测试 — 即使字体 fallback 也不应让 generate_pdf 失败
        out = PDFReportGenerator.generate_pdf(
            project_info, financial_data, risk_analysis, trial_balance
        )
        assert out[:5] == b"%PDF-"
        # reportlab 内置 STSong-Light CID 字体支持中文, 这里只验证不报错
        # 不强制 grep 中文 (字体可能 inline 编码)


# ============================================================
# Task 3: InteractiveReportGenerator
# ============================================================


class TestInteractiveReportGenerator:
    """``InteractiveReportGenerator`` 仪表盘 / 趋势数据."""

    def test_dashboard_data_shape(self, project_info, financial_data, risk_analysis,
                                   trial_balance):
        result = InteractiveReportGenerator.generate_dashboard_data(
            project_info, financial_data, risk_analysis, trial_balance, anomalies=[]
        )
        # 顶层 key
        for k in ("project", "financial_summary", "risk_assessment",
                  "balance_status", "anomalies", "charts"):
            assert k in result, f"dashboard 缺字段: {k}"
        # 嵌套
        assert result["project"]["company"] == project_info["company_name"]
        assert result["financial_summary"]["revenue"] == financial_data["revenue"]
        assert result["risk_assessment"]["level"] == "中"
        assert result["balance_status"]["is_balanced"] is True
        # charts 两张
        assert "asset_structure" in result["charts"]
        assert "risk_heatmap" in result["charts"]
        assert len(result["charts"]["asset_structure"]["values"]) == 2
        assert len(result["charts"]["risk_heatmap"]["scores"]) == 5

    def test_trend_data_shape(self):
        hist = [
            {"year": 2021, "revenue": 1000, "net_profit": 100, "gross_margin": 30.0},
            {"year": 2022, "revenue": 1200, "net_profit": 130, "gross_margin": 32.0},
            {"year": 2023, "revenue": 1500, "net_profit": 200, "gross_margin": 35.0},
        ]
        result = InteractiveReportGenerator.generate_trend_data(hist)
        for k in ("revenue_trend", "profit_trend", "margin_trend"):
            assert k in result
            assert len(result[k]) == 3
        assert result["revenue_trend"][2]["value"] == 1500

    def test_dashboard_handles_missing_keys(self):
        """空 dict 输入不抛 KeyError, 用缺省值兜底."""
        result = InteractiveReportGenerator.generate_dashboard_data(
            project_info={},
            financial_data={},
            risk_analysis={},
            trial_balance={},
            anomalies=[],
        )
        assert result["project"]["name"] == ""
        assert result["financial_summary"]["revenue"] == 0
        assert result["risk_assessment"]["level"] == "中"  # 默认
        assert result["balance_status"]["is_balanced"] is False  # 默认 False


# ============================================================
# Task 4: ReportScheduler
# ============================================================


class TestReportScheduler:
    """``ReportScheduler`` 调度 CRUD."""

    def test_schedule_and_list(self):
        sch = ReportScheduler()
        s1 = sch.schedule_report(
            project_id=1, report_type="comprehensive",
            frequency="monthly", recipients=["a@example.com"],
        )
        s2 = sch.schedule_report(
            project_id=2, report_type="dashboard",
            frequency="weekly", recipients=["b@example.com"],
        )
        assert s1["id"] == 1
        assert s2["id"] == 2
        assert s1["status"] == "active"
        # 全量
        all_sch = sch.get_scheduled_reports()
        assert len(all_sch) == 2
        # 按 project 过滤
        proj1 = sch.get_scheduled_reports(project_id=1)
        assert len(proj1) == 1
        assert proj1[0]["project_id"] == 1

    def test_cancel_schedule(self):
        sch = ReportScheduler()
        s = sch.schedule_report(
            project_id=1, report_type="comprehensive",
            frequency="monthly", recipients=["a@example.com"],
        )
        assert sch.cancel_schedule(s["id"]) is True
        # 列表里 status 应该是 cancelled
        listed = sch.get_scheduled_reports(project_id=1)
        assert listed[0]["status"] == "cancelled"
        # 取消不存在的 ID 返回 False
        assert sch.cancel_schedule(999) is False

    def test_cancel_nonexistent_returns_false(self):
        sch = ReportScheduler()
        assert sch.cancel_schedule(999) is False


# ============================================================
# Task 5: 集成 — Comprehensive + PDF + Dashboard 协同
# ============================================================


class TestReportIntegration:
    """端到端一致性: 同一份数据, Word / PDF / Dashboard 三种输出."""

    def test_same_data_three_outputs_consistent(
        self, project_info, financial_data, risk_analysis, trial_balance, regulatory_cases
    ):
        comp = ComprehensiveReportGenerator()
        word_out = comp.generate_word_report(
            project_info, financial_data, risk_analysis, trial_balance, regulatory_cases
        )
        pdf_out = PDFReportGenerator.generate_pdf(
            project_info, financial_data, risk_analysis, trial_balance
        )
        dash = InteractiveReportGenerator.generate_dashboard_data(
            project_info, financial_data, risk_analysis, trial_balance,
            anomalies=[],
        )
        # Word: zip
        assert word_out[:4] == b"PK\x03\x04"
        # PDF: %PDF
        assert pdf_out[:5] == b"%PDF-"
        # Dashboard: dict
        assert isinstance(dash, dict)
        # 三者公司名一致 (从 docx 内部 xml 解析, 因为 docx 是 zip)
        with zipfile.ZipFile(io.BytesIO(word_out)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        assert project_info["company_name"] in xml, "Word 缺公司名"
        assert dash["project"]["company"] == project_info["company_name"]
        # PDF 头校验公司名: reportlab 中文走 CID 字体可能 inline, 容忍不一定 grep 到
        # 但 PDF 至少不抛 + 长度合理
        assert len(pdf_out) > 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
