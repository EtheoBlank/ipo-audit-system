"""Confirmation module P0 修复端到端验证."""
import asyncio
import os
import sys
import json
from datetime import datetime, timezone

# Setup path (conftest.py 已经把 ROOT 加进 sys.path; 这里只补 site-packages)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".venv/Lib/site-packages"))
# 注意: 之前这里有 `for mod in list(sys.modules): if mod.startswith("app"): del sys.modules[mod]`
# 那一段会把 SQLAlchemy mapper registry 搞坏, 导致 test_auth.py 等后续测试的
# `from app.models.db.auth import User` 报 "expression 'Firm' failed to locate a name" — CI 全炸.
# 已删除, 改成依靠 conftest.py / pytest-asyncio 的 fixture.

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import logging
logging.disable(logging.CRITICAL)

from app.core.database import engine, Base, AsyncSessionLocal
from app.models.db_models import (
    Project, ConfirmationCase, ConfirmationItem, ConfirmationLetter,
    ConfirmationResponse,
    AccountBalance, ChronologicalAccount, BankStatement,
    PARTY_TYPE_BANK, ITEM_STATUS_MISMATCH, ITEM_STATUS_SENT,
)
from app.services.confirmation.stats_builder import ConfirmationStatsBuilder
from app.services.confirmation.response_processor import _heuristic_parse
from app.services.confirmation.excel_exporter import ConfirmationExporter
from app.models.confirmation import GenerateStatsRequest
from app.api.confirmations import _letter_no
from sqlalchemy import select, delete


async def e2e():
    print("=== P0 修复端到端验证 ===")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("1. [P0] Schema 创建 OK (修复 ORM 三向循环)")

    async with AsyncSessionLocal() as db:
        # 项目 + 账套
        p = Project(name="T", company_name="X", fiscal_year=2024)
        db.add(p); await db.commit(); await db.refresh(p)
        for acct, name, bal in [("6225880100000001", "工商银行", 500000.0),
                                 ("6225880100000002", "建设银行", 800000.0)]:
            db.add(BankStatement(project_id=p.id, statement_date="2024-12-31",
                voucher_no="X", description=name, debit_amount=0, credit_amount=0,
                balance=bal, bank_account=acct))
        for cust, bal in [("客户A", 200000.0), ("客户B", 1500000.0)]:
            db.add(ChronologicalAccount(project_id=p.id, voucher_date="2024-12-31",
                voucher_no="X", account_code="1122", account_name="应收账款",
                debit_amount=bal, credit_amount=0, auxiliary_accounting=cust))
        await db.commit()
        c = ConfirmationCase(project_id=p.id, case_name="T",
                             period_end="2024-12-31", fiscal_year=2024)
        db.add(c); await db.commit(); await db.refresh(c)
        print("2. 账套数据 OK")

        # 3) 统计表生成
        builder = ConfirmationStatsBuilder(db)
        req = GenerateStatsRequest(case_id=c.id, bank_threshold=0,
            customer_threshold=100000, supplier_threshold=100000,
            other_threshold=50000, additional_sample_ratio=0.5,
            random_seed=42, persist=True)
        result = await builder.generate(req)
        assert result["selected_count"] == 4, f"期望 4 个 item, 实际 {result['selected_count']}"
        print(f"3. [P0] 统计表生成 OK: {result['selected_count']} 个对象, "
              f"总额 {result['total_amount']:,.2f}")

        # 4) 锁定
        c.is_locked = True
        c.locked_at = datetime.now(timezone.utc)
        c.locked_by = "审计师"
        items = (await db.execute(
            select(ConfirmationItem).where(ConfirmationItem.case_id == c.id)
        )).scalars().all()
        for it in items:
            it.status = "confirmed"
        await db.commit()
        print("4. [P0] 案卷锁定 OK (状态机正常)")

        # 5) 发函
        first_item = items[0]
        sent_dt = datetime(2024, 12, 31)
        letter = ConfirmationLetter(
            case_id=c.id, item_id=first_item.id,
            letter_no=_letter_no(first_item.party_type, c.id, first_item.id,
                                 sent_dt, 1),
            letter_type=first_item.party_type, template_id="bank_official",
            sent_date=sent_dt, sent_method="邮寄", sent_by="审计师",
            recipient="中国工商银行", content_snapshot="询证函内容...",
            amount_snapshot=json.dumps({"book_balance": 500000.0}),
            file_path="/tmp/test.docx", file_format="docx",
            letter_status="sent", seq=1,
        )
        db.add(letter); await db.flush()
        first_item.status = ITEM_STATUS_SENT
        first_item.sent_letter_id = letter.id
        first_item.subject_matters_snapshot = first_item.subject_matters
        first_item.book_balance_snapshot = first_item.book_balance
        first_item.version = 2
        await db.commit()
        print(f"5. [P0] 发函 OK: {letter.letter_no} (含 seq 后缀防冲突)")

        # 6) 回函 mismatch
        resp = ConfirmationResponse(
            letter_id=letter.id, received_date=sent_dt,
            response_method="扫描件", response_status="mismatch",
            amount_confirmed=499000.0, amount_difference=-1000.0,
            difference_reason="跨期", version=1,
        )
        db.add(resp); await db.flush()
        first_item.status = ITEM_STATUS_MISMATCH
        first_item.response_id = resp.id
        first_item.version = 3
        await db.commit()
        print("6. [P0] 回函 mismatch OK (新增 ITEM_STATUS_MISMATCH)")

        # 7) void + resend
        await db.execute(delete(ConfirmationResponse).where(
            ConfirmationResponse.letter_id == letter.id))
        first_item.response_id = None
        first_item.status = "confirmed"
        first_item.version = 4
        letter.letter_status = "voided"
        first_item.sent_letter_id = None
        first_item.subject_matters_snapshot = None
        first_item.book_balance_snapshot = None
        first_item.version = 5
        await db.commit()
        letter2 = ConfirmationLetter(
            case_id=c.id, item_id=first_item.id,
            letter_no=_letter_no(first_item.party_type, c.id, first_item.id,
                                 sent_dt, 2),
            letter_type=first_item.party_type, template_id="bank_official",
            sent_date=sent_dt, sent_method="电邮", recipient="中国工商银行",
            content_snapshot="询证函内容... v2",
            amount_snapshot=json.dumps({"book_balance": 500000.0}),
            file_path="/tmp/test2.docx", file_format="docx",
            letter_status="sent", seq=2,
        )
        db.add(letter2); await db.flush()
        first_item.status = ITEM_STATUS_SENT
        first_item.sent_letter_id = letter2.id
        first_item.version = 6
        await db.commit()
        print("7. [P0] Void + Resend OK: seq=2 (无 letter_no 冲突)")

        # 8) heuristic
        r = _heuristic_parse("信息证明无误 余额 1,000,000.00")
        assert r.response_status == "unclear", \
            f"heuristic 应返回 unclear, 实际 {r.response_status}"
        assert r.amount_confirmed == 0.0, \
            f"heuristic 不应自动 amount, 实际 {r.amount_confirmed}"
        print("8. [P0] heuristic 返回 unclear only (不取 max 误识别)")

        # 9) Excel 导出
        all_items = (await db.execute(
            select(ConfirmationItem).where(ConfirmationItem.case_id == c.id)
        )).scalars().all()
        all_letters = (await db.execute(
            select(ConfirmationLetter).where(ConfirmationLetter.case_id == c.id)
        )).scalars().all()
        all_responses = (await db.execute(
            select(ConfirmationResponse).join(
                ConfirmationLetter,
                ConfirmationResponse.letter_id == ConfirmationLetter.id)
            .where(ConfirmationLetter.case_id == c.id)
        )).scalars().all()
        data = ConfirmationExporter.build(all_items, all_letters, all_responses)
        assert len(data) > 1000, f"Excel 数据过小: {len(data)} bytes"
        print(f"9. [P0] Excel 导出 OK: {len(data):,} bytes")


try:
    asyncio.run(e2e())
    print("\n=== 全部 P0 修复通过 ===")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("=== FAIL ===")
