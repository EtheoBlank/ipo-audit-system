"""Pydantic schemas for IPO Audit System."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ============ 项目相关 ============
class ProjectBase(BaseModel):
    """Base project schema."""
    name: str = Field(..., description="项目名称")
    company_name: str = Field(..., description="公司名称")
    industry: Optional[str] = Field(None, description="所属行业")
    fiscal_year: int = Field(..., description="审计年度")


class ProjectCreate(ProjectBase):
    """Create project schema."""
    pass


class ProjectUpdate(BaseModel):
    """Update project schema."""
    name: Optional[str] = None
    company_name: Optional[str] = None
    industry: Optional[str] = None
    fiscal_year: Optional[int] = None
    status: Optional[str] = None


class ProjectResponse(ProjectBase):
    """Project response schema."""
    id: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============ 科目余额表相关 ============
class AccountBalanceBase(BaseModel):
    """Base account balance schema."""
    account_code: str = Field(..., description="科目编码")
    account_name: str = Field(..., description="科目名称")
    balance_direction: str = Field(..., description="余额方向: 借/贷")
    beginning_balance: float = Field(0, description="期初余额")
    debit_amount: float = Field(0, description="借方发生额")
    credit_amount: float = Field(0, description="贷方发生额")
    ending_balance: float = Field(0, description="期末余额")


class AccountBalanceCreate(AccountBalanceBase):
    """Create account balance schema."""
    project_id: int


class AccountBalanceResponse(AccountBalanceBase):
    """Account balance response schema."""
    id: int
    project_id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ============ 序时账相关 ============
class ChronologicalAccountBase(BaseModel):
    """Base chronological account schema."""
    voucher_date: str = Field(..., description="凭证日期")
    voucher_no: str = Field(..., description="凭证号")
    account_code: str = Field(..., description="科目编码")
    account_name: str = Field(..., description="科目名称")
    debit_amount: float = Field(0, description="借方金额")
    credit_amount: float = Field(0, description="贷方金额")
    summary: Optional[str] = Field(None, description="摘要")
    auxiliary_accounting: Optional[str] = Field(None, description="辅助核算")


class ChronologicalAccountCreate(ChronologicalAccountBase):
    """Create chronological account schema."""
    project_id: int


class ChronologicalAccountResponse(ChronologicalAccountBase):
    """Chronological account response schema."""
    id: int
    project_id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ============ 银行对账单相关 ============
class BankStatementBase(BaseModel):
    """Base bank statement schema."""
    statement_date: str = Field(..., description="对账单日期")
    voucher_no: str = Field(..., description="凭证号")
    description: str = Field(..., description="描述")
    debit_amount: float = Field(0, description="借方金额")
    credit_amount: float = Field(0, description="贷方金额")
    balance: float = Field(0, description="余额")
    bank_account: Optional[str] = Field(None, description="银行账号")


class BankStatementCreate(BankStatementBase):
    """Create bank statement schema."""
    project_id: int


class BankStatementResponse(BankStatementBase):
    """Bank statement response schema."""
    id: int
    project_id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ============ 底稿生成相关 ============
class WorkbookGenerateRequest(BaseModel):
    """Request schema for generating workbooks."""
    project_id: int
    template_type: str = Field(..., description="模板类型: account_detail/income_statement/balance_sheet/cash_flow")
    include_charts: bool = Field(True, description="是否包含图表")


class WorkbookGenerateResponse(BaseModel):
    """Response schema for workbook generation."""
    file_path: str
    file_name: str
    download_url: str


# ============ 试算平衡相关 ============
class TrialBalanceRequest(BaseModel):
    """Trial balance request schema."""
    project_id: int


class TrialBalanceResponse(BaseModel):
    """Trial balance response schema."""
    is_balanced: bool
    total_debit: float
    total_credit: float
    difference: float
    account_details: list[dict]


# ============ 监管案例相关 ============
class RegulatoryCaseBase(BaseModel):
    """Base regulatory case schema."""
    case_no: str = Field(..., description="案例编号")
    case_type: str = Field(..., description="案例类型: 问询函/处罚决定")
    source: str = Field(..., description="来源: 证监会/交易所")
    publish_date: str = Field(..., description="发布日期")
    title: str = Field(..., description="标题")
    content: str = Field(..., description="内容")
    industry: Optional[str] = Field(None, description="所属行业")
    key_words: Optional[str] = Field(None, description="关键词")


class RegulatoryCaseCreate(RegulatoryCaseBase):
    """Create regulatory case schema."""
    pass


class RegulatoryCaseResponse(RegulatoryCaseBase):
    """Regulatory case response schema."""
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ============ AI 分析相关 ============
class RiskAnalysisRequest(BaseModel):
    """Risk analysis request schema."""
    project_id: int
    include_regulatory_cases: bool = Field(True, description="是否关联监管案例")


class RiskAnalysisResponse(BaseModel):
    """Risk analysis response schema."""
    project_id: int
    risk_level: str = Field(..., description="风险等级: 高/中/低")
    risk_points: list[dict]
    recommendations: list[str]
    related_cases: list[dict]


# ============ 通用响应 ============
class ApiResponse(BaseModel):
    """Standard API response."""
    success: bool = True
    message: str = "操作成功"
    data: Optional[dict] = None


class PaginatedResponse(BaseModel):
    """Paginated response schema."""
    items: list
    total: int
    page: int
    page_size: int
    total_pages: int