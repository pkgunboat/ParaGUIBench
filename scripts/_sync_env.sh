#!/usr/bin/env bash
# 共享的同步环境变量加载器，被 results_init.sh / results_sync.sh source。
#
# 解析 configs/sync.yaml（或 $BENCH_SYNC_CONFIG），导出:
#   REPO_ROOT      仓库根绝对路径
#   SYNC_CFG       使用的配置文件绝对路径
#   SELF_HOST      本机 host_tag
#   HUB_SSH        hub 的 user@host
#   HUB_PATH       hub 上 bare repo 绝对路径
#   HUB_BRANCH     默认分支
#   LOGS_ROOT      logs 根目录绝对路径
#   SSH_OPTS       传给 GIT_SSH_COMMAND 的 ssh 选项
#   AUTOSYNC       behavior.autosync_after_run (true|false)
#   PULL_BEFORE    behavior.pull_before_run (true|false)
#   FAIL_ON_ERR    behavior.fail_on_sync_error (true|false)
#
# 任何缺字段或 YAML 解析失败时以非零状态退出并给出可读错误。

set -e

# ── 1. 定位仓库根（从 _sync_env.sh 自身位置推算）──
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$_SCRIPT_DIR/.." && pwd)"
export REPO_ROOT

# ── 2. 选择配置文件 ──
if [[ -n "${BENCH_SYNC_CONFIG:-}" ]]; then
  SYNC_CFG="$BENCH_SYNC_CONFIG"
elif [[ -f "$REPO_ROOT/configs/sync.yaml" ]]; then
  SYNC_CFG="$REPO_ROOT/configs/sync.yaml"
elif [[ -f "$REPO_ROOT/configs/sync.example.yaml" ]]; then
  echo "[_sync_env] 警告: 未找到 configs/sync.yaml，临时回退到 sync.example.yaml" >&2
  SYNC_CFG="$REPO_ROOT/configs/sync.example.yaml"
else
  echo "[_sync_env] 错误: 找不到 configs/sync.yaml；请先复制 configs/sync.example.yaml 并修改" >&2
  exit 2
fi
export SYNC_CFG

# ── 3. 用 Python 解析（PyYAML 在项目里已是依赖）──
_PARSED=$(python3 - "$SYNC_CFG" <<'PYEOF'
import os
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write("[_sync_env] 错误: 需要 PyYAML（pip install pyyaml）\n")
    sys.exit(2)

with open(sys.argv[1], "r") as f:
    cfg = yaml.safe_load(f) or {}

def _need(d, *path):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            sys.stderr.write(f"[_sync_env] 错误: configs/sync.yaml 缺字段: {'.'.join(path)}\n")
            sys.exit(2)
        cur = cur[k]
    return cur

def _opt(d, default, *path):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _bool(v):
    return "true" if bool(v) else "false"

vals = {
    "SELF_HOST":   _need(cfg, "self"),
    "HUB_SSH":     _need(cfg, "hub", "ssh_target"),
    "HUB_PATH":    _need(cfg, "hub", "repo_path"),
    "HUB_BRANCH":  _opt(cfg, "main", "hub", "branch"),
    "LOGS_ROOT":   _opt(cfg, "logs", "logs_root"),
    "SSH_OPTS":    _opt(cfg, "-o BatchMode=yes -o ConnectTimeout=5", "ssh", "options"),
    "AUTOSYNC":    _bool(_opt(cfg, False, "behavior", "autosync_after_run")),
    "PULL_BEFORE": _bool(_opt(cfg, False, "behavior", "pull_before_run")),
    "FAIL_ON_ERR": _bool(_opt(cfg, False, "behavior", "fail_on_sync_error")),
}

# 单引号包裹 + 转义内嵌单引号，避免 shell 注入
def _q(s):
    s = str(s)
    return "'" + s.replace("'", "'\\''") + "'"

for k, v in vals.items():
    print(f"{k}={_q(v)}")
PYEOF
)
eval "$_PARSED"

# ── 4. logs_root 转绝对路径 ──
case "$LOGS_ROOT" in
  /*) ;;  # 已是绝对路径
  *)  LOGS_ROOT="$REPO_ROOT/$LOGS_ROOT" ;;
esac
export SELF_HOST HUB_SSH HUB_PATH HUB_BRANCH LOGS_ROOT SSH_OPTS \
       AUTOSYNC PULL_BEFORE FAIL_ON_ERR
