"""ERP系统数据接口适配器 - 支持金蝶、用友、SAP等主流ERP系统的数据导入."""

import pandas as pd
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum


class ERPType(Enum):
    """支持的ERP系统类型."""

    KINGDEE = "金蝶K3"  # 金蝶K3 Cloud
    KINGDEE_WISE = "金蝶云星空"  # 金蝶云星空
    YONYOU_NC = "用友NC"  # 用友NC
    YONYOU_U8 = "用友U8"  # 用友U8
    YONYOU_YONBIP = "用友YonBIP"  # 用友YonBIP
    SAP = "SAP"  # SAP S/4HANA
    SAP_ECC = "SAP_ECC"  # SAP ECC
    MANUAL = "手动导入"  # 手动整理的标准格式


@dataclass
class ERPColumnMapping:
    """ERP字段映射配置."""

    erp_field: str  # ERP系统原始字段名
    standard_field: str  # 标准字段名
    field_type: str  # 字段类型: string/number/date
    description: str  # 字段说明


@dataclass
class ERPParserResult:
    """ERP解析结果."""

    success: bool
    message: str
    data: Optional[pd.DataFrame] = None
    record_count: int = 0
    warnings: List[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class BaseERPAdapter(ABC):
    """ERP适配器基类.

    提供 parse_account_balance / parse_chronological_account / parse_bank_statement
    的默认实现 (map_columns → _normalize_direction → to_numeric + fillna(0)).
    子类只需覆盖 get_name / get_column_mappings / (可选) _normalize_direction.
    """

    # 3 类数据分别强转的数值字段 (子类可扩展)
    NUMERIC_FIELDS_BY_TYPE: Dict[str, List[str]] = {
        "account_balance": [
            "beginning_balance",
            "debit_amount",
            "credit_amount",
            "ending_balance",
        ],
        "chronological_account": ["debit_amount", "credit_amount"],
        "bank_statement": ["debit_amount", "credit_amount", "balance"],
    }
    # 默认方向列名 (与 map_columns 输出对齐); 子类通常无需改
    DIRECTION_FIELD: str = "balance_direction"

    def __init__(self):
        self.column_mappings: List[ERPColumnMapping] = []
        self.required_fields: List[str] = []

    @staticmethod
    def infer_balance_direction(account_code: Optional[str], ending_balance: float = 0.0) -> str:
        """P0 修复: 按 account_code 前缀推导借贷方向.

        旧版用 ending_balance >= 0 判断 → 负债 ending_balance 经常 > 0 被错判为"借".
        中国会计准则科目编码:
          - 1xxx 资产 (默认借方余额)
          - 2xxx 负债 (默认贷方余额)
          - 3xxx 所有者权益 (默认贷方余额)
          - 4xxx 权益类调整 (实收资本/资本公积, 贷方余额)
          - 5xxx 成本费用 (借方)
          - 6xxx 收入 (贷方)
          - 7xxx 损益类 (借方)
        ending_balance 反号时 (备抵/反向), 方向反转.
        """
        if not account_code:
            return "借"
        code = str(account_code).strip()
        if code.startswith("1"):  # 资产
            return "借" if ending_balance >= 0 else "贷"
        if code.startswith(("2", "3", "4")):  # 负债 / 权益
            return "贷" if ending_balance >= 0 else "借"
        if code.startswith("5"):  # 成本费用
            return "借" if ending_balance >= 0 else "贷"
        if code.startswith(("6", "7")):  # 收入 / 损益
            return "贷" if ending_balance >= 0 else "借"
        return "借"

    @abstractmethod
    def get_name(self) -> str:
        """返回ERP系统名称."""
        pass

    @abstractmethod
    def get_column_mappings(self) -> List[ERPColumnMapping]:
        """返回字段映射配置."""
        pass

    # ---- 三个 parse_* 的默认实现 ----

    def parse_account_balance(self, raw_data: pd.DataFrame) -> pd.DataFrame:
        """解析科目余额表数据 (默认实现: map → direction → numeric)."""
        df = self._coerce_numeric(
            self._normalize_direction(self.map_columns(raw_data)),
            "account_balance",
        )
        return df

    def parse_chronological_account(self, raw_data: pd.DataFrame) -> pd.DataFrame:
        """解析序时账数据 (默认实现: map → numeric, 不处理方向)."""
        return self._coerce_numeric(self.map_columns(raw_data), "chronological_account")

    def parse_bank_statement(self, raw_data: pd.DataFrame) -> pd.DataFrame:
        """解析银行对账单数据 (默认实现: map → numeric)."""
        return self._coerce_numeric(self.map_columns(raw_data), "bank_statement")

    # ---- 子类覆盖点 ----

    def _normalize_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        """子类按 ERP 习惯转换 balance_direction.

        默认: 若列存在则保持原样 (Manual 已是"借/贷"中文).
        子类: 按 ERP 原生编码 (1/2、S/H、j/d 等) 转"借/贷".
        """
        return df

    def _coerce_numeric(self, df: pd.DataFrame, data_type: str) -> pd.DataFrame:
        """按 data_type 强转数值为 float + fillna(0)."""
        for field in self.NUMERIC_FIELDS_BY_TYPE.get(data_type, []):
            if field in df.columns:
                df[field] = pd.to_numeric(df[field], errors="coerce").fillna(0)
        return df

    def validate_data(self, df: pd.DataFrame, data_type: str) -> ERPParserResult:
        """验证数据完整性."""
        warnings = []
        missing_fields = []

        for mapping in self.column_mappings:
            if mapping.standard_field in self.required_fields:
                if mapping.standard_field not in df.columns:
                    missing_fields.append(mapping.standard_field)

        if missing_fields:
            return ERPParserResult(
                success=False,
                message=f"缺少必需字段: {', '.join(missing_fields)}",
                warnings=warnings,
            )

        return ERPParserResult(
            success=True,
            message="数据验证通过",
            data=df,
            record_count=len(df),
            warnings=warnings,
        )

    def map_columns(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """将ERP原始字段映射为标准字段."""
        mapping_dict = {m.erp_field: m.standard_field for m in self.get_column_mappings()}
        available_mapping = {k: v for k, v in mapping_dict.items() if k in raw_df.columns}
        return raw_df.rename(columns=available_mapping)


# ============ 金蝶K3 Cloud适配器 ============
class KingdeeK3Adapter(BaseERPAdapter):
    """金蝶K3 Cloud 适配器.

    金蝶K3 Cloud常用表名和字段:
    - T_BD_Account (科目表)
    - T_BALANCE (余额表)
    - T_GL_VOUCHER (凭证表)
    - T_BOS_WFINSTANCERECORD (银行对账单)

    典型字段: FAccountID, FAccountName, FBalanceLocal, FDebit, FCredit
    """

    def get_name(self) -> str:
        return "金蝶K3 Cloud"

    def get_column_mappings(self) -> List[ERPColumnMapping]:
        return [
            # 科目余额表
            ERPColumnMapping("FAccountID", "account_code", "string", "科目编码"),
            ERPColumnMapping("FAccountName", "account_name", "string", "科目名称"),
            ERPColumnMapping("FAccountProperty", "balance_direction", "string", "科目属性(借/贷)"),
            ERPColumnMapping("FBeginBalance", "beginning_balance", "number", "期初余额"),
            ERPColumnMapping("FDebit", "debit_amount", "number", "借方发生额"),
            ERPColumnMapping("FCredit", "credit_amount", "number", "贷方发生额"),
            ERPColumnMapping("FEndBalance", "ending_balance", "number", "期末余额"),
            # 序时账
            ERPColumnMapping("FVoucherDate", "voucher_date", "date", "凭证日期"),
            ERPColumnMapping("FVoucherNo", "voucher_no", "string", "凭证号"),
            ERPColumnMapping("FExplanation", "summary", "string", "摘要"),
            ERPColumnMapping("FAuxiliary", "auxiliary_accounting", "string", "辅助核算"),
            # 银行对账单
            ERPColumnMapping("FBankDate", "statement_date", "date", "对账日期"),
            ERPColumnMapping("FDescription", "description", "string", "描述"),
            ERPColumnMapping("FBankBalance", "balance", "number", "余额"),
            ERPColumnMapping("FBankAccount", "bank_account", "string", "银行账号"),
        ]

    def _normalize_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        # 金蝶科目属性: 1=借, 2=贷 (字符串"借"也按借处理)
        if self.DIRECTION_FIELD in df.columns:
            df[self.DIRECTION_FIELD] = df[self.DIRECTION_FIELD].apply(
                lambda x: "借" if str(x) in ["1", "借"] else "贷"
            )
        return df


# ============ 金蝶云星空适配器 ============
class KingdeeCloudAdapter(BaseERPAdapter):
    """金蝶云星空适配器.

    云星空常用表名和字段:
    - BD_Account (科目表)
    - GL_Balance (余额表)
    - GL_Voucher (凭证表)
    - CN_BankStatement (银行对账单)

    典型字段: FNumber, FName, FBalanceDr, FBalanceCr
    """

    def get_name(self) -> str:
        return "金蝶云星空"

    def get_column_mappings(self) -> List[ERPColumnMapping]:
        return [
            # 科目余额表
            ERPColumnMapping("FNumber", "account_code", "string", "科目编码"),
            ERPColumnMapping("FName", "account_name", "string", "科目名称"),
            ERPColumnMapping("FBalanceDr", "beginning_balance", "number", "期初借方"),
            ERPColumnMapping("FBalanceCr", "beginning_balance_cr", "number", "期初贷方"),
            ERPColumnMapping("FDebit", "debit_amount", "number", "借方发生额"),
            ERPColumnMapping("FCredit", "credit_amount", "number", "贷方发生额"),
            ERPColumnMapping("FEndBalanceDr", "ending_balance", "number", "期末借方"),
            ERPColumnMapping("FEndBalanceCr", "ending_balance_cr", "number", "期末贷方"),
            # 序时账
            ERPColumnMapping("FDate", "voucher_date", "date", "日期"),
            ERPColumnMapping("FBillNo", "voucher_no", "string", "单据编号"),
            ERPColumnMapping("FExp", "summary", "string", "摘要"),
            ERPColumnMapping("FAccountID", "account_code", "string", "科目"),
            ERPColumnMapping("FAmountDr", "debit_amount", "number", "借方金额"),
            ERPColumnMapping("FAmountCr", "credit_amount", "number", "贷方金额"),
            # 银行对账单
            ERPColumnMapping("FBankDate", "statement_date", "date", "银行日期"),
            ERPColumnMapping("FRemark", "description", "string", "备注"),
            ERPColumnMapping("FOutAmount", "debit_amount", "number", "支出金额"),
            ERPColumnMapping("FInAmount", "credit_amount", "number", "收入金额"),
            ERPColumnMapping("FBalance", "balance", "number", "余额"),
        ]

    def _normalize_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        # P0 修复: 借贷方向按 account_code 前缀推导 (BaseERPAdapter.infer_balance_direction)
        # 旧版 ending_balance >= 0 → 负债 ending>0 误判为"借"
        if "account_code" in df.columns:
            df[self.DIRECTION_FIELD] = df.apply(
                lambda row: self.infer_balance_direction(
                    row.get("account_code"), row.get("ending_balance", 0) or 0
                ),
                axis=1,
            )
        else:
            df[self.DIRECTION_FIELD] = "借"
        return df


# ============ 用友NC适配器 ============
class YongyouNCAdapter(BaseERPAdapter):
    """用友NC适配器.

    用友NC常用表名和字段:
    - bd_accasoa (科目表)
    - gl_balance (余额表)
    - gl_voucher (凭证表)
    - bp_bankstatement (银行对账单)

    典型字段: accoaudcode, accoaudname, direct,primdebit, primcredit
    """

    def get_name(self) -> str:
        return "用友NC"

    def get_column_mappings(self) -> List[ERPColumnMapping]:
        return [
            # 科目余额表
            ERPColumnMapping("accoaudcode", "account_code", "string", "科目编码"),
            ERPColumnMapping("accoaudname", "account_name", "string", "科目名称"),
            ERPColumnMapping("direct", "balance_direction", "string", "余额方向(1借/-1贷)"),
            ERPColumnMapping("primdebit", "debit_amount", "number", "借方发生额(原币)"),
            ERPColumnMapping("primcredit", "credit_amount", "number", "贷方发生额(原币)"),
            ERPColumnMapping("balance", "ending_balance", "number", "期末余额"),
            ERPColumnMapping("periodbalance", "beginning_balance", "number", "期初余额"),
            # 序时账
            ERPColumnMapping("vouchdate", "voucher_date", "date", "凭证日期"),
            ERPColumnMapping("vouchno", "voucher_no", "string", "凭证号"),
            ERPColumnMapping("memo", "summary", "string", "凭证摘要"),
            ERPColumnMapping("accouncode", "account_code", "string", "科目编码"),
            ERPColumnMapping("debit", "debit_amount", "number", "借方金额"),
            ERPColumnMapping("credit", "credit_amount", "number", "贷方金额"),
            # 银行对账单
            ERPColumnMapping("transdate", "statement_date", "date", "交易日期"),
            ERPColumnMapping("docno", "voucher_no", "string", "单据号"),
            ERPColumnMapping("abstract", "description", "string", "摘要"),
            ERPColumnMapping("debitamount", "debit_amount", "number", "借方金额"),
            ERPColumnMapping("creditamount", "credit_amount", "number", "贷方金额"),
            ERPColumnMapping("balance", "balance", "number", "余额"),
            ERPColumnMapping("bankaccount", "bank_account", "string", "银行账号"),
        ]

    def _normalize_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        # 用友NC方向: 1=借, -1/其他=贷
        if self.DIRECTION_FIELD in df.columns:
            df[self.DIRECTION_FIELD] = df[self.DIRECTION_FIELD].apply(
                lambda x: "借" if str(x) == "1" else "贷"
            )
        return df


# ============ 用友U8适配器 ============
class YongyouU8Adapter(BaseERPAdapter):
    """用友U8适配器.

    用友U8常用表名和字段:
    - CodeDefine (科目表)
    - GL_accsum (余额表)
    - GL_accvouch (凭证表)
    - BankStatement (银行对账单)

    典型字段: ccode, ccode_name, md, mc, me
    """

    def get_name(self) -> str:
        return "用友U8"

    def get_column_mappings(self) -> List[ERPColumnMapping]:
        return [
            # 科目余额表
            ERPColumnMapping("ccode", "account_code", "string", "科目编码"),
            ERPColumnMapping("ccode_name", "account_name", "string", "科目名称"),
            ERPColumnMapping("md", "debit_amount", "number", "借方发生额"),
            ERPColumnMapping("mc", "credit_amount", "number", "贷方发生额"),
            ERPColumnMapping("me", "ending_balance", "number", "期末余额"),
            ERPColumnMapping("mb", "beginning_balance", "number", "期初余额"),
            ERPColumnMapping("cend", "balance_direction", "string", "余额方向(借/贷)"),
            # 序时账
            ERPColumnMapping("dbill_date", "voucher_date", "date", "单据日期"),
            ERPColumnMapping("cbill_no", "voucher_no", "string", "单据号"),
            ERPColumnMapping("cexp", "summary", "string", "摘要"),
            ERPColumnMapping("ccode", "account_code", "string", "科目编码"),
            ERPColumnMapping("md", "debit_amount", "number", "借方金额"),
            ERPColumnMapping("mc", "credit_amount", "number", "贷方金额"),
            ERPColumnMapping("casscode", "auxiliary_accounting", "string", "辅助核算"),
            # 银行对账单
            ERPColumnMapping("ddate", "statement_date", "date", "对账日期"),
            ERPColumnMapping("vouch_no", "voucher_no", "string", "凭证号"),
            ERPColumnMapping("description", "description", "string", "描述"),
            ERPColumnMapping("outmoney", "debit_amount", "number", "支出"),
            ERPColumnMapping("inmoney", "credit_amount", "number", "收入"),
            ERPColumnMapping("balance", "balance", "number", "余额"),
        ]

    def _normalize_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        # 用友U8方向: j=借, d=贷 (兼容 1/借)
        if self.DIRECTION_FIELD in df.columns:
            df[self.DIRECTION_FIELD] = df[self.DIRECTION_FIELD].apply(
                lambda x: "借" if str(x).lower() in ["j", "借", "1"] else "贷"
            )
        return df


# ============ SAP适配器 ============
class SAPAdapter(BaseERPAdapter):
    """SAP S/4HANA适配器.

    SAP常用表和字段:
    - SKA1 (科目表 G/L Account Master (Chart of Accounts))
    - SAK1 (科目表 G/L Account Master (Company Code))
    - GLFLEXT (总账余额表)
    - ACCHD (凭证抬头表)
    - ACDOCA (行项目表)
    - FAGLB03 (银行对账单)

    典型字段: SAKNR, KTOPL, DRCRK, HSL, TSL, BLDAT, BUDAT
    """

    def get_name(self) -> str:
        return "SAP S/4HANA"

    def get_column_mappings(self) -> List[ERPColumnMapping]:
        return [
            # 科目余额表 (来自 GLFLEXT 或 FAGLFLEXT)
            ERPColumnMapping("SAKNR", "account_code", "string", "科目编号"),
            ERPColumnMapping("KTOKS", "account_name", "string", "科目描述"),
            ERPColumnMapping("DRCRK", "balance_direction", "string", "借贷标识(S=借, H=贷)"),
            ERPColumnMapping("TSL", "ending_balance", "number", "期末余额(本位币)"),
            ERPColumnMapping("TSL_1", "beginning_balance", "number", "期初余额(本位币)"),
            ERPColumnMapping("HSL", "debit_amount", "number", "本年累计借方"),
            ERPColumnMapping("KSL", "credit_amount", "number", "本年累计贷方"),
            # 序时账 (来自 ACDOCA 或 BKPF + BSEG)
            ERPColumnMapping("BLDAT", "voucher_date", "date", "凭证日期"),
            ERPColumnMapping("BUDAT", "posting_date", "date", "过账日期"),
            ERPColumnMapping("BELNR", "voucher_no", "string", "凭证编号"),
            ERPColumnMapping("BKTXT", "summary", "string", "凭证抬头文本"),
            ERPColumnMapping("RACCT", "account_code", "string", "科目编号"),
            ERPColumnMapping("WSL", "debit_amount", "number", "金额(借方)"),
            ERPColumnMapping("KSL", "credit_amount", "number", "金额(贷方)"),
            ERPColumnMapping("PRCTR", "cost_center", "string", "利润中心"),
            ERPColumnMapping("AWTYP", "reference_table", "string", "参考交易类型"),
            ERPColumnMapping("AWREF", "reference_key", "string", "参考编号"),
            # 银行对账单 (来自 FAGLB03 或 FEBEP)
            ERPColumnMapping("BUKRS", "company_code", "string", "公司代码"),
            ERPColumnMapping("WAERS", "currency", "string", "币种"),
            ERPColumnMapping("HKDAT", "statement_date", "date", "对账日期"),
            ERPColumnMapping("BLDAT", "voucher_date", "date", "凭证日期"),
            ERPColumnMapping("VNMBR", "voucher_no", "string", "凭证号"),
            ERPColumnMapping("AWTYP", "statement_type", "string", "对账单类型"),
            ERPColumnMapping("WRSBR", "debit_amount", "number", "支出金额"),
            ERPColumnMapping("WRSBE", "credit_amount", "number", "收入金额"),
            ERPColumnMapping("SALDO", "balance", "number", "余额"),
            ERPColumnMapping("BANKN", "bank_account", "string", "银行账号"),
            ERPColumnMapping("BANKA", "bank_name", "string", "银行名称"),
        ]

    def _normalize_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        # SAP方向: S=借方(德语Soll), H=贷方(德语Haben)
        if self.DIRECTION_FIELD in df.columns:
            df[self.DIRECTION_FIELD] = df[self.DIRECTION_FIELD].apply(
                lambda x: "借" if str(x).upper() == "S" else "贷"
            )
        return df


# ============手动标准格式适配器 ============
class ManualAdapter(BaseERPAdapter):
    """手动整理的标准格式适配器.

    支持标准模板导出格式，便于与其他系统对接
    """

    def get_name(self) -> str:
        return "标准模板"

    def get_column_mappings(self) -> List[ERPColumnMapping]:
        return [
            # 科目余额表 -严格对应标准字段
            ERPColumnMapping("科目编码", "account_code", "string", "科目编码"),
            ERPColumnMapping("科目名称", "account_name", "string", "科目名称"),
            ERPColumnMapping("余额方向", "balance_direction", "string", "借/贷"),
            ERPColumnMapping("期初余额", "beginning_balance", "number", "期初余额"),
            ERPColumnMapping("借方发生额", "debit_amount", "number", "借方发生额"),
            ERPColumnMapping("贷方发生额", "credit_amount", "number", "贷方发生额"),
            ERPColumnMapping("期末余额", "ending_balance", "number", "期末余额"),
            # 序时账
            ERPColumnMapping("凭证日期", "voucher_date", "date", "凭证日期"),
            ERPColumnMapping("凭证号", "voucher_no", "string", "凭证号"),
            ERPColumnMapping("科目编码", "account_code", "string", "科目编码"),
            ERPColumnMapping("科目名称", "account_name", "string", "科目名称"),
            ERPColumnMapping("借方金额", "debit_amount", "number", "借方金额"),
            ERPColumnMapping("贷方金额", "credit_amount", "number", "贷方金额"),
            ERPColumnMapping("摘要", "summary", "string", "摘要"),
            ERPColumnMapping("辅助核算", "auxiliary_accounting", "string", "辅助核算"),
            # 银行对账单
            ERPColumnMapping("对账日期", "statement_date", "date", "对账日期"),
            ERPColumnMapping("凭证号", "voucher_no", "string", "凭证号"),
            ERPColumnMapping("描述", "description", "string", "描述"),
            ERPColumnMapping("借方金额", "debit_amount", "number", "借方金额"),
            ERPColumnMapping("贷方金额", "credit_amount", "number", "贷方金额"),
            ERPColumnMapping("余额", "balance", "number", "余额"),
            ERPColumnMapping("银行账号", "bank_account", "string", "银行账号"),
        ]


# ============ ERP适配器工厂 ============
class ERPAdapterFactory:
    """ERP适配器工厂."""

    _adapters: Dict[ERPType, BaseERPAdapter] = {
        ERPType.KINGDEE: KingdeeK3Adapter(),
        ERPType.KINGDEE_WISE: KingdeeCloudAdapter(),
        ERPType.YONYOU_NC: YongyouNCAdapter(),
        ERPType.YONYOU_U8: YongyouU8Adapter(),
        ERPType.YONYOU_YONBIP: YongyouU8Adapter(),  # YonBIP与U8字段类似
        ERPType.SAP: SAPAdapter(),
        ERPType.SAP_ECC: SAPAdapter(),  # ECC与S/4HANA字段类似
        ERPType.MANUAL: ManualAdapter(),
    }

    @classmethod
    def get_adapter(cls, erp_type: ERPType) -> BaseERPAdapter:
        """获取指定类型的ERP适配器."""
        adapter = cls._adapters.get(erp_type)
        if not adapter:
            raise ValueError(f"不支持的ERP类型: {erp_type}")
        return adapter

    @classmethod
    def detect_erp_type(cls, df: pd.DataFrame) -> ERPType:
        """根据数据内容自动检测ERP类型."""
        columns = set(df.columns)

        # SAP特征字段
        sap_fields = {"SAKNR", "DRCRK", "TSL", "HSL", "BELNR", "BUDAT"}
        if sap_fields.intersection(columns):
            return ERPType.SAP

        # 金蝶特征字段
        kingdee_fields = {"FAccountID", "FDebit", "FCredit", "FEndBalance"}
        if kingdee_fields.intersection(columns):
            return ERPType.KINGDEE

        # 用友NC特征字段
        nc_fields = {"accoaudcode", "accoaudname", "direct", "primdebit"}
        if nc_fields.intersection(columns):
            return ERPType.YONYOU_NC

        # 用友U8特征字段
        u8_fields = {"ccode", "ccode_name", "md", "mc"}
        if u8_fields.intersection(columns):
            return ERPType.YONYOU_U8

        # 标准格式
        standard_fields = {"科目编码", "科目名称", "余额方向"}
        if standard_fields.intersection(columns):
            return ERPType.MANUAL

        return ERPType.MANUAL  # 默认标准格式

    @classmethod
    def get_supported_types(cls) -> List[Dict[str, str]]:
        """获取支持的ERP类型列表."""
        return [
            {"value": erp_type.value, "label": erp_type.value, "adapter": erp_type.name}
            for erp_type in ERPType
        ]
