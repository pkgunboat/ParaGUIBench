# Evaluation protocol / 评价协议

ParaGUIBench keeps Agent execution and task evaluation as two independent outcomes.
This distinction prevents an evaluator failure or missing runtime asset from being
reported as an Agent task failure.

ParaGUIBench 将 Agent 执行结果与任务评价结果分别记录，避免把评价器异常或运行资产缺失错误地
记作 Agent 任务失败。

## Stable outcomes

| Surface | Terminal outcomes | Meaning |
|---|---|---|
| Execution | `SUCCEEDED` | The Agent returned a structurally valid result. Evaluation may still fail or error. |
| Execution | `FAILED`, `TIMED_OUT`, `CANCELLED` | The Agent did not complete normally. |
| Execution | `INFRA_ERROR` | Environment preparation, lifecycle, or cleanup failed. |
| Evaluation | `PASSED`, `FAILED` | The evaluator completed normally; a score is available. |
| Evaluation | `ERROR` | The evaluator ran but raised an error; no score is recorded. |
| Evaluation | `UNAVAILABLE` | The required evaluator or dependency is not available. |
| Evaluation | `NOT_REQUESTED` | Evaluation did not run, usually because execution or infrastructure failed first. |

Only `PASSED` and `FAILED` may carry a numeric score. `ERROR`, `UNAVAILABLE`, and
`NOT_REQUESTED` must not be encoded as `score=0`.

只有 `PASSED` 与 `FAILED` 可以携带数值得分。`ERROR`、`UNAVAILABLE` 和
`NOT_REQUESTED` 不得用 `score=0` 伪装。

## Public-preview support status

The per-task runtime-support manifest is authoritative for public-package readiness:

- `live_validated` means the declared task–Agent–environment–evaluator combination has
  completed an end-to-end run with required assets in place.
- `blocked` means the canonical task remains published, but one or more explicit blocker
  codes prevent a runnable claim.

Evaluator unit tests, parity checks, canonical schema validation, and installation checks
are necessary evidence, but none of them independently makes a task `live_validated`.

逐任务 runtime-support manifest 是公开包可运行范围的权威来源。评价器单元测试、parity
检查、canonical schema 校验和安装验证都是必要证据，但任一项单独通过都不能把任务标记为
`live_validated`。

## Evaluator families

The preview contains versioned protocol identifiers for answer-based evaluators,
OSWorld-compatible state checks, operation rules, WebMall bookmark/cart/checkout
evaluation, web-navigation bookmarks, and structured pipeline evaluation. The website
publishes only protocol identifiers, localized labels, support status, and blocker
codes. It never exports task instructions, expected answers, profile values, URLs, or
fixture contents.

当前预览版为答案评价、OSWorld 兼容状态检查、操作规则、WebMall 书签/购物车/结账评价、
网页导航书签和结构化流水线评价保留版本化协议标识。官网只公开协议标识、本地化标签、支持状态
和阻塞代码，不导出任务正文、预期答案、profile 值、URL 或 fixture 内容。

## Evidence storage

Each attempt stores execution and evaluation summaries under one stable
`run_id/task_id/attempt_id` boundary. Event streams identify their producer
(`runtime`, `environment`, `worker`, or `evaluator`), while persisted payloads use
allowlists and sanitization.

See [the RunStore ADR](../adr/0002-task-scoped-runstore.md) and the
[sanitized reference run](../reproduction/reference-run-20260729.md).
