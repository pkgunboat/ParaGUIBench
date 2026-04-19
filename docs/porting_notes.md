# 开源版移植备忘

> 本文面向对原内部仓库熟悉的开发者，记录从内部 `parallel-efficient-benchmark`
> 迁移到开源版 `ParaGUIBench` 过程中做过的关键改动。

## 1. 结构

- 顶层 `ubuntu_env/` 被扁平化：`pipeline_v2/` → `src/pipelines/`，`run_*.py` 一众脚本 → `src/stages/`，`mm_agents/` / `desktop_env/` / `parallel_benchmark/` 保留命名下沉到 `src/`。
- 已移除：`android_env/`、`tongagent/`、`agent_test/`、`screenshot/`、`test_results/`、`logs/`、`docker_vm_data/`、`_backup/`、所有 `test_*.py`（除 smoke 测试）、dataviewer 的可视化 UI、benchmarkClient/cookbooks 大文件、历史对比文档 (`z_*.md`、`CLAUDE_*.md`、`QWEN_*.md` 等)。
- 包体积：原 1.5 GB → 开源版约 8.5 MB（不含 resources/）。

## 2. 配置重构

所有机密 / IP / 路径一律走 `configs/deploy.yaml` + 环境变量。

- **明文 API key 已全部清除**：DeerAPI、Doubao、Kimi、BigAI、Claude、OpenAI 的 `sk-xxx` 以及 Azure `92209c51...` 都替换为 `${OPENAI_API_KEY}` / `os.environ.get(...)` 占位。
- **SSH 密码明文** 已删除；全部走 `BENCH_SSH_PASSWORD` env。
- **硬编码 `10.1.110.114/143`、`yuzedong`、`agentlab`** 在代码中已清理；任务 JSON 中的 WebMall URL 保留原样（建议用 `scripts/rewrite_task_urls.py` 运行期替换）。
- **路径重设计**：`UBUNTU_ENV_DIR` 现等于 REPO_ROOT（`logs/` 挂这里）；`EXAMPLES_DIR` 等于 `src/`；`UNIFIED_TASKS_DIR` 指 `src/parallel_benchmark/tasks/`。

## 3. Pipeline 单服务器改造

- `src/pipelines/qa_pipeline.py` **删除了 IP=143、user=agentlab 的覆盖** —— QA 现与其它 pipeline 共用 `deploy.yaml.server.*`。
- `searchwrite_pipeline.py` 的 `--onlyoffice-host-ip` 默认从 `deploy.yaml.services.onlyoffice.host_ip` 读取。
- `run_QA_pipeline.py`、`run_QA_pipeline_parallel.py`、`run_webmall_pipeline.py`、`eval/osworld_evaluator.py` 中的 `_MACHINE_CREDENTIALS` 字典全部移除，统一通过 `config_loader.DeployConfig` + `get_ssh_password()` 按需解析。

## 4. Agent 依赖裁剪

- `parallel_agents_as_tools/__init__.py` 改成 PEP 562 lazy `__getattr__` 加载，避免 Claude/benchmarkClient 路径传导导致整个 package 无法 import。
- `parallel_agents_as_tools/agent_tool_registry.py` 对 `ClaudeGUIAgentTool` 的 import 改成 `try/except`，失败时保留其它 GUI agent 可用。
- `claude_computer_use_agent.py` 对 `benchmarkClient.cookbooks.gpt.gpt_computer_use` 做 fallback stub（NotImplementedError），告知用户需要单独安装 benchmarkClient 才能启用 Claude agent。
- 删除 `mm_agents/plan_agent.py`（OSWorld 原版 Plan Agent，pipeline_v2 不使用）与 `parallel_agents/plan_agent_multi_code.py`（依赖已删除的 `execution_recorder_backup`）。

## 5. 外部服务

- `docker/docker-compose.yaml` 一把起 OnlyOffice DocumentServer + Flask 共享 + WebMall 商城（多服务架构：4× MariaDB + 4× WordPress/WooCommerce + Elasticsearch + Nginx）。
- WebMall 不再使用占位镜像 `benchmark/webmall:latest`，改为直接引用上游镜像（bitnami/mariadb、bitnami/wordpress、elasticsearch、nginx），首次部署通过 `scripts/setup_webmall.sh` 从备份恢复数据。
- `docker/webmall/` 包含 wp-config 模板、URL 修复脚本、前端入口页。
- `docker/onlyoffice/onlyoffice_benchmark_utils.py` 同步拷贝到 `src/stages/`（pipeline 作为 Python 模块 import）。

## 6. 资源

- `scripts/download_resources.py` 支持 `--source huggingface | local`，本地模式便于 U 盘离线迁移。
- HF dataset 需要 maintainer 提前上传，默认 repo 名 `your-org/ParaGUIBench-resources` 是占位。

## 7. 已知待办 / 风险

- ~~**WebMall 商城镜像未开源**~~：已改为多服务架构，直接使用上游镜像 + 备份数据恢复。见 `scripts/setup_webmall.sh`。
- ~~**`scripts/rewrite_task_urls.py` 尚未实现**~~：已实现（187 行），支持 `--from` / `--to` 参数替换任务 JSON 中的 URL。
- **部分 pip 包较重**（formulas、schedula、opencv-python 等），requirements.txt 未精简。
- **`coding_00N_*_evaluator.py` 已排除**：这些 evaluator 不在 run_ablation 路径内，如后续要支持 coding pipeline 需要单独补回。
- **运行期 smoke test 未跑通**：开发机无 Docker/LLM Key，仅做了 import-level 验证（4/5 pipeline 导入成功；operation 需 `pip install formulas`）。建议在目标 Linux 机器上完成首个任务 smoke run。

## 8. 目录最终体积

```
ParaGUIBench/
  src/  ≈ 8.3 MB（1000+ py 文件，含 249 个任务 JSON）
  docker/ ≈ 520 KB
  configs/ / scripts/ / docs/  < 100 KB 合计
  total ≈ 8.5 MB（不含 resources/）
```

原内部仓库 1.5 GB → 开源版 8.5 MB，保留 ~0.56%。
