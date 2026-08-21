# 原项目方法迁移记录（GUI-Only 与 ParaGUI 权威实现）

## 基线

- 迁移源：私有 dev 仓库 `ParaGUIBench-dev`（GitHub `pkgunboat/ParaGUIBench-dev`）。
- **当前基线：dev 库 main `509ad920`（2026-08-21）**。2026-08-19 首次迁移时的基线是
  `8d36e157` 工作树；2026-08-21 dev 库把散落的评价器审计修复收敛进 main 后，
  基线随之推进（见下节"基线推进"）。
- 首次迁移日期：2026-08-19；迁移方式 `rsync`，除排除项外**零改动**。
  一致性由 `tests/methods/test_methods_parity.py` + `tests/methods/parity_manifest.json`
  锁定：任何对迁移文件的修改都会使测试失败，必须显式更新清单并在本文件追加记录。

## 基线推进（2026-08-21，评价器审计修复同步）

2026-07 在 dev 库服务器工作区完成的评价器全量审计修复（233 任务 / 40 任务组 /
7 个评价器深审，368 条发现、51 条确认，含多条 HIGH 级 `BUG_FALSE_PASS`）长期以
未提交状态散落在工作区与未合并分支上，2026-08-19 的迁移因此拿到的是**修复前**
的评价器。dev 库已于 2026-08-21 将其收敛进 main（`docs/merge_report_20260821.md`），
本仓库同步跟进。

同步范围：锁定树内 75 个变更路径——66 个覆盖（其中 64 个哈希实际变化）、
4 个新增、5 个 Coding 任务 JSON 与 1 个 subset 两边早已同步删除。
parity manifest 条目 904 → 908。构成：

- 评价器核心代码 28 文件（+3029/-322）：`osworld_evaluator.py`、
  `operation_checks/{docx,xlsx,file}_checks.py`、`operation_evaluator.py`、
  `file_search_readonly_evaluator.py`、`webnavigate_bookmark_evaluator.py`、
  `desktop_env/evaluators/metrics/*`、7 个 `osworld_scripts/*.json` 评价配置、
  `webmall_eval_assets/` 全套（含新增 `webmall_gold_recovery.py` /
  `webmall_identity.py` / `webmall_run_contracts.py`）。
- 管线与 runner 12 文件（+795/-275）：`pipelines/*`、webmall 与 webnavigate
  runner、self_operation 与 searchwrite 并行 runner。
- 任务数据 36 文件（+260/-148）：19 个任务 JSON 的 gold 修正、`id_mapping.json`、
  两个 subset 清单。

需要注意的口径变化：

- **有效分母**：`skip_eval` 任务由 4 个增至 12 个，新增 8 个全部为 OnlineShopping
  （WebMall），依据 dev 库 `docs/eval_repair_20260714/WEBMALL_DYNAMIC_GOLD_REVIEW_20260714.md`
  判定为歧义任务并退出有效分母。233 个任务的总数不变，但参与评分的任务数相应减少。
- **webnavigate 默认任务集**：runner 内置列表原含 `Operation-WebOperate-Webnavigate-006`，
  但该任务 JSON 在迁移基线与本仓库中从未存在（悬空条目），dev 库已连同任务 ID
  大小写（`Webnavigate`/`settings` → `WebNavigate`/`Settings`）一并修正，
  `TASKS_LIST_DIR` 也由 `tasks_list` 软链改为直接指向 `tasks`。实际可运行任务集不变。

同步前的公开库原件备份见仓库外
`github_release/backups/20260821-eval-sync-preimage/`（含 SHA256SUMS 与还原说明）。

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
| Planner 模型 | `BENCH_DEFAULT_PLAN_AGENT` | 已验证有效 ID：`qwen3.8-max`（2026-08-20 实测；注意 `Qwen-3.8-Max` 写法会被端点报 model_not_found） |
| GUI worker 模型 | `BENCH_DEFAULT_QWEN_GUI_AGENT` | 已验证有效 ID：`qwen3.7-plus` |
| 方法选择 | `ABLATION_AGENT_MODE` / `--agent-mode` | `plan`（ParaGUI）或 `gui_only`；gui_only 模式仅要求 GUI worker 凭据 |
| GUI agent 选择 | `ABLATION_GUI_AGENT` / `--gui-agent` | `qwen` |
| 跳过 conda 检查 | `REQUIRED_CONDA_ENV_STRICT=0` | 服务器 venv 部署时使用 |
| 宿主机 SSH 用户 | `BENCH_SSH_USER` | 宿主机登录名（如 `yuzedong`）；密码经 `BENCH_SSH_PASSWORD` 提供（sudo docker 与 guest sshfs 回挂均使用） |
| VM 就绪等待 | `ABLATION_VM_READY_WAIT` / `..._REBUILT` / `..._PROBE_TIMEOUT` | 高负载宿主机上建议 300/600/10 |

**参数格式注意**：`--vm-memory` 必须带单位（如 `8G`）。无单位的裸数字会被镜像内
QEMU 包装器按 MiB 解释（`-m 8` = 8 MiB），guest 永远无法完成引导。

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

## 记录的偏离（2026-08-20，OCR 度量删除）

按管理者决定，删除了 `desktop_env/evaluators/metrics/docs.py` 的
`compare_image_text` 函数与模块级 `import easyocr`，并从
`metrics/__init__.py` 的再导出中移除同名条目。理由：该函数为 OSWorld 上游
遗留（对文档内嵌图片做 OCR 文字检查），全部 233 个正式任务及 evaluator
配置零引用；保留它会强制所有使用者安装 torch 级重依赖（easyocr）。

**状态更新（2026-08-21）**：该改动已回填 dev 库 main（`509ad920`），
自新基线起不再构成偏离。

webmall runner 读取原运行时目录 ``src/extra_docker_env/tasks``（仅含 91 个
OnlineShopping 任务 JSON，与 ``parallel_benchmark/tasks`` 中同名文件字段一致）。
装载器在 webmall 类别启动时为这些文件建立逐文件相对软链（不进入版本库）。

## 服务栈与第三方部署（2026-08-20 迁入）

方法验证所用的外部服务栈（WebMall 四店 compose、OnlyOffice 共享服务
原版代码）已从原 dev 库 `docker/` 迁入本仓库 `deploy/methods-services/`，
配套脚本在 `scripts/deployment/`（setup/start/stop + 任务 URL 改写）。
镜像获取、HF 任务素材自动下载与服务栈部署的完整第三方指南见
`docs/deployment/methods-services.md`。该目录不在 parity 锁定范围内；
`deploy/onlyoffice/` 是公开 CLI 路径的另一套独立编排，两者勿同时启动。

## 记录的偏离（2026-08-20，webmall 并行 runner 超时传参修复）

`run_webmall_pipeline_parallel.py` 构造 `execute_task` 时漏传
`timeout_per_subtask`（默认 0 在 `BaseAgentTool.execute` 层被当作 0 秒期限，
GUI worker 一轮未跑即超时）。原项目的非并行版与 QA runner 均显式传参
（600/读 ABLATION_SUBTASK_TIMEOUT）。修复为与 QA 并行版一致的
env 读取（默认 3600 秒）。

**状态更新（2026-08-21）**：该修复已回填 dev 库 main（`509ad920`），
自新基线起不再构成偏离。

## 记录的偏离（2026-08-20，webnavigate 并行 runner 汇总日志字段名修复）

`run_webnavigate_pipeline_parallel.py` 末尾任务级汇总日志读取
`match_detail.get("total_targets")`，而 webnavigate 书签评价器
（`parallel_benchmark/eval/webnavigate_bookmark_evaluator.py`）的
`match_detail` 顶层字段名为 `expected_count`——分母恒显示为
`(0/0 URL)`。仅影响日志显示；分数与 PASS/FAIL 判定使用评价器返回的
`score`/`pass`，不受影响。修复为读取 `expected_count`（缺失时仍回退 0，
与 evaluator_error 等无 match_detail 的返回兼容）。

**状态更新（2026-08-21）**：该修复已回填 dev 库 main（`509ad920`）。
批次 A 重写 bookmark 评价器后 `expected_count` 仍是分母字段名，修复继续适用；
自新基线起不再构成偏离。
