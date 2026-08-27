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
├── provider-neutral GUI worker contracts/action loop
├── provider model adapters
├── framework
├── integrations
└── runstore

evaluation
├── benchmark contracts
├── integrations
├── runstore
├── OSWorld profile / active-tab pure state evaluators
├── WebMall closed-world checkout core (Python standard library)
└── authoritative parity case manifest + observation JSONL (Python standard library)

framework
└── runstore

integrations
├── OSWorld controller / bounded artifact getter / controlled state setup / evidence contracts
├── WebMall manifest / URL registry / WP-CLI evidence / distributed lease / baseline-final session
├── OnlyOffice share service / 精确 4 项 SearchAndWrite 任务分流 [Flask 仅容器与 onlyoffice extra]
├── model_endpoint [stdlib URL 约定：公网 HTTPS，回环允许 HTTP]
└── runstore

benchmark
└── Python standard library

runstore
└── Python standard library
```

当前版本与资产装配链为：

```text
cli run
├── runtime protocol preflight
│   ├── runtime.run_versioning
│   ├── environment manifest protocol_ids
│   └── evaluator registry selected by declared protocol
├── integrations.osworld.image_manifest.live_run_ready
│   ├── schema v2 archive→member→output recipe [6bf default identity fixed]
│   └── audited reproducible materialization evidence [SHA-anchored outside repo]
│       └── osworld_vm_image_materialization_unverified [cleared for 233 tasks]
├── runtime.run_versioning
│   ├── src/paraguibench Python 源码闭集
│   ├── benchmark release/runtime-support manifests + schemas
│   ├── current task pinned asset manifest
│   ├── current task reference manifest [gold or audit-only known-negative]
│   ├── environment manifest
│   └── runstore.RunVersionVector
├── runtime.assets.resolve_task_assets
│   ├── NONE                           [不创建缓存或 shared 目录]
│   ├── PINNED_DOWNLOAD_MANIFEST       [大小、SHA-256、闭集]
│   └── non-empty legacy reference     [fail-closed]
├── runtime.osworld_gold.bind_osworld_task_gold
│   ├── NONE                           [不访问 gold cache]
│   └── PINNED_DOWNLOAD_MANIFEST
│       └── task/spec/provenance/key/media 精确闭合
├── runtime.doctor
│   ├── asset_cache                    [guest-visible input]
│   └── gold_cache                     [host-private/offline/fail-closed]
└── runstore.RunStore.start_run
    ├── runstore.versioning             [固定摘要与协议 ID 校验]
    └── runstore.inspection             [allowlist-only Attempt 诊断]
```

正式 live 晋升保持下列不可绕过的依赖顺序：

```text
runtime-support task projection
├── local_readiness_status                         [独立本地投影]
│   ├── live-only blocker 白名单闭包 → local_ready
│   └── 任一本地/未知 blocker → local_components_incomplete
├── image manifest live_run_ready                    [第一因子]
├── task-specific component blockers                [第二因子]
│   ├── evaluator + pinned input assets
│   ├── WebMall Cart reader reference
│   │   └── independent component receipt
│   │       ├── fixed component ID + receipt SHA
│   │       ├── exact 8-task identity SHA
│   │       ├── same-snapshot environment identity SHA
│   │       └── receipt-neutral component identity SHA
│   ├── OSWorld artifact getter/gold/setup/conflict
│   ├── pipeline typed/input/reference/live
│   └── Operation Word Writer-live/versioned-live
└── SHA-allowlisted sanitized RunStore-v2 task receipt [第三因子]
    ├── live-validation-receipt-allowlist-v1.json   [canonical task→SHA 空闭集]
    ├── anchored nofollow dirfd + bounded stable read
    │   └── 读前/读后复验物理文件闭集与目录身份
    ├── task/run/attempt + SUCCEEDED/PASSED + finite score [0,1]
    ├── six-field version vector + current protocols/environment
    └── promotion-safe component revision
        ├── loaded package == repo src
        ├── runtime_support_manifest.py guard bytes
        ├── release-validated task path + task input/reference
        └── WebMall manifest → nested OSWorld Chrome manifest SHA
```

RunStore-v2 task receipt 不得清除任何组件 blocker。Cart component
receipt 只能清除精确 8 项的 reader-reference blocker，不能代替镜像或
task receipt。两类 receipt 都不读取 Agent final text、details、events、
prompt/response、Cart 内容、runtime 文件系统路径、deployment host endpoint 或
credential；manifest 绑定的公开 OCI image identity 依然是允许字段。当前两份
allowlist 均为空，因此本地投影为 233/0，全部 233 项仍为
blocked/live=0。

Cart candidate 刻意在当前正式 `pending` WebMall manifest 上生成
receipt，以独立 allowlist 重验后的 receipt 作为活性唯一权威。
trusted loader 不要求先无证据地把 manifest 改成 `live_validated`；
反之，仅修改该 manifest 字段也不能清除 blocker。这样避免了
manifest/environment/component 摘要因状态切换而令刚生成的证据自失效。

`runtime.run_versioning` 当前采用保守的完整公开 Python 源码树摘要，同时作为 source、Agent code 和 evaluator revision；它会核对当前进程实际导入 package 与 repo-root 源码一致，并把当前 task 的 pinned input asset manifest 与 task-specific reference manifest 纳入闭包。reference 可以是 evaluator gold，也可以是显式标记为禁止 pass-oracle 的 audit known-negative。WebMall 环境 revision 另以 domain-separated 传递闭包同时绑定 WebMall manifest 与其嵌套的当前 OSWorld Chrome image manifest。这会把无关模块变化也视为新 Run，但不会漏记已知代码/资产/环境变化。后续只有在可验证的传递依赖闭包建立后，才能缩小 Agent/evaluator 摘要粒度。

已迁移 FileSearch Readonly 资产的依赖闭包为：

```text
scripts/benchmark/readonly_asset_manifests
├── 9 canonical task identities + asset_manifest bindings
├── fixed Lee repository revision
├── 30 path/size/SHA-256/media entries
├── benchmark/schemas/readonly-file-search-asset-manifest-v1.schema.json
└── runtime.assets
    ├── strict manifest/source/file field closure
    ├── assets fetch                              [唯一网络入口]
    └── assets verify                             [离线大小/SHA/文件闭集]
```

该闭包只完成资产完整性与类型核验；上游 Lee 资产许可仍为
`unverified`/`download_only`，文件不进入 Git。九个任务仍为
`blocked`，统一镜像物化 blocker 已清除，但仍保留
`versioned_live_validation_not_completed`，不构成
`live_validated` 声明。

Operation FileOperate 固定输入的依赖闭包为：

```text
scripts/benchmark/batch_operation_office_assets
├── 34 canonical BatchOperation/CombinationDocs/SearchAndWrite identities + bindings
├── leeLegendary/Parallel_benchmark@13bf942dfab6f9d71f16f0958f1edd8b436c7afa
├── xlangai/ubuntu_osworld_file_cache@711e0811642364e7aa8f10a8918367d0b626d578
├── 128 path/size/SHA-256/OOXML-or-JPEG-or-text media entries
├── license_status=unverified + distribution_policy=download_only
├── benchmark/schemas/batch-operation-office-asset-manifest-v1.schema.json
└── runtime.assets.resolve_task_assets
    ├── manifest path/source/file field closure
    ├── fetch_asset_manifest                 [唯一网络入口]
    ├── verify_asset_directory               [离线 size/SHA/文件闭集]
    ├── OSWorldTaskEnvironment guest node closure
    │   └── ordinary files + required directories only [symlink/special node fail-closed]
    └── runtime-support projection
        └── exact task/path/manifest-SHA binding [缺失/换位/漂移均失败关闭]
```

其中 Word-009/010 在不改变实机晋升状态的前提下，新增以下本地
production-core 传递闭包：

```text
canonical Word-009/010 task + operation_word_text_input_contract
└── OSWorldTaskEnvironment.prepare
    ├── verify_asset_directory                     [正式文件闭集]
    ├── raw manifest held-fd nofollow rebind        [固定 SHA/路径/数量]
    ├── DOCX held-fd stable snapshot                 [首次 guest I/O 前]
    └── WordTextBaseline                           [不含原文/路径 repr]
        └── SingleVMEnvironmentLeaseAdapter          [同一 typed DTO 透传]
            └── OperationTaskEvaluator               [baseline-first formal identity]
                └── OperationArtifactSnapshot         [Agent 后 owned post 闭集]
                    └── evaluate_operation_artifacts
                        ├── typed DOCX text/container/style/numbering/relationship
                        │   /ContentType/root-QName projection
                        ├── mismatch → fixed FAIL/0
                        └── identity/parse/unknown carrier → ERROR/null
                            └── AttemptRunner → RunStore [固定类型/计数；无 final text]
```

该闭包的本地对抗与隐私测试不等于 versioned-live receipt。
`operation_word009_010_writer_live_validation_not_completed` 仍作为任务级实机
blocker 保留，不由 core 单元测试、Agent final text 或合成 Office fixture 清除。

Word-012 使用独立于通用大写词 heuristic 的 host-only 语义链：

```text
canonical Word-012 task + operation_word_abbreviation_input_contract
└── OSWorldTaskEnvironment.prepare
    ├── verify_asset_directory + raw manifest held-fd nofollow
    ├── exact 4 DOCX path/size/SHA + formal manifest SHA
    └── capture_word_abbreviation_baseline              [首次 guest I/O 前]
        ├── fixed source paragraph SHA + unique occurrence context
        ├── canonical target-only in-memory DOCX transform
        └── WordAbbreviationBaseline                   [无原文/映射 repr]
            ├── process-local HMAC seal                  [全 snapshot 复合身份]
            └── SingleVMEnvironmentLeaseAdapter         [同一 DTO 透传]
                └── OperationTaskEvaluator             [post 前完整身份校验]
                    └── OperationArtifactSnapshot      [Agent 后四文档闭集]
                        └── evaluate_operation_artifacts
                            ├── exact target text + full typed visible-text/container fidelity
                            ├── bdo/dir/rtl + inherited xml:space semantics
                            ├── comparable drift → ABBREVIATION_SEMANTICS_MISMATCH FAIL/0
                            └── identity/parse/resource uncertainty → ERROR/null
                                └── AttemptRunner → RunStore [固定原因/计数或类型]
```

该链不修改 task instruction，不向 Agent 投影 occurrence 语义，不读取
Agent final text，也不采信历史 `answer_files` 作为 gold。本地正/负回放和
对抗测试完成后，历史本地语义 blocker 与统一镜像物化 blocker 已移除；
versioned-live 门禁仍保留到受控真实 Writer/LibreOffice 证据完成。

该资产闭包将三十四个可匿名重放的 Office legacy URL 与两个含固定锁文件的
ReadonlyPPT 来源替换为固定 manifest，不以资产就绪代替 evaluator 或 gold
语义闭合。统一镜像物化 blocker 已清除；全部任务仍保留 versioned-live
门禁，BatchOperation-001 还保留 artifact getter live 门禁。

`framework` 只提供可复用执行机制，不能拥有 prompt、模型调用、任务类别或
评价逻辑。完整的 planner、worker policy、prompt 和结果合成属于具体
`agents/systems/*`。`runtime` 负责选择并装配 environment、Agent System 和
evaluator；evaluation 不根据 Agent 类型分支。

当前 Qwen 内部依赖保持单向，GUI-only 与 ParaGUI 不复制 provider 逻辑：

```text
agents/systems/gui_only/qwen
└── agents/workers/qwen
    ├── agents/workers/gui
    ├── integrations/model_endpoint
    └── integrations/qwen
        └── openai SDK (lazy import)

agents/systems/gui_only/seed18
├── integrations/model_endpoint
└── openai SDK (lazy import)

cli model-probe qwen-native
└── cli.model_probe
    ├── in-memory 32x32 PNG                     [Pillow lazy import]
    └── agents/workers/qwen.QwenOpenAIModel.next_action
        ├── integrations/qwen                         [openai lazy import]
        ├── native computer_use + parser             [返回动作不执行]
        └── no VM/controller/RunStore                 [固定脱敏输出]

agents/systems/paragui/gui_worker_adapter
├── agents/workers contracts
└── framework contracts

agents/systems/paragui/kimi
├── agents/systems/paragui/planner
├── integrations/model_endpoint
└── integrations/kimi
    └── openai SDK (lazy import)

agents/systems/paragui/system
├── DAGScheduler(max_workers=1 in single-VM CLI)
└── GUIWorkerParaGUIAdapter
    └── QwenGUIWorker

runtime/single_vm_lease
└── one prepared OSWorldTaskEnvironment

runtime/osworld_artifact_evidence
├── integrations.osworld.artifact_evidence_specs
├── integrations.osworld.artifact_gold_media   [12 contract 的位置化媒体闭集]
├── environment-prepared frozen guest shared locator
├── integrations.osworld.controller
│   ├── collect_image_pixel_hashes
│   └── collect_file_bytes
├── runtime.gold_assets.GoldAssetResolver [evaluator-only, offline]
└── evaluation.osworld.artifact_metrics

integrations/osworld/artifact_metric_projection
├── integrations.osworld.artifact_family_evidence   [raw capture；不接受路径]
├── integrations.osworld.artifact_evidence_specs    [contract/gold key/预算]
├── evaluation.osworld.artifact_metric_values       [14 个 pure typed contracts]
└── evaluator-only optional parsers
    ├── openpyxl                                    [XLSX/CSV]
    ├── python-docx                                 [DOCX]
    ├── python-pptx                                 [PPTX]
    ├── pypdf                                      [PDF；延迟导入]
    └── Pillow                                     [RGB/HSV 联合归一化]

integrations/osworld/artifact_family_task_prepare [production wiring complete]
├── 13-task canonical asset-mode identity + input-draft SHA + evidence-spec identity
│   ├── 13 strict input manifests [legacy URL 禁止共存]
│   └── 13 formal gold identities [12 v1 download + Settings private-derived v2]
├── 71 input path bindings [0 inferred paths；统一来自固定 xlang source config]
├── 13 actionable ordered setup specs [assets 已验证时]
│   └── argv-only mkdir/copy/safe-unzip/Chrome/LibreOffice/VLC/Files actions
├── 0 ambiguous specs [13 source start contexts 已唯一绑定]
└── ArtifactFamilyTaskPrepareSource
    ├── unverified/missing/drifted assets             [I/O 前 fail-closed]
    └── runtime/osworld_environment                   [host+guest 双重闭集后执行]

integrations/osworld/artifact_finalizer [production runtime binding complete; live pending]
├── artifact_evidence_specs                    [13-task 身份与 finalize 合同单一来源]
├── 3 finalize-none tasks                     [不执行 guest 动作]
└── 10 fixed non-none actions
    ├── archive-pdf-directory                  [nofollow/有界/原子 ZIP]
    ├── save-active-libreoffice-document       [严格窗口激活 + 有界稳定等待 + Ctrl+S]
    └── export-calc-first-sheet-csv            [nofollow 私有快照 + 固定 filter + 原子提交]

runtime/osworld_artifact_finalization
└── exact 10-task runtime capability           [仅 action!=none；供 source/清单共同取证]

runtime/osworld_environment
└── Agent → finalizer once → evidence capture  [失败缓存为脱敏 evaluation ERROR/null]

runtime/artifact_family_task_prepare
├── 13 canonical + input draft + strict asset manifest [只读身份闭包]
├── 13 tasks use a durable pre-Docker capability gate
├── durable blocker projection                         [只含 task/count/code]
│   ├── 71/71 input verified + 13 strict manifests bound
│   ├── 0 inferred paths / xlang Apache-2.0 download-only
│   ├── 0 source start-context ambiguous [13 bindings exact]
│   └── Settings strict v2 derived-gold contract [local identity only]
├── CLI _load_task_context                             [Docker/guest/Agent/RunStore 前]
└── ArtifactFamilyTaskPrepareBinding                  [不持久化 host/guest/远端路径]
    └── runtime/osworld_environment
        ├── actual manifest SHA/文件闭集复核           [guest I/O 前]
        ├── host cache size/SHA/闭集
        ├── guest upload SHA/闭集
        └── ArtifactFamilyPreparedAssets → 专属 source

integrations/osworld/operation_artifacts
├── evaluation.operation.OPERATION_TASK_RULES   [固定 32-task 身份]
├── integrations.osworld.controller
│   ├── collect_artifact_tree_manifest     [递归 openat/O_NOFOLLOW + SHA-256]
│   └── collect_file_bytes                 [逐文件 nofollow 复核]
└── owned host TemporaryDirectory            [Attempt close 必清理]

evaluation/pipeline_implicit/searchwrite_contract
├── task/UID/evaluation protocol                      [唯一合同源]
├── input/gold manifest raw SHA-256
├── 2 document paths + ordered 9 cells               [coordinate/type/value]
├── 2 baseline semantic SHA-256                       [projection v6]
└── cell-match protocol v1 + machine-identity SHA-256
    ├── evaluation/searchwrite_xlsx                    [期望值单向派生]
    ├── integrations/searchwrite_bridge                [坐标/基线单向派生]
    └── runtime/pipeline_implicit_binding               [strict identity 重算]

integrations/pipeline_implicit/searchwrite_bridge
├── generic manifest—nofollow bytes—manifest artifact observation
├── parent ZIP/XML streaming preflight                 [Python 标准库]
├── multiprocessing spawn child
│   ├── resource RLIMIT CPU/core/fd/file-size          [Linux 再加 AS]
│   ├── openpyxl                                      [artifact extra；child-only lazy import]
│   └── fixed target-cell + baseline semantic projection
├── parent RSS monitor
│   ├── Linux /proc/<pid>/statm
│   └── macOS ctypes → /usr/lib/libproc.dylib
├── <=64 KiB strict UTF-8 JSON one-way IPC
└── typed SearchWriteObservation → pure evaluator → RunStore-safe counts
    └── fixed 2+2 real fixture                          [76 pass / 0 skip]

cli/osworld_qcow2_materializer                          [唯一正式 python -m 入口]
└── canonical integrations/osworld/qcow2_materializer.main [单一模块/type identity]
    ├── fixed redacted argparse error                  [不回显路径/未知参数值]
    ├── absolute repo-root
    │   └── fixed environments/osworld/image-manifest.json [逐级 nofollow held path]
    ├── typed archive→single-member→output recipe
    ├── provenance/output capability
    │   ├── archive held FD + full-stat + size/SHA
    │   ├── fixed manifest held FD + full-stat + size/SHA
    │   └── final readonly qcow2 FD + output-parent held path + full-stat + size/SHA
    ├── O_TMPFILE → fsync → 0400 → full hash → linkat no-replace
    └── with result → verify_full → fixed name/SHA/size JSON → deterministic close
        └── materialization-at-evidence-time only; no receipt/allowlist/status promotion

integrations/osworld/qcow2_materializer as __main__    [固定迁移拒绝]
└── no argv parse / manifest read / archive read / output I/O

runtime/pipeline_implicit_binding → OSWorldTaskEnvironment.prepare
├── preflight capability: task/protocol/input+gold raw SHA
├── actual ResolvedTaskAssets.manifest == deterministic formal input
├── reject A→B→A/B ABA before first guest upload
└── live/image/versioned validation remains an independent gate

scripts/deployment/run_cleanroom_cli.py                 [冻结源码正式 bootstrap]
├── sys.dont_write_bytecode=True                        [任何 paraguibench import 前]
├── PYTHONDONTWRITEBYTECODE=1                           [子进程继承]
└── cli.main                                             [统一 production parser/脱敏错误]
    ├── assets / gold / doctor / model-probe / run / inspect
    └── `pipeline-implicit component-validate`          [无 Agent refresh 唯一公开入口]
        └── runtime/pipeline_implicit_component_candidate
            ├── benchmark.prepare_release_task + receipt-neutral local preflight
            ├── integrations/osworld/image_manifest               [same-FD bytes+SHA]
            ├── runtime/osworld_attested_qcow2                    [O_EXCL 0400 snapshot]
            ├── runtime/osworld_environment.prepare               [verified input only]
            ├── integrations/pipeline_implicit/artifact_evidence [typed production capture]
            ├── runtime/evaluators.PipelineImplicitTaskEvaluator [formal score == 1.0]
            ├── runstore Attempt + double inspection             [details={}]
            └── owned close → qcow/OCI attestation → current identity recheck
                └── runtime/pipeline_implicit_component_receipts.PipelineImplicitComponentReceipt

runtime/pipeline_implicit_component_receipts
├── task identity        [release entry + exact task/input/reference bytes + typed protocol]
├── environment identity [held image SHA + extracted qcow SHA + digest-pinned OCI]
├── component identity   [task+environment + all src Python + schemas + CLI/guard]
├── dedicated allowlist  [task → receipt SHA + three current identities]
├── nofollow dirfd / nlink=1 / bounded stable reads / exact directory closure
└── receipt/current/allowlist before+after equality
    └── scripts/benchmark/runtime_support_manifest
        └── only matching `pipeline_implicit_live_validation_not_completed` removed

pipeline task readiness
├── BatchOperationPPT-003   [local ready; optional audit receipt not a production gate; pipeline-live + versioned-live remain]
├── BatchOperationExcel-008 [local ready; component receipt allowlist empty]
├── CombinationDocs-002     [local ready; historical answer audit-only; receipt allowlist empty]
└── SearchAndWrite-008      [local ready; evaluator closed; outside 3-task no-agent candidate set; allowlist empty]

runtime/evaluators.OperationTaskEvaluator
├── runtime/osworld_environment.operation_artifact_snapshot
│   └── integrations/osworld/operation_artifacts
├── evaluation.operation.evaluate_operation_artifacts
└── RunStore-safe protocol/rule/reason/count projection

runtime/single_vm_lease.operation_artifact_snapshot
└── same prepared OSWorldTaskEnvironment snapshot

runtime/osworld_gold
├── integrations.osworld.artifact_evidence_specs [gold key/source contract]
├── integrations.osworld.artifact_gold_media [contract/key 位置媒体闭集]
└── runtime.gold_assets
    ├── strict pinned manifest loader
    ├── explicit fetch_gold_assets           [唯一联网入口]
    └── nofollow private-cache resolver       [doctor/evaluator 只读]

cli gold fetch/verify/materialize
└── runtime/osworld_gold
    ├── runtime/gold_assets                  [strict v1/v2 bytes loaders + offline resolver]
    └── runtime/derived_gold                  [held input → fixed 8.008s PNG; host-private]

scripts/benchmark/osworld_state_asset_drafts [13 strict input + 13 formal gold identities]
├── canonical task strict input / optional strict gold 资产状态
├── integrations.osworld.artifact_evidence_specs [source/evaluator/gold-key 合同]
├── 已审计的 OSWorld source task config [只作为路径与 guest 用途证据]
└── 13 input drafts + 13 evaluator-only gold drafts [71 input / 15 gold]
    ├── 13 strict input manifests [71/71 input verified]
    ├── 12 strict v1 download gold manifests [14 remote gold verified]
    ├── Settings strict v2 derived manifest [8.008s output/RGB digests fixed]
    └── Settings legacy remote draft [v1 / integrity unverified / non-authoritative]

runtime/osworld_environment.prepare
├── runtime.assets                       [pinned input 闭集上传与摘要门禁]
├── runtime.artifact_family_task_prepare [13-task binding + manifest 复核]
│   └── integrations.osworld.artifact_family_task_prepare
│       └── 仅 verified DTO 可进入有序动作 source
├── integrations.osworld.task_prepare   [版本化 task identity + action allowlist]
│   └── integrations.osworld.controller    [launch/execute/wait CDP 窄接口]
├── task-specific matched              [跳过通用 Files 窗口]
├── task-specific unmatched            [打开通用 shared Files 窗口]
└── optional state evidence setup

cli doctor/run [WebMall URL/Cart/Checkout/EndToEnd]
├── runtime.webmall_binding.preflight_webmall_identity/runtime
│   ├── integrations.webmall.environment_manifest
│   ├── integrations.webmall.registry        [logical URL ↔ runtime origin]
│   ├── runtime.webmall_preparation          [仅 Agent 投影物化 origin]
│   ├── runtime.run_versioning               [WebMall + OSWorld 环境身份]
│   └── 三态 evidence mode                    [reported URL / browser Cart / privileged order]
├── runtime.webmall_doctor
│   └── 4 origins + 协议专属 Cart/订单 reader 与 live-validation gate
├── runtime.webmall_cart_environment          [仅 Cart 协议]
│   └── integrations.webmall.browser_cart_source
│       ├── 同一 BrowserContext、单 worker × 四店
│       ├── 每店两读规范化结果必须一致且完整
│       └── integrations.webmall.cart_evidence
│           └── evaluation.webmall.cart       [quantity-aware closed-world multiset]
├── runtime.webmall_binding.bind_webmall_privileged_runtime
│   ├── integrations.webmall.wpcli_order_source
│   │   └── wp --ssh=<target> --quiet eval-file - [shell=False, bounded I/O]
│   ├── integrations.webmall.distributed_lease [HTTPS or loopback HTTP]
│   └── integrations.webmall.order_evidence   [global lease + baseline/final]
└── runtime.webmall_environment
    └── one OSWorld browser environment + closed-world observation

separate WebMall lease service
└── integrations.webmall.lease_coordinator
    └── SQLite BEGIN IMMEDIATE + persistent fencing-token high water mark
```

`runtime/osworld_environment.prepare` 只消费 guest-visible input assets；它不导入
`runtime.gold_assets`，也不会接收 gold cache root。evaluator-only gold 只沿
CLI/doctor/source 路径在 host 内流动。

`agents/workers/gui` 只拥有 provider-neutral 动作 IR、白名单 compiler、截图循环和有界
原始截图窗口；`agents/workers/qwen` 拥有 Qwen 配置、prompt、当前/历史图片预算、多图
消息构造、请求策略与响应解析；
`integrations/qwen` 只负责延迟创建 OpenAI-compatible SDK client，不拥有 Agent
policy，也不记录请求、响应或凭据。ParaGUI adapter 只能从 runtime 提供的 pool
租用独占环境，不能把同一 controller/VM 交给多个 scheduler 线程。

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
| `paraguibench.agents` | Agent 统一结果契约、共享 GUI workers 及 runnable systems | GUI-only Seed18 是首个版本化复验候选；Qwen GUI-only 与 Kimi+Qwen 单 VM 串行 ParaGUI 已完成契约测试但尚未 live-validated；多 VM ParaGUI 尚未完成 |
| `paraguibench.evaluation` | answer 契约、OSWorld profile/active-tab 与 artifact-state 纯状态协议、artifact 固定 metric registry、Operation 纯 artifact 规则闭包、WebMall logical URL set、quantity-aware closed-world Cart、closed-world checkout core、FindAndOrder 组合协议与带权威 case manifest 的 parity gate | 78 个 QA、8 个 Cart、8 个 Checkout、8 个 EndToEnd 与 2 个 OSWorld Chrome 状态协议已完成 runtime 注册；Cart 以严格商品多集合、数量、店铺和 worker 归属判定，忽略 Agent 最终文本；15 个 artifact-state 纯规则及 adapter 已绑定 source contract 与 canonical evidence-spec SHA，14 个唯一 metric contract 均已以强类型、无 I/O 值语义实现；它们不读取或下载 gold。32 个 Operation eval-rules 任务已固定完整规则摘要与 33-check 最小闭包，并接入 runtime registry、guest artifact capture 与 runtime-support 原生协议。4 个 pipeline-implicit pure evaluator 已注册；PPT-003、Excel-008 与 CombinationDocs-002 的 production typed bridge、input-only candidate 和正式机器身份已本地闭合。历史 797 身份的 PPT-003 receipt 仅作可选官方审计，普通 runtime-support 对四项 pipeline 任务都保留 pipeline-live/versioned-live。Excel-008 保持原题面并按最终行可见性评价；CombinationDocs-002 以 input XLSX 为唯一事实源，历史错误 answer 仅作 host-side audit known-negative，不生成修正 gold。SearchWrite-008 已闭合 input/gold、隔离 typed parser、纯 evaluator 与 production local capability，但尚无 Search 专属 no-Agent candidate，仍等待 pipeline-live/versioned-live。Word-004 已恢复逐文件规则并拒绝额外高亮，且其五份原始 DOCX 已绑定固定 download-only manifest；live validation 仍未完成。Operation Office parser 仅从 `operation` extra 延迟导入；32 项原生任务现均绑定固定输入 manifest，并因 versioned live Attempt 等门禁保持 blocked。资产生成器总计固定 34 任务/128 文件，其中 2 项属于非 eval-rules 原生协议。CombinationDocs-003 使用源表格相对双通道评价，但仍保留真实 LibreOffice 渲染门禁；Word-012 的固定四文档逐处语境 evaluator、pre-first typed runtime 链、正文/容器保真及 RunStore 脱敏已本地闭合，历史本地语义与统一镜像物化 blocker 已移除，仍等待 versioned-live 复验 |
| `paraguibench.integrations.osworld` | loopback controller、argv-only guest 执行、有界文件读取、no-follow 有界目录/图片像素取证、Operation 递归 manifest/快照、版本化 artifact evidence-spec 与 task-prepare catalog、受控 Chrome state setup、AT→CDP→AT active-tab 采集、固定 digest Docker session、镜像 manifest | 固定 HF ZIP 直接派生的 6bf 镜像已被选为默认 environment identity，历史 6d 仅作为独立 legacy identity。schema v2 已固定 archive→member→output recipe 与 6bf 摘要；受控 Linux 物化证据使 `live_run_ready=True`，233 项统一镜像 blocker 已清除。Operation source 使用有界 nofollow guest manifest、逐文件 SHA 复核和 owned host 临时树；15 条 artifact-state spec 固定 source/runtime locator、getter、metric options/gold key、limits 和 finalize。13-task prepare catalog 的 71 个 input、13 份 strict input manifest、start context 与 prepare 身份已全部闭合；12 项使用固定下载 gold，Settings 使用私有 schema-v2 derived gold。所有任务仍保留各自的真实 setup/getter/gold/versioned-live 门禁 |
| `paraguibench.integrations.webmall` | `webmall://store-*` 地址映射、固定四店 manifest、报告 logical 化、同 BrowserContext Cart Store API 证据、WP-CLI 订单证据、baseline/final 差分、distributed-lease 客户端与 SQLite coordinator | Cart reader 强制单 worker×四店完整两读一致并 fail-closed；生产代码和本地契约测试已接入，但 114 参考部署的 Store API、四店真实 target、coordinator 及同版本 Attempt 尚未 live-validated |
| `paraguibench.integrations.onlyoffice` | 精确 5/5 SearchAndWrite 分流、share service 工厂与宿主 HTTP 客户端 | 单实例实验室部署与单元测试已接入；不改 evaluator，也不声明 live_validated |
| `paraguibench.integrations.model_endpoint` | 模型 base URL 最小约定：公网 HTTPS，`localhost`/`127.0.0.1`/`::1` 允许 HTTP，禁止 userinfo/query/fragment | 已接入 Qwen、Seed18、Kimi 与 OSWorld doctor |
| `paraguibench.integrations.qwen` | 延迟创建 OpenAI-compatible Qwen SDK client，不拥有 Agent policy | 契约测试完成；Qwen 3.7 Flash 尚未 live-validated |
| `paraguibench.integrations.kimi` | 延迟创建 OpenAI-compatible Kimi SDK client，不拥有 DAG 策略 | Function Calling 契约测试完成；尚未 live-validated |
| `paraguibench.runtime` | 输入资产与 evaluator gold 的独立解析/闭集、协议预检、聚合 doctor、环境生命周期、版本向量构造、AttemptRunner、OSWorld/WebMall evidence environment 与评价适配 | 13 个 artifact-family task 已通过 durable pre-Docker capability gate、strict input manifest identity binding 与 host/guest 双重闭集；10 个 non-none finalizer 已接入 Agent 后、capture 前的一次性脱敏生命周期。gold resolver 严格区分 v1 download 与 v2 private-derived；Settings 的本地 8.008 秒语义闭合但不进入当前 12-task candidate/receipt 闭集。SingleVM adapter 仅透传同一 raw environment。32 个 Operation task 已通过 environment 冻结快照、single-VM 透传和脱敏 adapter 接入 AttemptRunner/RunStore；所有任务仍等待其 runtime-support 所列的受控 live Attempt |
| `paraguibench.runstore` | run/task/attempt 身份、版本向量、安全诊断、独立终态、事件流、artifact、原子持久化和脱敏 | schema 2.0 父子/三层身份、score/stage 和严格只读检查已通过契约测试；新 schema 尚待 114 live gate |
| `paraguibench.cli` | `assets fetch/verify`、`gold fetch/verify/materialize`、`model-probe qwen-native`、`doctor`、`run`、`inspect --diagnostics` | `gold materialize` 只在显式子命令中延迟导入媒体依赖，且只接受严格 v2 private-derived 任务；输出不包含路径、摘要或内容。其余命令已按 task source 分流 OSWorld/WebMall 预检、doctor 与 environment；13 个 artifact-family task 在 `_load_task_context` 内先形成脱敏 capability/binding；WebMall 只从 manifest 指定的环境变量引用读取四店、reader 与租约绑定，不接受值类 CLI 参数；所有 Agent 路径仍待新 live gate |

当前实际导入链比允许边界更窄。例如，`framework` 目前仅使用标准库；
`integrations.osworld.controller` 在实例化真实 HTTP session 时才加载
`requests`；Seed18 model adapter、`integrations.qwen` 与 `integrations.kimi` 都在首个真实请求时才加载
`openai`，Qwen 图片处理同样延迟加载 Pillow；active-tab 证据源只在真实
CDP 采集时延迟加载 Playwright。`integrations.onlyoffice` 的任务分流常量只使用
标准库；Flask 应用工厂仅在 share service 与其测试中导入。

## 分发与 extras

项目当前是一个 Python distribution，要求 Python 3.11–3.13：

```text
paraguibench
├── core (默认安装)
│   └── 第三方 runtime dependencies: none
├── live
│   ├── openai >=1.82,<3
│   ├── Pillow >=11,<13                [screenshots + private derived-gold PNG verification]
│   ├── requests >=2.32,<3
│   └── playwright >=1.50,<2
├── operation
│   ├── openpyxl >=3.1.5,<4
│   ├── python-docx >=1.1.2,<2
│   ├── python-pptx >=1.0.2,<2
│   └── Pillow >=11,<13
├── artifact
│   ├── openpyxl >=3.1.5,<4
│   ├── python-docx >=1.1.2,<2
│   ├── python-pptx >=1.0.2,<2
│   ├── Pillow >=11,<13                [typed image projection + derived-gold materializer]
│   └── pypdf >=5,<7
├── onlyoffice
│   ├── flask >=3,<4                   [share service 与其单元测试]
│   ├── gunicorn >=22,<24              [容器内单 worker 多线程]
│   └── requests >=2.32,<3             [callback 回源下载]
├── dev
│   └── pytest >=8.3,<9
└── build-system
    └── hatchling >=1.27
```

- `openai`：OpenAI-compatible Seed18、Qwen 与 Kimi model adapter。
- `Pillow`：截图尺寸、artifact 图像投影与私有 derived-gold PNG 像素复核；
  `gold materialize` 必须在安装 `artifact` 或 `live` extra 的 provisioning host 上执行。
- `requests`：loopback OSWorld controller HTTP client。
- `playwright`：仅用于 OSWorld active-tab 评价的 host-side CDP 证据采集。
- `pytest`：开发与回归测试，不属于 runtime。
- `flask` / `gunicorn` / `requests`：仅 `onlyoffice` extra 与 share 容器使用。
  Core 导入任务分流常量不需要这些包；share service 不得在容器启动时再
  `pip install`。

WebMall 的 manifest、reader parser、HTTP lease client 与 coordinator 只使用 Python
标准库，因此不增加 Python extra；`wp` CLI、WordPress/WooCommerce、Docker/SSH 和
TLS 反向代理属于部署系统边界。OnlyOffice DocumentServer 镜像与外部状态目录同样
属于部署系统边界；Python extra 只覆盖 share service 本身。

## 公开站点依赖方向

`website` 是独立的静态交付面，不属于 Python runtime，也不能直接读取 canonical
任务正文：

```text
benchmark/manifests + benchmark/tasks hashes
└── scripts/site/generate_site_data.py
    └── website/public/data/site-data.json
        └── website/src/lib/taskData.js
            └── website/src/components/TaskExplorer.jsx
                └── website/src/App.jsx

website/src/content.js
└── website/src/components/*
    └── website/src/App.jsx
        └── website/src/main.jsx
            └── website/index.html

website/vite.config.js
└── website/dist
    └── website/scripts/validate-static-site.mjs
        └── GitHub Pages artifact
```

站点数据生成器只允许输出白名单元数据、双语标签、计数和输入摘要；任务 instruction、
expected answer、profile、URL、fixture 值、内部路径、模型信息与凭据均不能进入
`site-data.json`。React 组件只能依赖该公开投影，不能在构建阶段绕过生成器读取
`benchmark/tasks/*.json`。

前端 production dependencies 仅为 `react` 与 `react-dom`；Vite 和 React plugin
只参与构建。站点不包含后端、分析脚本、外部字体、credential 或运行时 API 调用。
