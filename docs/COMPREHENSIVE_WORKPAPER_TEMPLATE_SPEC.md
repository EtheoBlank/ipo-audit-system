# 综合底稿 Excel 模板规范

> 版本: 1.0  
> 适用范围: `ipo-audit-system` 综合底稿自动生成模块  
> 配套依赖: openpyxl ≥ 3.1.2

## 1. 设计目标

让 AI 系统能**自动识别**综合底稿 Excel 模板中"哪些单元格需要填、写在哪里、从哪里取数"，从而实现"基础底稿 + 审计手册 + 联网核查 + 一次性问答"四路数据自动汇入。

核心原则：
- **零侵入**: 不破坏原有 Excel 公式、合并、格式
- **可读性**: 模板作者（审计员）能直接用 Excel 制作
- **可追溯**: 每个填充值都有来源标签，方便审计复核

## 2. 整体结构

每个综合底稿模板是一个**普通 .xlsx 文件**，由两类工作表组成：

| 类型 | 名称规则 | 作用 |
|------|---------|------|
| 业务表 | 任意（如"应收账款综合底稿"） | 真正的底稿内容，含占位符 |
| 元数据表 | `_meta`（隐藏或命名） | 模板配置、字段定义、规则绑定 |

**严禁**使用 VBA、宏、外部链接。AI 系统只通过单元格内容、命名区域、注释、隐藏 sheet 读取信息。

## 3. 占位符语法

### 3.1 单元格内占位符

在单元格中写入 `{{field_id}}`，AI 解析时识别为待填字段：

```
单元格 A5 = "{{company_name}}"        → 待填：公司名称
单元格 B5 = "审计期间：{{audit_period}}"  → 待填：审计期间
```

支持**文字拼接**（占位符可与普通文本混合）：

```
="公司截至 {{period_end} 应收账款余额为 {{ar_balance} 元"
```

### 3.2 命名区域占位符

为单元格/区域定义**命名区域**（Name Manager），用于结构化字段：

| 命名区域 | 引用 | 含义 |
|---------|------|------|
| `ar_balance` | `应收账款综合底稿!$B$10` | 应收账款余额 |
| `ar_aging_total` | `应收账款综合底稿!$B$15:$F$15` | 账龄合计行（区域） |
| `confirmation_rate` | `函证情况!$D$20` | 函证比例 |

**优先级**: 命名区域 > 单元格内 `{{...}}` 占位符。同名字段以命名区域为准。

### 3.3 字段 ID 命名规范

- 仅允许小写字母、数字、下划线
- 推荐 `<table>_<column>_<role>` 形式，例如 `ar_balance_prior_year`
- 长度 ≤ 64 字符

## 4. 元数据表（`_meta`）

模板必须包含一个名为 `_meta` 的工作表（建议隐藏），存放模板级与字段级元数据，采用**两列键值对**或**结构化表格**两种形式。

### 4.1 模板级配置（A1:B10 区域）

| Key | Value | 说明 |
|-----|-------|------|
| `template_id` | `ar_comprehensive_v1` | 模板唯一标识 |
| `template_name` | 应收账款综合底稿 | 模板显示名 |
| `version` | `1.0.0` | 模板版本 |
| `firm_id` | `firm_xxx` | 所属事务所 ID |
| `industry` | 制造业 | 适用行业 |
| `audit_period` | `2024-01-01~2024-12-31` | 默认审计期间 |
| `required_workpapers` | 应收账款明细表, 函证汇总表 | 依赖的基础底稿 |
| `manual_ref` | `manual/ar_v3.md` | 关联的审计手册规则文件 |

### 4.2 字段定义表（A12 起，标准表头）

| field_id | label | type | source | required | hint | options |
|----------|-------|------|--------|----------|------|---------|
| `ar_balance` | 应收账款期末余额 | number | workpaper:ar_ledger.total | true | 单位:元 | — |
| `audit_period` | 审计期间 | text | workpaper:project.audit_period | true | — | — |
| `confirmation_rate` | 函证比例 | percent | workpaper:confirmation.coverage | true | — | — |
| `risk_level` | 重大风险评估 | choice | rule:ar_high_risk_assessment | false | — | 低,中,高 |
| `disclosure_note` | 披露事项 | text | web_search:csrc_ar_disclosure | false | — | — |
| `mgmt_judgment` | 管理层判断说明 | text | human_qa | true | 200字以上 | — |
| `formula:turnover_days` | 周转天数 | number | calculated:365*ar_avg/revenue | false | 自动算 | — |

**字段说明：**

- `type`: `text` | `number` | `percent` | `date` | `choice` | `text_long` | `boolean`
- `source`: 五类填充来源之一（见第 5 节）
- `required`: 必填 / 选填
- `hint`: 给 AI 或人类的填写提示
- `options`: `choice` 类型的可选项（逗号分隔）

## 5. 填充来源（source）协议

每个字段必须有且仅有一种 `source`：

| 前缀 | 含义 | 引擎 |
|------|------|------|
| `workpaper:<path>` | 从基础底稿/项目数据中抽取 | 字段映射引擎 |
| `rule:<rule_id>` | 触发审计手册规则 | 规则引擎 |
| `web_search:<query_id>` | 联网检索权威源 | 网络核查引擎 |
| `human_qa` | 人类回答 | 问答引擎 |
| `calculated:<expr>` | 表达式计算 | 公式引擎 |

**优先级**（从高到低）：
1. `workpaper` — 最可信，直接数据
2. `rule` — 规则推导，可附依据
3. `web_search` — 权威信息，必须附引用
4. `human_qa` — 兜底，AI 询问人类

`calculated` 不算"填充来源"，而是在其他字段填好后自动执行。

## 6. 单元格注释（可选但推荐）

为关键占位符单元格添加**批注**，说明填写规范：

```
单元格 A5 批注:
  字段ID: ar_balance
  来源: workpaper:ar_ledger.total
  类型: number(单位:元)
  必填: 是
```

AI 解析时优先读批注（信息更丰富），回退到 `_meta` 表。

## 7. 填充结果标签

AI 填充完成后，会在**相邻单元格**（通常是右侧一列）写入来源标签，便于审计复核：

| 写入列 | 内容 | 示例 |
|--------|------|------|
| 紧邻右列 | `来源: 基础底稿 / 应收账款明细表 / B100` | 审计员可一键追溯 |
| 同一行最右 | 填充时间戳 | `2024-06-12 10:30:00` |
| `_log` sheet | 填充日志（JSON） | 完整审计线索 |

## 8. 模板示例（最小可工作样例）

参见 `templates/comprehensive/ar_comprehensive_v1.xlsx`（后续随实现提供）。

简化版字段表：

```
_field_id_             | _label_                  | _type_   | _source_                                  | _required_
company_name           | 公司全称                  | text     | workpaper:project.company_name             | true
audit_period           | 审计期间                  | text     | workpaper:project.audit_period             | true
ar_balance             | 应收账款期末余额           | number   | workpaper:ar_ledger.total_ending          | true
ar_turnover_days       | 应收账款周转天数            | number   | calculated:365*ar_avg/revenue             | true
risk_level             | 风险等级                  | choice   | rule:ar_risk_classify                     | true
disclosure_note        | 披露事项                  | text_long| web_search:csrc_ar_disclosure              | false
mgmt_judgment          | 管理层判断                | text_long| human_qa                                  | true
```

## 9. 解析器接口约定

模板解析器（`app/services/comprehensive/template_parser.py`）必须输出如下 Pydantic 模型：

```python
class TemplateField(BaseModel):
    field_id: str
    label: str
    type: Literal["text","number","percent","date","choice","text_long","boolean"]
    source: str  # 形如 "workpaper:xxx" / "rule:xxx" / "web_search:xxx" / "human_qa" / "calculated:xxx"
    required: bool
    hint: str | None
    options: list[str] | None
    cell_ref: str  # 例 "应收账款综合底稿!A5"
    name_range: str | None  # 例 "ar_balance"（如果有命名区域）

class TemplateSchema(BaseModel):
    template_id: str
    template_name: str
    version: str
    firm_id: str
    fields: list[TemplateField]
    sheets: list[str]
```

## 10. 限制与禁忌

- ❌ 不支持 VBA / 宏
- ❌ 不支持外部数据连接
- ❌ 不支持图片/图表作为占位符（但可作为静态背景）
- ❌ 不支持跨模板引用（用 `workpaper:` 走标准通道）
- ✅ 支持普通公式、合并单元格、条件格式、数据验证
- ✅ 支持中文表头、合并区域
- ✅ 支持 1000+ 字段的复杂模板

## 11. 版本演进

| 版本 | 变更 |
|------|------|
| 1.0  | 初版：占位符、命名区域、_meta 表、5 类 source |
| 1.1  | 计划：增加 `cross_ref` 跨字段校验、`derived_from` 派生关系 |
| 1.2  | 计划：支持 Word 模板（叙述类综合报告） |

---

**配套文档**:
- `docs/COMPREHENSIVE_WORKPAPER_ENGINE.md` — 引擎实现设计（待写）
- `docs/COMPREHENSIVE_WORKPAPER_QA.md` — 问答协议（待写）
