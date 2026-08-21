#!/usr/bin/env bash
# 启动 methods_runner 验证栈的外部服务（OnlyOffice + WebMall）。
# 所有端口和 host 从 configs/deploy.yaml 读取（缺省时用 config_loader 默认值：
# OnlyOffice 8080/5050、WebMall 9081-9084），通过 python 解析后导出环境变量，
# 再交给 docker compose。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="deploy/methods-services/docker-compose.yaml"

# 1) 把 deploy.yaml 的关键字段转成环境变量
eval "$(python3 - <<'PY'
import os, sys, shlex
sys.path.insert(0, 'src')
from config_loader import DeployConfig
d = DeployConfig()
print(f"export ONLYOFFICE_HOST_IP={shlex.quote(d.onlyoffice_host)}")
# OnlyOffice 实例列表（多实例部署）：实例 1 走 legacy 变量，
# 实例 2/3 走 _2/_3 后缀变量并启用 compose profile
instances = d.onlyoffice_instances
print(f"export ONLYOFFICE_FLASK_PORT={instances[0]['flask_port']}")
print(f"export ONLYOFFICE_DOC_PORT={instances[0]['doc_server_port']}")
for i, inst in enumerate(instances[1:3], start=2):
    print(f"export ONLYOFFICE_FLASK_PORT_{i}={inst['flask_port']}")
    print(f"export ONLYOFFICE_DOC_PORT_{i}={inst['doc_server_port']}")
print(f"export ONLYOFFICE_INSTANCE_COUNT={min(len(instances), 3)}")
raw = d.raw()
ports = d.webmall_ports
for i, p in enumerate(ports[:4], start=1):
    print(f"export WEBMALL_PORT_{i}={p}")
# WebMall 前端入口端口；保持与 docker-compose.yaml 默认值一致。
webmall_cfg = raw.get('services', {}).get('webmall', {})
frontend_port = webmall_cfg.get('frontend_port', 8090)
print(f"export WEBMALL_FRONTEND_PORT={frontend_port}")
PY
)"

echo "[start] OnlyOffice host: $ONLYOFFICE_HOST_IP  doc-port: $ONLYOFFICE_DOC_PORT  flask-port: $ONLYOFFICE_FLASK_PORT  instances: $ONLYOFFICE_INSTANCE_COUNT"
echo "[start] WebMall ports:   ${WEBMALL_PORT_1} ${WEBMALL_PORT_2} ${WEBMALL_PORT_3} ${WEBMALL_PORT_4}"

# 2) 启动（OnlyOffice 多实例时启用 onlyoffice-multi profile，
#    带起 bench-onlyoffice-2/3 + bench-onlyoffice-share-2/3）
# 注意：不用 bash 数组——macOS 自带 bash 3.2 在 set -u 下展开空数组
# "${arr[@]}" 会报 unbound variable
COMPOSE_PROFILE_FLAG=""
if [ "${ONLYOFFICE_INSTANCE_COUNT}" -gt 1 ]; then
  COMPOSE_PROFILE_FLAG="--profile onlyoffice-multi"
  echo "[start] OnlyOffice 多实例模式: ${ONLYOFFICE_INSTANCE_COUNT} 套 (flask ${ONLYOFFICE_FLASK_PORT} ${ONLYOFFICE_FLASK_PORT_2:-} ${ONLYOFFICE_FLASK_PORT_3:-})"
fi
# shellcheck disable=SC2086  # COMPOSE_PROFILE_FLAG 需要按词拆分
docker compose -f "${COMPOSE_FILE}" ${COMPOSE_PROFILE_FLAG} up -d

echo "[start] docker compose 完成；用 'docker compose -f ${COMPOSE_FILE} ps' 查看状态"
