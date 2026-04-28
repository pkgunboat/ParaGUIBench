# 多机实验结果同步（事件驱动 + 单 hub 拓扑）

ParaGUIBench 的实验在多台节点上分别运行，本文档说明如何让结果在 LAN
内自动汇集、版本化、可追溯。

## 设计要点

- **底层**：git over SSH，所有节点通过 SSH 推拉同一个 bare repo（hub）
- **拓扑**：星形——一台稳定服务器作为 hub，其他节点（含 Mac 控制端）
  把 hub 配为 `origin`
- **触发**：完全事件驱动
    1. 每次 `run_ablation` 启动早期：自动 `git pull --rebase`
    2. 每次 `run_ablation` 结束后：自动 `git add` + `commit` + `push`
       本机命名空间下新增的结果
  没有 cron / launchd / 常驻 daemon
- **隔离**：每台节点的输出落到 `logs/<host_tag>/...` 子树；
  跨节点 path 集合不相交，merge 永远是 fast-forward 或纯 add，
  无冲突
- **追溯**：天然得到 git log/diff/revert/blame——任何一次结果改动都有
  commit 可查、可回滚

## 拓扑示意

```
                          ┌──────────────────────┐
                          │   hub (10.1.110.114) │
                          │  parabench-results.git│   ← bare repo
                          └──────────┬───────────┘
                                     │ git over SSH
        ┌──────────────┬─────────────┴────────────┬──────────────┐
        ▼              ▼                          ▼              ▼
   mac-laptop      server-b                   server-c       ...
   logs/           logs/                      logs/
    ├ .git/         ├ .git/                    ├ .git/
    ├ mac-laptop/   ├ mac-laptop/              ├ mac-laptop/
    ├ server-b/     ├ server-b/                ├ server-b/
    └ server-c/     └ server-c/                └ server-c/
```

## 一次性 setup

每台节点都需要做一次。

### 1. SSH 公钥到 hub

确保 `ssh yuzedong@10.1.110.114` 不需要输密码即可登录。
未配置时执行（在节点上）：

```bash
ssh-copy-id yuzedong@10.1.110.114
```

随后用 `BatchMode` 验证（应直接返回 OK）：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 yuzedong@10.1.110.114 'echo OK'
```

### 2. Hub 上准备 bare repo（仅在 hub 那台机器上做一次）

```bash
ssh yuzedong@10.1.110.114
mkdir -p /home/yuzedong/git
cd /home/yuzedong/git
git init --bare parabench-results.git
cd parabench-results.git
git symbolic-ref HEAD refs/heads/main
exit
```

完成后 hub 不再需要任何手工操作。

### 3. 写本机 sync 配置

```bash
cd <repo>
cp configs/sync.example.yaml configs/sync.yaml
$EDITOR configs/sync.yaml
```

至少需要修改：

- `self`：本机 host_tag。建议设为简短可识别的名字，例如 `mac-laptop`、
  `server-b`。该值必须等于运行时 `get_host_tag()` 返回的字符串
  （即 `PARABENCH_HOST_TAG` 环境变量优先，否则 `socket.gethostname()`
  短名经合法化后的结果）。验证当前值：

  ```bash
  python3 -c "from src.pipelines._host_tag import get_host_tag; print(get_host_tag())"
  ```

  推荐做法：在 `~/.bashrc` / `~/.zshrc` 里固定写
  `export PARABENCH_HOST_TAG=<想要的名字>`，避免依赖 hostname。

- `hub.ssh_target` / `hub.repo_path`：默认已填好
  `yuzedong@10.1.110.114:/home/yuzedong/git/parabench-results.git`，
  如有改动同步修改。

- `behavior.autosync_after_run` / `behavior.pull_before_run`：
  跑实验的节点都打开；纯分析机可关 push、保留 pull。

### 4. 引导嵌套 git

```bash
bash scripts/results_init.sh
```

脚本是幂等的，做的事：

1. 在 `logs/` 内 `git init -b main`
2. 写入白名单 `.gitignore`（只追踪 JSON / MD / LOG / XLSX 等明确产物）
3. 配 `remote origin = $HUB_SSH:$HUB_PATH`
4. 在 hub 上拉一次最新（如果还没有就跳过）
5. 推第一个 init commit

成功后，`logs/.git/` 出现，但**主仓 `git status` 不受影响**（主仓
`.gitignore` 已经忽略 `logs/`，这是嵌套独立 git 仓库）。

## 日常工作流

正常情况下你不需要手动调任何同步命令——`run_ablation.py` 会自动处理。

```bash
python src/pipelines/run_ablation.py --conditions baseline
# 启动早期: 自动 git pull --rebase
# ... 跑实验 ...
# 结束后:   自动 git add logs/<host>/ + commit + push
```

需要手动操作的两个场景：

### 手动改某个 results.json（事件 2）

```bash
bash scripts/results_sync.sh pull                  # 1. 先拉最新
$EDITOR logs/<host>/<run>/results.json             # 2. 改
bash scripts/results_sync.sh push -m "manual: ..."  # 3. 推
```

### 离线改后再批量推

```bash
bash scripts/results_sync.sh commit -m "offline edits"  # 仅本地 commit
# 联网后:
bash scripts/results_sync.sh push                       # 一次性推所有
```

## 同步包装命令速查

| 命令 | 行为 |
|---|---|
| `bash scripts/results_sync.sh pull` | `git pull --rebase` 拉所有节点最新 |
| `bash scripts/results_sync.sh push [-m MSG]` | 仅 add 本机 host_tag 命名空间，commit + push |
| `bash scripts/results_sync.sh commit [-m MSG]` | 同 push 但不联网 |
| `bash scripts/results_sync.sh status` | 本机 ahead/behind + hub 最近 history + 各节点最新 run |
| `bash scripts/results_sync.sh log [host]` | `git log --oneline`，可限定到某节点目录 |
| `bash scripts/results_sync.sh diff <a> <b>` | 透传 `git diff` |

## 追溯示例

进入嵌套仓库使用原生 git 命令获得最大灵活度：

```bash
cd logs

# 看某节点最近 10 次 run
git log --oneline -- server-b/ | head -10

# 看某次 run 修改了哪些 task 的分数
git show <commit_hash> -- 'mac-laptop/ablation_*/results.json'

# 看一天内 LAN 上谁跑了什么
git log --since=1.day --pretty=format:'%h %an %s'

# 回滚一次误改
git revert <commit_hash>
bash ../scripts/results_sync.sh push -m "revert: <reason>"

# 某条结果是哪次 commit 引入的
git blame mac-laptop/ablation_<ts>/baseline/qa_results.json
```

## 故障排查

### `Permission denied (publickey)`

SSH 公钥未生效。在节点上重做 `ssh-copy-id yuzedong@10.1.110.114`，
然后 `ssh -o BatchMode=yes yuzedong@10.1.110.114 echo OK` 验证。

### `Could not read from remote repository` / `fatal: ...`

通常是：(a) hub 路径打错；(b) hub bare repo 还没 init；(c) 防火墙挡 22 端口。
逐一检查 `configs/sync.yaml` 里 `hub.ssh_target` / `hub.repo_path`。

### `nothing to commit, working tree clean` 后 push 没反应

预期行为——本机命名空间没有新增就直接跳过 commit，但 push 仍会同步
之前未推的本地 commit。如果连一个本地 commit 都没有，push 会立即返回。

### 改了 hostname 但 `self` 字段没改

`get_host_tag()` 返回的目录会和 `self` 不一致 → push 时 add 范围不对，
等于"什么都没提交"。**修了 hostname 务必同步改 `configs/sync.yaml` 的
`self`**，或反过来设 `PARABENCH_HOST_TAG` 锁死命名空间。

### 想清掉一次错误 commit

```bash
cd logs
git log --oneline                # 找到坏 commit
git revert <hash>                # 干净的 forward fix
# 或者（仅本机未推时）
git reset --hard HEAD~1          # 危险，仅当此 commit 未 push
```
**绝不要** `git push --force` 到 hub——会破坏其他节点的 history。

### 误把大文件提交进来导致仓库膨胀

立刻按上面 revert，并在 `logs/.gitignore` 顶部加显式 `*.bin` 之类的
忽略规则。如果已 push 且仓库已显著膨胀，需要 `git filter-repo`
重写 history（请在 hub 上做、并通知所有节点重新 clone）。

## 何时升级到 Git LFS

当前结果都是 JSON / MD / LOG，单文件 < 1 MB，可放心走原生 git。

出现以下信号时考虑接入 LFS：

- 单次 run 出现 > 5 MB 单文件（截图/视频/.pkl）
- 仓库总体积 / `du -sh logs/.git` 月度涨幅 > 1 GB
- `git push` 时长 > 30 s

接入大致需要：在 `logs/` 下 `git lfs install` + `git lfs track "*.png"`，
并在 hub 上同样 enable。届时再独立写一份 LFS 切换说明。

## 不在本同步层负责的事

- 跨节点的实时索引 / 报表（`master_table.py` / `master_report.py`
  待统一改成扫 `logs/*/ablation_*` 模式后才能工作）
- 加密层（git over SSH 已加密）
- 自动迁移老的 `logs/ablation_*` 目录到新的 `logs/<host>/...`
  命名空间——需要时手动 `mv logs/ablation_* logs/<self>/`
