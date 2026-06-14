#!/usr/bin/env bash
# scripts/sync.sh — 一键本地同步到 GitHub / Hugging Face Space
#
# 设计原则:
#   * GitHub 推送 (push-github) 和 HF 推送 (push-hf) 是独立子命令,
#     避免误操作直接把 feature 分支推到 hf/main 触发公开 rebuild。
#   * HF 推送前必跑安全检查 + 显式确认交互 + origin/master 与 hf/main 双向比对,
#     拒绝覆盖 hf 端的本地提交。
#   * 安全检查覆盖: .env 文件、sk- 字面量、>50MB 文件、历史里出现过的 .env。
#
# 用法:
#   bash scripts/sync.sh status                  # 看 remote 状态和 ahead/behind
#   bash scripts/sync.sh push-github             # 当前分支 → origin/<branch>
#   bash scripts/sync.sh push-hf                 # origin/master → hf/main (公开 rebuild)

set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE_GH="origin"
REMOTE_HF="hf"
BRANCH_HF="main"
BRANCH_GH_DEFAULT="master"
SIZE_LIMIT_MB=50

# ---------- 颜色 ----------
if [ -t 1 ]; then
  red()    { printf '\033[31m%s\033[0m\n' "$*"; }
  green()  { printf '\033[32m%s\033[0m\n' "$*"; }
  yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
else
  red()    { printf '%s\n' "$*"; }
  green()  { printf '%s\n' "$*"; }
  yellow() { printf '%s\n' "$*"; }
fi
log() { printf '[sync] %s\n' "$*"; }

# ---------- 询问 ----------
confirm() {
  local prompt="$1"
  local ans
  if [ -n "${SYNC_ASSUME_YES:-}" ]; then
    log "  (SYNC_ASSUME_YES=1, 默认 y) $prompt [Y/n] "
    return 0
  fi
  read -rp "  $prompt [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

# ---------- 安全检查 ----------
safety_check() {
  log "安全检查"

  # 1. .env 文件存在性
  if [ -f .env ]; then
    red "  ❌ 检测到 .env 文件,脚本中止(提交 .env 会泄露密钥)"
    exit 1
  fi

  # 2. sk- 字面量扫描(忽略 .venv / node_modules / 本脚本 / CLAUDE.md 文档里的占位符)
  if grep -RIn --include='*.py' --include='*.md' --include='*.toml' --include='*.example' \
      -E 'sk-[a-f0-9]{20,}' . 2>/dev/null \
      | grep -v '\.venv/' \
      | grep -v 'node_modules/' \
      | grep -v 'scripts/sync.sh' \
      | grep -v 'CLAUDE\.md' \
      | grep -v 'HF_SYNC' \
      | grep -q .; then
    red "  ❌ 检测到疑似真实 API key 字面量 (sk- 开头),脚本中止"
    echo "     涉及文件:"
    grep -RIn --include='*.py' --include='*.md' --include='*.toml' --include='*.example' \
        -E 'sk-[a-f0-9]{20,}' . 2>/dev/null \
        | grep -v '\.venv/' | grep -v 'node_modules/' | grep -v 'scripts/sync.sh' \
        | grep -v 'CLAUDE\.md' | grep -v 'HF_SYNC' | head -5
    exit 1
  fi

  # 3. > SIZE_LIMIT_MB 文件
  local big_files
  big_files=$(find . -type f -size +${SIZE_LIMIT_MB}M \
      ! -path './.venv/*' \
      ! -path './.git/*' \
      ! -path './node_modules/*' \
      ! -path './.pytest_cache/*' \
      ! -path './.ruff_cache/*' \
      ! -name '*.db' \
      ! -name 'uv.lock' \
      2>/dev/null || true)
  if [ -n "$big_files" ]; then
    yellow "  ⚠️  发现 > ${SIZE_LIMIT_MB}MB 文件,请确认是否应被提交:"
    echo "$big_files" | head -10
    confirm "是否继续?" || { red "  中止"; exit 1; }
  fi

  # 4. 历史里出现过 .env (最严重 — 已泄露,即使现在删了 commit 还能看到)
  if git log --all --full-history --oneline -- .env 2>/dev/null | grep -q .; then
    red "  ❌ 历史 commit 里出现过 .env,必须先用 git filter-repo / BFG 清历史"
    echo "     涉及 commit:"
    git log --all --full-history --oneline -- .env | head -5
    exit 1
  fi

  green "  ✅ 安全检查通过"
}

# ---------- status ----------
cmd_status() {
  log "remote 配置:"
  echo "  $REMOTE_GH: $(git remote get-url "$REMOTE_GH" 2>/dev/null || echo '未配置')"
  echo "  $REMOTE_HF: $(git remote get-url "$REMOTE_HF" 2>/dev/null || echo '未配置')"
  echo ""
  log "本地分支跟踪:"
  git for-each-ref --format='  %(refname:short) → %(upstream:short)' refs/heads \
    | grep '→ ' || echo "  (无跟踪分支)"
  echo ""

  if git rev-parse --verify origin/master >/dev/null 2>&1; then
    log "当前 HEAD vs origin/master:"
    local ahead behind
    ahead=$(git rev-list --count HEAD ^origin/master 2>/dev/null || echo "?")
    behind=$(git rev-list --count origin/master ^HEAD 2>/dev/null || echo "?")
    echo "  ahead:  $ahead"
    echo "  behind: $behind"
  fi

  if git rev-parse --verify hf/main >/dev/null 2>&1 && git rev-parse --verify origin/master >/dev/null 2>&1; then
    echo ""
    log "origin/master vs hf/main:"
    local hf_ahead hf_behind
    hf_ahead=$(git rev-list --count origin/master ^hf/main 2>/dev/null || echo "?")
    hf_behind=$(git rev-list --count hf/main ^origin/master 2>/dev/null || echo "?")
    echo "  origin/master 比 hf/main 领先: $hf_ahead"
    echo "  hf/main 比 origin/master 领先: $hf_behind"
    if [ "$hf_ahead" = "0" ] && [ "$hf_behind" = "0" ]; then
      green "  ✅ 两边已对齐"
    elif [ "$hf_ahead" != "0" ] && [ "$hf_behind" = "0" ]; then
      yellow "  → 可以 push-hf (origin/master 领先 $hf_ahead 个 commit)"
    elif [ "$hf_ahead" = "0" ] && [ "$hf_behind" != "0" ]; then
      yellow "  ⚠️  hf/main 领先, 不能直接 push (会被 fast-forward 拒绝)"
    else
      red "  ❌ 分叉,需要手动处理 (git log --oneline origin/master...hf/main)"
    fi
  fi
}

# ---------- push-github ----------
cmd_push_github() {
  safety_check
  local branch
  branch=$(git branch --show-current)
  log "当前分支: $branch"

  if [ -n "$(git status --porcelain)" ]; then
    yellow "  ⚠️  有未提交改动:"
    git status --short | head -20
    confirm "继续推送?" || exit 1
  fi

  if ! git rev-parse --verify "$REMOTE_GH/$branch" >/dev/null 2>&1; then
    log "$REMOTE_GH/$branch 不存在,首次推送(会 -u)"
    git push -u "$REMOTE_GH" "$branch"
  else
    local ahead behind
    ahead=$(git rev-list --count HEAD ^"$REMOTE_GH/$branch" 2>/dev/null || echo "?")
    behind=$(git rev-list --count "$REMOTE_GH/$branch" ^HEAD 2>/dev/null || echo "?")
    log "$REMOTE_GH/$branch: ahead=$ahead behind=$behind"
    if [ "$behind" != "0" ]; then
      red "  ❌ 本地落后远程 $behind 个 commit,先 git pull"
      exit 1
    fi
    git push "$REMOTE_GH" "$branch"
  fi

  green "✅ 已推到 $REMOTE_GH/$branch"
  case "$branch" in
    master)
      echo ""
      yellow "下一步: PR 合并完成后可执行 'bash scripts/sync.sh push-hf' 同步到 HF Space"
      ;;
  esac
}

# ---------- push-hf ----------
cmd_push_hf() {
  local force_mode="false"
  for arg in "$@"; do
    case "$arg" in
      --force-with-lease) force_mode="true" ;;
      --force)            force_mode="true" ;;
      -h|--help)
        echo "  push-hf [--force-with-lease]"
        echo "    默认 fast-forward(安全,但要求 origin/master 是 hf/main 的祖先)"
        echo "    --force-with-lease: 当历史分叉时强推,会覆盖 hf/main 历史"
        echo "                        (Space 配置/YAML 头在文件层面保留,commit 层面消失)"
        echo "                        lease 保证 hf/main 不会被别人并发更新"
        exit 0
        ;;
    esac
  done

  safety_check

  if ! git remote get-url "$REMOTE_HF" >/dev/null 2>&1; then
    red "  ❌ $REMOTE_HF remote 未配置"
    exit 1
  fi

  local current_branch
  current_branch=$(git branch --show-current)

  # 先 fetch
  log "fetch $REMOTE_GH/$BRANCH_GH_DEFAULT 和 $REMOTE_HF/$BRANCH_HF"
  git fetch "$REMOTE_GH" "$BRANCH_GH_DEFAULT"
  git fetch "$REMOTE_HF" "$BRANCH_HF"

  # 强制切到 master
  if [ "$current_branch" != "$BRANCH_GH_DEFAULT" ]; then
    yellow "  当前在 $current_branch, 切到 $BRANCH_GH_DEFAULT"
    if [ -n "$(git status --porcelain)" ]; then
      red "  ❌ 有未提交改动, 请先 commit / stash 再切分支"
      exit 1
    fi
    git checkout "$BRANCH_GH_DEFAULT"
  fi

  # 比对 origin/master vs hf/main
  if ! git rev-parse --verify "$REMOTE_HF/$BRANCH_HF" >/dev/null 2>&1; then
    red "  ❌ 本地无 $REMOTE_HF/$BRANCH_HF 引用,fetch 失败?"
    exit 1
  fi

  local hf_ahead hf_behind
  hf_ahead=$(git rev-list --count "$REMOTE_GH/$BRANCH_GH_DEFAULT" ^"$REMOTE_HF/$BRANCH_HF" 2>/dev/null || echo "?")
  hf_behind=$(git rev-list --count "$REMOTE_HF/$BRANCH_HF" ^"$REMOTE_GH/$BRANCH_GH_DEFAULT" 2>/dev/null || echo "?")
  log "$REMOTE_GH/$BRANCH_GH_DEFAULT 比 $REMOTE_HF/$BRANCH_HF 领先 $hf_ahead, 落后 $hf_behind"

  if [ "$hf_ahead" = "0" ] && [ "$hf_behind" = "0" ]; then
    green "  ✅ 两边已对齐,无需推送"
    exit 0
  fi
  if [ "$hf_behind" != "0" ]; then
    if [ "$force_mode" != "true" ]; then
      red "  ❌ $REMOTE_HF/$BRANCH_HF 领先 $REMOTE_GH/$BRANCH_GH_DEFAULT,拒绝 push (避免覆盖)"
      echo ""
      echo "  这通常意味着:"
      echo "    1. 有人在 HF 网页/CLI 直接 commit 过 hf/main"
      echo "    2. 历史分叉(常见情况:HF Space 从 streamlit 模板创建,GitHub master 是独立仓库)"
      echo ""
      echo "  查看差异:"
      echo "    git log --oneline $REMOTE_GH/$BRANCH_GH_DEFAULT..$REMOTE_HF/$BRANCH_HF"
      echo ""
      echo "  强推(覆盖 hf/main 历史,Space 配置在文件层面保留):"
      echo "    bash scripts/sync.sh push-hf --force-with-lease"
      exit 1
    fi
    yellow "  ⚠️  $REMOTE_HF/$BRANCH_HF 领先 $hf_behind 个 commit,使用 --force-with-lease 强推"
    echo "  以下 commit 会被覆盖:"
    git log --oneline "$REMOTE_GH/$BRANCH_GH_DEFAULT..$REMOTE_HF/$BRANCH_HF" | head -10
    confirm "确认强推?" || exit 1
  fi

  # fast-forward 本地 master 到 origin/master
  log "fast-forward 本地 $BRANCH_GH_DEFAULT 到 $REMOTE_GH/$BRANCH_GH_DEFAULT"
  git merge --ff-only "$REMOTE_GH/$BRANCH_GH_DEFAULT"

  echo ""
  local push_cmd="git push $REMOTE_HF $BRANCH_GH_DEFAULT:$BRANCH_HF"
  if [ "$force_mode" = "true" ]; then
    push_cmd="git push --force-with-lease $REMOTE_HF $BRANCH_GH_DEFAULT:$BRANCH_HF"
  fi
  yellow "即将执行: $push_cmd"
  yellow "这会立即触发 HF Space 公开 rebuild (大约 90 秒冷启 + 公开 demo 刷新)"
  if ! confirm "确认推送?"; then
    red "  中止"
    exit 1
  fi

  if [ "$force_mode" = "true" ]; then
    git push --force-with-lease "$REMOTE_HF" "$BRANCH_GH_DEFAULT:$BRANCH_HF"
  else
    git push "$REMOTE_HF" "$BRANCH_GH_DEFAULT:$BRANCH_HF"
  fi
  green "✅ 已推到 $REMOTE_HF/$BRANCH_HF"
  echo ""
  echo "查看 Space 重建进度: https://huggingface.co/spaces/EtheoZheng/EtheoBlank"
}

# ---------- dispatch ----------
case "${1:-}" in
  status)
    cmd_status
    ;;
  push-github)
    shift
    cmd_push_github "$@"
    ;;
  push-hf)
    shift
    cmd_push_hf "$@"
    ;;
  *)
    cat <<EOF
用法: bash scripts/sync.sh <子命令>

子命令:
  status        查看 remote 状态、ahead/behind、对齐情况
  push-github   推当前分支到 origin/<branch> (会跑安全检查)
  push-hf       origin/master → hf/main (会跑安全检查 + 二次确认)

环境变量:
  SYNC_ASSUME_YES=1   自动回答所有确认问题为 y (CI 用)
EOF
    exit 1
    ;;
esac