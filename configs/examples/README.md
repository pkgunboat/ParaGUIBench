# 公开配置示例约定

本目录只保存可以安全提交和复制的配置示例，用于说明字段和依赖关系，不承担
secret store 职责。`.env.example` 位于仓库根目录，仅列出变量名；CLI 和
Python package **不会自动加载**它。

配置按以下边界分层：

1. 仓库内示例只保存非敏感默认值、空值或环境变量引用。
2. 机器相关但非敏感的覆盖配置保存在未跟踪的本地文件中。
3. API key、token、密码、cookie、认证头和带签名 URL 只能由部署平台 secret
   manager，或 checkout 外部且权限为 `0600` 的所有者文件注入。
4. 运行日志、任务资产缓存和 VM 数据也位于 checkout 外，分别使用 XDG state、
   cache 和 data 目录。

当前 live CLI 默认引用两个变量名：

- `PARAGUIBENCH_MODEL_API_KEY`
- `PARAGUIBENCH_MODEL_BASE_URL`

这两个变量的值不得写入 README、配置示例、命令行参数、shell history 或
RunStore。CLI 的 `--api-key-env` 与 `--base-url-env` 只允许改变引用的变量名，
不接收实际值。模型 ID、端口、资源上限、缓存根和 RunStore 根属于非敏感部署
配置，但仍不应硬编码开发者路径或内部网络信息。

建议按以下方式创建外部 secret 文件：

```bash
export PARAGUIBENCH_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/paraguibench/secrets.env"
install -d -m 700 "$(dirname "$PARAGUIBENCH_SECRET_FILE")"
install -m 600 /dev/null "$PARAGUIBENCH_SECRET_FILE"
"${EDITOR:-vi}" "$PARAGUIBENCH_SECRET_FILE"

set +x
test -O "$PARAGUIBENCH_SECRET_FILE"
test "$(stat -c '%a' "$PARAGUIBENCH_SECRET_FILE")" = 600
. "$PARAGUIBENCH_SECRET_FILE"
```

文件中需要定义并 `export` 上述两个变量，但本仓库不提供或记录示例值。使用
secret manager 时，不需要创建该文件；只需保证启动 `paraguibench` 的进程能
读取对应环境变量。

新增公开配置时必须满足：

- 从 schema 或空模板开始创建，不复制真实部署配置后再手工删减。
- 不包含固定私网 IP、内部域名、主机名、开发者绝对路径、用户名或真实资源 ID。
- 不使用看似可用的假 token；敏感字段保持空值或只使用变量名引用。
- 配置加载器和日志只暴露 Credential Reference 与是否配置，不暴露值、长度、
  摘要或前后缀。
- 本地私有配置放入已忽略的 `configs/private/`；运行目录、下载缓存和 secret
  文件也必须位于 checkout 外。
- 提交前运行 `python scripts/security/scan_repository.py --root .`，并额外做
  私网地址、绝对路径和凭据文件名的定向扫描。

完整源码部署流程见
[`docs/deployment/osworld-linux.md`](../../docs/deployment/osworld-linux.md)。
