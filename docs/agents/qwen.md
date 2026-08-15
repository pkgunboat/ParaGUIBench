# Qwen 3.7 GUI worker

当前实现提供一个可复用的 `QwenGUIWorker`，并分别由
`QwenGUIOnlyAgentSystem` 和 `GUIWorkerParaGUIAdapter` 装配为完整任务与 ParaGUI
subtask。Python 包名保持为 `qwen`，具体模型 ID 由配置决定；开发可使用
`qwen3.7-flash` 浮动别名，正式 benchmark 应固定
`qwen3.7-flash-2026-07-15`，并由 RunStore 记录实际模型 ID。

这一路径参考了 OSWorld 固定提交
[`091f5ef`](https://github.com/xlang-ai/OSWorld/tree/091f5ef1d5544bc74953c77875d5feb5bed30108/mm_agents/qwen)
中的截图缩放、0–999 相对坐标与 `computer_use` 动作语义，但没有直接执行模型生成的
Python 字符串。模型输出先转换为公共 `GUIAction`，再由固定白名单模板编译为
shell-free guest argv。原生 Function Calling 是主路径；OSWorld XML 只作为严格的单
tool-call 兼容路径。无法解析、多个 tool call、越界坐标、非有限数值、未知字段和终端
启动快捷键均 fail-closed，不会被默认解释为任务完成。
已分类的响应解析或动作契约偏差会消耗当前步数并以脱敏
`rejected_action` 历史重试；凭据、网络、provider 或未知异常仍立即传播，不会被
重试机制掩盖。

`--qwen-tool-protocol native` 是默认模式：请求通过 API `tools` 发送
`computer_use` schema；默认的非 thinking 配置会强制调用该函数，响应只接受
恰好一个 native `tool_call`。`--qwen-tool-protocol osworld_xml` 不发送
`tools`、`tool_choice` 或 `parallel_tool_calls`，而是把同一 schema 嵌入 system
prompt，响应只接受正文中的恰好一个 `<tool_call>`。两种协议显式互斥，
解析失败时不会自动跨协议回退，避免把异常文本误执行为 GUI 动作。

上述 OSWorld 固定提交中的示例面向 Plus 系列，而不是 Qwen 3.7 Flash，因此该提交
只能作为协议、坐标和动作语义参考，不能作为 Flash 已通过 OSWorld 实机验证的
证据。此外，OSWorld 上游会保留并回放多张历史截图及历史模型响应。ParaGUIBench
每一步仍重新构造请求，但 Qwen worker 默认会按旧到新回放最近 2 张历史
截图，可显式设为 0–4 张。历史图是只读语境，当前截图始终放在最后，坐标也只允许
应用于当前图。请求不回放历史动作参数、模型原文或隐藏推理，只保留最近的脱敏动作名。
因此该实现能支持跨视图比较，但仍不等价于 OSWorld 的完整会话回放策略。

## GUI-only 验证

Qwen 当前属于 **contract-tested experimental** 路径。runtime support
manifest 当前为 0 个 `live_validated`；Seed18 早期冒烟运行也须先按新版本门禁复验。先按
[OSWorld Linux 部署说明](../deployment/osworld-linux.md)完成镜像、资产、端口和
secret-reference 门禁，再显式选择 Qwen。

截至 2026-07-31，真实 DashScope `qwen3.7-flash` 已通过文本、当前截图和原生
Function Calling 契约验证；固定 OSWorld VM 上的非 thinking 与 thinking
GUI-only 基线也都到达 `execution=SUCCEEDED`。但两次对代表任务的答案均未命中
exact evaluator，因此这些结果只证明 endpoint、协议和完整运行链路可执行，不证明
模型任务准确率。增加 4 张历史图后的受控复测在 12 步终止，执行仍成功，但答案仍为
`strict_exact_no_match`。因此有界多图链路已获得实机执行证据，但它没有在该样本上解决
模型准确性问题，上述三次结果都不构成 `live_validated`。
在完全相同的非 thinking、4 张历史图和 24 步配置下，仅将模型替换为
`qwen3.7-plus` 的 A/B 复测在 13 步终止，仍为
`execution=SUCCEEDED` 与 `strict_exact_no_match`。这一个样本不足以比较两个
模型的整体能力，但表明单纯从 Flash 替换为 Plus 并未解决该跨 PDF 任务。

截至 2026-08-01，`kimi-k2.6` + `qwen3.7-flash` 单 VM 串行路径也已在
同一代表任务上完成实机执行。总步数为 14，Agent 终止为 `partial`，
runtime 为 `execution=SUCCEEDED`，最终评价为 `strict_exact_no_match`。这证明
Kimi Function Calling、顺序租约、Qwen GUI 执行、Kimi synthesis 和 evaluator 链路
可运行，但不证明任务准确率，也不构成 `live_validated`。

```bash
export PARAGUIBENCH_MODEL_ID="qwen3.7-flash-2026-07-15"

paraguibench run \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --agent-system gui-only \
  --worker qwen \
  --model "$PARAGUIBENCH_MODEL_ID" \
  --qwen-visual-history 2 \
  --max-history-image-pixels 1048576 \
  --qwen-tool-protocol native
```

API key 和 endpoint 的值仍只能由默认的
`PARAGUIBENCH_MODEL_API_KEY`、`PARAGUIBENCH_MODEL_BASE_URL` 环境变量提供，或用
`--api-key-env`、`--base-url-env` 指向其他变量名。CLI 不接受 secret 值参数，也不记录
模型原始回复或截图。Qwen 默认 `enable_thinking=false`、单步最多 1024 输出 token、
当前图最多 4194304 像素、历史窗口 2 张，且每张历史图最多 1048576 像素。
历史图预算只能向下调，不能超过该硬上限；当前图预算更小时，历史图还会受它二次限制。
默认单步最多发送 3 张图（当前 1 张加历史 2 张），显式设为 4 张历史时最多为
5 张图。按像素预算计算，两种上限分别约为 6291456 和 8388608 像素/步；
这是输入边界而非服务商计费单位。每张原图和重编码图另有 25 MiB 上限，多图请求的
聚合体积仍可能明显高于单图。可以显式覆盖其他成本参数，但便宜验证不建议开启
thinking。

“不记录”不等于“不外发”。每个模型步骤都会把完整任务或 ParaGUI 子任务指令、
当前重新编码的桌面截图、有界的重编码历史截图、当前步号和有限个动作名称发送到配置的外部模型
endpoint；ParaGUI 子任务还可能把成功依赖的输出作为 evidence 包含在指令中。
截图可能包含网页、文档、账户或用户数据，部署者必须确认这些内容允许交由模型服务
处理，并单独核对服务方的数据保留条款。本实现不会向模型发送 gold answer、API key、
endpoint credential、历史动作参数或历史模型原文，RunStore 也不会默认持久化截图和
原始响应。测试 VM 中不应存放真实账号、凭据或个人数据。
Kimi planner 与 synthesis 的额外外发字段见
[`kimi-qwen-single-vm.md`](kimi-qwen-single-vm.md)。

## ParaGUI worker 装配边界

ParaGUI adapter 既可作为库接口使用，也已进入实验性
`paragui-single-vm` CLI。该 CLI 使用 `DAGScheduler(max_workers=1)` 和
`SingleVMEnvironmentLeaseAdapter`，把 Kimi 生成的步骤收紧为全先驱线性链。
每个 subtask 虽创建独立 Qwen worker，但任一时刻只有一个 worker 租用同一个
已准备桌面。这是单 VM 串行验证路径，不是多 worker 并发 runtime。

```python
from paraguibench.agents.systems.paragui import GUIWorkerParaGUIAdapter
from paraguibench.agents.workers.qwen import QwenGUIWorker, QwenModelConfig

config = QwenModelConfig(
    base_url="https://<workspace-endpoint>/compatible-mode/v1",
    model="qwen3.7-flash-2026-07-15",
    api_key_env="PARAGUIBENCH_MODEL_API_KEY",
)
worker_adapter = GUIWorkerParaGUIAdapter(
    worker_factory=lambda: QwenGUIWorker(config=config),
)
```

每个 subtask 会创建独立 worker；成功依赖的输出按 DAG 声明顺序附加为 evidence，并明确
标记为数据而非可覆盖系统策略的命令。`finished` 映射为 `SUCCEEDED`，`infeasible`、
`call_user` 和 `max_steps` 映射为带稳定 failure type 的 `FAILED`。多 VM
environment pool、每 worker 独立端口门禁、真正并发调度和多环境状态
evaluator 仍未实现；当前一次规划、强制线性依赖的路径也不等价于论文中
的完整自适应 ParaGUI 复现。

Qwen 3.7 Flash 的模型能力与 OpenAI-compatible 调用约定见阿里云官方
[模型页](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)、
[Chat Completions 文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
和 [Function Calling 文档](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。
