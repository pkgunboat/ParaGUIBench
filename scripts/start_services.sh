#!/usr/bin/env bash
# 启动 benchmark 所需的外部服务（OnlyOffice + WebMall）。
# 所有端口和 host 从 configs/deploy.yaml 读取，通过 python 解析后
# 导出为环境变量，再交给 docker compose。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 1) 把 deploy.yaml 的关键字段转成环境变量
eval "$(python3 - <<'PY'
import os, sys, shlex
sys.path.insert(0, 'src')
from config_loader import DeployConfig
d = DeployConfig()
print(f"export ONLYOFFICE_HOST_IP={shlex.quote(d.onlyoffice_host)}")
print(f"export ONLYOFFICE_FLASK_PORT={d.onlyoffice_flask_port}")
# doc_server_port 在 deploy.yaml 的 services.onlyoffice.doc_server_port
raw = d.raw()
print(f"export ONLYOFFICE_DOC_PORT={raw.get('services', {}).get('onlyoffice', {}).get('doc_server_port', 8080)}")
ports = d.webmall_ports
for i, p in enumerate(ports[:4], start=1):
    print(f"export WEBMALL_PORT_{i}={p}")
# WebMall 前端入口端口；保持与 docker-compose.yaml 默认值一致。
webmall_cfg = raw.get('services', {}).get('webmall', {})
frontend_port = webmall_cfg.get('frontend_port', 8090)
print(f"export WEBMALL_FRONTEND_PORT={frontend_port}")
PY
)"

echo "[start] OnlyOffice host: $ONLYOFFICE_HOST_IP  doc-port: $ONLYOFFICE_DOC_PORT  flask-port: $ONLYOFFICE_FLASK_PORT"
echo "[start] WebMall ports:   ${WEBMALL_PORT_1} ${WEBMALL_PORT_2} ${WEBMALL_PORT_3} ${WEBMALL_PORT_4}"

# 2) 启动
docker compose -f docker/docker-compose.yaml up -d

echo "[start] docker compose 完成；用 'docker compose -f docker/docker-compose.yaml ps' 查看状态"
