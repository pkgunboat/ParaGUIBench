# ParaGUIBench 领域上下文

本文档定义公开仓库中跨 module 使用的领域术语。新增架构概念前应先在此处明确其含义，避免代码、任务数据、论文和运行日志使用不同口径。

## Benchmark

**ParaGUIBench** 是用于评测 GUI Agent 并行执行与跨 worker 协调能力的 benchmark，包括任务语料、运行环境、执行基础设施和评价协议。它不等同于 ParaGUI Agent。

**Benchmark Task** 是一次评价的最小稳定对象。每个 Benchmark Task 具有唯一 `task_id`、版本化任务定义、环境准备协议和评价协议。

**Release Manifest** 是某一公开版本所包含 Benchmark Task 的唯一事实源。首个公开版本固定包含 233 个正式任务；实验性 Coding 任务不计入该版本。

**Task Assets** 是任务开始前必须额外放入环境的文件闭集。`NONE` 表示任务不需要外部任务文件，不等价于环境或 evaluator 已可运行；`PINNED_DOWNLOAD_MANIFEST` 表示文件来源、revision、大小和 SHA-256 已固定。非空 legacy `prepare_script_path` 在迁移前必须 fail-closed。

**Evaluator Gold** 是只允许评价器读取的预期结果闭集，不属于 Task Assets，不能上传到 guest、下发给 Agent 或进入 RunStore。schema-v1 gold 由独立 manifest 固定 revision、大小、SHA-256、媒体类型、许可与 source evaluator provenance，只有显式 `gold fetch` 可联网；schema-v2 private-derived gold 则固定 canonical input、派生工具链、产物及许可身份，只允许受控私有 host 显式 `gold materialize`。`gold verify`、doctor 与真实评价只访问仓库外私有离线缓存。`NONE` 表示任务不依赖 evaluator gold，不会创建空缓存。

**Runtime Protocol Binding** 是一个 Benchmark Task 的 environment protocol、evaluation protocol、实际 environment manifest 与 evaluator implementation 的闭合装配。`doctor` 和 `run` 必须在 probe、凭据读取或 RunStore 写入前完成该绑定；仅有匹配的任务元数据不代表实际 runtime 已装配。

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

**Run Version Vector** 是 Run 身份中独立于自由格式配置的六字段向量：source、Agent code、evaluator、evaluation protocol、environment protocol 和 environment manifest revision。source 闭包还绑定当前 task 的 pinned input asset manifest 与 evaluator gold manifest；实际导入 Python package 必须与所选 repo-root 源码一致。新 Run 必须提供完整固定摘要；旧记录只通过身份完整的 schema 1.0 manifest 标为 `LEGACY_UNVERSIONED`，不得事后伪造。

**Attempt Inspection** 是 RunStore 从身份一致的 Run、Attempt 与 summary 记录产生的 allowlist-only 诊断投影，只包含执行/评价终态、得分、runtime 保留的枚举化失败阶段和 Run Version Vector。summary details、异常消息、模型输出和 evaluator 扩展字段不属于该接口；只读检查不得创建或修改 runs-root。

## Evaluation Evidence

**Evaluator Parity Case Manifest** 是旧/新 evaluator 差分门禁的第三方权威闭包，固定 fixture source revision、双方 evaluator revision 以及每个 case 的 normalized input SHA-256。两侧共同漏掉 case 或共同返回 `UNAVAILABLE` 都不能通过 strict gate。

**Checkout Observation Batch** 是 WebMall Attempt 基线之后，对 environment manifest 声明的完整 logical store universe（当前四店）新增订单的完整枚举；只扫描 gold store 不满足闭集。正式商品身份由可信 evidence adapter 根据 WooCommerce product ID 解析为 canonical slug，不使用 display label。`complete=False`、全局租约 ownership 丢失、同一订单身份的冲突证据或非法商品数量属于 evaluator error。8 个 Checkout 任务只评价订单闭集；8 个 EndToEnd/FindAndOrder 任务还要求 Agent 报告的 logical product URL 多集合精确匹配，并与订单结果做逻辑 AND。checkout profile、runtime URL、订单 ID 与商品原文不得进入公开评价结果。

## Privacy

**Credential Reference** 只表示某项凭据来自哪个受控注入位置以及是否已配置，不包含凭据值、摘要或可用于验证凭据的派生值。

**Sanitized Record** 是经过 allowlist-first 规则处理后允许持久化的结构化记录。完整环境变量、认证请求头、cookie、密码、token、API key、带凭据 URL 和第三方客户端对象禁止进入日志。
