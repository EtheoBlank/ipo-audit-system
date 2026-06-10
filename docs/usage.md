# 📖 使用教程

> 本文档按模块分步骤讲解如何使用 IPO 审计系统。**每一步都有截图位** —— 截图待你跑起来后补上（把图片放到 `docs/screenshots/` 下，对应文件名引用即可）。

---

## 目录

- [1. 30 秒快速上手](#1-30-秒快速上手)
- [2. 销售清单整理](#2-销售清单整理)
- [3. 收入合同分析](#3-收入合同分析)
- [4. AI 风险分析](#4-ai-风险分析)
- [5. 底稿生成与试算平衡](#5-底稿生成与试算平衡)
- [6. 故障排查 FAQ](#6-故障排查-faq)

---

## 1. 30 秒快速上手

### 1.1 启动后端

```bash
uv run uvicorn app.main:app --reload --port 8000
```

启动成功会看到：

```
🚀 IPO审计系统 v0.2.0 启动成功
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**截图位**：[docs/screenshots/backend_startup.png](screenshots/)

### 1.2 启动前端

新开一个终端：

```bash
uv run streamlit run frontend/app.py
```

浏览器自动打开 http://localhost:8501，看到 Streamlit 界面：

```
┌─────────────────┬────────────────────────────────┐
│ 功能菜单          │  📊 IPO 审计系统 (专业版)        │
│ ─────────       │                                │
│ 🏠 首页概览       │  系统概览                       │
│ 📁 项目管理       │  [项目总数] [进行中] [API] [版本] │
│ 📤 数据导入       │                                │
│ 📊 底稿生成       │  ⚡ 快速操作                    │
│ ⚖️ 试算平衡       │  [➕新建] [📤导入] [📊底稿] [📄报告]│
│ 🔍 监管案例库     │                                │
│ 🤖 AI风险分析     │  📋 最近项目                    │
│ 📋异常检测        │  ...                           │
│ 📄 综合报告       │                                │
│ 📦 销售清单整理  ⬅│  ← 新功能！                    │
│ 📄 收入合同分析  ⬅│  ← 新功能！                    │
└─────────────────┴────────────────────────────────┘
```

**截图位**：[docs/screenshots/streamlit_home.png](screenshots/)

### 1.3 创建第一个项目

1. 左侧菜单 → **📁 项目管理**
2. 选 **➕ 新建项目** Tab
3. 填写：
   - 项目名称：`XX 公司 IPO 审计 2024`
   - 公司名称：`XX 科技股份有限公司`
   - 所属行业：选一个
   - 审计年度：2024
4. 点 **创建项目**

**截图位**：[docs/screenshots/create_project.png](screenshots/)

---

## 2. 销售清单整理

> 收入循环审计最缺的那张表 — AI 自动把散乱文档变成结构化销售清单。

### 2.1 流程一览

```
📤 上传文档  →  🤖 AI 合成  →  ✏️ 核对  →  💰 收入分析  →  📥 导出 Excel
   (5 分钟)     (1-2 分钟)     (人工)       (秒级)          (秒级)
```

### 2.2 Step 1: 上传文档

左侧菜单 → **📦 销售清单整理** → **📤 文档上传** Tab

**支持的文件类型**：
- `.docx` / `.pdf` / `.xlsx` / `.xls`

**典型上传清单**（被审计单位往往会提供）：
| 文档类型 | 用途 |
|---------|------|
| 销售合同 | 客户、产品、价格、付款条款 |
| 增值税发票 | 发票号、税率、税额、价税合计 |
| 发货单 | 发货日期、签收日期 |
| 报关单 | 出口销售的发货时间 |
| 客户对账单 | 函证基础、回款状态 |

> 💡 **小贴士**：一次上传多份会自动合并去重。**没有这些文档？** AI 会基于任何含有"销售明细"的 Word/PDF 抽取 —— 给你手头的就行。

**截图位**：[docs/screenshots/sales_upload.png](screenshots/)

### 2.3 Step 2: AI 合成

切到 **🤖 AI 合成** Tab → 点 **开始合成**

控制台会看到类似输出：

```
⏳ DeepSeek 正在解析文档……
✅ 已合成 47 条记录
```

合成完成后，下方会显示所有结构化销售记录，包括 22 个字段：

| 字段 | 示例 |
|------|------|
| 合同号 | SO-2024-001 |
| 客户 | XX 科技有限公司 |
| 产品编号 | P-A100 |
| 数量 | 1000 |
| 不含税收入 | 100,000.00 |
| 税额 | 13,000.00 |
| 价税合计 | 113,000.00 |
| 成本 | 65,000.00 |
| 运费 | 1,200.00 |
| 发货日期 | 2024-03-15 |
| 签收日期 | 2024-03-18 |
| 收入确认日期 | 2024-03-18 |
| 函证状态 | 未发函 |

**截图位**：[docs/screenshots/sales_synthesize.png](screenshots/)

### 2.4 Step 3: 核对修改

切到 **✏️ 核对修改** Tab

界面是一个可编辑表格，审计师可以：
- 修改任意字段
- 勾选 **已核对** 表示已人工确认
- 改完点 **💾 保存修改**

**截图位**：[docs/screenshots/sales_review.png](screenshots/)

### 2.5 Step 4: 收入分析

切到 **💰 收入分析** Tab

配置参数：
- 期末日期（默认今天）
- 截止性测试窗口（默认 ±10 天）
- 单价波动报警阈值（默认 20%）
- 是否生成同行业 AI 参考（需要 DeepSeek key）
- 行业（用于行业参考）

点 **📊 开始分析**

**9 个分析维度**自动展开：

```
💰 收入分析
├── 总览 KPI
│     记录数: 47   总收入: 5,200,000   毛利率: 28.5%
├── 👥 客户毛利率        ← 展开
├── 📦 产品毛利率        ← 展开
├── 📅 月度毛利率        ← 展开
├── 🔀 客户×产品×月度   ← 展开
├── ✉️ 函证覆盖率       ← 整体函证覆盖率 76% (需提升)
├── ⏱️ DSO 分客户      ← 平均账期 45 天
├── ↩️ 退折返对毛利     ← 真实毛利率 27.2%
├── 🕒 收入确认时点     ← 0 警告
├── ⚠️ 截止性测试       ← 2 条年末跨期风险
├── 📈 单价波动         ← 1 个产品+客户波动超 20%
└── 🏭 同行业 AI 参考   ← 仅参考！
```

**截图位**：[docs/screenshots/sales_analysis.png](screenshots/)

### 2.6 Step 5: 导出 Excel

切到 **📥 导出** Tab → 点 **📥 生成 Excel** → **⬇️ 下载 Excel**

生成的工作簿含 12 个 Sheet：

| Sheet | 内容 |
|-------|------|
| 销售清单 | 22 字段原始数据 |
| 总览 | KPI |
| 客户毛利率 / 产品毛利率 / 月度毛利率 | 透视表 |
| 客户×产品×月度 | 三维交叉 |
| 截止性测试 / 单价波动 | 报警 |
| 函证覆盖率 / DSO 分客户 / 退折返影响 / 确认时点差异 | 审计程序 |
| 收发存对账 | 若提供了收发存数据 |
| 行业参考 | DeepSeek 输出 |

**截图位**：[docs/screenshots/sales_export.png](screenshots/)

---

## 3. 收入合同分析

> 按 **CAS 14** 五步法分析合同 — 上传图片/扫描件也行（自动 OCR）。

### 3.1 流程一览

```
📷 上传合同图片   ─┐
                 ├→ OCR → 📝 文本 → 🤖 五步法分析 → ⚠️ 风险扫描
📝 粘贴纯文本     ─┘
```

### 3.2 Step 1: 上传合同

左侧菜单 → **📄 收入合同分析** → **📤 上传合同** Tab

**两种模式**：

**A) 图片/扫描件上传（推荐）**
- 支持 `.png` / `.jpg` / `.pdf` 等
- 自动 OCR，**首次使用需安装 paddleocr**：
  ```bash
  uv add paddleocr
  ```

**B) 纯文本直传**
- 已经用本地 OCR 工具跑过、或不想装 paddleocr 时
- 把合同正文粘到文本框

**截图位**：[docs/screenshots/contract_upload.png](screenshots/)

### 3.3 Step 2: 查看合同列表

切到 **📋 合同列表** Tab

显示项目下所有合同，每行显示：
- ID / 文件名 / 媒体类型 / OCR 引擎 / 是否已分析 / 上传时间 / 风险点数

展开任一合同可看到 OCR 文本（前 2000 字）和风险点。

**截图位**：[docs/screenshots/contract_list.png](screenshots/)

### 3.4 Step 3: 五步法分析

切到 **🤖 五步法分析** Tab → 选合同 → 勾选要运行的分析 → 点 **开始分析**

结果展示：

```
⚠️ 风险点：回购条款、可变对价（CAS 14 §16-19）、存在补充协议 / Side Letter

🔑 7 字段要点提取
   合同号: HT-2024-001
   甲方:  XX 科技股份有限公司
   乙方:  YY 贸易有限公司
   总金额: 5,000,000 CNY
   有效期: 2024-01-01 至 2024-12-31
   违约/争议: 违约方需支付合同总额 30% 违约金，争议由合同签订地法院管辖
   补充协议: 附件 3 约定乙方可申请分期付款，分 4 期

📐 CAS 14 五步法分析
   ① 合同识别     {...}
   ② 合同变更     {...}
   ③ 履约义务分拆 [2 项履约义务]
   ④ 交易价格     {固定 500 万 + 可变对价（分期）}
   ⑤ 收入确认     [时点确认 / 客户验收法]
   ⚠️ 审计关注    [关注分期付款的融资成分分拆]
```

**截图位**：[docs/screenshots/contract_five_step.png](screenshots/)

---

## 4. AI 风险分析

> 基于监管案例库 + AI 模型识别项目潜在风险。

### 4.1 启动分析

左侧菜单 → **🤖 AI风险分析**

输入项目上下文（公司、行业、年度、关注点），AI 会：
1. 从监管案例库（证监会 / 交易所）检索同行业案例
2. 比对项目情况
3. 输出风险点列表

**截图位**：[docs/screenshots/ai_risk.png](screenshots/)

### 4.2 解读结果

每条风险包含：
- 风险类型
- 风险等级（高/中/低）
- 受影响的科目
- 审计建议
- 关联案例编号

---

## 5. 底稿生成与试算平衡

### 5.1 生成底稿

左侧菜单 → **📊 底稿生成** → 选项目 → 选底稿类型

支持 5 种标准底稿：
- 科目明细表 (`account_detail`)
- 利润表 (`income_statement`)
- 资产负债表 (`balance_sheet`)
- 现金流量表 (`cash_flow`)
- 试算平衡表 (`trial_balance`)

**截图位**：[docs/screenshots/workbook.png](screenshots/)

### 5.2 试算平衡

左侧菜单 → **⚖️ 试算平衡**

系统自动检查：
- 资产 = 负债 + 所有者权益
- 各科目借方发生额 = 贷方发生额
- 报表勾稽关系

**截图位**：[docs/screenshots/trial_balance.png](screenshots/)

---

## 6. 故障排查 FAQ

### Q1: 启动时 "ModuleNotFoundError: No module named 'pandas'"

**A**: 没装依赖。运行：
```bash
uv sync
```

### Q2: 启动时 "DEEPSEEK_API_KEY 未配置"

**A**: `.env` 没配或 key 错了。检查：
```bash
cat .env | grep DEEPSEEK
# 应该看到: DEEPSEEK_API_KEY=sk-xxx...
```

### Q3: OCR 失败 "未安装任何 OCR 引擎"

**A**: paddleocr / easyocr / tesseract 都没装。任选其一：
```bash
# 推荐 — 中文最佳
uv add paddleocr

# 备选 — 轻量
uv add easyocr

# 最轻 — 需额外装 tesseract 二进制
uv add pytesseract
```

或直接用「纯文本直传」模式。

### Q4: AI 合成后记录数 = 0

**A**: 文档里没有可识别的销售明细。试试：
1. 把表头补上"销售/客户/金额/日期"等关键词
2. 改用更结构化的 Excel
3. 试试纯文本直传 + 手动加结构化文字

### Q5: 收入分析报"项目下还没有销售记录"

**A**: 必须先在销售清单模块跑完 AI 合成。流程是：
1. 销售清单 → 上传文档
2. 销售清单 → AI 合成
3. 销售清单 → 收入分析

### Q6: 前端连接不到后端

**A**: 检查后端是否在 8000 端口运行：
```bash
curl http://localhost:8000/health
# 期望返回: {"status":"healthy",...}
```

### Q7: 端口被占用

**A**: 改端口：
```bash
uv run uvicorn app.main:app --reload --port 8001
# 前端 app.py 顶部的 API_BASE_URL 也要同步改
```

### Q8: paddleocr 装不上

**A**: Windows 上 paddlepaddle 经常出问题。两种方案：
- 用 WSL2 或 Docker
- 改用 `easyocr`：
  ```bash
  uv pip uninstall paddleocr paddlepaddle
  uv add easyocr
  ```

---

## 📸 截图占位清单

> 把你的真实截图放到 `docs/screenshots/` 目录，文件名按下表命名：

| 文件名 | 内容 |
|--------|------|
| `backend_startup.png` | 后端启动成功的终端 |
| `streamlit_home.png` | Streamlit 首页 |
| `create_project.png` | 新建项目表单 |
| `sales_upload.png` | 销售清单 - 文档上传 |
| `sales_synthesize.png` | 销售清单 - AI 合成结果 |
| `sales_review.png` | 销售清单 - 核对修改表格 |
| `sales_analysis.png` | 销售清单 - 收入分析 9 维度 |
| `sales_export.png` | 销售清单 - Excel 导出 |
| `contract_upload.png` | 合同 - 上传页面 |
| `contract_list.png` | 合同 - 列表 |
| `contract_five_step.png` | 合同 - CAS 14 五步法分析结果 |
| `ai_risk.png` | AI 风险分析结果 |
| `workbook.png` | 底稿生成 Excel |
| `trial_balance.png` | 试算平衡结果 |

---

## 💬 反馈

遇到文档没覆盖的问题？去 GitHub 提 Issue：
https://github.com/EtheoBlank/ipo-audit-system/issues
