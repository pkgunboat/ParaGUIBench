# OnlyOffice 服务

SearchWrite pipeline 需要一个可写的 OnlyOffice DocumentServer + Flask 共享层。

## 组件

| 文件 | 作用 |
|---|---|
| `document_sharing_server.py` | Flask 服务，管理任务级共享链接 |
| `manage_documents.py`        | 文档上传/清理辅助工具 |
| `onlyoffice_benchmark_utils.py` | 被 SearchWrite pipeline 引用的 Python utility |
| `start_document_server.sh`   | 直接跑 Flask（不走 docker） |

> `onlyoffice_benchmark_utils.py` 在打包时同时复制到 `src/stages/`，
> 作为 Python module 被 pipeline 导入。修改时保持两处同步，或用符号链接。

## 启动方式

推荐用根目录 `scripts/start_services.sh`（内部走 `docker-compose`），它会：

1. 拉取 `onlyoffice/documentserver:8.1` 镜像并启动 `bench-onlyoffice` 容器
2. 启一个 `python:3.11-slim` 容器运行 `document_sharing_server.py`

如果你想在宿主机原生跑 Flask：

```bash
cd docker/onlyoffice
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

所有端口由 `configs/deploy.yaml.services.onlyoffice` 驱动：
- `flask_port` → Flask 共享服务
- `doc_server_port` → OnlyOffice DocumentServer
