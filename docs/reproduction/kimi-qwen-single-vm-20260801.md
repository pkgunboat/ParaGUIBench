# Kimi + Qwen 单 VM 实验运行记录（2026-08-01）

本记录保存 `kimi-k2.6` planner + `qwen3.7-flash` GUI worker 在
`InformationRetrieval-FileSearch-Readonly-001` 上的首次单 VM 串行实机证据。
它是实验路径的功能验证，不是 runtime support manifest 的
`live_validated` 参考结果。

## 部署与前置门禁

运行从新的隔离源码目录和 Python 3.12 venv 开始，没有覆盖既有
Seed18、Qwen Flash 或 Qwen Plus 部署。安装 `.[live,dev]` 后完成：

- `pytest`：240 项全部通过；
- release validator：233 个 canonical task 通过；
- runtime support validator：2026-08-01 当时的旧清单输出为 1 个
  `live_validated`、232 个 `blocked`；该旧运行缺少 RunStore v2 版本向量，
  当前清单已保守降级为 0 个 `live_validated`、233 个 `blocked`；
- repository security scanner：420 个候选文件未发现高置信度凭据或内部路径。

不启动 VM 的真实 Kimi 探针强制调用
`emit_sequential_plan`，服务返回了唯一 function call，本地严格解析为
2 个有界顺序步骤。该证据验证的是 Function Calling，不是
`response_format` 的偶然 JSON 输出。

`doctor` 首先拒绝了一份 SHA-256 不匹配的本地 qcow2 候选，且没有启动
容器。切换到与 `image-manifest.json` 固定摘要一致的外部缓存后，
Python、KVM、Docker daemon、容器镜像、qcow2、任务资产、两个 loopback
端口、API key 引用与 HTTPS base URL 共十项全部为 `PASS`。

## 运行配置与结果

Run ID 为 `run-kimi26-qwen37-sequential-20260801-001`，公开配置为：

| 字段 | 值 |
|---|---|
| Agent System | `paragui.kimi_qwen.sequential_single_vm` |
| Planner | `kimi-k2.6` |
| Planner protocol | 原生强制 Function Calling |
| Planner 最多 subtask | 4 |
| Scheduler | `max_workers=1` |
| Environment | 1 个持久 OSWorld VM |
| Worker | `qwen3.7-flash` |
| Worker protocol | 原生 `computer_use` Function Calling |
| Worker 单 subtask 步数上限 | 12 |
| Qwen 视觉历史 | 4 张，每张最多 1048576 像素 |
| Thinking | 关闭 |

| 结果 | 值 |
|---|---|
| Execution outcome | `SUCCEEDED` |
| Agent termination | `partial` |
| 总 Qwen 动作步数 | 14 |
| Evaluation outcome | `FAILED` |
| Score | `0.0` |
| Match type | `strict_exact_no_match` |

`execution=SUCCEEDED` 表示 Kimi 规划、单 VM 租约、Qwen GUI 执行、Kimi
synthesis 和现有 evaluator 纵向链路没有基础设施异常。`partial` 表示
至少一个 subtask 没有以 `finished` 完成；当前默认日志不持久化模型原文、
subtask evidence 或逐 subtask 结果，因此本记录不对具体失败步骤做无证据
归因。Kimi 仍完成了最终汇总，但答案未命中 exact evaluator。

这一次样本不能用来比较 Kimi、Qwen Flash、Qwen Plus 或 Seed18 的整体能力，
也不能推导 ParaGUIBench 成功率或关键路径加速。运行请求的
`qwen3.7-flash` 是浮动别名，且当前工作树尚未固定为 Git commit，所以该记录
也不构成严格可复现的 benchmark run。

## 隐私与资源清理

凭据只以隐藏输入进入一次性子进程，运行后立即从 shell 状态清除。
源码部署与本次 RunStore 的凭据前缀命中数均为 0，RunStore 中 endpoint
值命中数为 0。部署目录只含不带值的 `.env.example`，没有含真实值的
`.env`。Run 目录权限为 `0700`，`run.json` 权限为 `0600`。

运行结束后，本项目 owned container 数为 0，两个本次 loopback 端口的
监听数为 0。这些证据只覆盖本次运行拥有的资源，不会扫描或终止其他
用户的容器、QEMU 进程或 VM。
