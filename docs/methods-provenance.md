# 原项目方法迁移记录（GUI-Only 与 ParaGUI 权威实现）

## 基线

- 迁移源：私有 dev 仓库 `ParaGUIBench-dev`（GitHub `pkgunboat/ParaGUIBench-dev`）。
- **当前基线：dev 库 main `028ddd0f`（2026-08-22）**。2026-08-19 首次迁移时的基线是
  `8d36e157` 工作树；此后经两轮基线推进（2026-08-21 的评价器审计修复同步、
  2026-08-22 的源修复线合并同步），见下两节。
- 首次迁移日期：2026-08-19；迁移方式 `rsync`，除排除项外**零改动**。
  一致性由 `tests/methods/test_methods_parity.py` + `tests/methods/parity_manifest.json`
  锁定：任何对迁移文件的修改都会使测试失败，必须显式更新清单并在本文件追加记录。

## 基线推进（2026-08-22，源修复线合并同步）

2026-08-21 同步之后发现，dev 库服务器上还存在一个未提交的工作区
（`ParaGUIBench-eval-audit-fix-20260714`），保存着 2026-07-14 至 07-28 期间完成的
**完整**评价器修复工作，覆盖面显著大于 2026-08-21 同步进来的两条修复线。dev 库已
于 2026-08-22 以该修复线为权威基线合并进 main（`7fe05d3e`），另两条线独有且未被
覆盖的修复逐项补回，未做回退。本仓库同步跟进。

同步范围：锁定树内 150 个变更路径——139 个覆盖、11 个新增、0 个删除。
parity manifest 条目 908 → 919。新增的 11 个全部是评价器源码：`metrics/image.py`、
`active_tab_evaluator.py`、`active_tab_probe.py`、`answer_extraction.py`、
`external_asset_repair.py`、`json_object_metric.py`、`qa_answer_contracts.py`、
`qa_run_contracts.py`、`searchwrite_run_contracts.py`、
`webnavigate_evaluation_router.py`、`webnavigate_url_rules.py`。

需要注意的口径变化：

- **有效分母恢复为全量**：`skip_eval` 任务由 12 个降至 **0 个**。2026-08-21 同步时
  按歧义退出分母的 8 个 OnlineShopping 任务，在本修复线中已通过 07-26 的动态 gold
  重标注给出确定答案（`WEBMALL_DYNAMIC_GOLD_REVIEW_20260726.md`），无需再退出分母。
  233 个任务全部参与评分。
- **WebMall gold 追溯**：8 个重标注任务补齐 `task_revision`、`gold_snapshot_id`
  （`webmall-reannotation-20260726`）、`gold_snapshot_path` 与 `gold_catalog_sha256`；
  后者定义为四店原始目录 JSON 哈希按端口升序以 `端口:哈希` 用 `|` 连接后的
  SHA-256，可从快照文件复算。
- **WebNavigate 评价重构**：由正则匹配改为主机白名单加路径语义组
  （`webnavigate_url_rules.py` / `webnavigate_evaluation_router.py`），判定更严：
  WebNavigate-001 只认月度预报页，011 拒绝 `search.fda.gov` 与搜索引擎结果页。
  Settings-001 由书签评价改为 OSWorld Chrome Profile 名称评价。
- **评价器故障与模型失败分离**：Operation 检查函数抛出的异常按来源分类，agent 产物
  损坏（BadZipFile、文件缺失等）计 FAIL 并保留在分母，其余异常记 `evaluator_error`
  并退出成功率分母；旧 CSV 无 `status` 列时以 `score=-1` 哨兵识别。互斥优先级统一为
  SKIP → EVALUATOR_ERROR → INTERRUPTED → PASS/FAIL。
- **ReadonlyPPT-004 匹配模式**：answer 形如 `match:2,3,5;unmatch:8`，组内页码无序，
  匹配模式由 `exact` 改为 `keyed_numeric_set`，避免组内换序被误判为错误。

无遗留偏离项：2026-08-21 记录的三处偏离（OCR metric 移除、webmall
`timeout_per_subtask`、webnavigate `expected_count`）此前已全部回填 dev main，
本次同步后公开库与 dev 库锁定树逐字节一致。

合并后用服务器上的真实工作簿复核了 BatchOperationExcel-003 的排序检查，发现两条
修复线在该任务上均失效：四个源工作簿的表头位于第 3 行，分别是 `Sales
revenue(yuan)`、`Sales revenue(dollars)`、`营业额（元）`、`营业额（元）`，其一
所用的关键字 `["sales","amount"]` 要求同一单元格同时含两词，四个工作簿无一可达；
另一所用的固定 B 列则自第 1 行起读取，把标题字符串混入数值列而报类型不可比。两者
都会使该任务对所有模型恒为 0 分。现改为 `check_sort_order_by_header_keywords`
支持 `header_keyword_groups` 备选关键字组（任一组全部命中即可），并要求表头行至少
有两个非空单元格，以免命中同样含「营业额」的首行大标题、进而从空行读数据得出
「数据量不足」的假通过；该任务参数设为 `[["sales","revenue"], ["营业额"]]`。dev
库对应提交 `028ddd0f`，中英文表头下已排序判通过、未排序判不通过均已实测。

同步前的公开库原件备份见仓库外
`github_release/backups/20260822-lineC-merge-preimage/`（含两库 git bundle 与还原说明）。

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
