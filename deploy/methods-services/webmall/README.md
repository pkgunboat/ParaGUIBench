# WebMall 商城服务

WebMall pipeline 需要 4 个独立的 WordPress + WooCommerce 商城后端实例，
模拟不同店铺，对应端口 `9081-9084`。

## 架构

每个店铺由以下服务组成：

| 服务 | 镜像 | 说明 |
|------|------|------|
| `webmall-frontend` | nginx | 前端入口页 |
| `elasticsearch` | elasticsearch:8.10.2 | 商品搜索引擎（4 店铺共享） |
| `mariadb-shopN` | bitnami/mariadb | 店铺 N 的数据库 |
| `wordpress-shopN` | bitnami/wordpress | 店铺 N 的 WooCommerce 前端 |

总计 12 个容器：1 nginx (webmall-frontend) + 1 Elasticsearch + 4 MariaDB + 4 WordPress + 2 来自 OnlyOffice（documentserver + onlyoffice-share）。

## 首次部署

### 1. 初始化数据

```bash
# 从公开备份恢复 4 个店铺的 MariaDB + WordPress 数据
# 备份文件会自动从 data.dws.informatik.uni-mannheim.de 下载（约 3.5 GB）
bash scripts/deployment/setup_webmall_services.sh              # 自动检测 IP
bash scripts/deployment/setup_webmall_services.sh <本机IP>    # 或指定 IP
```

该脚本会：
- 下载备份 tarball 到 `deploy/methods-services/webmall/backup/`（gitignored）
- 创建并恢复 8 个 Docker 卷（4×mariadb + 4×wordpress）
- 注入 `wp-config.php`（含正确端口号）
- 启动容器并执行 URL 修复

### 2. 启动服务

```bash
bash scripts/deployment/start_bench_services.sh
```

### 3. 验证

```bash
docker compose -f deploy/methods-services/docker-compose.yaml ps
curl http://127.0.0.1:9081/   # Shop 1
curl http://127.0.0.1:9082/   # Shop 2
curl http://127.0.0.1:9083/   # Shop 3
curl http://127.0.0.1:9084/   # Shop 4
```

### 4. 停止服务

```bash
bash scripts/deployment/stop_bench_services.sh
```

数据保留在 Docker 卷中，下次 `start_bench_services.sh` 直接恢复。

## 商城商品数据

商品数据来自公开备份 tarball（Uni Mannheim WebMall 上游，
`data.dws.informatik.uni-mannheim.de/webmall/backup`）恢复出的
MariaDB + WP 卷数据。离线部署可把 tarball 预放到
`resources/webmall_assets/backup/`（见 `configs/deploy.example.yaml` 的
`resources.root`）。

## 任务 JSON 里的 URL

任务 JSON 文件（`src/parallel_benchmark/tasks/` 下的 OnlineShopping 任务，
运行时经 `src/extra_docker_env/tasks/` 软链读取）的 `answer` 字段包含形如
`http://<ORIGIN_HOST>:9082/product/...` 的 URL，这是打包者原始环境的 host。
部署到自有环境后，运行：

```bash
# 自动从 configs/deploy.yaml.services.webmall.host_ip 读取目标 host
# （该字段支持 "auto"，将自动探测当前设备默认出口 IP）
python scripts/deployment/rewrite_webmall_task_urls.py

# 或显式指定
python scripts/deployment/rewrite_webmall_task_urls.py \
    --from http://<ORIGIN_HOST> --to http://<YOUR_HOST>
```

注意：重写会改动 parity 锁定的任务 JSON，属部署期有意偏离（详见脚本
docstring 与 docs/deployment/methods-services.md）。

## 文件结构

```
deploy/methods-services/webmall/
├── README.md              # 本文件
├── index.html             # WebMall 前端入口页
├── backup/                # 备份 tarball（setup 脚本自动下载，gitignored）
├── wp-config/             # WordPress 配置模板
│   ├── shop_1.php
│   ├── shop_2.php
│   ├── shop_3.php
│   └── shop_4.php
├── fix_urls.sh            # WordPress URL 修复脚本（容器内使用）
└── fix_urls_deploy.sh     # 远程部署 URL 修复脚本
```
