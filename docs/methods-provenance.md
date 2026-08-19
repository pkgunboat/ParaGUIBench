# 原项目方法迁移记录（GUI-Only 与 ParaGUI 权威实现）

## 基线

- 迁移源：私有 dev 仓库 `ParaGUIBench-dev`（GitHub `pkgunboat/ParaGUIBench-dev`），
  基线提交 `8d36e157`（2026-06-14 "test: 补 precision 分母回归测试"）。
- 迁移基准是其**工作树**状态：相对 `8d36e157` 仅有一组任务数据差异——删除 5 个
  实验性 Coding 任务 JSON（`Operation-FileOperate-Coding-001..005`）、
  `tasks/id_mapping.json` 与 `tasks/subsets/by_subtype/operation_fileoperate_coding.txt`
  相应更新（与公开版 233 正式任务口径一致）。
- 迁移日期：2026-08-19；迁移方式 `rsync`，除下述排除项外**零改动**。
  一致性由 `tests/methods/test_parity.py` + `tests/methods/parity_manifest.json`
  锁定：任何对迁移文件的修改都会使测试失败，必须显式更新清单并在本文件追加记录。

## 迁入内容（保持原路径与 import）

- `src/parallel_benchmark/`：`parallel_agents/`（PlanAgentThoughtAction、
  Qwen3GUIAgent、KimiGUIAgent、seed18、model_adapters）、
  `parallel_agents_as_tools/`（BaseAgentTool、AgentToolRegistry、9 个 GUI agent
  tool、tool_definitions）、`prompts/`、`utils/`、`config/`、`tasks/`、`eval/`、
  `dataviewer/`（planner 硬依赖）。
- `src/desktop_env/`：`controllers/`（PythonController）、`providers/docker/`
  （ContainerSetConfig、端口分配、容器重建/init、sshfs 共享目录、MemoryGuard）、
  `server/`（guest agent server 源，镜像内已部署）、`evaluators/` 等（OSWorld
  派生，Apache-2.0，署名见该目录 README）。
- `src/stages/`：五类 runner（qa / webmall / webnavigate / self_operation /
  searchwrite 的 parallel 版与原始版）及 `webmall_eval_assets/` 等评测资产。

排除项（不属源码）：`__pycache__`、`logs/`、`hf_data/`、`hf_data_staging/`、
`self_operation_pipeline/gt_cache/`（运行时缓存/数据下载，已加入 .gitignore）。

## 权威入口

```
python -m paraguibench.methods_runner <category> [原 runner 参数...]
# 或安装后：paraguibench-methods <category> ...
```

类别：`qa` / `webmall` / `webnavigate` / `self_operation` / `searchwrite`。
装载器（`src/paraguibench/methods_runner/launcher.py`）只做凭据 fail-fast 检查与
`runpy` 原样执行，不改动原 runner 行为。公开 CLI（`paraguibench run` 等）保留为
开源发布面；两个方法的权威实现以本目录迁移代码为准。

## 运行环境映射（不改代码，只设环境变量）

| 用途 | 环境变量 | 说明 |
|---|---|---|
| GUI worker（qwen）凭据 | `DEERAPI_API_KEY` 或 `DASHSCOPE_API_KEY` | 指向阿里云兼容端点时，把 key 同时设给 `DEERAPI_API_KEY`，`DEERAPI_BASE_URL` 设为阿里云 compatible-mode `/v1` |
| Planner 凭据 | `DEERAPI_API_KEY` + `DEERAPI_BASE_URL` | 同上（planner 走 OpenAI-compatible chat + tools） |
| Planner 模型 | `BENCH_DEFAULT_PLAN_AGENT` | 例：`Qwen-3.8-Max`（精确 ID 首跑校准） |
| GUI worker 模型 | `BENCH_DEFAULT_QWEN_GUI_AGENT` | 例：`qwen3.7-plus` |
| 方法选择 | `ABLATION_AGENT_MODE` / `--agent-mode` | `plan`（ParaGUI）或 `gui_only` |
| GUI agent 选择 | `ABLATION_GUI_AGENT` / `--gui-agent` | `qwen` |
| 跳过 conda 检查 | `REQUIRED_CONDA_ENV_STRICT=0` | 服务器 venv 部署时使用 |

## 已验证的可运行组合（内部执行主机，地址见仓库外运维记录）

- Docker 镜像：`happysixd/osworld-docker-sshfs`（digest
  `sha256:3be783bb…`，dev 库在 `happysixd/osworld-docker` 基础上装 sshfs/proxy
  依赖构建，`ensure_docker_image_with_sshfs`）。
- qcow2：历史 6d 镜像（`~/.local/share/paraguibench/osworld/6d8056d8…/Ubuntu.qcow2`），
  guest 内 pyautogui/pyperclip 已实测可用（2026-08-19 探针）。
- 注意：开源默认 6bf archive 镜像 guest 内缺 pyautogui，打字类任务不可用；
  两个组合内容不同（见 README 的镜像身份说明）。

## 与公开实现的关系

公开树原有 `agents/workers/qwen`、`agents/systems/paragui/kimi.py`（单 VM 顺序
ParaGUI）等重写实现保留为开源发布面，但**不是**原项目方法；论文口径的两个方法
以本文件迁移的代码为权威。后续若需要把原方法接入公开 RunStore/版本向量，另行
立项并更新本记录。
