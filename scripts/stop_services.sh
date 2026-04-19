#!/usr/bin/env bash
# 停止外部服务（保留数据卷）
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
docker compose -f docker/docker-compose.yaml down
