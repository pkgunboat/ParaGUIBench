#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# WebMall 初始化脚本
# 从备份 tarball 恢复 4 个 WooCommerce 店铺的 MariaDB + WordPress 数据，
# 注入 wp-config.php，启动容器，并修复 URL。
#
# 用法：
#   bash scripts/setup_webmall.sh                 # 自动检测 IP
#   bash scripts/setup_webmall.sh 10.1.110.114    # 使用指定 IP
#   bash scripts/setup_webmall.sh --force         # 强制重新初始化
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MARKER_FILE="${REPO_ROOT}/.webmall_setup_done"

# 解析 --force 参数
FORCE_SETUP=0
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE_SETUP=1; shift ;;
    *) POSITIONAL_ARGS+=("$1"); shift ;;
  esac
done
set -- "${POSITIONAL_ARGS[@]}"

# 幂等检查：已初始化则提示
if [ -f "${MARKER_FILE}" ] && [ "${FORCE_SETUP}" = "0" ]; then
  echo "WebMall 已初始化（标记文件: ${MARKER_FILE}）。"
  echo "如需重新初始化，请运行: bash scripts/setup_webmall.sh --force"
  exit 0
fi

# 校验 tarball 不包含路径穿越成员
_safe_tar_check() {
  local tarball="$1"
  local bad
  bad="$(tar tzf "${tarball}" 2>/dev/null | grep -E '^(/|\.\./)' || true)"
  if [ -n "${bad}" ]; then
    echo "安全错误：${tarball} 包含路径穿越成员："
    echo "${bad}"
    exit 1
  fi
}

# 优先使用 configs/deploy.yaml 指定的 resources.root/webmall_assets/backup
# （离线/U 盘部署场景）；没有时回落到 docker/webmall/backup 并从 Mannheim 下载。
RES_ROOT="$(python3 - <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, "src")
from config_loader import DeployConfig
print(DeployConfig().resources_root)
PY
)"
RES_ROOT="${RES_ROOT:-${REPO_ROOT}/resources}"

CONFIG_DIR="docker/webmall/wp-config"
COMPOSE_FILE="docker/docker-compose.yaml"
BACKUP_URL="https://data.dws.informatik.uni-mannheim.de/webmall/backup"

if [ -d "${RES_ROOT}/webmall_assets/backup" ]; then
  BACKUP_DIR="${RES_ROOT}/webmall_assets/backup"
  DOWNLOAD_BACKUPS=0
  echo "使用 resources 目录下的 WebMall backup: ${BACKUP_DIR}"
else
  BACKUP_DIR="docker/webmall/backup"
  DOWNLOAD_BACKUPS=1
  echo "resources 目录无 webmall_assets/backup，将从 Mannheim 公网下载到 ${BACKUP_DIR}"
fi

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

# ── 1) 备份文件就绪（本地已有则跳过下载） ─────────────────────
echo "=== [1/5] 检查备份文件 ==="
mkdir -p "${BACKUP_DIR}"

for file in "${FILES[@]}"; do
  if [ -f "${BACKUP_DIR}/${file}" ]; then
    echo "  ✓ ${file} 已存在"
    continue
  fi
  if [ "${DOWNLOAD_BACKUPS}" = "1" ]; then
    echo "  下载 ${file} ..."
    curl -fL --retry 3 --progress-bar "${BACKUP_URL}/${file}" -o "${BACKUP_DIR}/${file}.tmp"
    mv "${BACKUP_DIR}/${file}.tmp" "${BACKUP_DIR}/${file}"
  else
    echo "  ✗ 缺少 ${file}（resources 目录不完整）"
    exit 1
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
    print(f"export SHOP{i}_PORT={shlex.quote(str(p))}")
# 使用 services.webmall.host_ip（与 rewrite_task_urls.py 一致），回退到 server.vm_host
host = d.webmall_host or d.vm_host
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
  _safe_tar_check "${REPO_ROOT}/${BACKUP_DIR}/wordpress_data_shop${SHOP_ID}.tar.gz"
  docker run --rm \
    -v "${WP_VOL}":/volume \
    -v "${REPO_ROOT}/${BACKUP_DIR}":/backup \
    busybox \
    tar xzf "/backup/wordpress_data_shop${SHOP_ID}.tar.gz" -C /volume

  echo "  Shop ${SHOP_ID}: 注入 wp-config.php ..."
  SHOP_PORT_VAR="SHOP${SHOP_ID}_PORT"
  SHOP_PORT_VALUE="${!SHOP_PORT_VAR}"
  TEMP_CONFIG="/tmp/webmall_shop_${SHOP_ID}_wpconfig.php"

  # 使用 compose 默认值或环境变量中的 DB 密码
  DB_PASS="${WEBMALL_DB_PASSWORD:-wordpress_db_password}"

  # 为每个 shop 生成唯一的 SALT 值（hex 避免sed 元字符问题）
  AUTH_KEY_VAL="$(openssl rand -hex 32)"
  SECURE_AUTH_KEY_VAL="$(openssl rand -hex 32)"
  LOGGED_IN_KEY_VAL="$(openssl rand -hex 32)"
  NONCE_KEY_VAL="$(openssl rand -hex 32)"
  AUTH_SALT_VAL="$(openssl rand -hex 32)"
  SECURE_AUTH_SALT_VAL="$(openssl rand -hex 32)"
  LOGGED_IN_SALT_VAL="$(openssl rand -hex 32)"
  NONCE_SALT_VAL="$(openssl rand -hex 32)"

  sed \
    -e "s/SHOP${SHOP_ID}_PORT_PLACEHOLDER/${SHOP_PORT_VALUE}/g" \
    -e "s/DB_PASSWORD_PLACEHOLDER/${DB_PASS}/g" \
    -e "s/AUTH_KEY_PLACEHOLDER/${AUTH_KEY_VAL}/g" \
    -e "s/SECURE_AUTH_KEY_PLACEHOLDER/${SECURE_AUTH_KEY_VAL}/g" \
    -e "s/LOGGED_IN_KEY_PLACEHOLDER/${LOGGED_IN_KEY_VAL}/g" \
    -e "s/NONCE_KEY_PLACEHOLDER/${NONCE_KEY_VAL}/g" \
    -e "s/AUTH_SALT_PLACEHOLDER/${AUTH_SALT_VAL}/g" \
    -e "s/SECURE_AUTH_SALT_PLACEHOLDER/${SECURE_AUTH_SALT_VAL}/g" \
    -e "s/LOGGED_IN_SALT_PLACEHOLDER/${LOGGED_IN_SALT_VAL}/g" \
    -e "s/NONCE_SALT_PLACEHOLDER/${NONCE_SALT_VAL}/g" \
    "${REPO_ROOT}/${CONFIG_DIR}/shop_${SHOP_ID}.php" > "${TEMP_CONFIG}"

  docker run --rm \
    -v "${WP_VOL}":/volume \
    -v "${TEMP_CONFIG}":/tmp/wp-config.php:ro \
    busybox \
    cp /tmp/wp-config.php /volume/wp-config.php

  rm -f "${TEMP_CONFIG}"

  echo "  Shop ${SHOP_ID}: 恢复 MariaDB 数据 ..."
  _safe_tar_check "${REPO_ROOT}/${BACKUP_DIR}/mariadb_data_shop${SHOP_ID}.tar.gz"
  docker run --rm \
    -v "${DB_VOL}":/volume \
    -v "${REPO_ROOT}/${BACKUP_DIR}":/backup \
    busybox \
    tar xzf "/backup/mariadb_data_shop${SHOP_ID}.tar.gz" -C /volume

  echo "  ✓ Shop ${SHOP_ID} 恢复完成"
done

# ── 4) 启动容器 ──────────────────────────────────────────────
echo ""
echo "=== [4/5] 启动 Docker Compose ==="

# Elasticsearch 8.x requires vm.max_map_count >= 262144
MAX_MAP_COUNT="$(cat /proc/sys/vm/max_map_count 2>/dev/null || echo 0)"
if [ "${MAX_MAP_COUNT}" -lt 262144 ]; then
  echo "  ⚠ Elasticsearch 需要 vm.max_map_count >= 262144（当前: ${MAX_MAP_COUNT}）"
  echo "  请运行: sudo sysctl -w vm.max_map_count=262144"
  echo "  或添加到 /etc/sysctl.conf 永久生效"
  exit 1
fi

# 导出端口变量供 docker compose 使用
export WEBMALL_PORT_1="${SHOP1_PORT}"
export WEBMALL_PORT_2="${SHOP2_PORT}"
export WEBMALL_PORT_3="${SHOP3_PORT}"
export WEBMALL_PORT_4="${SHOP4_PORT}"

docker compose -f "${COMPOSE_FILE}" up -d --wait

echo "  容器已就绪"

# ── 5) 修复 URL ──────────────────────────────────────────────
echo ""
echo "=== [5/5] 修复 WordPress URL ==="

for SHOP_ID in 1 2 3 4; do
  SHOP_PORT_VAR="SHOP${SHOP_ID}_PORT"
  SHOP_PORT="${!SHOP_PORT_VAR}"
  ACTUAL_URL="http://${DEPLOY_HOST}:${SHOP_PORT}"
  SERVICE_NAME="wordpress-shop${SHOP_ID}"
  CONTAINER_ID="$(docker compose -f "${COMPOSE_FILE}" ps -q "${SERVICE_NAME}" 2>/dev/null || true)"

  echo "  Shop ${SHOP_ID}: ${ACTUAL_URL}"

  if [ -z "${CONTAINER_ID}" ]; then
    echo "    ⚠ 服务 ${SERVICE_NAME} 未运行，跳过"
    continue
  fi

  # 修复 wp-config.php
  docker exec "${CONTAINER_ID}" \
    sed -i "s|http://localhost[:0-9]*|${ACTUAL_URL}|g" \
    /bitnami/wordpress/wp-config.php 2>/dev/null || {
      echo "    ⚠ wp-config.php 修改失败"
    }

  # 使用 regex 匹配 http://localhost 可选带端口，替换为目标 URL
  # 避免 http://localhost:XXXX → http://localhost:XXXX:XXXX 双端口问题
  docker exec "${CONTAINER_ID}" \
    wp search-replace 'http://localhost[:0-9]*' "${ACTUAL_URL}" \
    --regex --all-tables --path=/opt/bitnami/wordpress > /dev/null 2>&1 || {
      echo "    ⚠ wp search-replace 失败（数据库可能尚未就绪）"
    }

  # 清除缓存
  docker exec "${CONTAINER_ID}" \
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

# 写入标记文件
date -Iseconds > "${MARKER_FILE}"

echo "访问地址："
for SHOP_ID in 1 2 3 4; do
  SHOP_PORT_VAR="SHOP${SHOP_ID}_PORT"
  echo "  Shop ${SHOP_ID}: http://${DEPLOY_HOST}:${!SHOP_PORT_VAR}"
done
echo ""
echo "注意：任务 JSON 中的 answer URL 可能仍包含旧 IP，请运行："
echo "  python scripts/rewrite_task_urls.py --from http://10.1.110.114 --to http://${DEPLOY_HOST}"
