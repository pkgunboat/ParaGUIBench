# OnlyOffice 单实例部署

本目录只编排 OnlyOffice DocumentServer 与 ParaGUIBench share service。它不包含
WebMall，也不实现多实例调度。第一版只承诺单实例实验室部署。

只有以下 4 个任务使用本服务：

- `Operation-FileOperate-SearchAndWrite-002`
- `Operation-FileOperate-SearchAndWrite-004`
- `Operation-FileOperate-SearchAndWrite-006`
- `Operation-FileOperate-SearchAndWrite-008`

其余 SearchAndWrite 任务继续使用 OSWorld / LibreOffice。单元测试通过不等于真实
服务可用，也不等于这些任务已经 `live_validated`。

## 端口合同

| 服务 | 容器端口 | 默认宿主端口 | 环境变量 |
|---|---:|---:|---|
| DocumentServer | 80 | 8080 | `PARAGUIBENCH_ONLYOFFICE_DOC_PORT` |
| share service | 5050 | 5050 | `PARAGUIBENCH_ONLYOFFICE_SHARE_PORT` |

浏览器与 guest Chrome 应访问宿主上的这两个端口。DocumentServer 回源拉文件时走
Docker 网络内的 `http://paraguibench-onlyoffice-share:5050`，不要改成
`127.0.0.1`。

## 外部状态目录

运行状态不得写入 Git checkout。先导出仓库外的状态根：

```bash
export PARAGUIBENCH_ONLYOFFICE_STATE_ROOT="$HOME/.local/share/paraguibench/onlyoffice"
export PARAGUIBENCH_ONLYOFFICE_HOST_IP="127.0.0.1"
mkdir -p \
  "$PARAGUIBENCH_ONLYOFFICE_STATE_ROOT/share" \
  "$PARAGUIBENCH_ONLYOFFICE_STATE_ROOT/documentserver/data" \
  "$PARAGUIBENCH_ONLYOFFICE_STATE_ROOT/documentserver/logs"
```

`HOST_IP` 是浏览器看到的 DocumentServer 宿主地址。本机实验室用 `127.0.0.1`；
以后 OSWorld guest 需要改成 guest 可达的宿主地址，但不能把真实内网地址写进仓库。

## start

官方身份使用已验证的 DocumentServer digest：

`onlyoffice/documentserver@sha256:b9e3c35eab182d3de822a53b109b0f27070f6eacea3b1388b9c50d1182f638f2`

```bash
docker compose -f deploy/onlyoffice/compose.yaml config
docker compose -f deploy/onlyoffice/compose.yaml up -d --build
```

本机若没有该 digest，不要让 compose 自动拉取。可先只启动 share service，或在得到
授权后显式拉取 pinned 镜像。本地存在其他 tag 时，可用
`ONLYOFFICE_DOCUMENTSERVER_IMAGE` 做一次性覆盖，但那不是正式身份。

share 镜像由 `Dockerfile.share` 构建，启动时不再 `pip install`。Gunicorn 固定
1 worker、16 threads，并以 `share_server:create_app()` 工厂启动。

## health

```bash
curl -sf "http://127.0.0.1:${PARAGUIBENCH_ONLYOFFICE_SHARE_PORT:-5050}/healthz"
curl -sf "http://127.0.0.1:${PARAGUIBENCH_ONLYOFFICE_DOC_PORT:-8080}/healthcheck"
```

两个接口都成功只说明单实例进程起来了，不构成任务 live 验证。

## stop

```bash
docker compose -f deploy/onlyoffice/compose.yaml stop
```

## reset

停止服务后删除外部状态根，再按 start 重新创建空目录：

```bash
docker compose -f deploy/onlyoffice/compose.yaml down
rm -rf "$PARAGUIBENCH_ONLYOFFICE_STATE_ROOT"
```

reset 会丢掉共享文档、协作 key 和 DocumentServer 缓存。不要删除其他项目的 Docker
网络或卷。

## 不在本目录做的事

- 不提交 `.env`、JWT secret、`shared_links.json`、`document_keys.json` 或文档字节
- 不启用反向代理或企业认证
- 不调度第二、第三个 DocumentServer 实例
