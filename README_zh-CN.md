# ParaGUIBench

[English](README.md) | **简体中文**

论文 **《Beyond Sequential Interaction: Benchmarking Parallel Execution and Coordination for
GUI Agents》**（超越串行交互：GUI 智能体并行执行与协调基准）的官方项目仓库。

> [!IMPORTANT]
> 当前版本是 **0.1 preview**，并非完整基准运行时。233 个 canonical 任务定义已经全部迁入，
> 但 runtime support manifest 目前没有任务标记为 `live_validated`。早期 GUI-only
> Seed18 冒烟运行早于 RunStore v2 版本向量门禁，现仅作为历史证据保留。任务定义
> 已发布不代表其环境、资产、评价器和 Agent System 已能在当前 preview 中执行。

## 项目概述

现有 GUI 智能体通常以串行“感知—决策—动作”循环执行长时程任务。ParaGUIBench 研究多个 GUI
智能体能否在隔离桌面实例间协调可分解工作负载，在不弱化任务级评价的前提下缩短关键路径。

仓库将可复用的协调机制与可运行的 Agent System 明确分离：

- `framework` 定义与模型提供方无关的 DAG 契约和有界调度机制。
- `agents/systems` 保存可运行策略。GUI-only Seed18 纵向切片是首个待执行新版本复验的候选；
  实验性单 VM CLI 已可组合 `kimi-k2.6` planner 与 Qwen 3.7 GUI worker，
  但多 VM ParaGUI runtime 尚未完成。
- `benchmark` 保存 233 个 canonical JSON 定义、release 完整性记录、分别面向 guest
  的输入资产 manifest 和仅供宿主评价器使用的 gold manifest、schema、provenance
  及逐任务 runtime support manifest。
- `runtime`、`evaluation` 与 `integrations` 分别装配任务准备、一次性 OSWorld session、
  确定性评价和环境适配器。
- `runstore` 按 run/task/attempt 保存记录，独立记录执行与评价终态，并采用原子写入和默认脱敏。

ParaGUI 是论文提出的 planner–worker 智能体：它把任务分解为带依赖的计划，将已就绪子任务分派
给并发工作器并汇总结果。论文报告其成功率为 **46.4%**，比该实验中的最强串行基线高
**12.9 个百分点**。这些是论文实验结果；用于完整复现这些数字的实验套件尚未在当前 preview
中达到 `live_validated`。

## 发布状态

| 范围 | Preview 状态 |
|---|---|
| Canonical 基准定义 | 233/233 已迁入，由 `benchmark/manifests/release-v1.json` 固定 |
| Runtime 支持声明 | `benchmark/manifests/runtime-support-v1.json` 含 233 条逐任务记录 |
| 本地组件就绪度 | 233 个 `local_ready`；0 个 `local_components_incomplete` |
| 已真实验证任务 | 0 个；首个带版本向量的复验待执行 |
| 阻塞任务 | 233 个；runtime support manifest 为每项记录明确 blocker code |
| 首个真实门禁候选 | GUI-only Seed18，单 VM、单 worker |
| 实验性 Agent 代码 | Qwen 3.7 Flash GUI-only 与 Kimi+Qwen 单 VM 串行 ParaGUI 已通过契约测试并完成一个样本实机执行，尚未 `live_validated` |
| 历史部署 | 执行 `SUCCEEDED`、评价 `PASSED`、分数 `1.0`；仅作无版本向量的历史证据 |
| WebMall Checkout 切片 | logical URL、版本化 fixture/environment、WP-CLI 订单证据、分布式租约、CLI 绑定与原生评价器已在本地接入；尚无通过的版本化实机 Attempt |
| CombinationDocs-015 评价切片 | 已在本地接入原生 `paraguibench.osworld.artifact-state.v1`、固定输入资产、evaluator-only gold 及 CLI/doctor/source；任务仍为 blocked，等待 manifest 所列四项 runtime 与实机门禁 |
| 后续发布工作 | 私有资产预置、真实环境部署、Agent System、套件指标、许可复核及分类级真实验证 |

runtime support manifest 是两层就绪度的机器可读权威记录。
`local_readiness_status` 表示仓库侧组件是否闭合；`support_status` 仍是正式实机声明。
因此 `local_ready` 任务在固定真实环境门禁与版本化 receipt 完成前仍会是
`blocked`，CLI 不得将其表述为已正式支持。

## 基准概览

| 领域 | 任务类别 | 任务数 | 示例指令 |
|---|---|---:|---|
| 信息检索 | 网页搜索 | 65 | *2024 年出版的《Science》杂志中，有多少期封面出现了鱼？* |
| 信息检索 | 文件搜索 | 12 | *文件夹中的哪些人工智能相关论文使用了卡通风格的插图？* |
| 操作与处理 | 在线购物 | 91 | *查找 Samsung Galaxy S24 Plus 的最低价商品；如果最低价相同，请返回全部结果。* |
| 操作与处理 | 文件操作 | 42 | *根据主题创建子文件夹，并对 Word、PowerPoint 和 Excel 文件进行分类。* |
| 操作与处理 | 网页导航 | 13 | *打开三个 Tesla 车型页面进行比较，并分别将其加入书签。* |
| 操作与处理 | 搜索与写入 | 10 | *将 2025 年 QS 排名前五的学校及其详细信息填写到表格中。* |
| **合计** |  | **233** |  |

## 从源码 checkout 开始

Python 支持范围为 3.11–3.13。真实 OSWorld 运行还需要 Linux x86-64、Docker、当前用户可读写的
`/dev/kvm`、足以容纳 VM 镜像的本地磁盘，以及 OpenAI-compatible 模型服务。

```bash
git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[live,dev]'
python -m pytest
python scripts/benchmark/validate_release.py --repo-root .
python scripts/benchmark/validate_runtime_support.py --repo-root .
python scripts/security/scan_repository.py --root .
```

## 最短评测路径

凭据只放环境变量，不要放进命令行。公网模型 endpoint 必须是 HTTPS；本地服务可用
`http://127.0.0.1:...` 或 `http://localhost:...`。URL 中不要带用户名、密码、query 或 fragment。

```bash
export PARAGUIBENCH_MODEL_API_KEY="..."
export PARAGUIBENCH_MODEL_BASE_URL="https://example.invalid/v1"
export PARAGUIBENCH_MODEL_ID="qwen3.7-flash-2026-07-15"
export PARAGUIBENCH_ASSET_CACHE_ROOT="$HOME/.cache/paraguibench/assets"
export PARAGUIBENCH_GOLD_CACHE_ROOT="$HOME/.cache/paraguibench/gold"
export PARAGUIBENCH_QCOW2_PATH="$HOME/.local/share/paraguibench/Ubuntu.qcow2"
export PARAGUIBENCH_RUNS_ROOT="$HOME/.local/share/paraguibench/runs"
export PARAGUIBENCH_SERVER_PORT=5527
export PARAGUIBENCH_VNC_PORT=8527
export PARAGUIBENCH_CHROMIUM_PORT=9527

# 只测协议，不启动 VM，也不打任务分。
paraguibench model-probe qwen-native

# 真实 OSWorld 需要 Linux x86-64、Docker、/dev/kvm 和 qcow2。
# macOS 可以跑单元测试，不能启动 guest VM。
paraguibench doctor \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT"
paraguibench run \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --agent-system gui-only \
  --worker qwen \
  --model "$PARAGUIBENCH_MODEL_ID"
```

把缓存、qcow2 和端口变量改成你机器上的路径；预置步骤见
[OSWorld Linux 部署说明](docs/deployment/osworld-linux.md)。分数来自 guest artifact
与 typed observation，不使用 Agent 最终文本。gold 只留在 host。

模型凭据必须通过 secret manager，或 checkout 外部且仅所有者可读写的文件注入。CLI 只读取
`PARAGUIBENCH_MODEL_API_KEY` 与 `PARAGUIBENCH_MODEL_BASE_URL` 两个环境变量引用，不提供直接
接收其值的命令行选项。`.env.example` 仅用于说明变量名，程序**不会自动加载**该文件。

WebMall runner 还会通过 manifest 指定的环境变量引用绑定四个 origin、四个
WP-CLI reader target、coordinator URL 和租约 credential；coordinator 在独立进程
边界中接收匹配的 credential。真实绑定和凭据不得进入 checkout 或日志，
完整变量表与命令见 WebMall 部署说明。

固定 VM、任务输入资产、evaluator-only gold 预置、完整 `doctor` 门禁、真实运行命令和安全查看流程见
[OSWorld Linux 部署说明](docs/deployment/osworld-linux.md)。

## 运行记录与隐私边界

RunStore 使用稳定的 `run_id`、`task_id` 和 `attempt_id` 分层：

```text
<runs-root>/<run_id>/
├── run.json
└── tasks/<task_id>/attempts/<attempt_id>/
    ├── task.json
    ├── summary.json
    ├── events/
    └── artifacts/
```

目录权限固定为 `0700`，文件权限固定为 `0600`。执行与评价结果相互独立，避免把评价器失败错误
归因给 Agent。持久化记录采用 allowlist 和脱敏策略；凭据、endpoint 值、模型原始响应和完整
checkout fixture 值不属于默认日志契约。运行目录、面向 guest 的输入资产缓存和 evaluator-only
gold 私有缓存应位于源码 checkout 外；若误建在仓库内，也会被 `.gitignore` 排除。gold 缓存
与输入资产物理及接口分离，不进入 Agent 投影或默认 RunStore 记录。

新 Run 会保存 source、Agent、evaluator、评价协议和环境组成的六字段版本向量。
`paraguibench inspect --diagnostics` 只追加固定版本身份和枚举化失败阶段，不输出自由格式 details。
无外部文件的任务走显式零资产路径；非空 legacy 资产引用在迁移前继续 fail-closed。

SearchAndWrite 中只有 `002`、`004`、`006`、`008` 依赖本机 OnlyOffice
DocumentServer 与 ParaGUIBench share service；其余 6 项仍走 OSWorld / LibreOffice。
第一版只承诺单实例实验室部署。单元测试通过不等于真实编辑服务可用，也不等于这些
任务已经 `live_validated`。部署命令见
[OnlyOffice 单实例部署](docs/deployment/onlyoffice.md)。

## 文档

- [安装说明](INSTALL.md)
- [中文安装说明](docs/installation/zh-CN.md)
- [安装排障](docs/installation/troubleshooting.md)
- [OSWorld Linux 部署](docs/deployment/osworld-linux.md)
- [WebMall Linux 部署与 Checkout 运行](docs/deployment/webmall-linux.md)
- [OnlyOffice 单实例部署](docs/deployment/onlyoffice.md)
- [Qwen 3.7 GUI worker 与验证边界](docs/agents/qwen.md)
- [Kimi + Qwen 单 VM 串行 ParaGUI](docs/agents/kimi-qwen-single-vm.md)
- [架构与依赖方向](docs/architecture/dependency-tree.md)
- [评价协议与结果边界](docs/evaluation/protocol.md)
- [Benchmark provenance](benchmark/provenance/README.md)
- [第三方来源与发布边界](docs/licenses/third-party-sources.md)
- [安全配置示例](configs/examples/README.md)
- [贡献指南](CONTRIBUTING.md)
- [GitHub Pages 源码与本地预览](website/README.md)

## 论文与引用

论文在 arXiv 发布后，我们将在此补充预印本链接和 BibTeX。

## 许可证

本项目原创源代码采用 [Apache License 2.0](LICENSE)。该许可证不会自动覆盖基准数据、任务资产、
VM/容器镜像、模型服务或其他第三方材料。

ParaGUIBench 适配了 OSWorld 的部分评价协议，并使用来源于或改编自 VeriWeb、OSWorld 和 WebMall
的任务或环境。固定上游 OSWorld 归档与历史 reference qcow2 当前对应 guest-visible 内容不同的
镜像。归档直接派生的 6bf 镜像现为新的开源默认 identity，历史 6d 镜像仅作为
独立 legacy identity。schema v2 物化 recipe 和输出摘要已固定，冻结 cleanroom
源码形成的可重现物化证据已经独立审核；每任务 live 门禁仍保持失败关闭，再分发边界与
分层许可也仍在审计。
运行、打包或重新分发外部资产前，请先查看
[OSWorld 环境边界](environments/osworld/README.md)与
[third-party-sources.md](docs/licenses/third-party-sources.md)。
