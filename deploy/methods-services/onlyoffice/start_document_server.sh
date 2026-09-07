#!/bin/bash
# OnlyOffice 文档服务启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Docker-DocumentServer/ 是上游 ONLYOFFICE/Docker-DocumentServer 的 clone，
# 不随本仓库发布；缺失时给出明确指引而不是 cd 失败。
if [ ! -d "$SCRIPT_DIR/Docker-DocumentServer" ]; then
    echo "错误: 缺少 $SCRIPT_DIR/Docker-DocumentServer" >&2
    echo "该目录是上游 https://github.com/ONLYOFFICE/Docker-DocumentServer 的 clone，" >&2
    echo "不随本仓库发布。如需 legacy 流程，请先 clone 到上述位置；" >&2
    echo "受支持的公开部署路径见 deploy/onlyoffice/compose.yaml（或" >&2
    echo "scripts/deployment/start_bench_services.sh）。" >&2
    exit 1
fi
cd "$SCRIPT_DIR/Docker-DocumentServer"

echo "=========================================="
echo "OnlyOffice 文档服务启动脚本"
echo "=========================================="
echo ""

# 检查 Docker 是否运行
if ! docker info >/dev/null 2>&1; then
    echo "错误: Docker 未运行。请先启动 Docker Desktop。"
    exit 1
fi

# 启动 OnlyOffice 服务
echo "正在启动 OnlyOffice Document Server..."
docker compose up -d

echo ""
echo "等待服务启动..."
sleep 5

# 检查服务状态
echo ""
echo "服务状态:"
docker compose ps

echo ""
echo "服务已启动。模板上传等任务级操作由共享服务"
echo "document_sharing_server.py 与 manage_documents.py 提供，"
echo "用法见同目录 README.md 与 README_Linux安装与使用指南.md。"
echo ""
echo "常用命令（在 Docker-DocumentServer/ 内执行）:"
echo "  - 查看日志: docker compose logs -f"
echo "  - 停止服务: docker compose stop"
echo "  - 重启服务: docker compose restart"
echo ""
