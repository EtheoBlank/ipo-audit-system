#!/usr/bin/env bash
# 一键分三个 commit + push 到 origin/master
# 用法：在 ipo-audit-system/ 目录下执行 bash scripts/git_push.sh
set -euo pipefail

cd "$(dirname "$0")/.."

REMOTE_URL="https://github.com/EtheoBlank/ipo-audit-system"
BRANCH="master"

# --- 安全检查：拒绝推送任何 .env 或含 sk- 的真 key ---
echo "[1/6] 安全检查：.env 与 key 字面量扫描"
if [ -f .env ]; then
  echo "  ❌ 检测到 .env 文件，请勿提交！脚本中止。" >&2
  exit 1
fi
if grep -RIn --include='*.py' --include='*.md' --include='*.toml' --include='*.example' \
    -E 'sk-[a-f0-9]{20,}' . 2>/dev/null | grep -v '.venv' | grep -v 'node_modules'; then
  echo "  ❌ 检测到疑似真实 API key 字面量，脚本中止。" >&2
  exit 1
fi
echo "  ✅ 安全检查通过"

# --- 准备：作者身份 ---
echo "[2/6] 设置 git 作者"
git config user.name  "KIMI"
git config user.email "audit@example.com"

# --- Commit 1: 销售清单整理（核心）---
echo "[3/6] Commit 1/3 — feat: 销售清单整理（销售台账 + 9 维收入分析）"
git add app/core/config.py \
        app/main.py \
        app/models/db_models.py \
        app/models/sales_ledger.py \
        app/api/sales_ledger.py \
        app/services/sales_ledger/ \
        frontend/pages_sales_ledger.py \
        frontend/app.py \
        pyproject.toml \
        .env.example
git commit -m "feat(sales-ledger): 销售清单整理核心

- DeepSeek 客户端（OpenAI 兼容 + JSON Mode，密钥仅走 .env）
- 文档解析：docx / pdf / xlsx / 扫描件
- AI 合成：把散乱文档抽取为结构化销售清单（22 字段）
- 收入循环分析 9 维度：客户 / 产品 / 月度 / 3D 交叉 /
  截止性 / 单价波动 / 收发存对账 / 同行业 AI 参考
- 多 Sheet Excel 工作簿导出
- Streamlit 5 步骤工作流（上传→合成→核对→分析→导出）
- 新增依赖 pdfplumber / tabulate"

# --- Commit 2: 销售清单增量补丁（实务对账）---
echo "[4/6] Commit 2/3 — feat: 销售清单增量补丁（12 字段 + 4 程序）"
git add app/api/sales_ledger.py \
        app/models/db_models.py \
        app/models/sales_ledger.py \
        app/services/sales_ledger/synthesizer.py \
        app/services/sales_ledger/analyzer.py \
        app/services/sales_ledger/excel_exporter.py \
        frontend/pages_sales_ledger.py
git commit -m "feat(sales-ledger): 增量补丁 — 12 字段 + 4 分析程序

按 IPO 审计实务对账后补足的高频字段：
- 发票号 / 币种 / 税率 / 税额 / 价税合计（增值税底稿闭环）
- 签收日期（IFRS 15 控制权转移关键证据）
- 退货金额 / 折扣折让 / 销售返利（毛利真实性）
- 函证状态 / 编号 / 差异（审计轨迹闭环）

新增 4 个分析程序：
- 函证覆盖率（按客户 + 整体）
- DSO 分客户（账期分析）
- 退折返对月度毛利率的侵蚀
- 发货→签收时点差异（确认时点风险）

更新 Excel 导出至 12 个 sheet。"

# --- Commit 3: 收入合同五步法分析 + README 重写 ---
echo "[5/6] Commit 3/3 — feat: 收入合同分析（OCR + CAS 14 五步法）+ README"
git add app/api/contracts.py \
        app/models/contracts.py \
        app/models/db_models.py \
        app/services/contract_analysis/ \
        frontend/pages_contracts.py \
        frontend/app.py \
        pyproject.toml \
        README.md
git commit -m "feat(contracts): 收入合同五步法分析 + README 重写

合同模块（app/services/contract_analysis/）：
- OCR 服务：paddleocr 优先 / easyocr / tesseract 兜底；PDF 直抽文本
- CAS 14 五步法：合同识别 → 合同变更 → 履约义务分拆 →
  交易价格 → 收入确认 → 审计关注
- 7 字段要点提取（合同号 / 双方 / 金额 / 期限 / 违约 / 补充）
- 本地风险扫描：回购条款 / 寄售 / 重大融资成分 / 可变对价 / 补充协议
- API：图片/PDF 上传、文本直传、批量分析
- Streamlit 「📄 收入合同分析」页面（3 步：上传→列表→五步法）

README 全面重写：
- 徽章 / 架构图 / 30 秒快速开始
- 模块亮点表格 / 9 维度收入分析详解
- CAS 14 五步法示意图
- 技术栈 / 项目结构 / 路线图
- API Key 安全说明"

# --- Push ---
echo "[6/6] 推送到 $REMOTE_URL ($BRANCH)"
git push -u origin "$BRANCH"

echo ""
echo "✅ 全部完成。请到 GitHub 仓库查看："
echo "   $REMOTE_URL"
