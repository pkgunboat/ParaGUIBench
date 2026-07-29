# ParaGUIBench 依赖关系树

本文档同时记录模块的允许依赖方向、当前实现表面和 Python extras。它描述代码
结构，不等价于 runtime 支持声明；任务能否真实运行仍以
`benchmark/manifests/runtime-support-v1.json` 为准。

## 允许依赖方向

箭头表示“左侧可以导入右侧”。高层装配模块可以依赖多个低层模块，低层模块
不得反向导入调用方。

```text
cli / future experiments
├── runtime
├── benchmark
├── selected agents
├── evaluation
├── integrations
└── runstore

runtime
├── benchmark
├── selected agents
├── evaluation adapters
├── integrations
├── framework contracts
└── runstore

agents
├── agents contracts
├── framework
├── integrations
└── runstore

evaluation
├── benchmark contracts
├── integrations
└── runstore

framework
└── runstore

integrations
└── runstore

benchmark
└── Python standard library

runstore
└── Python standard library
```

`framework` 只提供可复用执行机制，不能拥有 prompt、模型调用、任务类别或
评价逻辑。完整的 planner、worker policy、prompt 和结果合成属于具体
`agents/systems/*`。`runtime` 负责选择并装配 environment、Agent System 和
evaluator；evaluation 不根据 Agent 类型分支。

禁止的依赖包括：

- `runstore` 导入 framework、agents、runtime、evaluation 或具体 provider。
- `framework` 导入具体 Agent System、provider、runtime pipeline 或 evaluator。
- `agents` 导入评价器，或把 credential 值交给 framework。
- `evaluation` 根据具体 Agent 类型分支，或读取模型凭据。
- `benchmark` 依赖部署地址、凭据、开发者绝对路径或运行时进程。
- `integrations` 反向导入 CLI，或以 eager import 初始化所有 provider。

## 当前模块与实现状态

| 模块 | 当前职责 | Preview 状态 |
|---|---|---|
| `paraguibench.benchmark` | release task/fixture 摘要校验、环境绑定、Agent allowlist、trusted/agent/audit 投影 | 233 个 canonical task 可加载；WebMall logical URL、guest binding 和 checkout fixture 已完成可移植化 |
| `paraguibench.framework` | `ExecutionPlan`、`SubtaskSpec`、`SubtaskResult` 和有界 `DAGScheduler` | 单元测试覆盖；不直接创建 VM 或调用模型 |
| `paraguibench.agents` | Agent 统一结果契约及 runnable systems | GUI-only Seed18 已 live-validated；ParaGUI planner–worker 已有严格 DAG 组件，但尚未进入 live-validated CLI 路径 |
| `paraguibench.evaluation` | exact、numeric、keyed numeric set、ordered/structured answer 契约及 WebMall logical URL set | 组件测试已迁入；当前真实任务装配只使用 exact evaluator |
| `paraguibench.integrations.osworld` | loopback controller、argv-only guest 执行、固定 digest Docker session、镜像 manifest | 单 VM 纵向切片已 live-validated |
| `paraguibench.integrations.webmall` | `webmall://store-*` 与部署 origin 的内存映射 | 规范化与单元测试完成；完整 WebMall 服务/状态评价未 live-validated |
| `paraguibench.runtime` | 资产闭集、十项 doctor、环境生命周期、AttemptRunner、评价适配 | 当前装配为单 VM、单 worker、Seed18、exact evaluator |
| `paraguibench.runstore` | run/task/attempt 身份、独立终态、事件流、artifact、原子持久化和脱敏 | 目录 `0700`、文件 `0600`；参考真实运行已验证 |
| `paraguibench.cli` | `assets fetch/verify`、`doctor`、`run`、`inspect` | 只暴露当前 live gate；不接受 secret 值参数 |

当前实际导入链比允许边界更窄。例如，`framework` 目前仅使用标准库；
`integrations.osworld.controller` 在实例化真实 HTTP session 时才加载
`requests`；Seed18 模型 adapter 在需要 live client 时才加载 `openai`。

## 分发与 extras

项目当前是一个 Python distribution，要求 Python 3.11–3.13：

```text
paraguibench
├── core (默认安装)
│   └── 第三方 runtime dependencies: none
├── live
│   ├── openai >=1.82,<3
│   ├── Pillow >=11,<13
│   └── requests >=2.32,<3
├── dev
│   └── pytest >=8.3,<9
└── build-system
    └── hatchling >=1.27
```

- `openai`：当前 OpenAI-compatible Seed18 model adapter。
- `Pillow`：上传模型前的截图尺寸上限处理。
- `requests`：loopback OSWorld controller HTTP client。
- `pytest`：开发与回归测试，不属于 runtime。

后续 Office、WebMall、OnlyOffice 或其他 provider 能力应按功能拆分新的 optional
extra，不能把旧项目的大型统一依赖集合复制到 `core`。每个新 extra 都需要同步
更新依赖许可证清单、lazy import 测试、对应 evaluator/integration 测试和至少
一个分类级 live gate。
