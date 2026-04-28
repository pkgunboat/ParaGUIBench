#!/usr/bin/env bash
# 节点端一次性引导：把 logs/ 变成嵌套 git 工作树并接入 hub。
#
# 行为（幂等）:
#   1. 读 configs/sync.yaml 获取 self / hub 信息
#   2. 在 logs/ 内 git init -b main（已存在则跳过）
#   3. 写白名单 .gitignore（只追踪 JSON / MD / LOG / XLSX / TXT）
#   4. 配 remote origin = $HUB_SSH:$HUB_PATH
#   5. 尝试 fetch hub；若 hub 已有 main → pull --rebase；否则 push 第一个 commit
#
# 使用:
#   bash scripts/results_init.sh
#
# 退出码:
#   0 = 成功（或已初始化、无需重复）
#   1 = 配置或环境错误
#   2 = SSH / git 网络错误（hub 不可达等）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/_sync_env.sh
source "$SCRIPT_DIR/_sync_env.sh"

# ── 用 git over SSH 时的统一 SSH 选项 ──
export GIT_SSH_COMMAND="ssh $SSH_OPTS"

log()  { echo "[results_init] $*"; }
warn() { echo "[results_init] 警告: $*" >&2; }
die()  { echo "[results_init] 错误: $*" >&2; exit "${2:-1}"; }

log "REPO_ROOT  : $REPO_ROOT"
log "SYNC_CFG   : $SYNC_CFG"
log "SELF_HOST  : $SELF_HOST"
log "HUB        : $HUB_SSH:$HUB_PATH (branch=$HUB_BRANCH)"
log "LOGS_ROOT  : $LOGS_ROOT"

# ── 1. 准备 logs/ 目录 ──
mkdir -p "$LOGS_ROOT"
cd "$LOGS_ROOT"

# ── 2. git init（idempotent）──
if [[ -d ".git" ]]; then
  log "logs/.git 已存在，跳过 git init"
  ALREADY_INIT=1
else
  ALREADY_INIT=0
  log "执行 git init -b $HUB_BRANCH"
  git init -b "$HUB_BRANCH" >/dev/null

  # 配 user.name / user.email（避免 commit 时报错；仅设到该子仓库）
  if ! git config user.name >/dev/null 2>&1; then
    git config user.name  "$SELF_HOST"
    git config user.email "$SELF_HOST@$(hostname -f 2>/dev/null || hostname)"
  fi
fi

# ── 3. 白名单 .gitignore（无论是否首次都覆盖，保持期望状态）──
GITIGNORE_HEADER="# Managed by scripts/results_init.sh — do not edit manually"
if [[ ! -f .gitignore ]] || ! head -1 .gitignore | grep -q "Managed by scripts/results_init.sh"; then
  log "写入白名单 .gitignore"
  cat > .gitignore <<'EOF'
# Managed by scripts/results_init.sh — do not edit manually
#
# 白名单策略：默认忽略所有文件，再 unignore 明确的产物类型。
# 避免误把大型二进制（截图、视频、模型权重）混入 git history。

# 1. 默认忽略一切
*

# 2. 例外：保留管理元信息
!.gitignore

# 3. 允许任意子目录被 git 进入（host 命名空间下的层级）
!*/

# 4. 允许的产物类型（按需扩展）
!**/*.json
!**/*.jsonl
!**/*.md
!**/*.log
!**/*.txt
!**/*.csv
!**/*.tsv
!**/*.xlsx
!**/*.yaml
!**/*.yml

# 5. 维持空目录的占位符
!**/.gitkeep
EOF
fi

# ── 4. 配 remote origin（idempotent）──
EXPECTED_URL="$HUB_SSH:$HUB_PATH"
if git remote get-url origin >/dev/null 2>&1; then
  CURRENT_URL=$(git remote get-url origin)
  if [[ "$CURRENT_URL" != "$EXPECTED_URL" ]]; then
    warn "remote origin 当前指向 $CURRENT_URL，与配置 $EXPECTED_URL 不一致；改写"
    git remote set-url origin "$EXPECTED_URL"
  else
    log "remote origin 已配置正确"
  fi
else
  log "配置 remote origin = $EXPECTED_URL"
  git remote add origin "$EXPECTED_URL"
fi

# ── 5. 确保本机命名空间目录存在并有占位符 ──
mkdir -p "$SELF_HOST"
[[ -f "$SELF_HOST/.gitkeep" ]] || touch "$SELF_HOST/.gitkeep"

# ── 6. 探测 hub 状态 ──
log "探测 hub: $EXPECTED_URL"
if ! git ls-remote --exit-code --heads origin >/dev/null 2>&1; then
  HUB_HAS_BRANCH=0
  if git ls-remote origin >/dev/null 2>&1; then
    log "hub 可达但还没有任何 branch（fresh bare repo）"
  else
    die "无法连接 hub，请检查 SSH 公钥 / 防火墙 / hub 路径是否正确" 2
  fi
else
  HUB_HAS_BRANCH=1
  log "hub 已有 history"
fi

# ── 7. 同步策略 ──
if [[ "$HUB_HAS_BRANCH" == "1" ]]; then
  # hub 已有 commit；先 fetch + 检查本地 branch 是否需要 rebase
  log "git fetch origin $HUB_BRANCH"
  git fetch origin "$HUB_BRANCH" >/dev/null

  if git rev-parse --verify HEAD >/dev/null 2>&1; then
    # 本地已有 commit，做 pull --rebase
    log "git pull --rebase origin $HUB_BRANCH"
    git pull --rebase origin "$HUB_BRANCH"
  else
    # 本地 main 还没 commit；直接以 origin/main 为基线
    log "本地无 commit，checkout origin/$HUB_BRANCH 作为基线"
    git reset --hard "origin/$HUB_BRANCH"
  fi
fi

# ── 8. 如果有未提交的本地新增（白名单或 .gitkeep），提交一次 init commit ──
git add .gitignore "$SELF_HOST/.gitkeep" >/dev/null
if ! git diff --cached --quiet; then
  COMMIT_MSG="[$SELF_HOST] init: bootstrap nested logs/ repo"
  log "$COMMIT_MSG"
  git commit -m "$COMMIT_MSG" >/dev/null
fi

# ── 9. 推到 hub ──
log "git push -u origin $HUB_BRANCH"
git push -u origin "$HUB_BRANCH"

log "✓ 引导完成。logs/ 现在已是 git 工作树并连接 hub。"
log "  本机命名空间: $LOGS_ROOT/$SELF_HOST/"
log "  以后跑实验产生的结果将自动落到该目录下。"
