# OnlyOffice 服务（methods 验证栈）

SearchWrite pipeline（methods_runner searchwrite）需要一个可写的 OnlyOffice
DocumentServer + Flask 共享层。本目录是原方法验证时使用的服务栈，与
`deploy/onlyoffice/`（公开 CLI 的单实例重写版）相互独立，见
`docs/deployment/methods-services.md` 的对比说明。

## 组件

| 文件 | 作用 |
|---|---|
| `document_sharing_server.py` | Flask 服务，管理任务级共享链接 |
| `manage_documents.py`        | 文档上传/清理辅助工具 |
| `start_document_server.sh`   | 直接跑 Flask（不走 docker） |

> pipeline 引用的 `onlyoffice_benchmark_utils.py` 已位于
> `src/stages/`（迁移基线的一部分）；修改本目录服务代码后需
> `docker restart bench-onlyoffice-share` 让容器重新加载（容器以绑定挂载
> 方式运行这里的源码）。

## 启动方式

推荐用根目录 `scripts/deployment/start_bench_services.sh`（内部走
`docker compose -f deploy/methods-services/docker-compose.yaml`），它会：

1. 拉取 `onlyoffice/documentserver:8.1` 镜像并启动 `bench-onlyoffice` 容器
2. 启一个 `python:3.11-slim` 容器运行 `document_sharing_server.py`

如果你想在宿主机原生跑 Flask：

```bash
cd deploy/methods-services/onlyoffice
pip install flask requests python-docx openpyxl python-pptx
HOST_IP=127.0.0.1 DOC_SERVER_PORT=8080 FLASK_PORT=5050 \
    python document_sharing_server.py
```

## 验证

```bash
curl http://127.0.0.1:5050/healthz    # Flask
curl http://127.0.0.1:8080/           # DocumentServer 首页
```

## 端口

所有端口由 `configs/deploy.yaml.services.onlyoffice` 驱动（无该文件时用
`config_loader` 默认值 5050/8080）：
- `flask_port` → Flask 共享服务
- `doc_server_port` → OnlyOffice DocumentServer
