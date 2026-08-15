# Kimi + Qwen 单 VM 串行 ParaGUI

`paragui-single-vm` 是 ParaGUIBench 0.1 preview 中的实验性 Agent System：
`kimi-k2.6` 负责一次规划和最终答案汇总，Qwen 3.7 GUI worker 负责
在一个持久 OSWorld VM 中依次执行子任务。该路径已通过无网络纵向
契约测试，并于 2026-08-01 完成一次实机执行；但该运行与 Seed18
早期冒烟运行都缺少当前必需的 RunStore v2 版本向量。runtime support
manifest 因此当前为 0 个 `live_validated`。

运行链路为：

```text
AttemptRunner.start/prepare
└── Kimi Function Calling: emit_sequential_plan
    └── StructuredParaGUIPlanner
        └── 全先驱线性依赖链
            └── DAGScheduler(max_workers=1)
                └── SingleVMEnvironmentLeaseAdapter
                    └── QwenGUIWorker × N（严格串行）
└── Kimi Function Calling: emit_final_answer
└── 现有 task evaluator
└── AttemptRunner.close
```

Kimi 必须通过强制具名的原生 Function Calling 返回唯一
`emit_sequential_plan` 或 `emit_final_answer`。实现不依赖
`response_format=json_object`：即使某次请求恰好返回 JSON，阿里云部署的
`kimi-k2.6` 也不保证 structured output。所有 tool arguments 都会在本地
二次校验：最多 1–6 个节点、ASCII 安全且唯一的 ID、无额外字段与有界
指令/答案长度。模型只返回 ID 与指令，runtime 会把第 N 步强制设为依赖
全部前 N-1 步，从而确保后续 worker 能按稳定顺序获得累计 evidence。

每个 subtask 都创建一个新的 `QwenGUIWorker`，但租用同一个已准备的
VM，同时活跃租约上限为 1。这种设计可以验证 planner–worker–synthesis 接口
和子任务传递，但不会缩短关键路径，也不能当作论文多 VM 并行 ParaGUI
的复现。当前 planner 还是一次性静态计划，没有在 worker 执行期间自适应重规划。

## 配置与成本边界

CLI 只接受环境变量名，不接受 API key 或 endpoint 值。planner 默认复用
`PARAGUIBENCH_MODEL_API_KEY` 与 `PARAGUIBENCH_MODEL_BASE_URL`，也可用
`--planner-api-key-env` 和 `--planner-base-url-env` 指向另一对环境变量名。
完整命令见 [OSWorld Linux 部署说明](../deployment/osworld-linux.md)。

`--planner-max-subtasks` 默认为 4；`--max-steps` 是每个 Qwen subtask 的独立上限。
因此默认最坏情况可接近 4 条 GUI 轨迹，再加一次 Kimi 规划和一次 Kimi
汇总；它不应与单条 GUI-only 轨迹的调用成本直接等同。浮动别名
`qwen3.7-flash` 适合低成本功能验证；严格 benchmark 需要固定快照，或另行
记录服务端实际版本。

## 数据、日志与评价边界

Kimi 规划请求会外发 task ID、instruction 和可选启动上下文；汇总请求
还会外发按顺序排列的 subtask 状态、输出、步数与稳定失败类型。
Qwen 请求会外发 subtask 指令、依赖 evidence、当前截图、有界历史截图、
步号和脱敏动作名。“不持久化”不等于“不外发”，部署者必须核对模型服务
数据条款，并确保测试 VM 中没有真实账号、凭据或个人数据。

RunStore 只记录 planner/worker 模型 ID、环境变量名、成本上限与稳定终态；
不默认持久化 key、endpoint 值、截图、原始模型响应、subtask evidence 或最终
答案。最终只有 Kimi 汇总的文本会被现有 task evaluator 评价；subtask evidence 不单独
计分。所以 `execution=SUCCEEDED` 只表示链路执行完成，不表示答案正确；只有
`evaluation=PASSED` 与 `score=1.0` 才表示该任务、模型和配置组合在该次
Attempt 中命中评价协议，仍不能推导整体 benchmark 准确率。

本次实机结果为 `execution=SUCCEEDED`、`termination=partial`、
`evaluation=FAILED`、`score=0.0`，匹配类型是 `strict_exact_no_match`。
详细脱敏证据见
[`kimi-qwen-single-vm-20260801.md`](../reproduction/kimi-qwen-single-vm-20260801.md)。
