# OnlyOffice 文档共享系统（U盘版）Linux 安装与使用指南

本文档面向：你把整个项目目录（例如本仓库 `webmall/onlyoffice`）放在 **U盘** 中，插到一台 **Linux** 设备上，希望在该设备上启动 OnlyOffice DocumentServer + Flask 文档共享服务，并通过浏览器进行上传/编辑/共享/协作。

> 说明：本文以当前仓库内的实现为准：
> - 文档共享服务：`onlyoffice/document_sharing_server.py`
> - 文档管理 CLI：`onlyoffice/manage_documents.py`
> - OnlyOffice DocumentServer：`onlyoffice/Docker-DocumentServer/`（Docker Compose 部署）

---

## 1. 系统要求与依赖

### 1.1 Linux 系统要求

- 建议：Ubuntu 20.04+/Debian 11+/CentOS 7+（能正常运行 Docker 即可）
- CPU/内存：OnlyOffice DocumentServer 较吃资源，建议至少 **2C4G**（更大更好）
- 磁盘：至少预留 5GB+（容器镜像与文档存储）

### 1.2 必备软件

#### A. Docker & Docker Compose

- Docker Engine
- Docker Compose（推荐 v2：`docker compose`，但本项目脚本使用 `docker-compose` 也可）

快速验证：

```bash
docker -v
docker-compose -v || docker compose version
```

#### B. Python 3

- Python 3.10+（推荐）

验证：

```bash
python3 -V
```

#### C. Python 依赖

文档共享服务依赖：

```bash
pip3 install --user flask requests
```

> 说明：`启动文档共享服务.sh` 会自动检查并尝试安装依赖。

---

## 2. 从 U 盘运行的注意事项（强烈建议先看）

### 2.1 建议将 onlyoffice 目录复制到本地磁盘运行

虽然可以直接在 U 盘上运行，但在 Linux 上可能遇到：

- U 盘文件系统权限/挂载参数导致可执行脚本不能运行
- 写入 `shared_documents/`、`shared_links.json`、`document_keys.json` 失败
- I/O 性能差，协作保存回调写文件慢

因此建议：把 `onlyoffice/` 复制到本机目录运行（例如 `~/onlyoffice`），而不是直接在 `/media/xxx/U盘/...` 运行。

示例：

```bash
cp -r /media/$USER/<你的U盘目录>/webmall/onlyoffice ~/onlyoffice
cd ~/onlyoffice
```

若你坚持直接在 U 盘运行，请确保 U 盘挂载为可写、且脚本可执行：

```bash
chmod +x start_document_server.sh "启动文档共享服务.sh"
```

### 2.2 端口占用说明

- OnlyOffice DocumentServer 默认映射：宿主机 `80 -> 容器 80`，`443 -> 容器 443`
- 文档共享 Flask 服务默认尝试：`5000`，若占用则自动切到 `5001`

Linux 上通常 `5000` 不会被占用（macOS 才常见 AirPlay 占用 5000）。

---

## 3. 安装与启动（推荐流程）

本系统需要 **先启动 OnlyOffice DocumentServer**，再启动 **文档共享服务（Flask）**。

### 3.1 启动 OnlyOffice DocumentServer（Docker）

在 `onlyoffice/` 目录下运行：

```bash
./start_document_server.sh
```

脚本会做：

- 检测 Docker 是否运行
- 进入 `Docker-DocumentServer/` 并执行 `docker-compose up -d`

验证 OnlyOffice 是否正常：

```bash
curl -s http://localhost/info/info.json | head
```

> 若你要在其它机器访问（非本机浏览器），请确保访问的是 Linux 机器的 IP，例如 `http://<LINUX_IP>/info/info.json`。

### 3.2 配置 HOST_IP（用于局域网/远程访问）

`document_sharing_server.py` 会根据环境变量 `HOST_IP`（优先）设置对外地址。

#### A. 仅本机访问

可以不设置 `HOST_IP`。

#### B. 局域网其它设备访问（推荐设置）

1) 先查 Linux 机器 IP：

```bash
ip -4 addr | grep inet
```

2) 例如 IP 是 `192.168.1.100`，则在启动 Flask 前设置：

```bash
export HOST_IP=192.168.1.100
```

> 说明：这会影响：
> - 页面引用的 OnlyOffice API 地址（`http://$HOST_IP/web-apps/.../api.js`）
> - OnlyOffice 容器回调的 callbackUrl
> - OnlyOffice 容器拉取文档文件的 URL

### 3.3 启动文档共享服务（Flask）

在 `onlyoffice/` 目录下运行：

```bash
./启动文档共享服务.sh
```

或直接：

```bash
python3 document_sharing_server.py
```

启动后会打印访问地址，通常是：

- 文档管理界面：`http://localhost:5000`（或 `5001`）

若你设置了 `HOST_IP` 并希望局域网访问，则用：

- `http://192.168.1.100:5000`（示例）

---

## 4. 使用方法（网页端）

打开文档管理界面后：

1) **上传文档**：选择文件 → 上传
2) **编辑**：点击“编辑”按钮，会嵌入 OnlyOffice 编辑器
3) **共享**：点击“共享”生成链接，把链接发给别人协作
4) **删除**：点击“删除”删除文档，并清理共享链接与协作 key（见后端实现）

文档存储位置：

```
onlyoffice/shared_documents/
```

共享链接存储：

```
onlyoffice/shared_links.json
```

协作 key 存储：

```
onlyoffice/document_keys.json
```

---

## 5. 使用方法（命令行管理）

命令行工具：`onlyoffice/manage_documents.py`

### 5.1 列出文档

```bash
python3 manage_documents.py list
```

### 5.2 查看文档详情

```bash
python3 manage_documents.py info <doc_id>
```

### 5.3 删除文档（并清理共享链接/协作 key）

```bash
python3 manage_documents.py delete <doc_id>
```

### 5.4 清理孤立数据

当你手动删了文件，或异常关机导致 JSON 有残留项，可以执行：

```bash
python3 manage_documents.py cleanup
```

---

## 6. 常见问题排查（含“文档一直加载中”）

OnlyOffice 编辑器卡在“文档加载中”，本质上通常是：

1) 浏览器端无法加载 `api.js`
2) OnlyOffice DocumentServer 无法访问你的文档 URL（`document.url`）
3) OnlyOffice DocumentServer 无法回调保存地址（`callbackUrl`）
4) JWT 校验不匹配（若 DocumentServer 开启了 JWT）
5) 网络/防火墙/反向代理导致跨机器访问不通

下面按优先级给出排查步骤。

### 6.1 检查 OnlyOffice 是否存活

在 Linux 主机上执行：

```bash
curl -s http://localhost/info/info.json
```

若失败：

```bash
cd onlyoffice/Docker-DocumentServer
docker-compose ps
docker-compose logs -f --tail=200
```

### 6.2 检查浏览器是否能加载 api.js

在浏览器打开：

```
http://<HOST_IP>/web-apps/apps/api/documents/api.js
```

若 404/连接失败：

- 说明 DocumentServer 没启动，或 80 端口没对外开放
- 检查安全组/防火墙是否允许 80 端口

### 6.3 检查 OnlyOffice 容器是否能访问文档共享服务（最常见）

你的 Flask 服务可能跑在宿主机 `5000/5001`，OnlyOffice 容器需要能访问到：

```
http://<HOST_IP>:5000/api/document/<doc_id>/file
```

建议在宿主机先自测：

```bash
curl -I "http://127.0.0.1:5000/api/documents"
```

再从容器内测试（关键）：

```bash
docker exec -it onlyoffice-documentserver bash -lc \
  "apt-get update >/dev/null 2>&1 || true; \
   apt-get install -y curl >/dev/null 2>&1 || true; \
   curl -I http://host.docker.internal:5000/api/documents || true"
```

> 重要：
> - 在 Docker Desktop（macOS/Windows）通常用 `host.docker.internal`。
> - 在 Linux 上，`host.docker.internal` **可能不存在**（取决于 Docker 版本/配置）。
>   因此本项目在 Linux 上建议使用 **真实 IP**（`HOST_IP=192.168...`），让容器走局域网访问宿主机。

若容器访问不到宿主机：

- 确保 Flask 监听 `0.0.0.0`（本项目是 `app.run(host='0.0.0.0', ...)`）
- 确保 Linux 防火墙允许 `5000/5001`：
  - Ubuntu(UFW)：`sudo ufw allow 5000/tcp`（或 5001）
  - firewalld：`sudo firewall-cmd --add-port=5000/tcp --permanent && sudo firewall-cmd --reload`

### 6.4 检查 callbackUrl 是否可达（保存相关）

本项目已将 callbackUrl 设置为 **Docker 可访问的地址**（`docServerUrl`）：

```
http://<HOST_IP>:5000/api/document/<doc_id>/callback
```

如果 callbackUrl 不可达，可能出现保存失败/加载异常。

你可以在 Flask 控制台观察是否有打印：

```
[Callback] 文档: ..., 状态: ...
```

### 6.5 JWT 相关

当前 `Docker-DocumentServer/docker-compose.yml` 里默认 **未开启** JWT（`JWT_ENABLED=true` 被注释）。

但 `document_sharing_server.py` 内部仍会生成 token（`config.token`），通常不会导致问题。

如果你手动开启了 JWT（在 compose 里取消注释），必须保证：

- DocumentServer 与 Flask 里 `JWT_SECRET` 完全一致
- 必要时配置 `JWT_IN_BODY=true`、header 等

### 6.6 浏览器控制台/网络面板定位

打开开发者工具（F12）：

- Console 看是否报错（跨域、401、404、连接失败）
- Network 重点看：
  - `api.js` 是否加载成功
  - `/api/document/<id>/token` 是否返回 200
  - 文档 `document.url` 是否能被 DocumentServer 拉取

---

## 7. 数据备份与迁移（U盘场景特别实用）

你真正需要迁移/备份的主要是三个地方：

```text
onlyoffice/shared_documents/     # 所有文档文件
onlyoffice/shared_links.json     # 共享链接
onlyoffice/document_keys.json    # 协作 key
```

建议打包：

```bash
tar -czf onlyoffice_data_backup.tar.gz shared_documents shared_links.json document_keys.json
```

---

## 8. 常用运维命令

### 8.1 查看 OnlyOffice 容器状态/日志

```bash
cd onlyoffice/Docker-DocumentServer
docker-compose ps
docker-compose logs -f --tail=200
```

### 8.2 停止 OnlyOffice

```bash
cd onlyoffice/Docker-DocumentServer
docker-compose stop
```

### 8.3 重启 OnlyOffice

```bash
cd onlyoffice/Docker-DocumentServer
docker-compose restart
```

---

## 9. 依赖关系树（简版）

```text
浏览器
  ├─ 访问文档管理页（Flask）: http://HOST_IP:5000/
  │    ├─ 调用 Flask API: /api/documents /api/upload /api/document/<id>/token ...
  │    └─ 加载 OnlyOffice 前端 SDK: http://HOST_IP/web-apps/apps/api/documents/api.js
  │
  └─ OnlyOffice DocEditor (运行在浏览器 JS)
       └─ 与 OnlyOffice DocumentServer(http://HOST_IP:80) 通信
            ├─ DocumentServer 从 Flask 拉取文档文件: http://HOST_IP:5000/api/document/<id>/file
            └─ DocumentServer 回调 Flask 保存: http://HOST_IP:5000/api/document/<id>/callback

Docker Compose (onlyoffice/Docker-DocumentServer)
  ├─ onlyoffice-documentserver (80/443)
  ├─ onlyoffice-postgresql
  └─ onlyoffice-rabbitmq
```

