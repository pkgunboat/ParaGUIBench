# ParaGUIBench 领域上下文

本文档定义公开仓库中跨 module 使用的领域术语。新增架构概念前应先在此处明确其含义，避免代码、任务数据、论文和运行日志使用不同口径。

## Benchmark

**ParaGUIBench** 是用于评测 GUI Agent 并行执行与跨 worker 协调能力的 benchmark，包括任务语料、运行环境、执行基础设施和评价协议。它不等同于 ParaGUI Agent。

**Benchmark Task** 是一次评价的最小稳定对象。每个 Benchmark Task 具有唯一 `task_id`、版本化任务定义、环境准备协议和评价协议。

**Release Manifest** 是某一公开版本所包含 Benchmark Task 的唯一事实源。首个公开版本固定包含 233 个正式任务；实验性 Coding 任务不计入该版本。

## Agent

**Agent System** 是能够接收一个 Benchmark Task 并产生完整执行结果的可运行系统。

**ParaGUI** 是基于 planner–worker 结构的 Agent System。planner 负责分解、分派与聚合，worker 在隔离的 GUI 环境中执行 subtask。

**GUI-only Agent** 是不使用 planner、直接完成观察—推理—动作闭环的 Agent System。它可以独立运行，也可以通过 adapter 充当 ParaGUI worker。

**Framework** 是 ParaGUI 与其他 planner–worker Agent System 可复用的调度、并发、结果回传和生命周期机制。Framework 不包含具体模型、任务类别或评价器策略。

**Baseline** 是实验配置中的比较角色，不是 Agent System 的固有类型。

## Execution

**Run** 是在一份固定代码、benchmark manifest、Agent System、配置和环境版本下发起的一次实验运行，由唯一 `run_id` 标识。

**Attempt** 是 Run 内某个 Benchmark Task 的一次执行尝试，由唯一 `attempt_id` 标识。重试产生新的 Attempt；模型、Agent System 或实验条件改变则产生新的 Run。

**Execution Outcome** 描述 Agent 与运行环境是否完成执行。

**Evaluation Outcome** 描述评价协议是否执行以及评分结果。Execution Outcome 与 Evaluation Outcome 必须独立记录。

**Artifact** 是 Attempt 产生的截图、视频、下载文件、计划、轨迹或评价证据。事件日志只引用 Artifact，不内嵌二进制内容。

## RunStore

**RunStore** 是持久化 Run、Benchmark Task、Attempt、事件、Artifact 索引和评价结果的一级 deep module。Framework、Agent、runtime、environment 和 evaluator 均通过 RunStore 的 interface 写入记录。

RunStore 默认以 `run_id/task_id/attempt_id` 组织数据，采用原子写入、版本化 schema、并发隔离和默认脱敏。RunStore 不认识任何具体 Agent implementation。

## Privacy

**Credential Reference** 只表示某项凭据来自哪个受控注入位置以及是否已配置，不包含凭据值、摘要或可用于验证凭据的派生值。

**Sanitized Record** 是经过 allowlist-first 规则处理后允许持久化的结构化记录。完整环境变量、认证请求头、cookie、密码、token、API key、带凭据 URL 和第三方客户端对象禁止进入日志。
