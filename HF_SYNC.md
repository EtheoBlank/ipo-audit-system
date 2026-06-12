# Pack A — Hugging Face Space 同步操作手册

GitHub PR #1 已开: https://github.com/EtheoBlank/ipo-audit-system/pull/1

HF Space 自动同步**未能完成**, 原因:
1. 本地网络环境连不上 `huggingface.co:443` (curl 测试超时, GitHub 同链路 200 OK)
2. 安全分类器拒绝直推 HF main 分支 (HF main 是 Space 部署分支, 推送会自动 rebuild 公开 demo, 需要用户明确授权)

## 建议同步方案 (任选一)

### 方案 A — PR 合并后再同步 (推荐)

1. **GitHub** 网页打开 PR #1: https://github.com/EtheoBlank/ipo-audit-system/pull/1
2. 自检 + Review 通过后, 合并到 master
3. 本地拉取最新 master:
   ```bash
   git checkout master
   git pull origin master
   ```
4. 推送到 HF Space:
   ```bash
   git push hf master:main
   ```
5. HF Space 自动 rebuild (冷启 ~90s), 进度: https://huggingface.co/spaces/EtheoZheng/EtheoBlank

### 方案 B — 直接推 HF (跳过 GitHub PR 流程)

如果你想立即让 HF Space demo 跑上 Pack A:
```bash
git push hf feat/pack-a-multi-user-audit-trail-long-term-asset:main --force-with-lease
```
**注意**: 这会立即触发 HF Space 重建 + 公开访问. 跑通验证后再合 GitHub PR.

## HF Space Secrets 配置

升级到 Pack A 之前, 进 HF Space Settings → Variables and secrets 加上:

| 类型 | 变量 | 值 |
|------|------|-----|
| Secret | `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成 |
| Variable | `AUTH_ENABLED` | `false` (建议先保持兼容, 启用前先测) |
| Secret | `AUTH_BOOTSTRAP_ADMIN_PASSWORD` | 强密码 (不要用 `Admin@1234`) |
| Variable | `REPORT_TEMPLATE_DIR` | `/data/templates/reports` (持久化) |
| Variable | `REPORT_OUTPUT_DIR` | `/data/outputs/reports` (持久化) |

已有的 `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY` 不动.

## 部署后验证清单

1. 健康检查: `curl https://etheozheng-etheoblank.hf.space/health` 应返 `{"auth_enabled":false,...}`
2. 浏览器进 https://etheozheng-etheoblank.hf.space — sidebar 应看到 21 项菜单 (新增 4 个 Pack A 入口)
3. 进 `📑 长期资产发生额审定` 应能加载页面 (不报 ImportError)
4. 进 `🎨 报告模板` 应能加载页面
5. 进 `🔔 通知中心` 应看到通知列表
6. 进 `🔐 系统管理` 在 AUTH_ENABLED=false 时应显示登录表单 (但会提示已禁用认证)

## 回滚

如果 HF Space rebuild 后出问题:
```bash
git push hf <previous-commit-sha>:main --force-with-lease
```

13 张 Pack A 新表会留在 `/data/ipo_audit.db`, 不影响老业务 (新表无外键引用老表, 老业务不读新表).

---

**生成时间**: 2026-06-12
**关联 PR**: https://github.com/EtheoBlank/ipo-audit-system/pull/1
**Pack A commit 范围**: 6 个 commit (从 `8f01364` 到 `ee7cac3`)
