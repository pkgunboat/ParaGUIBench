#!/bin/bash
# 启动 OnlyOffice 文档共享服务

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "OnlyOffice 文档共享服务启动脚本"
echo "=========================================="
echo ""

# 检查 OnlyOffice 服务是否运行
if ! curl -s http://localhost/info/info.json >/dev/null 2>&1; then
    echo "警告: OnlyOffice 服务可能未运行"
    echo "请先启动 OnlyOffice 服务:"
    echo "  cd $SCRIPT_DIR/Docker-DocumentServer && docker compose up -d"
    echo ""
    read -p "是否继续？(y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 检查 Python Flask 是否安装
if ! python3 -c "import flask" 2>/dev/null; then
    echo "正在安装 Flask 和 requests..."
    pip3 install --user flask requests
    if [ $? -ne 0 ]; then
        echo "安装失败，尝试使用 --break-system-packages..."
        pip3 install --break-system-packages --user flask requests || pip3 install flask requests
    fi
    echo ""
fi

echo "启动文档共享服务器..."
echo ""
echo "访问地址:"
echo "  - 文档管理界面: http://localhost:5001 (如果 5000 被占用)"
echo "  - OnlyOffice 服务: http://localhost"
echo ""
echo "按 Ctrl+C 停止服务器"
echo "=========================================="
echo ""

python3 "$SCRIPT_DIR/document_sharing_server.py"
