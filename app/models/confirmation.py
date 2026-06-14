"""Pydantic schemas for the confirmation (函证) module.

Provides:
- Subject catalogue (函证涉及的全部科目)
- ConfirmationCase / Item / Letter / Response schemas
- Generation, sending, response upload request/response models
- Statistics summary schemas
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.db_models import (
    ITEM_STATUS_LABELS,
    PARTY_TYPE_LABELS,
)


# ============================================================
# 1. 函证科目清单（涉及的全部科目）
# ============================================================


# 每个 entry: code, name, party_type, default_subjects, threshold (金额阈值)
# default_subjects 是发函时默认勾选的函证项

CONFIRMATION_SUBJECTS: list[dict[str, Any]] = [
    # ---- 货币资金 / 银行相关 --------------------------------------
    {
        "code": "1001",
        "name": "库存现金",
        "category": "货币资金",
        "party_type": "bank",  # 不函证，仅现金盘点
        "default_subjects": [],
        "is_cash": True,
        "note": "现金不函证，适用监盘程序",
    },
    {
        "code": "1002",
        "name": "银行存款",
        "category": "货币资金",
        "party_type": "bank",
        "default_subjects": [
            "存款余额（含活期/定期/通知存款/协定存款）",
            "存款利率与计息方式",
            "是否存在质押/冻结/担保/久悬",
            "银行对账单余额",
        ],
        "threshold": 0,
        "is_required": True,
    },
    {
        "code": "1012",
        "name": "其他货币资金",
        "category": "货币资金",
        "party_type": "bank",
        "default_subjects": [
            "外埠存款余额",
            "银行汇票存款",
            "银行本票存款",
            "信用证保证金",
            "保函保证金",
            "信用卡存款",
        ],
        "threshold": 0,
    },
    {
        "code": "1002-loan",
        "name": "短期借款 / 长期借款 / 应付债券",
        "category": "筹资",
        "party_type": "loan",
        "default_subjects": [
            "贷款余额（短期/长期）",
            "贷款利率与计息方式",
            "贷款起止日 / 到期日",
            "是否存在担保 / 抵押 / 质押",
            "未偿还本金及利息",
            "授信额度及占用情况",
            "是否存在违约 / 关注类 / 逾期",
        ],
        "threshold": 0,
    },
    {
        "code": "note-receivable",
        "name": "应收票据 / 应付票据",
        "category": "票据",
        "party_type": "bank",
        "default_subjects": [
            "银行承兑汇票余额及明细",
            "商业承兑汇票余额及明细",
            "已贴现未到期票据",
            "已背书未到期票据",
            "票据质押情况",
            "是否存在逾期 / 拒付",
        ],
        "threshold": 0,
    },
    {
        "code": "guarantee",
        "name": "对外担保 / 保函 / 信用证",
        "category": "或有事项",
        "party_type": "bank",
        "default_subjects": [
            "保函余额及明细",
            "信用证余额及明细",
            "对外担保余额",
            "履约保函 / 投标保函 / 预付款保函",
            "担保起止日 / 反担保安排",
        ],
        "threshold": 0,
    },
    # ---- 应收账款 / 预付 / 其他应收 --------------------------------
    {
        "code": "1122",
        "name": "应收账款",
        "category": "应收",
        "party_type": "customer",
        "default_subjects": [
            "应收账款余额（按我方账面）",
            "本期销售额（贷方发生额）",
            "本期回款额（借方发生额）",
            "未结算发票明细",
            "已背书/已转让未到期应收款",
            "关键销售合同条款（付款条件/账期/折扣/所有权保留）",
            "在执行销售订单",
            "是否存在退货折让安排",
        ],
        "threshold": 100000,  # 10 万
    },
    {
        "code": "1123",
        "name": "预付账款",
        "category": "应收",
        "party_type": "supplier",
        "default_subjects": [
            "预付账款余额",
            "对应采购合同条款",
            "本期采购到货情况",
            "在执行采购订单",
        ],
        "threshold": 100000,
    },
    {
        "code": "1221",
        "name": "其他应收款",
        "category": "应收",
        "party_type": "other_recv",
        "default_subjects": [
            "其他应收款余额",
            "款项性质（押金/备用金/代垫/关联方）",
            "本期发生额",
            "预计可收回性",
        ],
        "threshold": 50000,
    },
    # ---- 应付账款 / 预收 / 其他应付 --------------------------------
    {
        "code": "2202",
        "name": "应付账款",
        "category": "应付",
        "party_type": "supplier",
        "default_subjects": [
            "应付账款余额（按我方账面）",
            "本期采购额（借方发生额）",
            "本期付款额（贷方发生额）",
            "未结算发票明细",
            "已背书/已转让未到期应付款",
            "关键采购合同条款（付款条件/账期/质保金）",
            "在执行采购订单",
            "是否存在质量索赔/退货折让",
        ],
        "threshold": 100000,
    },
    {
        "code": "2203",
        "name": "预收账款 / 合同负债",
        "category": "应付",
        "party_type": "customer",
        "default_subjects": [
            "预收/合同负债余额",
            "对应销售合同条款",
            "本期确认收入情况",
            "在执行销售订单履约进度",
        ],
        "threshold": 100000,
    },
    {
        "code": "2241",
        "name": "其他应付款",
        "category": "应付",
        "party_type": "other_pay",
        "default_subjects": [
            "其他应付款余额",
            "款项性质（押金/代收代付/费用挂账/关联方）",
            "本期发生额",
        ],
        "threshold": 50000,
    },
    # ---- 投资 / 长期股权投资 -------------------------------------
    {
        "code": "1511",
        "name": "长期股权投资 / 投资款",
        "category": "投资",
        "party_type": "investment",
        "default_subjects": [
            "投资余额（账面/出资额）",
            "持股比例",
            "本期增减变动",
            "投资协议关键条款（分红/退出/业绩承诺）",
            "是否存在质押/冻结",
        ],
        "threshold": 0,
    },
    {
        "code": "1101",
        "name": "交易性金融资产 / 理财",
        "category": "投资",
        "party_type": "bank",
        "default_subjects": [
            "理财/结构性存款余额",
            "产品名称/编号/起止日",
            "预期收益率",
            "是否存在质押",
        ],
        "threshold": 0,
    },
    # ---- 特殊项 ----------------------------------------------------
    {
        "code": "litigation",
        "name": "诉讼 / 仲裁",
        "category": "或有事项",
        "party_type": "litigation",
        "default_subjects": [
            "在诉/在裁案件标的金额",
            "案件进展与可能结果",
            "代理律师",
        ],
        "threshold": 0,
    },
]


# ---- 银行询证函官方字段（中国注册会计师审计准则问题解答 + 财政部格式）---
# 来源: 财政部《银行询证函参考格式》(财会[2024]6号 等更新版)
# 主要信息类别: 1) 存款 2) 贷款 3) 银行承兑汇票 4) 信用证 5) 保函
#              6) 担保 7) 委托贷款 8) 托收 9) 资金归集/池 10) 投资理财 等

BANK_CONFIRMATION_TEMPLATE_FIELDS: list[dict[str, str]] = [
    {"section": "存款", "field": "活期存款余额"},
    {"section": "存款", "field": "定期存款余额"},
    {"section": "存款", "field": "通知存款余额"},
    {"section": "存款", "field": "协定存款余额"},
    {"section": "存款", "field": "结构性存款余额"},
    {"section": "存款", "field": "外币存款余额（折人民币）"},
    {"section": "存款", "field": "存款利率"},
    {"section": "存款", "field": "是否存在质押/冻结/担保"},
    {"section": "贷款", "field": "短期借款余额"},
    {"section": "贷款", "field": "长期借款余额"},
    {"section": "贷款", "field": "贷款利率 / 起止日 / 到期日"},
    {"section": "贷款", "field": "未偿还本金及利息"},
    {"section": "贷款", "field": "担保 / 抵押 / 质押情况"},
    {"section": "贷款", "field": "授信额度及占用"},
    {"section": "贷款", "field": "是否存在违约 / 关注类 / 逾期"},
    {"section": "票据", "field": "银行承兑汇票余额（出票/承兑）"},
    {"section": "票据", "field": "商业承兑汇票余额"},
    {"section": "票据", "field": "已贴现未到期票据"},
    {"section": "票据", "field": "已背书未到期票据"},
    {"section": "票据", "field": "票据质押情况"},
    {"section": "票据", "field": "票据逾期/拒付"},
    {"section": "信用证", "field": "信用证余额（开立/未使用）"},
    {"section": "信用证", "field": "信用证到期日"},
    {"section": "保函", "field": "保函余额（履约/投标/预付款/质保）"},
    {"section": "保函", "field": "保函起止日"},
    {"section": "担保", "field": "对外担保余额"},
    {"section": "担保", "field": "被担保方 / 反担保安排"},
    {"section": "委托贷款", "field": "委托贷款本金/利息"},
    {"section": "资金归集", "field": "资金池/归集账户余额"},
    {"section": "其他", "field": "其他业务（请函证方说明）"},
]


# ============================================================
# 2. 函证 Pydantic schemas
# ============================================================


# ---- 案卷 (Case) ----------------------------------------------------


class ConfirmationCaseCreateRequest(BaseModel):
    project_id: int
    case_name: str
    period_end: date
    fiscal_year: int
    generated_by: Optional[str] = None
    notes: Optional[str] = None


class ConfirmationCaseResponse(BaseModel):
    id: int
    project_id: int
    case_name: str
    period_end: str
    fiscal_year: int
    is_locked: bool
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    lock_reason: Optional[str] = None
    generated_at: datetime
    generated_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- 函证对象 (Item) -----------------------------------------------


class ConfirmationItemResponse(BaseModel):
    id: int
    case_id: int
    party_type: str
    party_type_label: str = ""
    party_name: str
    party_id: Optional[str] = None
    contact_person: Optional[str] = None
    contact_info: Optional[str] = None
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    book_balance: float
    book_balance_date: Optional[str] = None
    subject_matters: list[str] = Field(default_factory=list)
    total_confirm_amount: float = 0.0
    selection_method: str
    selection_reason: Optional[str] = None
    importance: str
    status: str
    status_label: str = ""
    sent_letter_id: Optional[int] = None
    response_id: Optional[int] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_item(cls, obj: Any) -> "ConfirmationItemResponse":
        """Helper that decorates status/party_type with human labels."""
        import json as _json

        try:
            subjects = _json.loads(obj.subject_matters or "[]")
            if not isinstance(subjects, list):
                subjects = [str(subjects)]
        except Exception:
            subjects = []
        return cls(
            id=obj.id,
            case_id=obj.case_id,
            party_type=obj.party_type,
            party_type_label=PARTY_TYPE_LABELS.get(obj.party_type, obj.party_type),
            party_name=obj.party_name,
            party_id=obj.party_id,
            contact_person=obj.contact_person,
            contact_info=obj.contact_info,
            account_code=obj.account_code,
            account_name=obj.account_name,
            book_balance=obj.book_balance or 0.0,
            book_balance_date=obj.book_balance_date,
            subject_matters=subjects,
            total_confirm_amount=obj.total_confirm_amount or 0.0,
            selection_method=obj.selection_method,
            selection_reason=obj.selection_reason,
            importance=obj.importance,
            status=obj.status,
            status_label=ITEM_STATUS_LABELS.get(obj.status, obj.status),
            sent_letter_id=obj.sent_letter_id,
            response_id=obj.response_id,
        )


# ---- 统计表生成请求 --------------------------------------------------


class SubjectSelection(BaseModel):
    account_code: str
    account_name: str
    party_type: str
    party_name: str
    party_id: Optional[str] = None
    book_balance: float = 0.0
    book_balance_date: Optional[str] = None
    subject_matters: list[str] = Field(default_factory=list)
    importance: str = "B"  # A=必发 / B=抽样 / C=补充
    selection_reason: Optional[str] = None
    contact_person: Optional[str] = None
    contact_info: Optional[str] = None
    account_codes: list[str] = Field(default_factory=list)  # 多科目合并时使用


class GenerateStatsRequest(BaseModel):
    case_id: int
    period_end: Optional[date] = None
    # 选样规则
    bank_threshold: float = 0  # 银行: 0 必发（银行机构数）
    customer_threshold: float = 100000  # 客户: 10 万
    supplier_threshold: float = 100000  # 供应商: 10 万
    other_threshold: float = 50000
    # 抽样补充
    additional_sample_ratio: float = 0.10  # 阈值以下随机抽样比例
    random_seed: int = 42
    # 是否包含内部交易对手方
    include_zero_balance: bool = False
    selected_items: Optional[list[SubjectSelection]] = None  # 用户手工调整后
    persist: bool = True
    generated_by: Optional[str] = None


class GenerateStatsResponse(BaseModel):
    case_id: int
    selected_count: int
    total_amount: float
    by_party_type: dict[str, dict[str, Any]] = Field(default_factory=dict)
    items: list[ConfirmationItemResponse] = Field(default_factory=list)


# ---- 锁定 / 确定发函 --------------------------------------------------


class LockCaseRequest(BaseModel):
    locked_by: str
    lock_reason: Optional[str] = None


# ---- 发函 (Letter) --------------------------------------------------


class SendLetterRequest(BaseModel):
    item_id: int
    sent_date: date
    sent_method: str = "邮寄"
    sent_by: Optional[str] = None
    sender_firm: Optional[str] = None
    recipient: Optional[str] = None
    recipient_address: Optional[str] = None
    courier_no: Optional[str] = None
    expected_reply_date: Optional[date] = None
    template_id: str = "standard"  # bank_official / customer_std / supplier_std / other_std
    file_format: str = "docx"  # docx / pdf
    notes: Optional[str] = None


class ConfirmationLetterResponse(BaseModel):
    id: int
    case_id: int
    item_id: int
    letter_no: str
    letter_type: str
    template_id: Optional[str] = None
    sent_date: datetime
    sent_method: str
    sent_by: Optional[str] = None
    sender_firm: Optional[str] = None
    recipient: Optional[str] = None
    recipient_address: Optional[str] = None
    courier_no: Optional[str] = None
    content_snapshot: Optional[str] = None
    amount_snapshot: Optional[dict[str, Any]] = None
    file_path: Optional[str] = None
    file_format: Optional[str] = None
    letter_status: str
    expected_reply_date: Optional[datetime] = None
    reminder_count: int
    last_reminded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- 回函 (Response) ------------------------------------------------


class ConfirmationResponseInput(BaseModel):
    """手工录入的回函信息。"""

    letter_id: int
    received_date: Optional[date] = None
    response_method: str = "纸质原件"
    response_status: str = "match"  # match / partial / mismatch / reject / unclear
    amount_confirmed: float = 0.0
    difference_reason: Optional[str] = None
    response_summary: Optional[str] = None
    subjects_detail: Optional[dict[str, Any]] = None
    auditor_note: Optional[str] = None
    confirmed_by: Optional[str] = None


class PhotoUploadResponse(BaseModel):
    photo_id: int
    response_id: int
    ocr_engine: str
    parsed: bool
    matched_amount: Optional[float] = None
    parsed_data: Optional[dict[str, Any]] = None
    message: str = ""


class ConfirmationResponseDetail(BaseModel):
    id: int
    letter_id: int
    received_date: Optional[datetime] = None
    response_method: str
    response_status: str
    response_status_label: str = ""
    amount_confirmed: float
    amount_difference: float
    difference_reason: Optional[str] = None
    response_summary: Optional[str] = None
    subjects_detail: Optional[dict[str, Any]] = None
    raw_text: Optional[str] = None
    ai_extracted: Optional[dict[str, Any]] = None
    is_manually_confirmed: bool
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    auditor_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    photos: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ---- 汇总统计 -------------------------------------------------------


class ConfirmationSummaryResponse(BaseModel):
    """一份统计表的函证汇总。"""

    case_id: int
    case_name: str
    period_end: str
    is_locked: bool

    # 总体
    total_items: int = 0
    total_amount: float = 0.0
    total_confirmed: float = 0.0
    total_difference: float = 0.0

    # 状态分布
    status_summary: dict[str, int] = Field(default_factory=dict)
    response_status_summary: dict[str, int] = Field(default_factory=dict)

    # 按 party_type 分组
    by_party_type: list[dict[str, Any]] = Field(default_factory=list)

    # 回函率
    sent_count: int = 0
    responded_count: int = 0
    response_rate: float = 0.0

    # 差异
    items_with_difference: int = 0
    total_difference_amount: float = 0.0

    # 待办
    pending_items: list[dict[str, Any]] = Field(default_factory=list)
    no_reply_items: list[dict[str, Any]] = Field(default_factory=list)


# ---- 模板元数据 ------------------------------------------------------


class SubjectInfo(BaseModel):
    code: str
    name: str
    category: str
    party_type: str
    party_type_label: str
    default_subjects: list[str]
    threshold: float = 0.0
    is_cash: bool = False
    is_required: bool = False
    note: Optional[str] = None


class SubjectCatalogueResponse(BaseModel):
    subjects: list[SubjectInfo]
    bank_template_fields: list[dict[str, str]]
    party_types: dict[str, str]
    response_status_labels: dict[str, str]
    item_status_labels: dict[str, str]
