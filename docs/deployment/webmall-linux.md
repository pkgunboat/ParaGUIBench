# WebMall Linux 部署与首个 Checkout 任务

本页记录 WebMall 四店 Checkout/EndToEnd 运行链的可复现部署边界。当前
已在本地接入固定 environment manifest、logical URL 物化、WP-CLI 订单证据、
baseline/final 差分、跨进程租约、CLI `doctor/run` 与 RunStore v2 版本向量。
这些本地契约测试不等于实机验证；在同一版本向量的真实 Attempt 通过前，
runtime-support manifest 仍应保持 `blocked` 和
`versioned_live_validation_not_completed`。

当前原生运行链覆盖 67 个 URL-multiset 任务、8 个 `Checkout`
任务与 8 个 `EndToEnd` 任务，合计 83 个 WebMall 任务。URL 任务
只需固定 manifest 和四店 origin，不读取 WP-CLI target 或租约凭据；
Checkout/EndToEnd 才装配特权订单证据与分布式租约。其他 WebMall
任务若仍声明 legacy evaluator，CLI 会在启动 VM、读取凭据或创建
RunStore 前失败关闭。

## 前置条件

先按 [OSWorld Linux 部署指南](osworld-linux.md) 完成 `live` extra、固定 qcow2、
Docker/KVM、三个 loopback 端口、模型凭据和仓库外状态目录准备。WebMall 还
需要：

- 四个互不重复的 HTTP(S) store origin，分别对应 `store-1` 至 `store-4`；
- 四个互不重复、可由 `wp --ssh=<target>` 访问的 WordPress/WooCommerce
  reader target；
- host 上可执行的 `wp` CLI，以及 reader target 所需的 Docker 或 SSH 认证；
- 与 `environments/webmall/environment-manifest.json` 一致的四店软件、支付
  method ID 与 HPOS 订单存储；
- 一个持久化 distributed-lease v1 coordinator。

manifest 锁定镜像 digest 和软件身份，但 ParaGUIBench 不会自动创建、重置或
填充 WordPress 商店。当前 reset 策略是 Attempt 开始前捕获订单 baseline，评价时
只考察新增订单。因此所有共享同一四店后端的 runner 都必须使用同一
coordinator namespace，不能以本地文件锁代替。

## 部署变量与隐私边界

`.env.example` 只记录变量名，程序不会自动加载。真实值必须来自仓库外、
当前用户所有且权限为 `0600` 的普通文件，或由部署平台的 secret/config
manager 直接注入进程。

| 逻辑身份 | Browser origin 变量 | WP-CLI reader target 变量 |
|---|---|---|
| `store-1` | `PARAGUIBENCH_WEBMALL_STORE_1_ORIGIN` | `PARAGUIBENCH_WEBMALL_STORE_1_READER_TARGET` |
| `store-2` | `PARAGUIBENCH_WEBMALL_STORE_2_ORIGIN` | `PARAGUIBENCH_WEBMALL_STORE_2_READER_TARGET` |
| `store-3` | `PARAGUIBENCH_WEBMALL_STORE_3_ORIGIN` | `PARAGUIBENCH_WEBMALL_STORE_3_READER_TARGET` |
| `store-4` | `PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN` | `PARAGUIBENCH_WEBMALL_STORE_4_READER_TARGET` |

租约变量分为两个进程边界：

| 进程 | 变量 | 用途 |
|---|---|---|
| benchmark runner | `PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL` | coordinator 基地址；远程必须为 HTTPS，只有 loopback 可使用 HTTP |
| benchmark runner | `PARAGUIBENCH_WEBMALL_LEASE_TOKEN` | 客户端 Bearer credential |
| coordinator | `PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN` | 服务端期望的 Bearer credential，必须是 32–4089 个无填充 base64url 字符 |

服务端的 `PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN` 与客户端的
`PARAGUIBENCH_WEBMALL_LEASE_TOKEN` 必须由 secret manager 提供相同的秘密值，但不应
把两个进程的整份环境互相复制。Origin 和 reader target 不是认证凭据，但可能
暴露内部拓扑，因此同样不得进入 Git、公开 issue 或默认日志。不要使用
`env`、`printenv`、shell tracing 或带值的 CLI 参数检查注入结果。

Docker 类 reader target 应在 runner 进程中设置：

```bash
export WP_CLI_DOCKER_NO_TTY=1
```

该变量会经过 WP-CLI 子进程环境 allowlist，避免 `eval-file -` 的 stdin 脚本
被 Docker TTY 修改。WP-CLI reader v2 以 `shell=False` 运行仓库内按 SHA-256
锁定的 PHP 脚本；identity 模式只枚举订单 ID，details 模式只读取
Attempt 内新增的精确 ID 闭集，两种模式共用 4 MiB stdout 硬上限。
子进程不会收到模型凭据或租约 token。

## 启动租约 coordinator

在 coordinator host 上使用独立的仓库外状态目录和 secret 文件。下列命令不
包含真实 credential；使用编辑器向文件写入
`export PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN=...`，不要在 shell 命令行中输入秘密值。

```bash
export PARAGUIBENCH_WEBMALL_LEASE_STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/paraguibench/webmall-lease"
export PARAGUIBENCH_WEBMALL_LEASE_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/paraguibench/webmall-lease.env"

install -d -m 700 \
  "$PARAGUIBENCH_WEBMALL_LEASE_STATE_ROOT" \
  "$(dirname "$PARAGUIBENCH_WEBMALL_LEASE_SECRET_FILE")"
install -m 600 /dev/null "$PARAGUIBENCH_WEBMALL_LEASE_SECRET_FILE"
"${EDITOR:-vi}" "$PARAGUIBENCH_WEBMALL_LEASE_SECRET_FILE"

set +x
test -O "$PARAGUIBENCH_WEBMALL_LEASE_SECRET_FILE"
test "$(stat -c '%a' "$PARAGUIBENCH_WEBMALL_LEASE_SECRET_FILE")" = 600
. "$PARAGUIBENCH_WEBMALL_LEASE_SECRET_FILE"

.venv-live/bin/python -m paraguibench.integrations.webmall.lease_coordinator \
  --database "$PARAGUIBENCH_WEBMALL_LEASE_STATE_ROOT/leases.sqlite3" \
  --host 127.0.0.1 \
  --port 8765
```

运行器与 coordinator 在同一 host 时，客户端 URL 可设为
`http://127.0.0.1:8765`。跨主机时，Python 服务仍只监听 loopback，由可信
TLS 反向代理对外提供 HTTPS；可复用仓库中的
[`deploy/webmall-lease`](../../deploy/webmall-lease/README.md) systemd 与 nginx 样例。SQLite
文件保存单调 fencing token 高水位，服务重启时不得删除、置空或切换到
另一份独立数据库。

## 运行 doctor 和任务

在 runner host 上创建另一份仓库外 `0600` 配置/凭据文件，按上表定义
四个 origin、四个 reader target、`PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL`、
`PARAGUIBENCH_WEBMALL_LEASE_TOKEN`、`WP_CLI_DOCKER_NO_TTY=1` 与模型凭据，然后在
`set +x` 后 source 该文件。下列命令复用 OSWorld 指南已创建的路径和三个
loopback 端口：

```bash
export PARAGUIBENCH_WEBMALL_TASK_ID=Operation-OnlineShopping-Checkout-001

.venv-live/bin/paraguibench doctor \
  --repo-root . \
  --task-id "$PARAGUIBENCH_WEBMALL_TASK_ID" \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT"
```

URL-multiset 任务的 WebMall doctor 只列出 5 项：`webmall_manifest` 和
四个 `webmall_store_<n>_origin`。Checkout/EndToEnd 任务会列出 12 项：
在上述 5 项之外，再加四个 `webmall_store_<n>_reader_target`、
`webmall_wp_cli`、`webmall_lease_endpoint` 和 `webmall_lease_credential`。
这些检查验证固定 manifest、绑定结构和本机依赖，不联系四店、
不获取租约、不启动 VM，也不调用模型。因此 `doctor=PASS`
只是执行前门禁，不是实机成功证据。

当 OSWorld 和 WebMall 全部检查都为 `PASS` 且 coordinator 正在运行时，使用
确定的模型 ID 启动 GUI-only Attempt：

```bash
export PARAGUIBENCH_MODEL_ID="<pinned OpenAI-compatible model identifier>"

.venv-live/bin/paraguibench run \
  --repo-root . \
  --task-id "$PARAGUIBENCH_WEBMALL_TASK_ID" \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --agent-system gui-only \
  --worker qwen \
  --model "$PARAGUIBENCH_MODEL_ID" \
  --qwen-tool-protocol native
```

`run` 会先启动 Attempt-scoped 浏览器环境；进入 task prepare 后获取四店
全局租约，完成底层环境准备并在 Agent 产生副作用前捕获 baseline。评价前
重新确认 fencing 身份、捕获 final 订单并只评价 Attempt 新增订单，最后释放
租约。WP-CLI stdout/stderr、订单 ID、billing 资料、origin、reader target、租约
credential 和模型原始响应均不属于默认 RunStore 日志契约。公开排障时只分享
`PASS/FAIL <check-id>`、枚举失败阶段、版本向量与退出码，不上传原始
RunStore 目录、订单证据或环境 dump。
