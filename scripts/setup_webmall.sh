#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# WebMall 初始化脚本
# 从备份 tarball 恢复 4 个 WooCommerce 店铺的 MariaDB + WordPress 数据，
# 注入 wp-config.php，启动容器，并修复 URL。
#
# 用法：
#   bash scripts/setup_webmall.sh                 # 自动检测 IP
#   bash scripts/setup_webmall.sh 10.1.110.114    # 使用指定 IP
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKUP_DIR="docker/webmall/backup"
CONFIG_DIR="docker/webmall/wp-config"
COMPOSE_FILE="docker/docker-compose.yaml"
BACKUP_URL="https://data.dws.informatik.uni-mannheim.de/webmall/backup"

FILES=(
  "mariadb_data_shop1.tar.gz"
  "mariadb_data_shop2.tar.gz"
  "mariadb_data_shop3.tar.gz"
  "mariadb_data_shop4.tar.gz"
  "wordpress_data_shop1.tar.gz"
  "wordpress_data_shop2.tar.gz"
  "wordpress_data_shop3.tar.gz"
  "wordpress_data_shop4.tar.gz"
)

# ── 1) 下载备份文件 ─────────────────────────────────────────
echo "=== [1/5] 检查备份文件 ==="
mkdir -p "${BACKUP_DIR}"

for file in "${FILES[@]}"; do
  if [ ! -f "${BACKUP_DIR}/${file}" ]; then
    echo "  下载 ${file} ..."
    curl -L --progress-bar "${BACKUP_URL}/${file}" -o "${BACKUP_DIR}/${file}"
  else
    echo "  ✓ ${file} 已存在"
  fi
done
echo "=== 备份文件就绪 ==="

# ── 2) 读取端口配置 ─────────────────────────────────────────
echo ""
echo "=== [2/5] 读取 deploy.yaml 端口配置 ==="
eval "$(python3 - <<'PY'
import os, sys, shlex
sys.path.insert(0, 'src')
from config_loader import DeployConfig
d = DeployConfig()
ports = d.webmall_ports
for i, p in enumerate(ports[:4], start=1):
    print(f"export SHOP{i}_PORT={p}")
raw = d.raw()
host = raw.get('server', {}).get('vm_host', '127.0.0.1')
print(f"export DEPLOY_HOST={shlex.quote(host)}")
PY
)"

SPECIFIED_IP="${1:-}"
if [ -n "$SPECIFIED_IP" ]; then
  DEPLOY_HOST="$SPECIFIED_IP"
fi
echo "  Shop 端口: ${SHOP1_PORT} ${SHOP2_PORT} ${SHOP3_PORT} ${SHOP4_PORT}"
echo "  目标 Host: ${DEPLOY_HOST}"

# ── 3) 创建并恢复 Docker 卷 ──────────────────────────────────
echo ""
echo "=== [3/5] 恢复 Docker 卷数据 ==="

for SHOP_ID in 1 2 3 4; do
  WP_VOL="webmall_wordpress_shop${SHOP_ID}"
  DB_VOL="webmall_mariadb_shop${SHOP_ID}"

  echo "  Shop ${SHOP_ID}: 创建卷 ..."
  docker volume create "${WP_VOL}" 2>/dev/null || true
  docker volume create "${DB_VOL}" 2>/dev/null || true

  echo "  Shop ${SHOP_ID}: 恢复 WordPress 数据 ..."
  docker run --rm \
    -v "${WP_VOL}":/volume \
    -v "${REPO_ROOT}/${BACKUP_DIR}":/backup \
    busybox \
    tar xzf "/backup/wordpress_data_shop${SHOP_ID}.tar.gz -C /volume"

  echo "  Shop ${SHOP_ID}: 注入 wp-config.php ..."
  SHOP_PORT_VAR="SHOP${SHOP_ID}_PORT"
  SHOP_PORT_VALUE="${!SHOP_PORT_VAR}"
  TEMP_CONFIG="/tmp/webmall_shop_${SHOP_ID}_wpconfig.php"

  sed "s/SHOP${SHOP_ID}_PORT_PLACEHOLDER/${SHOP_PORT_VALUE}/g" \
    "${REPO_ROOT}/${CONFIG_DIR}/shop_${SHOP_ID}.php" > "${TEMP_CONFIG}"

  docker run --rm \
    -v "${WP_VOL}":/volume \
    -v "${TEMP_CONFIG}":/tmp/wp-config.php:ro \
    busybox \
    cp /tmp/wp-config.php /volume/wp-config.php

  rm -f "${TEMP_CONFIG}"

  echo "  Shop ${SHOP_ID}: 恢复 MariaDB 数据 ..."
  docker run --rm \
    -v "${DB_VOL}":/volume \
    -v "${REPO_ROOT}/${BACKUP_DIR}":/backup \
    busybox \
    tar xzf "/backup/mariadb_data_shop${SHOP_ID}.tar.gz -C /volume"

  echo "  ✓ Shop ${SHOP_ID} 恢复完成"
done

# ── 4) 启动容器 ──────────────────────────────────────────────
echo ""
echo "=== [4/5] 启动 Docker Compose ==="
# 导出端口变量供 docker compose 使用
export WEBMALL_PORT_1="${SHOP1_PORT}"
export WEBMALL_PORT_2="${SHOP2_PORT}"
export WEBMALL_PORT_3="${SHOP3_PORT}"
export WEBMALL_PORT_4="${SHOP4_PORT}"

docker compose -f "${COMPOSE_FILE}" up -d

echo "  等待容器就绪 ..."
sleep 15

# ── 5) 修复 URL ──────────────────────────────────────────────
echo ""
echo "=== [5/5] 修复 WordPress URL ==="

for SHOP_ID in 1 2 3 4; do
  SHOP_PORT_VAR="SHOP${SHOP_ID}_PORT"
  SHOP_PORT="${!SHOP_PORT_VAR}"
  LOCALHOST_URL="http://localhost:${SHOP_PORT}"
  ACTUAL_URL="http://${DEPLOY_HOST}:${SHOP_PORT}"
  CONTAINER_NAME="bench-wordpress-${SHOP_ID}"

  # 备份中的原始端口（8081-8084）
  ORIGINAL_PORT=$((8080 + SHOP_ID))
  DOUBLE_PORT_URL="http://localhost:${ORIGINAL_PORT}:${ORIGINAL_PORT}"

  echo "  Shop ${SHOP_ID}: ${ACTUAL_URL}"

  if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "    ⚠ 容器 ${CONTAINER_NAME} 未运行，跳过"
    continue
  fi

  # 修复 wp-config.php
  docker exec "${CONTAINER_NAME}" \
    sed -i "s|http://localhost:[0-9]*|${ACTUAL_URL}|g" \
    /bitnami/wordpress/wp-config.php 2>/dev/null || true

  # 修复数据库中的双端口格式 URL
  docker exec "${CONTAINER_NAME}" \
    wp search-replace "${DOUBLE_PORT_URL}" "${ACTUAL_URL}" \
    --all-tables --path=/opt/bitnami/wordpress > /dev/null 2>&1 || true

  # 修复数据库中的 localhost URL
  docker exec "${CONTAINER_NAME}" \
    wp search-replace "${LOCALHOST_URL}" "${ACTUAL_URL}" \
    --all-tables --path=/opt/bitnami/wordpress > /dev/null 2>&1 || true

  # 清除缓存
  docker exec "${CONTAINER_NAME}" \
    wp cache flush --path=/opt/bitnami/wordpress > /dev/null 2>&1 || true

  echo "    ✓ URL 已更新"
done

# ── 重启以应用更改 ──────────────────────────────────────────
echo ""
echo "  重启容器以应用更改 ..."
docker compose -f "${COMPOSE_FILE}" restart

echo ""
echo "=== WebMall 初始化完成！==="
echo ""
echo "访问地址："
for SHOP_ID in 1 2 3 4; do
  SHOP_PORT_VAR="SHOP${SHOP_ID}_PORT"
  echo "  Shop ${SHOP_ID}: http://${DEPLOY_HOST}:${!SHOP_PORT_VAR}"
done
echo ""
echo "注意：任务 JSON 中的 answer URL 可能仍包含旧 IP，请运行："
echo "  python scripts/rewrite_task_urls.py --from http://10.1.110.114 --to http://${DEPLOY_HOST}"
