# ParaGUIBench 安装指南

ParaGUIBench 0.1 preview 将安装边界分成两层。**Core** 包含 benchmark
协议、framework、agents、评价器、RunStore 和 CLI，不引入第三方运行依赖；
**Live OSWorld** 在 Core 上增加当前 Linux/KVM 真实运行路径所需的模型、
图像和 HTTP 依赖。两层均支持 Python 3.11–3.13，安装过程只使用标准
`venv` 与项目构建出的 wheel，不依赖已有项目环境。Core 可安装在 Linux
或 macOS；Live OSWorld 的真实 GUI 运行需要 Linux x86-64、Docker 与 KVM。

先从公开仓库构建唯一 wheel：

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

该 profile 会额外验证 `openai`、`Pillow` 和 `requests`，但不会下载 VM、
连接模型服务、启动 Docker 或执行任务。安装通过后，再按照
[`../deployment/osworld-linux.md`](../deployment/osworld-linux.md) 准备固定
镜像与任务资产，并运行 `paraguibench doctor`。

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
`PARAGUIBENCH_MODEL_BASE_URL`。验证器只检查文件类型、所有权、权限和位置，
不会打开文件。使用 secret manager 时，将同名变量直接注入
`paraguibench` 进程，不创建 checkout 内配置，也不要通过 `env`、
`printenv` 或 shell tracing 检查值。

贡献者可用同一个 wheel 安装 `live,dev` extra，并运行全部公开门禁：

```bash
python3 -m venv .venv-dev
.venv-dev/bin/python -m pip install \
  "paraguibench[live,dev] @ file://${WHEEL_PATH}"
.venv-dev/bin/python -m pytest
.venv-dev/bin/python scripts/benchmark/validate_release.py --repo-root .
.venv-dev/bin/python scripts/benchmark/validate_runtime_support.py --repo-root .
.venv-dev/bin/python scripts/security/scan_repository.py --root .
```

公共 CI 会在 Python 3.11、3.12 和 3.13 上重复 wheel-first 安装、CLI、
测试与三个 validator，但不接收 API key，也不执行真实 GUI E2E。详细依赖
边界见 [`dependency-tree.md`](dependency-tree.md)，稳定失败标识与安全排查方式
见 [`troubleshooting.md`](troubleshooting.md)。
