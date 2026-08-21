#!/bin/bash
# OnlyOffice 文档服务启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
echo "=========================================="
echo "服务已启动！"
echo "=========================================="
echo ""
echo "访问地址:"
echo "  - OnlyOffice 服务: http://localhost"
echo ""
echo "创建文档页面:"
echo "  1. 首先运行以下命令创建空文档模板:"
echo "     python3 $SCRIPT_DIR/create_empty_docs.py"
echo ""
echo "  2. 启动一个简单的 HTTP 服务器提供模板文件:"
echo "     cd $SCRIPT_DIR && python3 -m http.server 8080"
echo ""
echo "  3. 在浏览器中打开:"
echo "     file://$SCRIPT_DIR/create_document.html"
echo "     或者通过服务器: http://localhost:8080/create_document.html"
echo ""
echo "常用命令:"
echo "  - 查看日志: cd $SCRIPT_DIR/Docker-DocumentServer && docker compose logs -f"
echo "  - 停止服务: cd $SCRIPT_DIR/Docker-DocumentServer && docker compose stop"
echo "  - 重启服务: cd $SCRIPT_DIR/Docker-DocumentServer && docker compose restart"
echo ""
