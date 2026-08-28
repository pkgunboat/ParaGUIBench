# ParaGUIBench 安装指南

ParaGUIBench 0.3 preview 将安装边界分成两层。**Core** 包含 benchmark
协议、framework、agents、评价器、RunStore 和 CLI，不引入第三方运行依赖；
**Live OSWorld** 在 Core 上增加候选 Linux/KVM 真实运行路径所需的模型、
图像、HTTP 和 Chrome CDP 探针依赖。该路径已有历史冒烟证据，但当前 manifest 中版本化
`live_validated` 任务仍为 0。两层均支持 Python 3.11–3.13，安装过程只使用标准
`venv` 与项目构建出的 wheel，不依赖已有项目环境。Core 可安装在 Linux
或 macOS；Live OSWorld 的真实 GUI 运行需要 Linux x86-64、Docker 与 KVM。
WebMall Checkout/EndToEnd 在该浏览器层上还需要四个已部署商店、WP-CLI
reader target 和分布式租约 coordinator。

先从公开仓库构建唯一 wheel。Ubuntu/Debian 需先安装 venv 组件
（`sudo apt install python3-venv`），否则下文所有 `python3 -m venv` 都会因缺
ensurepip 失败；无 sudo 权限时可改用自带 ensurepip 的发行版 Python
（如 conda base）：

```bash
git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench

python3 -m venv .build-venv
.build-venv/bin/python -m pip install --upgrade pip
.build-venv/bin/python -m pip wheel --no-deps --wheel-dir dist .

WHEEL_PATH="$(find "$(pwd)/dist" -type f -name 'paraguibench-*.whl' -print -quit)"
test -n "$WHEEL_PATH"
```

构建过程按照 `pyproject.toml` 在隔离环境中加载 `hatchling`。不要从旧
checkout 复制 package、依赖目录或配置文件。

Core 安装到一个全新的环境：

```bash
python3 -m venv .venv-core
.venv-core/bin/python -m pip install --no-index "$WHEEL_PATH"
.venv-core/bin/python scripts/installation/verify_install.py --profile core
.venv-core/bin/paraguibench --help >/dev/null
```

Live OSWorld 使用同一个 wheel 声明的 `live` extra：

```bash
python3 -m venv .venv-live
.venv-live/bin/python -m pip install \
  "paraguibench[live] @ file://${WHEEL_PATH}"
.venv-live/bin/python scripts/installation/verify_install.py \
  --profile live-osworld
```

该 profile 会额外验证 `openai`、`Pillow`、`requests` 和 `Playwright`。
Playwright 仅用于连接 guest Chrome 的 CDP 端点，不需要在 host 安装或
启动浏览器。`BatchOperation-001` 图片 getter 还会在 guest 内通过 `python3 -I`
导入 Pillow；host 安装 `live` extra 不能替代这一条件，固定 qcow2 镜像必须
在隔离 Python 中提供 Pillow。`CombinationDocs-015` 的单文件 getter 只使用 guest
标准库，但两项任务都必须在各自真实 guest 门禁通过前保持 blocked。
固定 HF 归档直接派生的 6bf qcow2 已被选为新的开源默认环境，
历史 6d reference qcow2 仅作为独立 legacy identity。image manifest 已以
schema v2 固定 archive→member→output recipe 和 6bf extracted SHA；冻结
cleanroom 源码在受控 Linux 上的可重现物化已完成独立审计，因此 233 项的
`osworld_vm_image_materialization_unverified` 已统一清除。该结论不替代
每任务 component、版本化 Attempt、receipt 或 allowlist；当前仍为
233 个 `blocked`、0 个 `live_validated`。
该检查不会下载 VM、连接模型服务、启动 Docker 或执行
任务。安装通过后，再按照
[`../deployment/osworld-linux.md`](../deployment/osworld-linux.md) 准备固定
镜像与任务输入资产，单独预置 evaluator-only gold，并运行
`paraguibench doctor`。外部 gold 不是 wheel 依赖：schema-v1 任务使用显式
`paraguibench gold fetch`；schema-v2 任务只在受控私有 provisioning host 上使用
`gold materialize`。`gold verify`、doctor 与评价均保持离线。

安装验证只证明 wheel 与依赖可加载，不会把任何任务升级为
`live_validated`；任务支持状态始终以 runtime-support manifest 为准。

### Evaluator-only 固定 gold

任务可以同时声明上传到 guest 的 `asset_manifest` 和只供宿主评价器读取的
`gold_manifest`。两类缓存必须分开，gold 字节不进入 Git、wheel、Agent 投影或
RunStore。`doctor` 与 `run` 不会隐式下载或派生；Settings-001 的
schema-v2 FFmpeg/ffprobe 8.1.1 私有物化流程见部署文档：

```bash
export PARAGUIBENCH_GOLD_CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/paraguibench/gold"
install -d -m 700 "$PARAGUIBENCH_GOLD_CACHE_ROOT"

.venv-live/bin/paraguibench gold fetch \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"

.venv-live/bin/paraguibench gold verify \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"
```

把同一 `--gold-cache-root` 传给 doctor 和 run。`gold_cache` 门禁会在 VM、
模型客户端构造、向模型服务传递凭据和 RunStore 创建前失败关闭；实际评价还会
重新打开并校验文件。doctor 仍可读取环境变量是否存在，以一次性报告全部门禁结果。

### WebMall 运行绑定

Core wheel 已包含标准库实现的 WebMall manifest loader、WP-CLI 证据 adapter、
distributed-lease 客户端/coordinator 与闭集评价器。真实任务还需要 Live
OSWorld 浏览器层、外部 WordPress/WooCommerce 服务及 host 上的 `wp` 命令；
pip 不会创建或重置这些服务。

environment manifest 固定了以下 runner 变量名：

```text
PARAGUIBENCH_WEBMALL_STORE_1_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_2_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_3_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_1_READER_TARGET
PARAGUIBENCH_WEBMALL_STORE_2_READER_TARGET
PARAGUIBENCH_WEBMALL_STORE_3_READER_TARGET
PARAGUIBENCH_WEBMALL_STORE_4_READER_TARGET
PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL
PARAGUIBENCH_WEBMALL_LEASE_TOKEN
WP_CLI_DOCKER_NO_TTY=1
```

coordinator 进程则从 `PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN` 读取匹配的
secret。它的值必须与 runner 的 `PARAGUIBENCH_WEBMALL_LEASE_TOKEN` 相同，但
两个进程应保持分离的环境 allowlist。真实 origin、reader target、endpoint 与
credential 都不得进入 Git 或日志；跨主机 coordinator 必须使用 HTTPS，只有
loopback endpoint 可使用明文 HTTP。

coordinator 启动、完整 `doctor` 和 GUI-only `run` 命令见
[`../deployment/webmall-linux.md`](../deployment/webmall-linux.md)。本地契约测试或
doctor 通过不会自动改变 runtime-support 状态；必须等带版本向量的实机
Attempt 成功后再单独审查。

真实凭据只能采用两种方式注入：仓库外、当前用户所有且权限为 `0600` 的
普通文件，或者部署平台的 secret manager。外部文件可按下面的步骤创建和
检查；命令本身不包含任何值：

```bash
export PARAGUIBENCH_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/paraguibench/secrets.env"
install -d -m 700 "$(dirname "$PARAGUIBENCH_SECRET_FILE")"
install -m 600 /dev/null "$PARAGUIBENCH_SECRET_FILE"
"${EDITOR:-vi}" "$PARAGUIBENCH_SECRET_FILE"

.venv-live/bin/python scripts/installation/verify_secret_file.py \
  --secret-file "$PARAGUIBENCH_SECRET_FILE" \
  --checkout-root .

set +x
. "$PARAGUIBENCH_SECRET_FILE"
```

文件内需要定义 `PARAGUIBENCH_MODEL_API_KEY` 和
`PARAGUIBENCH_MODEL_BASE_URL`。WebMall runner 还要由仓库外文件或部署平台
注入四店绑定、reader target、coordinator URL 和
`PARAGUIBENCH_WEBMALL_LEASE_TOKEN`；coordinator 使用它自己的
`PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN` secret 边界。验证器只检查文件类型、
所有权、权限和位置，
不会打开文件。使用 secret manager 时，将同名变量直接注入
`paraguibench` 进程，不创建 checkout 内配置，也不要通过 `env`、
`printenv` 或 shell tracing 检查值。

贡献者可用同一个 wheel 安装 `live,dev,artifact,methods` extra，并运行全部公开门禁：

```bash
python3 -m venv .venv-dev
.venv-dev/bin/python -m pip install \
  "paraguibench[live,dev,artifact,methods] @ file://${WHEEL_PATH}"
.venv-dev/bin/python -m pytest
.venv-dev/bin/python scripts/benchmark/validate_release.py --repo-root .
.venv-dev/bin/python scripts/benchmark/validate_runtime_support.py --repo-root .
.venv-dev/bin/python scripts/security/scan_repository.py --root .
```

`dev` extra 提供 pytest 以及测试套件必需的 `python-docx`、`openpyxl` 导入；
`artifact` 额外启用文档格式 evaluator 测试，缺省时这些测试会跳过；`methods`
提供原方法 runner 的运行时导入（`psutil`、`docker`、`pandas` 等）——缺省时
methods 的 1 个 smoke import 测试会失败，且 `methods_runner` 本身无法运行。

公共 CI 会在 Python 3.11、3.12 和 3.13 上重复 wheel-first 安装、CLI、
测试与三个 validator，但不接收 API key，也不执行真实 GUI E2E。详细依赖
边界见 [`dependency-tree.md`](dependency-tree.md)，稳定失败标识与安全排查方式
见 [`troubleshooting.md`](troubleshooting.md)。
