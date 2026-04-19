#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# ParaGUIBench 资源 U 盘传输工具
#
# 用法：
#   # 打包：在服务器上将老项目的资源复制到 U 盘
#   bash scripts/usb_transfer.sh pack /media/yuzedong/u盘1/ParaGUIBench-resources
#
#   # 解包：在目标机器上将 U 盘资源复制到 resources/
#   bash scripts/usb_transfer.sh unpack /media/yuzedong/u盘1/ParaGUIBench-resources
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 老项目路径（打包时使用）
OLD_PROJECT="${OLD_PROJECT:-/home/yuzedong/code/parallel-efficient-benchmark}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

usage() {
  echo "用法："
  echo "  $0 pack <USB_DIR>           打包资源到 U 盘"
  echo "  $0 unpack <USB_DIR>         从 U 盘解包资源"
  echo ""
  echo "环境变量："
  echo "  OLD_PROJECT  老项目路径（默认：/home/yuzedong/code/parallel-efficient-benchmark）"
  exit 1
}

# ── 打包：服务器 → U 盘 ──────────────────────────────────────
do_pack() {
  local USB_DIR="$1"

  if [ ! -d "${OLD_PROJECT}" ]; then
    error "老项目路径不存在: ${OLD_PROJECT}"
  fi

  mkdir -p "${USB_DIR}"
  info "U 盘目标目录: ${USB_DIR}"
  info "老项目路径: ${OLD_PROJECT}"
  echo ""

  # 1) Ubuntu.qcow2（zstd 压缩，节省约 40% 空间）
  local QCOW2_SRC="${OLD_PROJECT}/ubuntu_env/docker_vm_data/Ubuntu.qcow2"
  local QCOW2_DST="${USB_DIR}/Ubuntu.qcow2.zst"

  if [ ! -f "${QCOW2_SRC}" ]; then
    error "找不到 VM 镜像: ${QCOW2_SRC}"
  fi

  if [ -f "${QCOW2_DST}" ]; then
    info "[1/4] Ubuntu.qcow2.zst 已存在，跳过（如需重新打包请先删除）"
  else
    info "[1/4] 压缩 Ubuntu.qcow2 → Ubuntu.qcow2.zst（可能需要 10-20 分钟）..."
    if command -v zstd &>/dev/null; then
      zstd -19 --progress "${QCOW2_SRC}" -o "${QCOW2_DST}"
    else
      warn "zstd 未安装，直接复制（不压缩，占用更多空间）"
      cp --progress= "${QCOW2_SRC}" "${USB_DIR}/Ubuntu.qcow2"
    fi
  fi

  # 2) operation_gt_cache
  local GT_SRC="${OLD_PROJECT}/ubuntu_env/examples/self_operation_pipeline/gt_cache"
  local GT_DST="${USB_DIR}/operation_gt_cache.tar.gz"

  if [ -f "${GT_DST}" ]; then
    info "[2/4] operation_gt_cache.tar.gz 已存在，跳过"
  elif [ -d "${GT_SRC}" ]; then
    info "[2/4] 打包 operation_gt_cache ..."
    tar czf "${GT_DST}" -C "$(dirname "${GT_SRC}")" "$(basename "${GT_SRC}")"
  else
    warn "[2/4] 找不到 gt_cache 目录 (${GT_SRC})，创建空 tar"
    mkdir -p /tmp/empty_gt_cache
    tar czf "${GT_DST}" -C /tmp empty_gt_cache
    rmdir /tmp/empty_gt_cache 2>/dev/null || true
  fi

  # 3) searchwrite_templates
  local TPL_SRC="${OLD_PROJECT}/ubuntu_env/extra_docker_env/onlyoffice/templates"
  local TPL_DST="${USB_DIR}/searchwrite_templates.tar.gz"

  if [ -f "${TPL_DST}" ]; then
    info "[3/4] searchwrite_templates.tar.gz 已存在，跳过"
  elif [ -d "${TPL_SRC}" ]; then
    info "[3/4] 打包 searchwrite_templates ..."
    tar czf "${TPL_DST}" -C "$(dirname "${TPL_SRC}")" "$(basename "${TPL_SRC}")"
  else
    warn "[3/4] 找不到 templates 目录，创建空 tar"
    mkdir -p /tmp/empty_templates
    tar czf "${TPL_DST}" -C /tmp empty_templates
    rmdir /tmp/empty_templates 2>/dev/null || true
  fi

  # 4) webmall_assets（backup tarballs + product data CSV）
  local WM_DST="${USB_DIR}/webmall_assets.tar.gz"
  local WM_BACKUP="${OLD_PROJECT}/ubuntu_env/extra_docker_env/WebMall/docker_all/backup"
  local WM_PRODUCTS="${OLD_PROJECT}/ubuntu_env/extra_docker_env/WebMall/product_data"

  if [ -f "${WM_DST}" ]; then
    info "[4/4] webmall_assets.tar.gz 已存在，跳过"
  else
    info "[4/4] 打包 webmall_assets（backup + product_data，约 3.4 GB）..."
    local WM_TMP="$(mktemp -d)"
    mkdir -p "${WM_TMP}/webmall_assets"
    [ -d "${WM_BACKUP}" ] && cp -r "${WM_BACKUP}" "${WM_TMP}/webmall_assets/backup"
    [ -d "${WM_PRODUCTS}" ] && cp -r "${WM_PRODUCTS}" "${WM_TMP}/webmall_assets/product_data"
    tar czf "${WM_DST}" -C "${WM_TMP}" webmall_assets
    rm -rf "${WM_TMP}"
  fi

  # 校验
  echo ""
  info "打包完成！U 盘内容："
  ls -lh "${USB_DIR}/"
  echo ""
  info "磁盘空间："
  df -h "${USB_DIR}"
  echo ""

  # 生成 checksum
  info "生成 sha256sum ..."
  (cd "${USB_DIR}" && sha256sum Ubuntu.qcow2* operation_gt_cache.tar.gz searchwrite_templates.tar.gz webmall_assets.tar.gz > sha256sum.txt 2>/dev/null || true)
  info "校验和已写入 ${USB_DIR}/sha256sum.txt"
}

# ── 解包：U 盘 → resources/ ──────────────────────────────────
do_unpack() {
  local USB_DIR="$1"
  local RESOURCES_ROOT

  # 读 deploy.yaml 获取 resources.root
  RESOURCES_ROOT="$(python3 - -c "
import sys; sys.path.insert(0, '${REPO_ROOT}/src')
from config_loader import load_deploy_config
d = load_deploy_config() or {}
print(d.get('resources', {}).get('root', '${REPO_ROOT}/resources'))
" 2>/dev/null || echo "${REPO_ROOT}/resources")"

  RESOURCES_ROOT="${2:-${RESOURCES_ROOT}}"
  mkdir -p "${RESOURCES_ROOT}"

  info "U 盘源目录: ${USB_DIR}"
  info "资源目标目录: ${RESOURCES_ROOT}"

  if [ ! -f "${USB_DIR}/sha256sum.txt" ]; then
    warn "未找到 sha256sum.txt，跳过完整性校验"
  else
    info "校验文件完整性 ..."
    (cd "${USB_DIR}" && sha256sum -c sha256sum.txt 2>/dev/null) || warn "部分文件校验失败，请确认 U 盘数据完整"
  fi

  # 1) Ubuntu.qcow2
  echo ""
  if [ -f "${USB_DIR}/Ubuntu.qcow2.zst" ]; then
    info "[1/4] 解压 Ubuntu.qcow2.zst → ${RESOURCES_ROOT}/Ubuntu.qcow2 ..."
    if command -v zstd &>/dev/null; then
      zstd -d --progress "${USB_DIR}/Ubuntu.qcow2.zst" -o "${RESOURCES_ROOT}/Ubuntu.qcow2"
    else
      error "需要 zstd 来解压 .qcow2.zst 文件。请运行: pip install zstandard 或 apt install zstd"
    fi
  elif [ -f "${USB_DIR}/Ubuntu.qcow2" ]; then
    info "[1/4] 复制 Ubuntu.qcow2（未压缩）..."
    cp --progress= "${USB_DIR}/Ubuntu.qcow2" "${RESOURCES_ROOT}/Ubuntu.qcow2"
  else
    warn "[1/4] U 盘上未找到 Ubuntu.qcow2 或 Ubuntu.qcow2.zst"
  fi

  # 2) operation_gt_cache
  if [ -f "${USB_DIR}/operation_gt_cache.tar.gz" ]; then
    info "[2/4] 解压 operation_gt_cache ..."
    tar xzf "${USB_DIR}/operation_gt_cache.tar.gz" -C "${RESOURCES_ROOT}"
  else
    warn "[2/4] U 盘上未找到 operation_gt_cache.tar.gz"
  fi

  # 3) searchwrite_templates
  if [ -f "${USB_DIR}/searchwrite_templates.tar.gz" ]; then
    info "[3/4] 解压 searchwrite_templates ..."
    tar xzf "${USB_DIR}/searchwrite_templates.tar.gz" -C "${RESOURCES_ROOT}"
  else
    warn "[3/4] U 盘上未找到 searchwrite_templates.tar.gz"
  fi

  # 4) webmall_assets
  if [ -f "${USB_DIR}/webmall_assets.tar.gz" ]; then
    info "[4/4] 解压 webmall_assets ..."
    tar xzf "${USB_DIR}/webmall_assets.tar.gz" -C "${RESOURCES_ROOT}"
  else
    warn "[4/4] U 盘上未找到 webmall_assets.tar.gz"
  fi

  # 验证
  echo ""
  info "验证资源完整性 ..."
  python3 "${REPO_ROOT}/scripts/download_resources.py" --source local --root "${RESOURCES_ROOT}" || true

  echo ""
  info "完成！接下来："
  info "  bash scripts/setup_webmall.sh     # 初始化 WebMall"
  info "  bash scripts/start_services.sh     # 启动所有服务"
}

# ── 主入口 ─────────────────────────────────────────────────────
COMMAND="${1:-}"
USB_DIR="${2:-}"

[ -z "${COMMAND}" ] || [ -z "${USB_DIR}" ] && usage

case "${COMMAND}" in
  pack)   do_pack "${USB_DIR}" ;;
  unpack) do_unpack "${USB_DIR}" ;;
  *)      usage ;;
esac
