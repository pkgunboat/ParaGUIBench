# OSWorld Linux 从源码部署

本文档给出 ParaGUIBench 0.1 preview 的最小可复现路径：从公开源码 checkout
创建隔离 Python 环境，准备固定 OSWorld 镜像和任务资产，通过全部部署门禁，
再运行当前唯一 `live_validated` 的任务
`InformationRetrieval-FileSearch-Readonly-001`。

当前路径只覆盖 GUI-only Seed18、单 VM、单 worker 和 exact evaluator。233 个
canonical 任务定义中，只有该任务完成了真实端到端验证，其余 232 个条目仍在
`benchmark/manifests/runtime-support-v1.json` 中标记为 `blocked`。

> [!WARNING]
> OSWorld VM 归档和容器镜像仅按 manifest 执行 download/pull，不进入 Git。
> 解压后 qcow2 的 SHA-256 已在参考部署中验证，但镜像再分发边界和分层许可仍
> 在审计。获取或使用这些资产前，应自行确认上游条款；不要重新打包或镜像分发。

## 前置条件与源码安装

建议使用 Linux x86-64 主机，并满足以下条件：

- Python 3.11、3.12 或 3.13；参考部署使用 Python 3.12。
- Docker daemon 可用，当前用户能运行 `docker`。
- `/dev/kvm` 存在且当前用户可读写。
- VM 归档、解压镜像和容器层所需的充足本地磁盘空间。
- 两个未占用的 1024–65535 TCP 端口；runtime 仅绑定 loopback。
- 一个支持标准 tool calls 的 OpenAI-compatible 模型服务。

```bash
git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[live,dev]'

python -m pytest
python scripts/benchmark/validate_release.py --repo-root .
python scripts/benchmark/validate_runtime_support.py --repo-root .
python scripts/security/scan_repository.py --root .
```

安装依赖来自 `pyproject.toml`：默认 `core` 不含第三方运行依赖，`live` 安装
`openai`、`Pillow` 和 `requests`，`dev` 安装 `pytest`。请不要从旧项目复制
统一 `requirements.txt` 或私有配置。

## 外部状态目录与凭据

运行日志、资产缓存、VM 和 secret 均应放在源码 checkout 外。下面只创建目录和
空 secret 文件，不包含任何 endpoint 或 key 值：

```bash
export PARAGUIBENCH_ASSET_CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/paraguibench/assets"
export PARAGUIBENCH_RUNS_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/paraguibench/runs"
export PARAGUIBENCH_VM_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/paraguibench/osworld"
export PARAGUIBENCH_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/paraguibench/secrets.env"

install -d -m 700 \
  "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  "$PARAGUIBENCH_RUNS_ROOT" \
  "$PARAGUIBENCH_VM_ROOT" \
  "$(dirname "$PARAGUIBENCH_SECRET_FILE")"
install -m 600 /dev/null "$PARAGUIBENCH_SECRET_FILE"
"${EDITOR:-vi}" "$PARAGUIBENCH_SECRET_FILE"
```

该外部文件需要以 shell `export` 语句定义
`PARAGUIBENCH_MODEL_API_KEY` 和 `PARAGUIBENCH_MODEL_BASE_URL`，但不要把值
复制到命令历史、文档、issue 或日志。也可以由部署平台 secret manager 直接
注入这两个变量。加载文件前应关闭 shell tracing，并验证所有权和权限：

```bash
set +x
test -O "$PARAGUIBENCH_SECRET_FILE"
test "$(stat -c '%a' "$PARAGUIBENCH_SECRET_FILE")" = 600
. "$PARAGUIBENCH_SECRET_FILE"
```

`.env.example` 只说明可用变量名，CLI 不会自动读取它。不要在 checkout 中创建
含真实值的 `.env`，也不要用 `echo`、`env`、`printenv` 或调试 tracing 显示
secret。

## 固定 OSWorld 镜像

`environments/osworld/image-manifest.json` 固定 VM 归档的 provider、revision、
路径、大小和 SHA-256，以及解压后 qcow2 摘要和容器 digest。下列命令从
manifest 读取值，避免在部署脚本中复制可漂移的版本：

```bash
export PARAGUIBENCH_VM_ARCHIVE="$PARAGUIBENCH_VM_ROOT/Ubuntu.qcow2.zip"
export PARAGUIBENCH_QCOW2_PATH="$PARAGUIBENCH_VM_ROOT/Ubuntu.qcow2"

VM_REPOSITORY="$(python -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['repository'])")"
VM_REVISION="$(python -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['revision'])")"
VM_OBJECT="$(python -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['path'])")"
VM_ARCHIVE_SHA256="$(python -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['sha256'])")"

curl --fail --location \
  --output "$PARAGUIBENCH_VM_ARCHIVE" \
  "https://huggingface.co/datasets/${VM_REPOSITORY}/resolve/${VM_REVISION}/${VM_OBJECT}?download=true"
printf '%s  %s\n' "$VM_ARCHIVE_SHA256" "$PARAGUIBENCH_VM_ARCHIVE" | sha256sum --check

unzip "$PARAGUIBENCH_VM_ARCHIVE" -d "$PARAGUIBENCH_VM_ROOT"
QCOW2_SHA256="$(python -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['extracted_image']['sha256'])")"
printf '%s  %s\n' "$QCOW2_SHA256" "$PARAGUIBENCH_QCOW2_PATH" | sha256sum --check

CONTAINER_IMAGE="$(python -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['container']['image'])")"
docker pull "$CONTAINER_IMAGE"
docker image inspect "$CONTAINER_IMAGE" >/dev/null
```

`doctor` 会再次完整计算 qcow2 SHA-256。文件名、大小、mtime 或“镜像未变”的
判断都不能替代摘要验证。若已有外部缓存，可跳过下载和解压，但仍必须执行摘要
检查；这也正是参考部署采用的边界。

## 任务资产、部署门禁与真实运行

先下载并验证当前代表任务的固定资产闭集：

```bash
paraguibench assets fetch \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench assets verify \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"
```

选择两个空闲端口，并让 `doctor` 一次性检查 Python、KVM、Docker daemon、
固定容器镜像、qcow2 摘要、任务资产、两个 loopback 端口、API key 引用和
HTTPS 模型 base URL：

```bash
export PARAGUIBENCH_SERVER_PORT=5527
export PARAGUIBENCH_VNC_PORT=8527

paraguibench doctor \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT"
```

十项全部为 `PASS` 后，设置非敏感的模型标识并启动任务。CLI 不接受 key 或
endpoint 值作为参数；`--api-key-env` 与 `--base-url-env` 只接受环境变量名，
通常无需覆盖默认值。

```bash
export PARAGUIBENCH_MODEL_ID="<OpenAI-compatible model identifier>"

paraguibench run \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --model "$PARAGUIBENCH_MODEL_ID"
```

退出码 `0` 表示评价通过，`1` 表示任务完成但评价未通过，`2` 表示配置、部署
门禁或执行异常。命令只输出稳定身份、执行终态、评价终态和分数；不会回显
secret 或模型原始响应。可使用命令返回的身份安全复查：

```bash
paraguibench inspect \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --run-id "<run-id>" \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --attempt-id attempt-001
```

RunStore 目录固定为 `0700`、文件固定为 `0600`，并按
`run_id/task_id/attempt_id` 隔离。runtime 只清理本次 `docker run` 返回的精确
容器 ID，不扫描或停止其他容器、QEMU 进程或 VM。运行结束后可只读确认没有
活动的本项目 owned container：

```bash
docker ps --filter label=paraguibench.owned=true --format '{{.ID}}'
```

参考部署的真实结果与验证边界见
[`docs/reproduction/reference-run-20260729.md`](../reproduction/reference-run-20260729.md)。
