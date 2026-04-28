#!/usr/bin/env bash
# 多机实验结果同步包装脚本（事件驱动 + 单 hub 拓扑）。
#
# 子命令:
#   pull              git pull --rebase（拉所有节点的最新结果）
#   push [--message M]
#                     仅 add 本机命名空间 logs/<self>/，commit 并 push 到 hub
#   commit [--message M]
#                     仅 add + commit，不 push（离线场景）
#   status            本机未推送 commit + hub 最新 history + 各节点最新 run
#   log [host]        git log --oneline，可指定 host 限定路径
#   diff <a> <b>      透传到 git diff
#
# 退出码:
#   0 = 成功
#   1 = 用法错误 / 配置错误
#   2 = 网络 / git 错误（hub 不可达等；behavior.fail_on_sync_error=false 时仍以 2 退出但 caller 一般忽略）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/_sync_env.sh
source "$SCRIPT_DIR/_sync_env.sh"

export GIT_SSH_COMMAND="ssh $SSH_OPTS"

log()  { echo "[results_sync] $*"; }
warn() { echo "[results_sync] 警告: $*" >&2; }
die()  { echo "[results_sync] 错误: $*" >&2; exit "${2:-1}"; }

# ── 公共前置：进入 logs/ 并校验是否已 init ──
ensure_repo() {
  if [[ ! -d "$LOGS_ROOT/.git" ]]; then
    die "$LOGS_ROOT 还没有嵌套 git 仓库；请先跑 scripts/results_init.sh" 1
  fi
  cd "$LOGS_ROOT"
}

# ── 通用：包一层捕获网络/认证错误 ──
git_safe() {
  if git "$@"; then
    return 0
  fi
  local rc=$?
  warn "git $* 失败 (exit=$rc)"
  return "$rc"
}

cmd_pull() {
  ensure_repo
  log "git pull --rebase origin $HUB_BRANCH"
  if ! git_safe pull --rebase origin "$HUB_BRANCH"; then
    return 2
  fi
}

cmd_push() {
  local msg=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --message|-m) msg="$2"; shift 2 ;;
      *) die "push 未知参数: $1" ;;
    esac
  done
  ensure_repo

  # 仅 stage 本机命名空间 + .gitignore（保持元信息同步）
  git add .gitignore >/dev/null 2>&1 || true
  if [[ -d "$SELF_HOST" ]]; then
    git add -- "$SELF_HOST" >/dev/null
  fi

  if git diff --cached --quiet; then
    log "本机命名空间无新增 / 修改，跳过 commit"
  else
    if [[ -z "$msg" ]]; then
      local ts
      ts=$(date +%Y%m%d_%H%M%S)
      msg="[$SELF_HOST] sync: $ts"
    fi
    log "git commit -m \"$msg\""
    git commit -m "$msg" >/dev/null
  fi

  log "git push origin $HUB_BRANCH"
  if ! git_safe push origin "$HUB_BRANCH"; then
    return 2
  fi
}

cmd_commit() {
  local msg=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --message|-m) msg="$2"; shift 2 ;;
      *) die "commit 未知参数: $1" ;;
    esac
  done
  ensure_repo

  git add .gitignore >/dev/null 2>&1 || true
  if [[ -d "$SELF_HOST" ]]; then
    git add -- "$SELF_HOST" >/dev/null
  fi

  if git diff --cached --quiet; then
    log "无新增 / 修改，跳过 commit"
    return 0
  fi
  if [[ -z "$msg" ]]; then
    msg="[$SELF_HOST] sync: $(date +%Y%m%d_%H%M%S)"
  fi
  git commit -m "$msg" >/dev/null
  log "本地 commit 完成（未推送）：$msg"
}

cmd_status() {
  ensure_repo
  echo "=== 本机命名空间: $SELF_HOST ==="
  local ahead behind
  if git rev-parse --verify "@{u}" >/dev/null 2>&1; then
    ahead=$(git rev-list --count "@{u}..HEAD" 2>/dev/null || echo 0)
    behind=$(git rev-list --count "HEAD..@{u}" 2>/dev/null || echo 0)
    echo "  vs origin/$HUB_BRANCH: ahead=$ahead, behind=$behind"
  else
    echo "  (尚未与 origin 关联，先跑 results_init.sh 或 push 一次)"
  fi
  echo
  echo "=== 工作树是否干净 ==="
  git status --short || true
  echo
  echo "=== 最近 hub history (10 条) ==="
  git log --oneline -10 "origin/$HUB_BRANCH" 2>/dev/null \
    || echo "(无法读取 origin/$HUB_BRANCH，可能尚未 fetch / hub 不可达)"
  echo
  echo "=== 各节点最新 run（按目录修改时间） ==="
  for d in */; do
    local host="${d%/}"
    [[ "$host" == ".git" ]] && continue
    local last
    last=$(ls -td "$host"/*/ 2>/dev/null | head -1 || true)
    if [[ -n "$last" ]]; then
      printf "  %-20s %s\n" "$host" "$last"
    else
      printf "  %-20s (空)\n" "$host"
    fi
  done
}

cmd_log() {
  ensure_repo
  if [[ $# -ge 1 ]]; then
    git log --oneline -- "$1/"
  else
    git log --oneline
  fi
}

cmd_diff() {
  ensure_repo
  [[ $# -ge 2 ]] || die "diff 需要两个 commit/ref 参数"
  git diff "$1" "$2"
}

# ── 入口分发 ──
SUBCMD="${1:-}"
[[ -z "$SUBCMD" ]] && {
  cat >&2 <<EOF
用法: $0 <subcommand> [args...]
子命令:
  pull
  push    [--message MSG]
  commit  [--message MSG]
  status
  log     [host]
  diff    <ref_a> <ref_b>
EOF
  exit 1
}
shift

case "$SUBCMD" in
  pull)   cmd_pull "$@";;
  push)   cmd_push "$@";;
  commit) cmd_commit "$@";;
  status) cmd_status "$@";;
  log)    cmd_log "$@";;
  diff)   cmd_diff "$@";;
  *)      die "未知子命令: $SUBCMD";;
esac
