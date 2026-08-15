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

- `local_ready` means every remaining blocker belongs to the explicit live-only
  allowlist; repository-side evaluator, asset, binding, and unresolved-semantic gates
  are closed for that task. It is not live evidence.
- `local_components_incomplete` means at least one repository-side or unresolved
  semantic blocker remains. Unknown future blocker codes fail closed into this class.
- `live_validated` means the declared task–Agent–environment–evaluator combination has
  completed an end-to-end run with required assets in place and a valid RunStore v2
  source/Agent/evaluator/protocol/environment version vector.
- `blocked` means the canonical task remains published, but one or more explicit blocker
  codes prevent a runnable claim.

The current deterministic projection is 233 `local_ready` and 0
`local_components_incomplete`, while formal support remains 233 `blocked` and zero
`live_validated`. Evaluator unit tests, parity checks, canonical schema validation, and
installation checks are necessary evidence, but none of them independently makes a task
`live_validated`.

逐任务 runtime-support manifest 是公开包可运行范围的权威来源。评价器单元测试、parity
检查、canonical schema 校验和安装验证都是必要证据，但任一项单独通过都不能把任务标记为
`live_validated`。旧运行若没有 RunStore v2 版本向量，只能作为历史冒烟证据，
不得进入当前支持计数。

`local_ready` 表示剩余 blocker 全部属于显式 live-only 白名单，不是实机证据；
`local_components_incomplete` 表示仍有仓库组件或未裁定语义门禁，未知新 blocker
也默认归入此类。当前确定性投影为 233 个本地就绪、0 个本地未闭合；
正式支持仍为 233 个 blocked、0 个 live-validated。

Live promotion is a strict three-factor conjunction: the pinned OSWorld image must be
ready, the task's independently derived component-blocker list must be empty, and the
current task/component identity must match a SHA-allowlisted sanitized RunStore-v2
receipt. Receipts never remove component blockers. The image blocker is always first and
the versioned-live blocker is always last on a blocked entry. Receipt persistence is a
closed allowlist of task/run/attempt identity, `SUCCEEDED`/`PASSED`, finite score, the
six-field version vector, and a promotion-safe component revision; Agent final text and
free-form details are neither persisted nor read as evidence.

The task-to-receipt SHA mapping is an external closed data file, restricted to current
canonical task IDs, so the promotion guard itself can enter the component revision
without creating a receipt-digest cycle. Receipt score is restricted to finite `[0,1]`.
The no-follow directory chain is held by anchored descriptors and its exact file set is
revalidated after the bounded read. For WebMall, both the receipt environment revision
and component revision validate the nested OSWorld Chrome manifest reference and SHA.
The generator also rejects a loaded `paraguibench` package that differs from the repo
source it is about to summarize.

Live 晋升是严格的三因子合取：固定 OSWorld 镜像必须就绪，任务组件 blocker
必须先独立派生且为空，同时当前任务/组件身份必须匹配经 SHA allowlist 固定的
脱敏 RunStore-v2 receipt。Receipt 绝不清除组件 blocker；blocked 条目的镜像码始终居首，
versioned-live 码始终居末。Receipt 只保存 task/run/attempt 身份、`SUCCEEDED`/`PASSED`、
有限分数、六字段版本向量和 promotion-safe component revision；Agent 最终文本与自由 details
不持久化，也不作为证据读取。

task→receipt SHA 映射保存在独立字段闭合的数据文件中，且只允许当前
canonical task ID，因此 promotion guard 脚本本身可进入 component revision
而不形成 receipt 摘要循环。Receipt score 必须是 `[0,1]` 有限数。目录链以
nofollow dirfd 锚定，有界读取后复验精确文件闭集。WebMall receipt 的
environment/component revision 同时校验嵌套 OSWorld Chrome manifest 路径与
SHA；生成器也会拒绝与待摘要 repo source 不一致的已加载 `paraguibench`
package。

`operation_word009_010_writer_live_validation_not_completed` records only the
remaining pinned Writer gate. The local private pre/post OOXML evidence path and
adversarial preservation checks are complete; a versioned real Writer run must
still prove the same contract before this blocker clears.
Word-012 now has a task-specific typed production evaluator and no longer carries the
historical local semantic blocker. The global image-materialization blocker is now
cleared, but the task remains formally blocked by its versioned-live gate; local
synthetic or host replay is not promotion evidence.

`operation_word009_010_writer_live_validation_not_completed` 仅表示剩余的固定
Writer 实机门禁。本地私有 pre/post OOXML 证据链及对抗保真检查已闭合；
仍需版本化真实 Writer 运行证明同一契约后才能清除该 blocker。
Word-012 已绑定 task-specific typed production evaluator，不再携带历史本地
语义 blocker；统一镜像物化 blocker 已清除，但其正式状态仍受
versioned-live 门禁阻断。本地合成 fixture 或 host 回放不是晋升证据。

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

## OSWorld Chrome state protocols

Two formerly generic OSWorld state tasks now have distinct native protocols:

- `paraguibench.osworld.chrome-profile-name.v1` is bound only to
  `Operation-WebOperate-Settings-001`. One VM observation comes from the fixed default
  Chrome profile's `Preferences` file. An exact name match passes; a missing or different
  name is a normal Agent failure; an unreadable, malformed, or schema-invalid file is an
  evaluator error.
- `paraguibench.osworld.google-shopping-active-tab.v1` is bound only to
  `Operation-WebOperate-WebNavigate-009`. A single immutable tab snapshot must jointly
  contain an allowed HTTPS Google Shopping query and the exact closed set of selected
  filters. A wrong page, query, or filter set is an Agent failure. Unsupported locale,
  captcha/consent blocking, or incomplete DOM enumeration is an evaluator error.

Both protocols aggregate complete per-VM observations with `any_complete`: one VM must
satisfy the whole conjunction, and fields from different VMs are never spliced together.
The profile evaluator, controlled setup, bounded `Preferences` reader, runtime registry,
and CLI environment source are wired in production code. The active-tab pure evaluator,
controlled setup, AT→CDP→AT collector, and runtime/CLI binding are also wired in
production code. Neither task has completed the versioned 114 live gate, so both remain
`blocked`.

上述两个状态任务不再与 bookmark 协议混用。Profile 协议读取固定默认
Chrome profile 的 `Preferences`；文件可靠读取后名称缺失或不等属于 Agent
失败，文件或 schema 不可靠则属于评价异常。Active-tab 协议在同一个不可变
快照中同时检查 Google Shopping 查询与已选筛选项闭集；错误页面、查询或筛选
集合是 Agent 失败，locale、验证码/同意页或 DOM 枚举不完整是评价异常。
`any_complete` 只能聚合整台 VM 的完整快照，不允许跨 VM 拼接字段。

当前 profile 协议已完成生产代码接线，active-tab 的纯评价、受控 setup、
AT→CDP→AT 采集与 runtime/CLI 绑定也已接线。两个任务都未通过 114 上的
带版本向量 live gate。

## OSWorld artifact-state protocol

All 15 audited OSWorld artifact tasks have immutable pure rules and evidence specs bound
to the source evaluator contract and canonical evidence-spec SHA-256. The shared runtime
adapter is present for the complete rule catalog, but this is metadata and pure-protocol
coverage rather than production evidence coverage. Of 14 unique metric contracts (16
metric invocations), all 14 are implemented as fixed, no-I/O, strongly typed value
semantics. For the 11 contracts that require external Office, PDF, archive or image
bytes, an evaluator-only projection layer now converts already verified actual/gold
bytes into those values under bounded parsers. It does not fetch or verify remote gold,
or obtain input/gold bytes from an unverified source. The 13-task production source,
gold binder, typed projector and pure evaluator registry are now connected as one fixed
runtime path; Agent final text never participates.

Two production evidence slices are now wired locally. For
`Operation-FileOperate-BatchOperation-001`, the source uses the shared locator frozen
during environment preparation, performs one bounded `python -I` guest collection with
no-follow regular-file checks and Pillow pixel hashing, then evaluates frozen inline
gold. For `Operation-FileOperate-CombinationDocs-015`, a versioned task-specific setup
opens DBLP, creates `Desktop/references.bib`, opens the pinned input DOCX in Writer, and
keeps the actual-output locator separate from the uploaded shared directory. Before any
guest read, the source verifies the complete evaluator-only gold set from a private
offline cache; it then performs one no-follow, regular-only, size-bounded file read and
applies the frozen ignore-blanks text contract. Missing actual output is `missing`, a
trusted content mismatch is an available zero score, and gold/cache/transport/UTF-8/schema
failures are evaluator errors. Neither slice persists paths, names, digests, contents, or
gold.
`BatchOperation-003` additionally completes the strict asset layer: its input ZIP and
evaluator-only gold ZIP are pinned to one xlang commit, independently cached, and bound
to the existing safe-extract/finalize/evidence path. This proves local asset and binding
closure only; the guest getter, gold consumption, setup, and versioned Attempt still
require the same real-VM live gate.
Unlike the official source script, which would let `Image.open(path)` follow a member
symlink, the migrated getter fails closed on symlinks. This deliberate safety hardening is
part of the canonical evidence identity as
`symlink_policy=nofollow-fail-closed`; it is not represented as byte-for-byte source
parity.

The authoritative runtime-support manifest now declares all 15/15 tasks under
`paraguibench.osworld.artifact-state.v1`, but all remain `blocked`.
`BatchOperation-001` now pins its three JPEG inputs in a download-only manifest, but
still lacks real-environment getter plus RunStore v2 live evidence. `CombinationDocs-015`
has pinned input and gold
manifests and complete local source/CLI/doctor wiring, but still lacks real-VM validation
of its setup, guest getter, evaluator gold path, and versioned Attempt. The 13
artifact-family tasks now use a durable pre-Docker capability gate and all have strict
input manifests, resolved source start contexts, exact prepare bindings, and formal
evaluator-only gold identities. `Settings-001` uses a strict schema-v2 private derivation:
the held canonical MP4 yields the first frame whose PTS is at least 8.000000 seconds,
with output and decoded-RGB digests fixed by the manifest. Its 0.90 metric threshold
rejects the historical approximately 9.042-second image. This closes its local semantic
contract, but only the other twelve tasks are wired to the current no-Agent task-scoped
component candidate; the official component allowlist is
still empty. Independently, the audited 6bf qcow2 materialization has cleared the global
image gate; Settings gold materialization does not change image or live status. They retain
getter, gold, setup, and versioned-live gates in the checked-in projection. A future
reviewed component receipt may clear only getter/gold/setup for its own task; it cannot
change the image status or per-task versioned-live gate. Safe
implementations for the fixed finalize action allowlist are now attached to the
environment evidence lifecycle. The 10 non-`none` actions run exactly once after the
Agent and immediately before evidence capture; failures become redacted evaluation
errors with a null score. The three finalize-`none` tasks take the same identity path but
perform zero guest I/O. This removes the finalize-not-migrated blocker, but none of these
local and synthetic checks is live evidence.
Therefore the overall state remains `live_validated=0/233`; unit tests or code
reachability never substitute for a real-environment declaration.

15 条经审计的 OSWorld artifact 任务均已冻结纯规则和 evidence spec，并绑定源
evaluator contract 与 canonical spec SHA-256；共享 runtime adapter 也覆盖完整规则目录。
这些只证明规则与元数据闭环，不代表生产取证完成。16 个 metric 调用对应 14 个唯一
contract，当前均已实现为固定、仅标准库、无 I/O 的强类型值语义。
需要外部 Office、PDF、归档或图像 bytes 的 11 个 contract 也已实现
evaluator-only raw→typed projection；它只接受调用方已完成完整性验证的 actual/gold
bytes，并在有界 parser 下 fail-closed。该层不下载或验证远端 gold，不执行 task
setup/finalize，也不从未验证来源获取 input/gold bytes。13 项任务的 production
source、gold binder、typed projector 与 pure evaluator registry 已接成固定 runtime
路径，Agent final text 始终不参与评价。

当前有两个本地 production evidence 纵向切片。`BatchOperation-001` 只使用
environment prepare 阶段冻结的 shared locator，通过一次有界 `python -I` guest
调用执行 no-follow 普通文件检查与 Pillow 像素哈希，再对冻结的 inline gold 评分。
`CombinationDocs-015` 通过版本化 task-specific setup 打开 DBLP、创建
`Desktop/references.bib`、在 Writer 打开固定输入 DOCX，并保持 actual 输出与 shared
上传目录分离；source 在任何 guest 读取前先完整验证 evaluator-only 私有离线 gold，
再执行一次 no-follow、regular-only、有界文件读取和冻结的 ignore-blanks 文本比较。
actual 缺失记为 `missing`，可信内容不匹配是 available 0 分，gold/cache/传输/UTF-8/
schema 异常均为 evaluator error。两条链路都不落盘路径、文件名、摘要、正文或 gold。
`BatchOperation-003` 还完成了严格资产层：input ZIP 与 evaluator-only gold ZIP 固定到
同一 xlang commit，使用物理分离的缓存，并绑定现有 safe-extract/finalize/evidence
路径。该结论只证明本地资产与 binding 闭环；guest getter、gold 消费、setup 和带版本
Attempt 仍需同一真实 VM live gate。

官方源脚本的 `Image.open(path)` 会跟随目录成员符号链接；迁移后 getter 则对
symlink fail-closed。这是有意的安全收紧，不宣称字面完全等价，并已通过
`symlink_policy=nofollow-fail-closed` 纳入 canonical evidence spec 摘要。

权威 runtime-support manifest 已将 15/15 条全部声明为
`paraguibench.osworld.artifact-state.v1`，但都保持 `blocked`：BatchOperation-001
的三张 JPEG 已固定到 download-only manifest，仍缺真实 getter 与
RunStore v2 live 证据；CombinationDocs-015 已完成 pinned
输入/gold、source、CLI 和 doctor 的本地接线，但 task setup、guest getter、evaluator
gold 与带版本向量 Attempt 均未在真实 VM 上验证。13 项 artifact-family 任务
均通过 durable pre-Docker capability gate，严格 input manifest、source start
context 与 prepare 绑定已全部闭合，且 13 项均已闭合正式
evaluator-only gold 身份。`Settings-001` 使用严格 schema-v2 私有派生：
从持有并校验的 canonical MP4 中选取第一个 PTS 不小于 8.000000 秒的帧，
并固定输出与解码 RGB 摘要；0.90 阈值明确拒绝历史约 9.042 秒图像。
该结论只闭合本地语义合同，现有无 Agent task-scoped component candidate
与 receipt/promotion 任务闭集仍精确为其他 12 项。正式 component allowlist
当前仍为空。独立审计的 6bf qcow2 物化已清除统一镜像 blocker；
Settings gold 的本地物化不改变镜像或 live 状态。检入投影仍保留 getter、gold、setup 与 versioned-live
门禁。未来经审核的 component receipt 也只能清自身任务的
getter/gold/setup，不能改变镜像状态或每任务 versioned-live 门禁。固定
finalize allowlist 已安全接入
environment evidence lifecycle：
10 个非 `none` 动作在 Agent 完成后、evidence capture 前精确执行一次，失败只产生脱敏
evaluation error 与 null score；3 个 finalize-`none` 任务经过同一身份闭集但保持 guest
零 I/O。因此 finalize-not-migrated blocker 已移除，但这些本地与 synthetic 验证仍不是
live 证据。当前总体状态仍是 `live_validated=0/233`，不得由单元测试或代码可达性替代
真实环境声明。

## Differential parity contract

Legacy and migrated evaluators may run in different dependency environments. They do not
need to import each other. A third, authoritative
`evaluator-parity-case-manifest.v1` fixes the fixture-source revision, the reference and
candidate evaluator revisions, and the complete `(protocol_id, case_id, input_sha256)`
set. Each side exports one strict `evaluator-observation.v2` JSONL record per case with:

- its fixed evaluator revision, versioned `protocol_id`, and stable `case_id`;
- SHA-256 of the normalized evaluator input closure;
- `PASSED`, `FAILED`, `ERROR`, or `UNAVAILABLE`;
- a score only for `PASSED` or `FAILED`.

The parity harness rejects unknown or duplicate JSON fields, duplicate case keys, unsafe
paths, invalid revisions/scores, and input-digest drift from the authoritative manifest.
Cases missing from both outputs are still reported missing on both sides; identical extra
cases cannot enter the closed set. `outputs_equivalent` may be true when both sides return
the same `ERROR`/`UNAVAILABLE`, but the strict `equivalent` migration gate additionally
requires every expected case to produce a scored `PASSED` or `FAILED` result.
Observation files must not include instruction text, gold values, trajectories, exception
messages, model output, evaluator details, credentials, or endpoint values.

旧 evaluator 与迁移后 evaluator 可以在两个隔离依赖环境中执行，各自只导出严格的脱敏
observation JSONL。差分 harness 比较输入闭包摘要、终态和得分；缺失 case、输入漂移、状态漂移
与分数漂移都不会被自动容忍。通过 parity 仅证明所覆盖 fixture 的行为等价，仍不能替代 fresh-VM
live gate，也不能把 `blocked` 任务改成 `live_validated`。

## WebMall closed-world checkout

The 8 Checkout tasks use `paraguibench.webmall.checkout.closed-world.v2`; the 8
EndToEnd/FindAndOrder tasks use
`paraguibench.webmall.find-and-order.closed-world.v2`. Canonical product URLs are grouped
by logical store: each store requires exactly one new order and that order must contain
the exact product/quantity multiset. Trusted evidence resolves canonical product slugs
from WooCommerce product IDs rather than display labels. EndToEnd additionally requires
the Agent's reported logical URL multiset to match exactly; a missing, wrong, or duplicate
URL fails the composite AND. EndToEnd-001 therefore requires two orders across two stores,
while EndToEnd-005 requires one two-product order in one store. Formal scoring is binary;
malformed/incomplete evidence is evaluator `ERROR`, not an Agent failure.

The formal closed-world result now conjunctively verifies the canonical product/quantity
multiset, the exact new-order set per logical store, completed checkout state,
`credit_card` payment semantics, and the trusted synthetic billing profile's eight
identity/address fields: name, email, street, house number, postcode, city, state, and
country. Card number, CVV, and expiry are form inputs but are neither collected from the
order nor scored. Billing, payment, product, order, and URL source values remain in
trusted memory; RunStore receives only fixed reason codes, booleans, and aggregate counts.

The pure evaluator neither reads deployment origins nor performs network/VM/browser actions.
The pure baseline/final evidence state machine, report canonicalizer, runtime environment
wrapper, and evaluator registry are implemented locally. The 16 tasks remain `blocked`
until the privileged WooCommerce reader, cross-host lease implementation, pinned WebMall
environment/reset manifest, CLI environment binding, privacy checks, and live validation
are complete.

正式 WebMall 闭集结果已同时合取 canonical 商品/数量多集合、每个逻辑商店的
新订单闭集、completed 结账状态、`credit_card` 语义与可信 synthetic fixture 的
8 个 billing 身份/地址字段。卡号、CVV 和有效期只用于 Agent 填表，不从订单采集或评分。
商品、订单、支付、billing 与 URL 原值只在可信内存中比较，RunStore 仅保存固定原因码、
布尔值与汇总计数。生产 WP-CLI 证据源尚未接入，
16 个任务仍保持 `blocked`。
See [ADR-0004](../adr/0004-webmall-checkout-closed-world.md) and its versioned
[ADR-0005 state/billing upgrade](../adr/0005-webmall-checkout-state-and-billing.md).

## Evidence storage

Each attempt stores execution and evaluation summaries under one stable
`run_id/task_id/attempt_id` boundary. Event streams identify their producer
(`runtime`, `environment`, `worker`, or `evaluator`), while persisted payloads use
allowlists and sanitization.

See [the RunStore ADR](../adr/0002-task-scoped-runstore.md).
The public 0.1 package does not include historical smoke-run logs.
