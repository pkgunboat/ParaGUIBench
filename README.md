# ParaGUIBench

> 基于 [OSWorld](https://github.com/xlang-ai/OSWorld) 扩展的多模态 GUI Agent **并行执行** Benchmark。
> Plan Agent 将复杂任务拆成子任务，派发到 N 个运行在隔离 Docker VM 的 GUI Agent 并行完成，统一评分。

支持 5 类任务 pipeline：

| Pipeline | 覆盖场景 |
|---|---|
| `qa` | 信息检索（WebSearch / FileSearch / VisualSearch） |
| `webmall` | 电商网站操作（收藏、加购、下单） |
| `webnavigate` | 网页导航 / 收藏夹 / 浏览器设置 |
| `operation` | Word/Excel/PPT 批量文件操作 |
| `searchwrite` | 搜索后写入 OnlyOffice 在线文档 |

---

## 硬件 & 软件要求

- Linux 服务器（Ubuntu 20.04+ 推荐），或 MacOS 开发环境
- Docker + Docker Compose（>= v2）
- Python 3.9+（推荐 conda/venv 隔离）
- 至少 32 GB 内存（5 并发 VM）、4 CPU 核
- 约 80 GB 磁盘（VM 镜像 + 任务素材）

## 快速开始

```bash
# 1. 克隆 + 安装依赖
git clone <this-repo>
cd ParaGUIBench
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置部署参数
cp configs/deploy.example.yaml configs/deploy.yaml
cp configs/api.example.yaml    configs/api.yaml
#   → 按注释改 vm_host / shared_base_dir / hf_repo 等

# 3. 导出机密（不要写入仓库）
export BENCH_SSH_PASSWORD='your-docker-host-password'
export OPENAI_API_KEY=sk-xxxxx
#   → 其它 provider 的 key 按需导出，见 configs/api.example.yaml

# 4. 下载大文件资源（qcow2 + 任务素材）
python scripts/download_resources.py
#   如使用 U 盘离线迁移：
#   python scripts/download_resources.py --source local --root /mnt/usb/bench

# 5. 启动外部服务（OnlyOffice + WebMall）
bash scripts/start_services.sh

# 6. 跑 dry-run 验证配置
python src/pipelines/run_ablation.py \
    --conditions baseline --pipelines qa --dry-run

# 7. 正式跑 baseline
python src/pipelines/run_ablation.py \
    --conditions baseline --mode ablation
```

输出落在 `./logs/ablation_<timestamp>/`。

## 目录结构

```
ParaGUIBench/
├── configs/                        # 部署 / API / Agent 推理参数
├── docker/                         # 外部服务 (OnlyOffice, WebMall) 编排
├── docs/                           # 部署、快速开始、评估函数开发指南
├── scripts/                        # 资源下载 + 服务启停
├── src/
│   ├── config_loader.py            # 统一的三层配置（CLI > env > YAML）
│   ├── pipelines/                  # run_ablation.py 入口 + 5 个 Pipeline
│   ├── stages/                     # 各 pipeline 的 init / execute / eval 阶段
│   ├── parallel_benchmark/         # Plan/GUI Agents、prompts、evaluators、任务
│   └── desktop_env/                # Docker VM provider（OSWorld 子集）
└── tests/                          # 冒烟测试
```

## 文档

- [docs/quickstart.md](docs/quickstart.md) — 10 分钟跑通 baseline 的最小步骤
- [docs/deployment.md](docs/deployment.md) — 完整部署指南、资源清单
- [docs/troubleshooting.md](docs/troubleshooting.md) — 常见问题
- [docs/evaluator_guide.md](docs/evaluator_guide.md) — 新增评估函数

## License

Apache License 2.0。本项目派生自 [OSWorld](https://github.com/xlang-ai/OSWorld)（Copyright 2024 XLang Lab, Apache 2.0）。
