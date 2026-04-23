# Quickstart

目标：10 分钟内跑通 `qa` pipeline 的一个任务。

## 前置

- 一台可以跑 Docker 的 Linux 机器（以下简称「宿主」）
- 宿主上已安装 Docker + Docker Compose v2
- Python 3.9 + pip
- 有效的 OpenAI（或兼容）API key

## 步骤

### 1. 安装

```bash
git clone <this-repo> && cd ParaGUIBench
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

```bash
cp configs/deploy.example.yaml configs/deploy.yaml
cp configs/api.example.yaml    configs/api.yaml
```

最少改 `configs/deploy.yaml`：

```yaml
server:
  vm_host: 127.0.0.1            # 如果 Docker 宿主就是本机
  vm_user: yourname             # 本机用户名
  shared_base_dir: /home/yourname/shared
  qcow2_path: ./resources/Ubuntu.qcow2
resources:
  hf_repo: your-org/ParaGUIBench-resources  # 你自己的 HF 仓库
```

### 3. 机密

```bash
export BENCH_SSH_PASSWORD='your-docker-host-password'
export OPENAI_API_KEY='sk-...'
```

### 4. 下载资源（首次约 30 GB）

```bash
python scripts/download_resources.py
```

如果你已经把资源拷到 U 盘：

```bash
python scripts/download_resources.py --source local --root /mnt/usb/bench
```

### 5. 启动外部服务（仅 searchwrite / webmall 需要）

```bash
bash scripts/start_services.sh
docker compose -f docker/docker-compose.yaml ps   # 确认 healthy
```

### 6. 冒烟跑 QA 任务

```bash
python src/pipelines/run_ablation.py \
    --conditions baseline \
    --pipelines qa \
    --task-ids InformationRetrieval-FileSearch-Readonly-001 \
    --mode ablation
```

预期输出：

```
logs/ablation_2026MMDD_HHMMSS/baseline/qa_results.json
logs/ablation_2026MMDD_HHMMSS/baseline/agent_results/qa/.../task.log
logs/ablation_2026MMDD_HHMMSS/ablation_summary.json
```

## 下一步

- 跑全量 baseline：`python src/pipelines/run_ablation.py --conditions baseline --mode full`
- 对比多个模型：`--conditions baseline plan_claude_opus47 gui_kimi gui_claude ...`
- 查 [docs/deployment.md](deployment.md) 了解完整配置
