# 部署指南

## 1. 架构一览

```
┌─────────────────────────────────────────────┐
│  Docker 宿主机（单机即可）                   │
│                                             │
│   ┌──────────┐   ┌──────────┐  … ×N        │
│   │ VM 1     │   │ VM 2     │                │
│   │(5000)    │   │(5001)    │   GUI Agent   │
│   └──────────┘   └──────────┘   在此并行    │
│        ↑               ↑                     │
│        └───── 共享目录 /shared ──────┐        │
│                                      │        │
│   ┌────────────────┐   ┌──────────┐ │        │
│   │OnlyOffice DS   │   │WebMall   │ │        │
│   │(8080)          │   │(9081-84) │ │        │
│   └────────────────┘   └──────────┘ │        │
└─────────────────────────────────────────────┘
         ↑
         │  SSH / HTTP
         ↓
┌──────────────────┐
│ 运行 run_ablation │
│ 的控制端（本机）   │
└──────────────────┘
```

控制端与宿主机可以是同一台；若跨机，控制端通过 SSH + HTTP 与宿主通信。

## 2. 配置文件

### `configs/deploy.yaml` — 部署参数

| 字段 | 含义 | 典型值 |
|---|---|---|
| `server.vm_host`         | Docker 宿主机 IP | `127.0.0.1`（单机）/ `192.168.x.y` |
| `server.vm_user`         | SSH 登录名   | 宿主机上的普通用户 |
| `server.ssh_password_env`| SSH 密码读取自哪个环境变量 | `BENCH_SSH_PASSWORD` |
| `server.shared_base_dir` | 宿主机与 VM 的共享目录 | `/home/<user>/shared` |
| `server.qcow2_path`      | VM 磁盘镜像路径 | `./resources/Ubuntu.qcow2` |
| `resources.root`         | 下载到哪里 | `./resources` 或 `/mnt/usb/bench` |
| `resources.source`       | `huggingface` / `local` | |
| `resources.hf_repo`      | HF dataset repo id | `your-org/ParaGUIBench-resources` |
| `services.onlyoffice.*`  | OnlyOffice 服务端口 | `flask_port=5050`, `doc_server_port=8080` |
| `services.webmall.ports` | 4 个商城端口 | `[9081, 9082, 9083, 9084]` |
| `parallel.max_concurrent_vms` | 单机最大并发 | 20 |

### `configs/api.yaml` — LLM provider

每个 provider 至少需要：

```yaml
openai:
  api_key: "${OPENAI_API_KEY}"    # 从环境变量展开
  base_url: "https://api.openai.com/v1"
```

仓库**不接受明文 key**，一律走 env var。`config_loader.py` 的 `${VAR}`/`${VAR:-default}` 展开由 `src/config_loader.py` 自动处理。

### 环境变量清单

| 变量 | 作用 | 必需 |
|---|---|---|
| `BENCH_SSH_PASSWORD` | 宿主机 SSH 密码 | ✓（若走密码登录） |
| `OPENAI_API_KEY` | OpenAI / 兼容网关 | ✓（跑 baseline） |
| `TONGGPT_API_KEY` | Azure OpenAI 代理 | 可选 |
| `ANTHROPIC_API_KEY` | Claude | 可选（用 Claude GUI Agent 时） |
| `DOUBAO_API_KEY` + `DOUBAO_ENDPOINT` | 火山引擎 Seed 模型 | 可选 |
| `MOONSHOT_API_KEY` | Kimi | 可选 |
| `DASHSCOPE_API_KEY` | 通义千问 | 可选 |
| `BENCH_DEPLOY_CONFIG` | 自定义 deploy.yaml 路径 | 可选 |
| `BENCH_API_CONFIG` | 自定义 api.yaml 路径 | 可选 |

## 3. 资源下载

镜像和素材体积大，通过 HuggingFace Hub 分发。

```bash
python scripts/download_resources.py
```

脚本会下载到 `resources.root`（默认 `./resources`）：

```
resources/
├── Ubuntu.qcow2              # VM 磁盘（解压自 .zst）
├── operation_gt_cache/       # operation pipeline 的 GT 样例
├── searchwrite_templates/    # SearchWrite 文档模板
└── webmall_assets/           # WebMall 商城静态素材
```

离线迁移场景：把整个 `resources/` 目录拷到 U 盘，目标机器执行：

```bash
python scripts/download_resources.py \
    --source local \
    --root /mnt/usb/bench_resources
```

## 4. 外部服务

使用 `docker/docker-compose.yaml` 一键拉起：

```bash
bash scripts/start_services.sh
```

启动后的服务：

| 名称 | 端口 | 作用 |
|---|---|---|
| `bench-onlyoffice` | `:8080` | OnlyOffice DocumentServer |
| `bench-onlyoffice-share` | `:5050` | Flask 文档共享 API |
| `bench-webmall-1/2/3/4` | `:9081/2/3/4` | WebMall 商城后端 |

停止：`bash scripts/stop_services.sh`。

> **WebMall 镜像注意**：`benchmark/webmall:latest` 是占位镜像名；首次部署前请
> 根据 `docker/webmall/README.md` 自行构建或拉取实际的 WebMall 镜像。

## 5. 验证部署

```bash
# 5.1 配置加载
python3 -c "import sys; sys.path.insert(0,'src'); \
            from config_loader import DeployConfig; \
            d = DeployConfig(); \
            print('vm_host:', d.vm_host, 'webmall_ports:', d.webmall_ports)"

# 5.2 Pipeline import
python3 -c "import sys; sys.path.insert(0,'src'); sys.path.insert(0,'src/pipelines'); \
            sys.path.insert(0,'src/stages'); sys.path.insert(0,'src/parallel_benchmark'); \
            import qa_pipeline, webmall_pipeline, webnavigate_pipeline, searchwrite_pipeline"

# 5.3 跑 dry-run
python src/pipelines/run_ablation.py --conditions baseline --dry-run

# 5.4 跑单任务
python src/pipelines/run_ablation.py \
    --conditions baseline --pipelines qa \
    --task-ids InformationRetrieval-FileSearch-Readonly-001
```

## 6. 升级 / 清理

```bash
git pull                                 # 更新代码
pip install -r requirements.txt -U      # 升级依赖
bash scripts/stop_services.sh && \
    bash scripts/start_services.sh      # 重启服务
rm -rf logs/                             # 清运行日志
```
