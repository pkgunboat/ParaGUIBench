# ParaGUIBench

[English](README.md) | **简体中文**

论文 **《Beyond Sequential Interaction: Benchmarking Parallel Execution and Coordination for
GUI Agents》**（超越串行交互：GUI 智能体并行执行与协调基准）的官方项目仓库。

> [!IMPORTANT]
> 当前版本是 **0.1 preview**，并非完整基准运行时。233 个 canonical 任务定义已经全部迁入，
> 但 runtime support manifest 目前仅将 GUI-only Seed18 与
> `InformationRetrieval-FileSearch-Readonly-001` 的组合标记为 `live_validated`。任务定义
> 已发布不代表其环境、资产、评价器和 Agent System 已能在当前 preview 中执行。

## 项目概述

现有 GUI 智能体通常以串行“感知—决策—动作”循环执行长时程任务。ParaGUIBench 研究多个 GUI
智能体能否在隔离桌面实例间协调可分解工作负载，在不弱化任务级评价的前提下缩短关键路径。

仓库将可复用的协调机制与可运行的 Agent System 明确分离：

- `framework` 定义与模型提供方无关的 DAG 契约和有界调度机制。
- `agents/systems` 保存可运行策略。GUI-only Seed18 纵向切片是当前真实运行门禁；ParaGUI
  planner–worker 组件仍在继续集成。
- `benchmark` 保存 233 个 canonical JSON 定义、release 完整性记录、任务资产 manifest、
  schema、provenance 和逐任务 runtime support manifest。
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
| 已真实验证任务 | 仅 `InformationRetrieval-FileSearch-Readonly-001` |
| 阻塞任务 | 232 个；runtime support manifest 为每项记录明确 blocker code |
| 已真实验证 Agent System | GUI-only Seed18，单 VM、单 worker |
| 参考部署 | 执行 `SUCCEEDED`、评价 `PASSED`、分数 `1.0` |
| WebMall 可移植性 | logical URL、guest 目录绑定和版本化合成 checkout fixture 已完成；完整 WebMall runtime 尚未真实验证 |
| 后续发布工作 | 其余资产、环境适配器、评价器、Agent System、套件指标、许可审计及分类级真实验证 |

runtime support manifest 是当前可运行范围的机器可读权威记录。标记为 `blocked` 的条目仍是有效
canonical 基准定义，但 CLI 不应将其表述为已支持任务。

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

模型凭据必须通过 secret manager，或 checkout 外部且仅所有者可读写的文件注入。CLI 只读取
`PARAGUIBENCH_MODEL_API_KEY` 与 `PARAGUIBENCH_MODEL_BASE_URL` 两个环境变量引用，不提供直接
接收其值的命令行选项。`.env.example` 仅用于说明变量名，程序**不会自动加载**该文件。

固定 VM、任务资产、十项 `doctor` 门禁、真实运行命令和安全查看流程见
[OSWorld Linux 部署说明](docs/deployment/osworld-linux.md)；经过脱敏的成功部署证据见
[reference-run-20260729.md](docs/reproduction/reference-run-20260729.md)。

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
checkout fixture 值不属于默认日志契约。运行目录与资产缓存应位于源码 checkout 外；若误建在
仓库内，也会被 `.gitignore` 排除。

## 文档

- [安装说明](INSTALL.md)
- [中文安装说明](docs/installation/zh-CN.md)
- [安装排障](docs/installation/troubleshooting.md)
- [OSWorld Linux 部署](docs/deployment/osworld-linux.md)
- [参考真实运行](docs/reproduction/reference-run-20260729.md)
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
的任务或环境。OSWorld 镜像摘要已在参考部署中完成验证，但再分发边界与分层许可仍在审计。
打包或重新分发外部资产前，请先查看
[third-party-sources.md](docs/licenses/third-party-sources.md)。
