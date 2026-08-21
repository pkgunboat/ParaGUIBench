#!/usr/bin/env bash
# 停止 methods_runner 验证栈的外部服务（保留数据卷）
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
docker compose -f deploy/methods-services/docker-compose.yaml down
