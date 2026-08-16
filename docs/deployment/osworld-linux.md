# OSWorld Linux 从源码部署

本文档给出 ParaGUIBench 0.1 preview 的最小可复现路径：从公开源码 checkout
创建隔离 Python 环境，准备固定 OSWorld 镜像和任务资产，通过全部部署门禁，
再对首个候选任务 `InformationRetrieval-FileSearch-Readonly-001`
执行带 RunStore v2 版本向量的真实环境复验。

早期 GUI-only Seed18、单 VM、单 worker 和 exact evaluator 路径曾完成真实冒烟运行，
但该证据没有新版本向量，因此现在仅作为 historical unversioned 记录。
233 个 canonical 任务当前均在 `benchmark/manifests/runtime-support-v1.json`
中标记为 `blocked`，复验通过前不得恢复 live 声明。

CLI 另提供 Qwen GUI-only，以及 `kimi-k2.6` planner + Qwen worker
的 `paragui-single-vm` 实验路径。两者均不属于本页所声明的参考
live 结果；后者固定单 VM 串行，不是多 VM 并行 ParaGUI。
需要在同一浏览器层上运行 WebMall Checkout/EndToEnd 时，还必须按
[`webmall-linux.md`](webmall-linux.md) 绑定四店、WP-CLI reader 和分布式租约服务。

> [!WARNING]
> OSWorld VM 归档和容器镜像仅按 manifest 执行 download/pull，不进入 Git。
> 固定 ZIP 直接派生的 6bf 镜像已被选为新的开源默认环境，
> 历史 6d reference qcow2 仅作为独立 legacy identity，不得混用。
> schema v2 recipe 已固定，冻结 cleanroom 源码的受控 Linux 可重现物化
> 已完成并经独立审计；这只清除统一镜像物化 blocker，不会替代每任务
> component、版本化 Attempt、receipt 或 allowlist。不要把其他本地 qcow2
> 或物化结果手工写回 manifest 以绕过身份门禁。镜像
> 再分发边界和分层许可仍在审计；获取或使用前应自行确认上游条款。

## 从 dirty 工作树构建 cleanroom release

迁移分支尚未形成可公开 checkout 时，不应仅记录 Git HEAD：HEAD 不能唯一
标识已修改文件和必要的非忽略 untracked 迁移源码。在可信的本地工作树
运行下列命令；输出目录放在 checkout 之外，且不向脚本传递任何凭据、
endpoint 或内网主机参数：

```bash
RELEASE_OUT="/tmp/paraguibench-cleanroom-release"

python scripts/deployment/release_bundle.py build \
  --repo-root . \
  --output-dir "$RELEASE_OUT" \
  --name paragui-migration

python scripts/deployment/release_bundle.py verify \
  --archive "$RELEASE_OUT/paragui-migration.tar.gz" \
  --manifest "$RELEASE_OUT/paragui-migration.manifest.json" \
  --checksums "$RELEASE_OUT/paragui-migration.sha256"
```

build 会先执行 `scripts/security/scan_repository.py`，再从 Git tracked 与非忽略
untracked 候选中应用固定白名单。安全性不依赖 `.gitignore`：仅仓库根
`.env.example` 在严格 UTF-8、至多 64 KiB、只含注释/空行/唯一 `NAME=` 空值
声明的语义门禁后例外入包；其它根目录或嵌套 `.env*`、credential/key 文件、
`.git`、`.venv*`、`node_modules`、`__pycache__`、
run/log/cache、gold 私有字节、qcow2/镜像、归档和临时文件由独立拒绝
规则与类型门禁处理。`benchmark/gold/manifests/` 中的公开摘要元数据
可进入包，对应 gold 文件字节不进入。打包器不读取进程环境变量值，
且拒绝 symlink、special file、绝对/`..` 路径、大小写或 Unicode 路径冲突
以及文件数/字节越界。

输出为 tar.gz、外置 JSON 文件清单和 SHA256 sidecar。JSON 清单不放入
tar，避免自引用；文件顺序、tar mtime/uid/gid/mode 和 gzip mtime 均固定。
因此同一组路径与字节会得到同一 source-tree SHA-256 和 archive SHA-256。
依赖关系为：

```text
dirty Git tree
  → static security scanner
  → fixed public-source allowlist + nofollow/type/size/collision gates
  → strict empty-value root .env.example semantic gate
  → in-memory bounded file snapshots
  → deterministic tar.gz
  → external JSON per-file SHA-256 manifest
  → two-entry SHA256 sidecar
```

三件套上传不在 build 命令的权限范围内。经认证通道将三件套与当前
`release_bundle.py` 的可信副本送到 Linux staging 目录后，先离线验证、再解压。
`<source-tree-sha256>` 必须替换为 build 成功输出的完整摘要；目标目录必须
预先不存在，以保留这次冻结的不可变边界：

```bash
STAGING="$HOME/paraguibench-release-staging"
RELEASE_ROOT="$HOME/ParaGUIBench-cleanroom/<source-tree-sha256>"
VENV_ROOT="$HOME/.local/share/paraguibench/venvs/<source-tree-sha256>"

python -B "$STAGING/release_bundle.py" verify \
  --archive "$STAGING/paragui-migration.tar.gz" \
  --manifest "$STAGING/paragui-migration.manifest.json" \
  --checksums "$STAGING/paragui-migration.sha256"

test ! -e "$RELEASE_ROOT"
install -d -m 755 "$RELEASE_ROOT"
umask 022
tar --extract \
  --gzip \
  --file "$STAGING/paragui-migration.tar.gz" \
  --directory "$RELEASE_ROOT" \
  --strip-components=1 \
  --no-same-owner \
  --no-same-permissions

cd "$RELEASE_ROOT"
test ! -e "$VENV_ROOT"
python3.12 -m venv "$VENV_ROOT"
. "$VENV_ROOT/bin/activate"
python -m pip install --upgrade pip
python -m pip install '.[live,operation,artifact,dev]'
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider
python -B scripts/benchmark/validate_release.py --repo-root .
python -B scripts/benchmark/validate_runtime_support.py --repo-root .
python -B scripts/security/scan_repository.py --root .
```

上述 verify 在同一 archive file descriptor 上完成归档摘要、gzip/tar 元数据、
路径闭集与每文件摘要校验，不向文件系统解压。不应在 verify 成功前
直接展开 tar。缓存、VM、RunStore 和 secret 仍必须按下文放在
`RELEASE_ROOT` 之外；Python venv 也按 source-tree SHA 放在外部 data 目录，
并使用非 editable 安装与无 pytest/bytecode cache 的门禁命令，避免改写冻结的
源树。不得把旧环境的 `.env`、run/log 或 gold 实体复制进新目录。

## 前置条件与源码安装

建议使用 Linux x86-64 主机，并满足以下条件：

- Python 3.11、3.12 或 3.13；参考部署使用 Python 3.12。
- Docker daemon 可用，当前用户能运行 `docker`。
- `/dev/kvm` 存在且当前用户可读写。
- VM 归档、解压镜像和容器层所需的充足本地磁盘空间。
- 三个互不相同且未占用的 1024–65535 TCP 端口；runtime 仅绑定
  loopback，分别用于 OSWorld controller、VNC 与受控 Chromium/CDP 通道。
- 一个支持标准 tool calls 的 OpenAI-compatible 模型服务。

```bash
git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[live,dev,artifact]'

python -m pytest
python scripts/benchmark/validate_release.py --repo-root .
python scripts/benchmark/validate_runtime_support.py --repo-root .
python scripts/security/scan_repository.py --root .
```

安装依赖来自 `pyproject.toml`：默认 `core` 不含第三方运行依赖，`live` 安装
`openai`、`Pillow`、`requests` 和用于 active-tab CDP 采集的 `playwright`，
`dev` 安装 `pytest`。请不要从旧项目复制
统一 `requirements.txt` 或私有配置。

## 外部状态目录与凭据

运行日志、资产缓存、VM 和 secret 均应放在源码 checkout 外。下面只创建目录和
空 secret 文件，不包含任何 endpoint 或 key 值：

```bash
export PARAGUIBENCH_ASSET_CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/paraguibench/assets"
export PARAGUIBENCH_GOLD_CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/paraguibench/gold"
export PARAGUIBENCH_RUNS_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/paraguibench/runs"
export PARAGUIBENCH_VM_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/paraguibench/osworld"
export PARAGUIBENCH_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/paraguibench/secrets.env"

install -d -m 700 \
  "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
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

根 `.env.example` 只说明可用变量名，CLI 不会自动读取它。打包器与离线验证器
都只接受注释、空行和唯一 `NAME=` 空值；非空值、`export`、重复变量、非法名称、
NUL、非法编码或超限模板均 fail closed。不要在 checkout 中创建含真实值的
`.env`，也不要用 `echo`、`env`、`printenv` 或调试 tracing 显示 secret。

## 固定 OSWorld 镜像

`environments/osworld/image-manifest.json` 以 schema v2 固定 VM 归档、唯一 ZIP
member、local/central extra records、输出 qcow2 与容器 digest 的闭集。
固定 ZIP 直接派生的 6bf qcow2 已被选为新的开源默认
environment identity；历史 6d qcow2 与它在 guest-visible 内容上不同，
只能使用独立 legacy identity。当前
`status=verified_reproducible_materialization` 表示 recipe、
`extracted_image.sha256` 与冻结 cleanroom 源码的受控 Linux 物化结果已经
共同核验；统一的 `osworld_vm_image_materialization_unverified` 已从 233 项
投影中清除。所有任务仍因各自 component 或版本化 live Attempt 门禁保持
`blocked`，当前 `live_validated=0/233`。

下面的命令只允许下载并验证归档，不得把裸 `unzip` 的结果或历史缓存手工写回
manifest 后绕过门禁：

```bash
export PARAGUIBENCH_VM_ARCHIVE="$PARAGUIBENCH_VM_ROOT/Ubuntu.qcow2.zip"

VM_REPOSITORY="$(python -B -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['repository'])")"
VM_REVISION="$(python -B -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['revision'])")"
VM_OBJECT="$(python -B -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['path'])")"
VM_ARCHIVE_SHA256="$(python -B -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['vm_archive']['sha256'])")"

curl --fail --location \
  --output "$PARAGUIBENCH_VM_ARCHIVE" \
  "https://huggingface.co/datasets/${VM_REPOSITORY}/resolve/${VM_REVISION}/${VM_OBJECT}?download=true"
printf '%s  %s\n' "$VM_ARCHIVE_SHA256" "$PARAGUIBENCH_VM_ARCHIVE" | sha256sum --check

CONTAINER_IMAGE="$(python -B -c "import json; print(json.load(open('environments/osworld/image-manifest.json'))['container']['image'])")"
docker pull "$CONTAINER_IMAGE"
docker image inspect "$CONTAINER_IMAGE" >/dev/null
```

不得使用裸 `unzip`。正式物化入口只接受绝对 `--repo-root`，并将 trust anchor
固定为该仓库下的 `environments/osworld/image-manifest.json`；它不接受任意
`--manifest`。固定路径、归档与输出目录均逐级 nofollow 持有，Linux 上以匿名
`O_TMPFILE` 输出、完整重算摘要，再以 held-FD `linkat` no-replace 发布到新的
ParaGUIBench-owned 0700 目录。该入口不修改原归档、manifest 或既有 qcow2，
也不自动宣布 live 就绪：

```bash
REPO_ROOT="$(pwd)"
MATERIALIZED_DIR="$(mktemp -d "$PARAGUIBENCH_VM_ROOT/paraguibench-materialized-6bf.XXXXXX")"

PYTHONDONTWRITEBYTECODE=1 "$VENV_ROOT/bin/python" -B -m \
  paraguibench.cli.osworld_qcow2_materializer \
  --repo-root "$REPO_ROOT" \
  --archive "$PARAGUIBENCH_VM_ARCHIVE" \
  --output-parent "$MATERIALIZED_DIR"

export PARAGUIBENCH_QCOW2_PATH="$MATERIALIZED_DIR/Ubuntu.qcow2"
```

`paraguibench.cli.osworld_qcow2_materializer` 是唯一正式 `python -m`
入口。它是只导入 canonical implementation `main` 的薄模块，因此 manifest loader
与物化器共享同一个严格 `OSWorldQcow2MaterializationSpec` 类型身份。不得直接执行
`paraguibench.integrations.osworld.qcow2_materializer`；该实现模块会在解析参数或访问
任一路径前返回固定迁移错误，防止 `runpy` 以 `__main__` 再加载一份类型定义。正式
入口不接受 `--manifest`、凭据或其他扩展参数，所有参数错误只输出固定脱敏错误，
不会回显未知参数值。

### 冻结源码的无 bytecode 正式 CLI

cleanroom 的版本向量会直接绑定 `RELEASE_ROOT/src/paraguibench`，因此从该源码树
导入 production package 也不得生成 `__pycache__` 或 `*.pyc`。解释器会在执行
package 的 `__init__`、console script 或 CLI `main` 之前决定是否写 bytecode，
所以不得在冻结源码树上直接运行 `paraguibench ...` 或
`python -m paraguibench.cli.main`，也不能依赖 package 内部补设开关。

在正式 qcow2 物化完成后定义下面的部署函数。它通过 release 内固定 bootstrap
运行所有后续 production CLI；bootstrap 在首次 `paraguibench` import 前同时
设置当前解释器开关与可供子进程继承的环境开关，然后原样转交正式 CLI，
不解析、记录或回显参数值：

```bash
export PYTHONPATH="$RELEASE_ROOT/src"

paraguibench_cleanroom() {
  "$VENV_ROOT/bin/python" \
    "$RELEASE_ROOT/scripts/deployment/run_cleanroom_cli.py" "$@"
}
```

除该正式 CLI bootstrap 外，cleanroom 内所有一次性 Python resolver、preflight、
验证器和内联检查都必须以 `python -B ...` 启动；仅给某一个较晚的 package
import 设置 `sys.dont_write_bytecode` 不构成源码闭集保护。

正式物化要求 `MATERIALIZED_DIR` 是当次新建、当前用户所有、权限为 0700
且不含既有 `Ubuntu.qcow2`的目录。对冻结 cleanroom 源码的受控执行、
输出摘要、`qemu-img check`、原归档前后身份与安全扫描必须共同形成可审计
回执。正式命令在 output、archive 与固定 manifest 的 held-FD capability
仍打开时重新执行完整摘要和路径连续性验证，stdout 只输出固定 output name、
SHA-256 与大小；上下文退出后这些字段只证明
materialization-at-evidence-time，不表示 pathname 此后持续可信。
`doctor` 的 `qcow2_digest` 可以验证当次物化产物的字节摘要，但该单项
PASS 不等于正式物化证据，也不构成任务 live 授权。当前正式 manifest 已在
受控回执经独立审核后更新为 `live_run_ready=True`；CLI 仍必须继续执行
每任务 component、doctor、版本向量与 receipt 门禁。固定镜像身份、两份
摘要与环境分层见
[`environments/osworld/README.md`](../../environments/osworld/README.md)
与同目录 `image-manifest.json`。
文件名、大小、mtime、“镜像未变”或某台机器上存在旧缓存都不能替代该回执。

> [!WARNING]
> Qwen GUI worker 的快捷键拒绝列表只是应用层缓解措施，不能阻止模型点击可见的
> 终端图标、应用菜单或其他 launcher。Qwen 实机验证使用的 VM 镜像必须禁用或移除终端、
> shell launcher、开发者工具和对应桌面入口，并使用无宿主凭据的非特权 guest 用户。
> 若为此修改镜像，必须重新固定 qcow2 SHA-256、更新 manifest 并重新执行 doctor；在该
> 镜像门禁获得证据前，Qwen 路径不得升级为 `live_validated`。

## 任务输入资产、评价器 gold、部署门禁与真实运行

先下载并验证当前代表任务的固定资产闭集：

```bash
paraguibench_cleanroom assets fetch \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench_cleanroom assets verify \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"
```

若验证首个 external-gold artifact 切片
`Operation-FileOperate-CombinationDocs-015`，输入 DOCX 与评价器 BibTeX
必须分别预置。`assets` 缓存会上传到 guest；`gold` 缓存只允许 evaluator
读取，绝不能复用或放进源码 checkout：

```bash
paraguibench_cleanroom assets fetch \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench_cleanroom assets verify \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench_cleanroom gold fetch \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"

paraguibench_cleanroom gold verify \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"
```

只有 schema-v1 任务的显式 `gold fetch` 允许访问 manifest 固定的 Hugging Face commit；
`gold verify`、`doctor` 和真实评价均不联网，并重新检查私有目录权限、文件类型、
大小与 SHA-256。成功时 CLI 只输出 manifest ID、条目数和 PASS；失败时只输出
固定异常类型，不输出来源 URL、缓存路径、logical key、摘要或正文。无 external
gold 的任务不会创建或读取 gold 缓存。

`Operation-FileOperate-Settings-001` 是独立的 schema-v2 private-derived 模式，
不得调用 `gold fetch`。它必须先在受控、同一操作者的私有
provisioning host 上用已验证 input cache 和 FFmpeg/ffprobe 8.1.1 物化：

```bash
paraguibench_cleanroom assets fetch \
  --repo-root . \
  --task-id Operation-FileOperate-Settings-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench_cleanroom assets verify \
  --repo-root . \
  --task-id Operation-FileOperate-Settings-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench_cleanroom gold materialize \
  --repo-root . \
  --task-id Operation-FileOperate-Settings-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --ffmpeg-path /absolute/private/toolchain/ffmpeg \
  --ffprobe-path /absolute/private/toolchain/ffprobe \
  --timeout-seconds 120

paraguibench_cleanroom gold verify \
  --repo-root . \
  --task-id Operation-FileOperate-Settings-001 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"
```

物化器只能使用 manifest 固定的 canonical MP4、PTS 规则、产物摘要和
decoded-RGB 摘要，不接受 URL、输出覆盖或派生参数。产物必须保持
host-only、0700 目录/0600 单链接普通文件：不进 Git、guest、Agent、
RunStore 或公开主机。许可证据绑定 source dataset，不授予派生 PNG
的公开再分发权。当前受控 reference cleanroom host 的 `PATH` 中没有
ffmpeg/ffprobe；不得为此联网安装或改写 cleanroom。应在已审计的
私有 provisioning host 产生并完整验证固定 PNG，再经明确授权的私有通道
上传 mode-0600 产物；Linux 运行时只执行离线 production resolver。

上述派生、阈值标定和离线 resolver 只闭合本地语义合同，不是 live
证据。Settings 仍精确保留 getter、gold、setup 与 versioned-live 四个门禁；
它不在现有 12-task component candidate/receipt/promotion 闭集内。

上述本地接线不等于 Settings-001 已可声明 live。其 runtime-support 条目仍固定保留
`osworld_artifact_getter_live_validation_not_completed`、
`osworld_artifact_gold_live_validation_not_completed`、
`osworld_task_setup_live_validation_not_completed` 和
`versioned_live_validation_not_completed`；四项必须由同一真实 VM/Attempt 证据
闭合后才能调整。

`Operation-FileOperate-BatchOperation-003` 的 input ZIP 和 evaluator-only
gold ZIP 同样必须经由两套物理隔离的缓存获取与复核：

```bash
paraguibench_cleanroom assets fetch \
  --repo-root . \
  --task-id Operation-FileOperate-BatchOperation-003 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench_cleanroom assets verify \
  --repo-root . \
  --task-id Operation-FileOperate-BatchOperation-003 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT"

paraguibench_cleanroom gold fetch \
  --repo-root . \
  --task-id Operation-FileOperate-BatchOperation-003 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"

paraguibench_cleanroom gold verify \
  --repo-root . \
  --task-id Operation-FileOperate-BatchOperation-003 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"
```

这两个 manifest 固定在 xlang revision
`711e0811642364e7aa8f10a8918367d0b626d578`。生产 CLI 的匿名
fetch/verify 已复核 input `raw_book.zip` 与 gold `book.zip` 的精确字节、
MIME、ZIP 成员闭集与 CRC；这不表示 guest setup、任务后 getter、gold
评价或完整 Attempt 已在真实 VM 中验证。统一镜像物化 blocker 已清除；
该任务仍精确保留 `osworld_artifact_getter_live_validation_not_completed`、
`osworld_artifact_gold_live_validation_not_completed`、
`osworld_task_setup_live_validation_not_completed` 和
`versioned_live_validation_not_completed` 四个 blocker。

选择三个空闲端口，并让 `doctor` 一次性检查 Python、KVM、Docker daemon、
固定容器镜像、qcow2 摘要、任务输入资产、evaluator-only gold、三个 loopback
端口、Playwright 依赖、API key 引用和模型 base URL（公网 HTTPS，本地可用 HTTP）：

```bash
export PARAGUIBENCH_SERVER_PORT=5527
export PARAGUIBENCH_VNC_PORT=8527
export PARAGUIBENCH_CHROMIUM_PORT=9527
```

三个端口必须在当前主机重新选择并确认只绑定 loopback；示例数字不是资源预约。
若要先执行不运行 Agent 的 PPT-003 component candidate，还必须把固定 20 项 input
放在通用 asset root 下的 task-scoped 目录，并把固定 32 项 host-only gold 放在
一个只含该严格闭集的私有目录。gold 不得进入 guest、RunStore 或 stdout：

```bash
export PARAGUIBENCH_PPT003_GOLD_ROOT="$PARAGUIBENCH_GOLD_CACHE_ROOT/pipeline-implicit-ppt003-private"

paraguibench_cleanroom pipeline-implicit component-validate \
  --repo-root "$RELEASE_ROOT" \
  --task-id Operation-FileOperate-BatchOperationPPT-003 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_PPT003_GOLD_ROOT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --run-id run-pipeline-component-001 \
  --attempt-id attempt-001 \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT"
```

candidate 不接收模型、API key、Agent final text 或 receipt 输出路径。需要保存
stdout 时，应先以 `umask 077` 在 `RELEASE_ROOT` 外创建私有接收文件。该 component
receipt 仍必须独立审计和显式 allowlist；candidate PASS 不能直接提升 runtime
支持状态。candidate 完成并释放端口后，再对目标任务运行 doctor：

```bash
paraguibench_cleanroom doctor \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT"
```

全部 doctor 检查为 `PASS` 后，设置非敏感的模型标识并启动任务。CLI 不接受 key 或
endpoint 值作为参数；`--api-key-env` 与 `--base-url-env` 只接受环境变量名，
通常无需覆盖默认值。

Chromium 端口是为需要最终浏览器状态的评价协议预留的 host-side
loopback 入口，不应绑定到公网接口。它不表示 active-tab 任务已通过
live gate；当前总体仍为 `live_validated=0/233`。

> [!WARNING]
> Chrome CDP 本身没有本项目定义的认证层。Docker 只能将 guest `9222`
> 映射到 host `127.0.0.1:<chromium-port>`；guest 内的 `socat` 只为 QEMU/容器
> 转发监听 `9222`，容器必须 attempt-scoped 并按精确 container ID 清理。
> Controller、CDP 和 accessibility-tree HTTP 客户端必须禁用宿主的
> `HTTP_PROXY`/`HTTPS_PROXY` 继承，避免 loopback 流量外送或误路由。Playwright
> 在此路径仅 attach 已存在的 guest Chrome，不应为 host 另外下载浏览器。
> 正式 live gate 还必须实测 CDP/accessibility 的 10 秒超时与 2 MiB
> accessibility 响应上限，不能仅依赖单元测试。

`doctor` 不会向模型服务发请求，因此不能证明 Kimi 强制具名 Function
Calling 或 Qwen `computer_use` 兼容。Qwen GUI-only 实验运行前，先以
不启动 VM 的最小模型探针验证实际 native 协议。下列命令只
接受默认的三个环境变量名；若名称不同，只传
`--api-key-env`、`--base-url-env` 和 `--model-env` 覆盖引用名，
不得传入 key、URL 或 model 值。

```bash
# 加载 secret 前始终禁用 shell tracing；不要在命令行展开 key。
set +x
export PARAGUIBENCH_MODEL_ID="<OpenAI-compatible model identifier>"

paraguibench_cleanroom model-probe qwen-native
```

探针只在内存中生成 32×32 空白 PNG，通过 production
`QwenOpenAIModel.next_action` 发送一次有界请求，并丢弃返回动作。
它不创建 VM、controller、worker 或 RunStore，不执行 GUI 动作，
不写日志或响应文件。成功时 stdout 只有：

```text
PASS qwen-native-computer-use
```

失败时 stderr 只会是以下固定类型之一，不输出 endpoint、
key、模型响应、异常消息或 traceback：

```text
FAIL ProbeConfigurationError
FAIL QwenActionRejectedError
FAIL QwenModelError
FAIL ProbeInternalError
```

该命令会发生一次真实模型服务请求（SDK 仍可按 production
配置重试），因此需要 `.[live]` 依赖并可能产生少量费用。
探针 PASS 只证明 endpoint 返回可解析的 native `computer_use`，
不证明模型能完成 benchmark 任务，也不能替代实机 live gate。

```bash
paraguibench_cleanroom run \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --model "$PARAGUIBENCH_MODEL_ID"
```

上述命令默认使用 Seed18；它只有历史无版本向量的冒烟证据，尚未达到当前
`live_validated` 门禁。若要进行实验性 Qwen 3.7 Flash
GUI-only 验证，使用下列完整命令：

```bash
export PARAGUIBENCH_MODEL_ID="qwen3.7-flash-2026-07-15"

paraguibench_cleanroom run \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --model "$PARAGUIBENCH_MODEL_ID" \
  --agent-system gui-only \
  --worker qwen \
  --qwen-tool-protocol native
```

`native` 是默认模式。只有 endpoint 明确要求 OSWorld 上游的文本 XML 工具格式时，
才改为 `--qwen-tool-protocol osworld_xml`；它不是 native 请求失败后的自动 fallback。
Qwen 的调用、成本默认值与尚未完成的多 VM 边界见
[`docs/agents/qwen.md`](../agents/qwen.md)。

若要用 Kimi 规划并汇总、Qwen 3.7 Flash 执行 GUI subtask，使用下列
实验性单 VM 串行命令。默认的 planner 与 worker 引用同一对环境变量；
这只是复用变量名，值仍不会进入 CLI 参数、源码或 RunStore。

```bash
export PARAGUIBENCH_MODEL_ID="qwen3.7-flash"

paraguibench_cleanroom run \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --agent-system paragui-single-vm \
  --worker qwen \
  --model "$PARAGUIBENCH_MODEL_ID" \
  --planner-model kimi-k2.6 \
  --planner-max-subtasks 4 \
  --planner-max-output-tokens 2048 \
  --max-steps 12 \
  --qwen-visual-history 4 \
  --qwen-tool-protocol native
```

`--max-steps` 是每个 subtask 的上限，不是整个 plan 的总上限。
浮动别名 `qwen3.7-flash` 适合便宜的功能验证，不能作为严格可复现
benchmark 的模型身份。完整执行、成本、数据外发与评价边界见
[`docs/agents/kimi-qwen-single-vm.md`](../agents/kimi-qwen-single-vm.md)。

退出码 `0` 表示评价通过，`1` 表示任务完成但评价未通过，`2` 表示配置、部署
门禁或执行异常。命令只输出稳定身份、执行终态、评价终态和分数；不会回显
secret 或模型原始响应。可使用命令返回的身份安全复查：

```bash
paraguibench_cleanroom inspect \
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

运行记录与评价结果边界见
[`docs/evaluation/protocol.md`](../evaluation/protocol.md)。
公开 0.1 不附带历史冒烟运行日志。
