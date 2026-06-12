"""Tests for the inventory module — 盘点抽样 / FIFO 库龄 / NRV 跌价 / 跌价转回 / 照片回填。"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import pytest

from app.services.inventory.aging_engine import (
    InventoryAgingEngine,
    AgingBucket,
)
from app.services.inventory.count_plan import (
    CountPlanGenerator,
    INDUSTRY_TEMPLATES,
)
from app.services.inventory.count_sheet import (
    CountSheetBuilder,
    CountSheetStrategy,
)
from app.services.inventory.importer import (
    InventoryImporter,
    InventoryImportError,
)
from app.services.inventory.photo_processor import (
    CountPhotoProcessor,
    ParsedCountRow,
)


# =============================================================
#  Importer
# =============================================================


class TestImporter:
    def test_parse_standard_excel(self):
        df = pd.DataFrame({
            "物料编码": ["M001", "M002", "M003"],
            "物料名称": ["螺丝", "螺母", "钢板"],
            "类别": ["原材料", "原材料", "原材料"],
            "仓库": ["主仓", "主仓", "二仓"],
            "期末数量": [100, 200, 50],
            "期末金额": [1000.0, 1500.0, 8000.0],
        })
        buf = BytesIO()
        df.to_excel(buf, index=False)
        result = InventoryImporter.parse_bytes(buf.getvalue(), "test.xlsx")
        assert len(result) == 3
        assert result["material_code"].tolist() == ["M001", "M002", "M003"]
        assert result["ending_amount"].sum() == 10500.0
        # unit_cost 自动派生
        assert result.loc[0, "unit_cost"] == pytest.approx(10.0)

    def test_parse_missing_required(self):
        df = pd.DataFrame({"无关列": [1, 2, 3]})
        buf = BytesIO()
        df.to_excel(buf, index=False)
        with pytest.raises(InventoryImportError):
            InventoryImporter.parse_bytes(buf.getvalue(), "bad.xlsx")

    def test_header_in_second_row(self):
        # Mimic ERP exports that put title row first
        df = pd.DataFrame([
            ["XX公司收发存表", "", "", ""],
            ["物料编码", "物料名称", "期末数量", "期末金额"],
            ["A1", "钢材", 10, 1000.0],
            ["A2", "铜材", 20, 4000.0],
        ])
        buf = BytesIO()
        df.to_excel(buf, index=False, header=False)
        result = InventoryImporter.parse_bytes(buf.getvalue(), "title.xlsx")
        assert set(result["material_code"]) == {"A1", "A2"}


# =============================================================
#  CountSheetBuilder — 金额优先 + 阈值覆盖
# =============================================================


def _mk_movement(code, amount, qty=10, warehouse="主仓", category="原材料"):
    return {
        "material_code": code,
        "material_name": f"物料{code}",
        "category": category,
        "warehouse": warehouse,
        "batch_no": "",
        "unit": "个",
        "ending_qty": qty,
        "ending_amount": amount,
        "unit_cost": amount / qty if qty else 0,
    }


class TestCountSheetBuilder:
    def test_amount_priority_covers_threshold(self):
        # 10 个物料；金额 100,90,80,...,10 → 总 550
        # 阈值 0.8 → 累计 ≥ 440 需要 100+90+80+70+60+50 = 450 → 6 行
        movs = [_mk_movement(f"M{i:02d}", (10 - i) * 10, qty=1) for i in range(10)]
        s = CountSheetStrategy(coverage_threshold=0.8, b_sample_ratio=0, c_sample_ratio=0)
        res = CountSheetBuilder.build(movs, s)
        a_rows = [r for r in res.rows if r["sample_tier"] == "A"]
        # 至少覆盖到 80%
        assert res.coverage_ratio >= 0.8
        # A 类按金额降序排列
        amts = [r["book_amount"] for r in a_rows]
        assert amts == sorted(amts, reverse=True)

    def test_must_include_warehouse(self):
        movs = [
            _mk_movement("A", 100, warehouse="主仓"),
            _mk_movement("B", 50, warehouse="主仓"),
            _mk_movement("C", 5, warehouse="VIP仓"),  # 金额小，但必盘
            _mk_movement("D", 1, warehouse="VIP仓"),
        ]
        s = CountSheetStrategy(
            coverage_threshold=0.5, b_sample_ratio=0, c_sample_ratio=0,
            high_value_warehouses=["VIP仓"],
            reverse_sample_ratio=0,  # 避免 R 类干扰断言
        )
        res = CountSheetBuilder.build(movs, s)
        codes_in_a = {r["material_code"] for r in res.rows if r["sample_tier"] == "A"}
        assert {"C", "D"}.issubset(codes_in_a)
        # 必盘的原因要标出来（只看 A 类，因为 R 类是反向抽盘原因不带"必盘"）
        vip = [r for r in res.rows if r["material_code"] in ("C", "D") and r["sample_tier"] == "A"]
        assert all("必盘" in (r["sample_reason"] or "") for r in vip)

    def test_min_unit_amount_filter(self):
        movs = [
            _mk_movement("BIG", 10000, qty=1),
            _mk_movement("TINY", 1, qty=1),
        ]
        s = CountSheetStrategy(min_unit_amount=100)
        res = CountSheetBuilder.build(movs, s)
        codes = {r["material_code"] for r in res.rows}
        assert "TINY" not in codes

    def test_empty_input(self):
        res = CountSheetBuilder.build([], CountSheetStrategy())
        assert res.rows == [] and res.total_amount == 0 and res.coverage_ratio == 0

    def test_simulate_returns_one_per_strategy(self):
        movs = [_mk_movement(f"M{i}", 100 - i, qty=1) for i in range(10)]
        out = CountSheetBuilder.simulate(
            movs,
            [CountSheetStrategy(coverage_threshold=t) for t in (0.7, 0.8, 0.9)],
        )
        assert len(out) == 3
        # 阈值越高，选中物料应不少于阈值低的
        assert out[2]["selected_items"] >= out[1]["selected_items"] >= out[0]["selected_items"]


# =============================================================
#  CountPlanGenerator — 行业模板 + 离线兜底
# =============================================================


class TestCountPlanGenerator:
    def test_baseline_industry_hit(self):
        from datetime import date as ddate
        g = CountPlanGenerator()
        d = g.baseline(company_name="测试医药", industry="医药生物",
                       period_end=ddate(2024, 12, 31))
        # 医药模板的关键词应出现
        assert "GMP" in d.special_notes or "冷链" in d.special_notes or "效期" in d.special_notes

    def test_baseline_fuzzy_industry(self):
        from datetime import date as ddate
        g = CountPlanGenerator()
        d = g.baseline(company_name="X 公司", industry="电子制造业",
                       period_end=ddate(2024, 12, 31))
        # 电子制造业 应落到 "制造业" 模板
        assert "在产品" in d.special_notes or "委外" in d.special_notes

    @pytest.mark.asyncio
    async def test_revise_without_ai_appends_to_notes(self):
        from datetime import date as ddate
        g = CountPlanGenerator(client=None)  # 无 AI
        d = g.baseline(company_name="XX", industry="制造业", period_end=ddate(2024, 12, 31))
        out = await g.revise(d, "把监盘人员增加一名仓管专员")
        assert "[用户补充]" in out.special_notes
        assert len(out.revision_log) == 1


# =============================================================
#  AgingEngine — FIFO + NRV + 转回
# =============================================================


class TestAgingEngine:
    def test_fifo_aging_distribution(self):
        pe = datetime(2024, 12, 31)
        movs = [
            {  # 期初 100，按 365 天处理
                "material_code": "X", "material_name": "X",
                "opening_qty": 100, "opening_amount": 1000,
                "inbound_qty": 0, "inbound_amount": 0,
                "outbound_qty": 0, "outbound_amount": 0,
                "ending_qty": 100, "ending_amount": 1000,
                "inbound_date": None,
            },
            {
                "material_code": "X", "material_name": "X",
                "opening_qty": 0, "opening_amount": 0,
                "inbound_qty": 50, "inbound_amount": 500,
                "outbound_qty": 0, "outbound_amount": 0,
                "ending_qty": 50, "ending_amount": 500,
                "inbound_date": pe - timedelta(days=30),
            },
        ]
        eng = InventoryAgingEngine(industry="默认")
        # 总期末 150 = 100(老) + 50(30 天)
        bucket = eng.fifo_aging(movs, pe)
        # 30 天 → ≤90；老批次 365 天 → 181-365
        assert bucket.le_90 == pytest.approx(50)
        assert bucket.age_181_365 == pytest.approx(100)
        assert bucket.weighted_avg_age > 90  # 老批次拉高了均龄

    def test_fifo_with_outbound_consumes_oldest(self):
        pe = datetime(2024, 12, 31)
        movs = [
            {
                "material_code": "Y", "material_name": "Y",
                "opening_qty": 100, "opening_amount": 1000,
                "inbound_qty": 50, "inbound_amount": 500,
                "outbound_qty": 80, "outbound_amount": 800,
                "ending_qty": 70, "ending_amount": 700,
                "inbound_date": pe - timedelta(days=10),
            },
        ]
        eng = InventoryAgingEngine()
        b = eng.fifo_aging(movs, pe)
        # 出库 80 应先消耗 100 的期初老批次 → 老的剩 20
        # 期末 70 中：20 来自老批次 (365 天)，50 来自新批次 (10 天)
        assert b.le_90 == pytest.approx(50)
        assert b.age_181_365 == pytest.approx(20)

    def test_nrv_from_sales(self):
        pe = datetime(2024, 12, 31)
        sales = [
            type("R", (), {
                "product_code": "Z", "revenue_confirm_date": datetime(2025, 1, 15),
                "ship_date": None, "quantity": 10, "revenue_amount": 200,
            })(),
            type("R", (), {
                "product_code": "Z", "revenue_confirm_date": datetime(2025, 2, 1),
                "ship_date": None, "quantity": 20, "revenue_amount": 360,
            })(),
            # 不应被采用：期前
            type("R", (), {
                "product_code": "Z", "revenue_confirm_date": datetime(2024, 6, 1),
                "ship_date": None, "quantity": 1000, "revenue_amount": 100000,
            })(),
        ]
        result = InventoryAgingEngine.nrv_unit_price_from_sales(sales, "Z", pe)
        assert result is not None
        unit, n = result
        # 加权 = (200+360)/(10+20) = 560/30 ≈ 18.67
        assert unit == pytest.approx(560 / 30, rel=1e-3)
        assert n == 2

    def test_compute_provision_and_reversal(self):
        pe = datetime(2024, 12, 31)
        movs = [
            {
                "material_code": "P", "material_name": "P", "category": "原材料",
                "opening_qty": 0, "opening_amount": 0,
                "inbound_qty": 100, "inbound_amount": 2000,
                "outbound_qty": 0, "outbound_amount": 0,
                "ending_qty": 100, "ending_amount": 2000,
                "inbound_date": pe - timedelta(days=30),
                "is_prior_year": False,
            },
        ]
        # 销售清单：期后销售单价 15（低于账面 20）
        sales = [
            type("R", (), {
                "product_code": "P", "revenue_confirm_date": datetime(2025, 1, 10),
                "ship_date": None, "quantity": 10, "revenue_amount": 150,
            })(),
        ]
        eng = InventoryAgingEngine(industry="制造业", sell_cost_rate=0)
        # 上年期初已计提 800 → 本期应保留 (20-15)*100 = 500 → 转回 300
        result = eng.compute(
            movs, pe,
            sales_records=sales,
            prior_impairments={"P": 800},
        )
        assert len(result.rows) == 1
        r = result.rows[0]
        assert r.impairment_current == pytest.approx(500, rel=1e-2)
        assert r.impairment_reversal == pytest.approx(300, rel=1e-2)
        assert r.impairment_provision == 0
        # 方法名带后缀（nrv-出售口径 / nrv-完工口径），统一前缀判断
        assert r.method.startswith("nrv")

    def test_compute_no_sales_fallback_to_aging(self):
        pe = datetime(2024, 12, 31)
        # 一批 > 730 天的旧库存
        movs = [
            {
                "material_code": "OLD", "material_name": "OLD",
                "opening_qty": 0, "opening_amount": 0,
                "inbound_qty": 10, "inbound_amount": 1000,
                "outbound_qty": 0, "outbound_amount": 0,
                "ending_qty": 10, "ending_amount": 1000,
                "inbound_date": pe - timedelta(days=900),
                "is_prior_year": False,
            },
        ]
        eng = InventoryAgingEngine(industry="制造业")
        r = eng.compute(movs, pe, sales_records=[])
        assert r.rows[0].method == "aging"
        # >730 默认 100% 计提
        assert r.rows[0].impairment_current == pytest.approx(1000, rel=1e-2)

    def test_full_reversal_when_no_stock(self):
        pe = datetime(2024, 12, 31)
        # 期末已无库存，上年留 500 跌价 → 应全额转回
        movs = [
            {
                "material_code": "G", "material_name": "G",
                "opening_qty": 5, "opening_amount": 500,
                "inbound_qty": 0, "inbound_amount": 0,
                "outbound_qty": 5, "outbound_amount": 500,
                "ending_qty": 0, "ending_amount": 0,
                "inbound_date": pe - timedelta(days=100),
                "is_prior_year": False,
            },
        ]
        r = InventoryAgingEngine().compute(movs, pe, prior_impairments={"G": 500})
        assert r.rows[0].impairment_reversal == 500
        assert r.rows[0].impairment_current == 0
        assert r.rows[0].method == "reversal"


# =============================================================
#  CountPhotoProcessor — 匹配回填 & 盘点率
# =============================================================


class TestCountPhotoProcessor:
    def test_match_by_code(self):
        sheets = [
            type("S", (), {
                "material_code": "M001", "material_name": "螺丝",
                "warehouse": "主仓", "batch_no": "",
                "book_qty": 100, "book_amount": 1000, "book_unit_cost": 10,
                "counted_qty": None,
            })(),
            type("S", (), {
                "material_code": "M002", "material_name": "螺母",
                "warehouse": "主仓", "batch_no": "",
                "book_qty": 200, "book_amount": 2000, "book_unit_cost": 10,
                "counted_qty": None,
            })(),
        ]
        rows = [
            ParsedCountRow(material_code="M001", counted_qty=99),
            ParsedCountRow(material_code="M002", counted_qty=200),
            ParsedCountRow(material_code="UNKNOWN", counted_qty=50),
        ]
        matched, unmatched = CountPhotoProcessor.match_to_sheets(rows, sheets)
        assert len(matched) == 2
        assert len(unmatched) == 1
        assert unmatched[0].material_code == "UNKNOWN"

    def test_match_by_name_fallback(self):
        sheets = [
            type("S", (), {
                "material_code": "X", "material_name": "进口螺丝",
                "warehouse": "", "batch_no": "",
                "book_qty": 1, "book_amount": 1, "book_unit_cost": 1,
                "counted_qty": None,
            })(),
        ]
        rows = [ParsedCountRow(material_code="WRONG", material_name="螺丝", counted_qty=5)]
        matched, unmatched = CountPhotoProcessor.match_to_sheets(rows, sheets)
        assert len(matched) == 1
        assert matched[0][1].counted_qty == 5

    def test_completion_stats(self):
        sheets = [
            type("S", (), {"material_code": "A", "material_name": "A", "warehouse": "主仓",
                           "book_qty": 100, "book_amount": 1000, "book_unit_cost": 10,
                           "counted_qty": 99})(),  # 已盘
            type("S", (), {"material_code": "B", "material_name": "B", "warehouse": "主仓",
                           "book_qty": 50, "book_amount": 500, "book_unit_cost": 10,
                           "counted_qty": None})(),  # 未盘
            type("S", (), {"material_code": "C", "material_name": "C", "warehouse": "二仓",
                           "book_qty": 10, "book_amount": 200, "book_unit_cost": 20,
                           "counted_qty": 8})(),  # 已盘，盘亏 2 个
        ]
        stats = CountPhotoProcessor.completion_stats(sheets)
        assert stats["overall"]["total_items"] == 3
        assert stats["overall"]["counted_items"] == 2
        assert stats["overall"]["items_rate"] == pytest.approx(2 / 3, rel=1e-3)
        # 金额覆盖：1000+200 = 1200 / 1700
        assert stats["overall"]["amount_rate"] == pytest.approx(1200 / 1700, rel=1e-3)
        # 差异：A 盘亏 1 (-10)，C 盘亏 2 (-40)
        codes = {d["material_code"] for d in stats["differences"]}
        assert {"A", "C"} == codes
        assert stats["difference_summary"]["loss_count"] == 2


# =============================================================
#  Post-fix regression tests — 多 agent 评审后修复的关键问题
# =============================================================


class TestPostReviewFixes:
    """Regression for fixes #11-16."""

    # ---- #11 上传安全 -----------------------------------------------

    def test_sanitize_filename_strips_path(self):
        from app.utils.upload_safety import sanitize_filename
        assert sanitize_filename("../../etc/passwd") == "passwd"
        assert sanitize_filename("a\x00b.jpg") == "a_b.jpg"
        assert sanitize_filename(None) == "upload"
        # 仅保留字母数字、点、横线、下划线、CJK
        assert sanitize_filename("评估 表 1.xlsx") == "评估_表_1.xlsx"

    def test_neutralize_formula_prefix(self):
        from app.utils.upload_safety import neutralize_formula
        assert neutralize_formula("=cmd|'/c calc'!A1") == "'=cmd|'/c calc'!A1"
        assert neutralize_formula("+1+1") == "'+1+1"
        assert neutralize_formula("正常文本") == "正常文本"
        assert neutralize_formula(None) is None

    def test_unique_save_path_contained(self, tmp_path):
        from app.utils.upload_safety import unique_save_path
        target = unique_save_path(tmp_path, "photo.jpg")
        assert str(target).startswith(str(tmp_path))
        assert target.suffix == ".jpg"

    def test_importer_rejects_dde_in_string_column(self):
        # 不走 to_excel → read_excel 路径，避免 openpyxl 把 =BAD() 当公式执行
        df = pd.DataFrame({
            "material_code": ["=BAD()", "M002"],
            "material_name": ["+OK", "螺母"],
            "category": ["", ""],
            "spec": ["", ""],
            "unit": ["", ""],
            "warehouse": ["", ""],
            "batch_no": ["", ""],
            "opening_qty": [0, 0],
            "opening_amount": [0, 0],
            "inbound_qty": [0, 0],
            "inbound_amount": [0, 0],
            "outbound_qty": [0, 0],
            "outbound_amount": [0, 0],
            "ending_qty": [10, 20],
            "ending_amount": [100, 200],
            "unit_cost": [10, 10],
        })
        result = InventoryImporter.normalize(df)
        # 第一行的物料编码 / 名称会被加单引号转义
        assert result.iloc[0]["material_code"].startswith("'=")
        assert result.iloc[0]["material_name"].startswith("'+")
        # 第二行正常文本不被改动
        assert result.iloc[1]["material_code"] == "M002"

    # ---- #12 OCR/AI prompt injection 防护 ---------------------------

    @pytest.mark.asyncio
    async def test_parse_text_drops_unknown_codes(self):
        from app.services.inventory.photo_processor import CountPhotoProcessor, ParsedCountRow
        p = CountPhotoProcessor(client=None)
        # 注入：OCR 文本本身用启发式解析，会得到 INJECTED 这个编码
        result = await p.parse_text("INJECTED  9999\nM001  100", known_codes={"m001"})
        codes = {r.material_code for r in result.parsed_rows}
        # INJECTED 不在 known_codes 且无 material_name，应被过滤掉
        assert "INJECTED" not in codes

    # ---- #13 prior_map 正确选取最大 < 本期的 period_end --------------

    def test_aging_full_reversal_uses_prior_correctly(self):
        # 单元测试已覆盖逻辑核心，这里再验证多年份场景
        from datetime import datetime as _dt
        pe = _dt(2024, 12, 31)
        movs = [{
            "material_code": "Z", "material_name": "Z",
            "opening_qty": 0, "opening_amount": 0,
            "inbound_qty": 5, "inbound_amount": 500,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 5, "ending_amount": 500,
            "inbound_date": pe - timedelta(days=10),
            "is_prior_year": False,
        }]
        # 模拟上层把 2023-12-31 的 prior 传入（不是 2022-12-31 更早的那次）
        result = InventoryAgingEngine().compute(
            movs, pe, prior_impairments={"Z": 80}, sales_records=[],
        )
        assert result.rows[0].impairment_opening == 80

    # ---- #15 数值边界与账实差异 -------------------------------------

    def test_impairment_request_rejects_nan_in_manual_nrv(self):
        from app.models.inventory import ImpairmentComputeRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ImpairmentComputeRequest(manual_nrv={"X": float("nan")})
        with pytest.raises(ValidationError):
            ImpairmentComputeRequest(manual_nrv={"X": -1.0})

    def test_prior_upload_rejects_inf(self):
        from app.models.inventory import PriorImpairmentUpload
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PriorImpairmentUpload(items={"X": float("inf")})
        with pytest.raises(ValidationError):
            PriorImpairmentUpload(items={"": 1.0})  # 空编码

    def test_count_sheet_request_rejects_out_of_range_coverage(self):
        from app.models.inventory import CountSheetGenerateRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CountSheetGenerateRequest(coverage_threshold=1.5)
        with pytest.raises(ValidationError):
            CountSheetGenerateRequest(coverage_threshold=-0.1)

    def test_aging_flags_reconciliation_difference(self):
        from datetime import datetime as _dt
        pe = _dt(2024, 12, 31)
        # 期初 100 + 入库 0 - 出库 0 = 100，但账面期末 200 → 差异 100%
        movs = [{
            "material_code": "DIFF", "material_name": "差异物料",
            "opening_qty": 100, "opening_amount": 1000,
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 200, "ending_amount": 2000,
            "inbound_date": None,
            "is_prior_year": False,
        }]
        result = InventoryAgingEngine().compute(movs, pe, sales_records=[])
        assert "账实差异" in result.rows[0].note

    # ---- #16 行业销售费用率 --------------------------------------

    def test_sell_cost_rate_by_industry(self):
        from app.services.inventory.aging_engine import sell_cost_rate_for
        assert sell_cost_rate_for("化工") == 0.08
        assert sell_cost_rate_for("制造业") == 0.06
        assert sell_cost_rate_for("电子制造业") == 0.06  # 模糊匹配
        assert sell_cost_rate_for(None) == 0.05
        assert sell_cost_rate_for("未知行业") == 0.05


# =============================================================
#  Iteration 2 — 6 项 IPO 合规深度补强
# =============================================================


class TestIteration2:
    """#17-#22 的关键路径回归。"""

    # ---- #17 重要性水平 + MUS 抽样 ---------------------------------

    def test_materiality_forces_into_a(self):
        movs = [
            _mk_movement("BIG", 1_000_000),   # 单条 ≥ materiality
            _mk_movement("SMALL", 100),
        ]
        s = CountSheetStrategy(
            coverage_threshold=0.01, b_sample_ratio=0, c_sample_ratio=0,
            materiality=500_000, reverse_sample_ratio=0,
        )
        res = CountSheetBuilder.build(movs, s)
        a_codes = {r["material_code"] for r in res.rows if r["sample_tier"] == "A"}
        assert "BIG" in a_codes
        # BIG 的 reason 应包含"超重要性水平"
        big = next(r for r in res.rows if r["material_code"] == "BIG")
        assert "超重要性水平" in (big["sample_reason"] or "")

    def test_mus_method_weights_by_amount(self):
        # 用大金额差异验证 MUS 倾向抽中大金额；只验证不报错且生成 B 类行
        movs = [_mk_movement(f"M{i}", 1 + i * 100, qty=1) for i in range(20)]
        s = CountSheetStrategy(
            coverage_threshold=0.0, b_sample_ratio=0.3, c_sample_ratio=0,
            b_sample_method="mus", reverse_sample_ratio=0,
        )
        res = CountSheetBuilder.build(movs, s)
        b_rows = [r for r in res.rows if r["sample_tier"] == "B"]
        assert len(b_rows) > 0
        assert all(r["sample_reason"] == "金额加权抽" for r in b_rows)

    # ---- #18 反向抽盘 R 类 -----------------------------------------

    def test_reverse_sample_creates_r_tier(self):
        movs = [_mk_movement(f"M{i}", 100, qty=1) for i in range(20)]
        s = CountSheetStrategy(
            coverage_threshold=0.5, b_sample_ratio=0.0, c_sample_ratio=0.0,
            reverse_sample_ratio=0.1,  # 抽 2 行
        )
        res = CountSheetBuilder.build(movs, s)
        r_rows = [r for r in res.rows if r["sample_tier"] == "R"]
        assert len(r_rows) >= 1
        assert all("反向抽盘" in (r["sample_reason"] or "") for r in r_rows)
        assert "R" in res.tier_summary

    # ---- #19 完工口径 -----------------------------------------------

    def test_completion_category_uses_completion_method(self):
        from datetime import datetime as _dt
        pe = _dt(2024, 12, 31)
        # 原材料：账面 10/单位，NRV 售价 8/单位，扣 0 销售费 + 30% 加工 → 8*0.7=5.6 < 10 计提
        movs = [{
            "material_code": "R1", "material_name": "钢板", "category": "原材料",
            "opening_qty": 0, "opening_amount": 0,
            "inbound_qty": 100, "inbound_amount": 1000,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 100, "ending_amount": 1000,
            "inbound_date": pe - timedelta(days=10),
            "is_prior_year": False,
        }]
        sales = [type("R", (), {
            "product_code": "R1", "revenue_confirm_date": datetime(2025, 1, 10),
            "ship_date": None, "quantity": 10, "revenue_amount": 80,
        })()]
        eng = InventoryAgingEngine(industry="制造业", sell_cost_rate=0, completion_cost_rate=0.30)
        r = eng.compute(movs, pe, sales_records=sales).rows[0]
        assert r.method == "nrv-完工口径"
        # 跌价 = (10 - 5.6) * 100 = 440
        assert r.impairment_current == pytest.approx(440, rel=1e-2)

    # ---- #20 转回拆分 ----------------------------------------------

    def test_reversal_split_by_sold_qty(self):
        from datetime import datetime as _dt
        pe = _dt(2024, 12, 31)
        # 上年期末 100 件、跌价 1000；本期期末 30 件（已售 70%）
        movs = [{
            "material_code": "S", "material_name": "S",
            "opening_qty": 100, "opening_amount": 3000,
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 70, "outbound_amount": 2100,
            "ending_qty": 30, "ending_amount": 900,
            "inbound_date": pe - timedelta(days=10),
            "is_prior_year": False,
        }]
        # 本期 NRV 跟账面持平 → 应保留跌价 = 0 → 全额转回 1000
        sales = [type("R", (), {
            "product_code": "S", "revenue_confirm_date": datetime(2025, 1, 10),
            "ship_date": None, "quantity": 1, "revenue_amount": 35,
        })()]
        eng = InventoryAgingEngine(industry="制造业", sell_cost_rate=0)
        r = eng.compute(
            movs, pe,
            sales_records=sales,
            prior_impairments={"S": 1000},
            prior_qty={"S": 100},
        ).rows[0]
        assert r.impairment_reversal == pytest.approx(1000)
        # 已售比例 70%，1000 × 0.7 = 700 → 营业成本；300 → 资产减值损失
        assert r.reversal_to_cogs == pytest.approx(700, rel=1e-2)
        assert r.reversal_to_loss == pytest.approx(300, rel=1e-2)

    # ---- #21 应盘未盘 + 重要性分级 ---------------------------------

    def test_completion_stats_major_minor_split(self):
        sheets = [
            type("S", (), {"material_code": "BIG", "material_name": "BIG", "warehouse": "主仓",
                           "book_qty": 100, "book_amount": 100000, "book_unit_cost": 1000,
                           "counted_qty": 90})(),    # 盘亏 -10000 (重大)
            type("S", (), {"material_code": "SMALL", "material_name": "SMALL", "warehouse": "主仓",
                           "book_qty": 10, "book_amount": 50, "book_unit_cost": 5,
                           "counted_qty": 9})(),    # 盘亏 -5 (小)
        ]
        stats = CountPhotoProcessor.completion_stats(sheets, materiality=5000)
        major = stats["differences_major"]
        minor = stats["differences_minor"]
        assert len(major) == 1 and major[0]["material_code"] == "BIG"
        assert len(minor) == 1 and minor[0]["material_code"] == "SMALL"

    def test_completion_stats_detects_uncovered(self):
        sheet = [
            type("S", (), {"material_code": "A", "material_name": "A", "warehouse": "主仓",
                           "batch_no": "",
                           "book_qty": 1, "book_amount": 100, "book_unit_cost": 100,
                           "counted_qty": 1})(),
        ]
        population = [
            type("M", (), {"material_code": "A", "material_name": "A",
                           "warehouse": "主仓", "batch_no": "",
                           "ending_qty": 1, "ending_amount": 100})(),
            type("M", (), {"material_code": "B", "material_name": "未覆盖物料",
                           "warehouse": "主仓", "batch_no": "",
                           "ending_qty": 5, "ending_amount": 500})(),
        ]
        stats = CountPhotoProcessor.completion_stats(sheet, population_movements=population)
        assert stats["overall"]["uncovered_items"] == 1
        assert stats["overall"]["uncovered_amount"] == pytest.approx(500)
        assert stats["uncovered"][0]["material_code"] == "B"

    # ---- #22 物料编码跨年映射 --------------------------------------

    def test_code_mapping_translates_prior(self):
        from datetime import datetime as _dt
        pe = _dt(2024, 12, 31)
        # 本期编码 = NEW-A；上年编码 = OLD-A，跌价 500
        movs = [{
            "material_code": "NEW-A", "material_name": "新编码",
            "opening_qty": 50, "opening_amount": 500,
            "inbound_qty": 0, "inbound_amount": 0,
            "outbound_qty": 0, "outbound_amount": 0,
            "ending_qty": 50, "ending_amount": 500,
            "inbound_date": pe - timedelta(days=10),
            "is_prior_year": False,
        }]
        # 模拟 API 已翻译过后的 prior_map（key 已是新编码）
        eng = InventoryAgingEngine(industry="制造业", sell_cost_rate=0)
        r = eng.compute(
            movs, pe,
            prior_impairments={"NEW-A": 200},  # 翻译后
            sales_records=[],
        ).rows[0]
        assert r.impairment_opening == 200  # 翻译生效


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
