# Pipeline-implicit evidence 依赖树

```text
Operation-FileOperate-BatchOperationExcel-008 canonical task
├─ asset_manifest（input，5 份原名 XLSX）
│  ├─ 通用 runtime.assets.AssetManifest 读取/校验链
│  ├─ Lee revision 13bf942d... 固定
│  └─ path/size/SHA-256/MIME 严格闭集
├─ gold_manifest（evaluator-only，5 份原名 XLSX）
│  ├─ verified_assets.load_verified_pipeline_implicit_gold_manifest
│  │  └─ 严格 JSON + task/UID/revision/base_path/5-entry 身份
│  └─ resolve_verified_pipeline_implicit_gold_bundle
│     ├─ held parent dirfd + O_NOFOLLOW + pre/post fstat
│     └─ ZIP CRC + XLSX main content type
└─ formal local core（待与 release/runtime-support 串行派生）
   ├─ PipelineImplicitArtifactEvidenceSource.capture
   │  └─ manifest—nofollow bytes—manifest 原子闭集
   └─ hide_na_rows_bridge.build_hide_na_rows_observation
      ├─ SearchWrite 共享的 OOXML 流式资源门
      ├─ multiprocessing ``spawn`` 解析子进程
      │  ├─ wall/CPU/RSS/fd/core/file-size 上限
      │  ├─ 只读空 cwd + Python audit 禁写边界
      │  ├─ 64 KiB strict JSON IPC（不使用 pickle）
      │  └─ 父进程 RSS ACK 保活 + 共享脱敏 sandbox cleanup
      ├─ 固定五文件语义指纹
      │  ├─ derive_hide_na_rows_baseline_sha256 受控审计 builder
      │  │  └─ baseline v4：真实 input/gold 5 对摘要逐对相等并锁定
      │  ├─ 原始 package part/Content-Type/relationship 图精确闭集
      │  │  ├─ 未登记 part、relationship type/target、MIME 或 namespace 失败关闭
      │  │  ├─ 空 DrawingML/custom-properties 与无 part 仅在已审定条件下等价
      │  │  └─ 非空 DrawingML/custom payload 及 worksheet extLst/未知 namespace 以 SHA-256 投影
      │  ├─ 值/公式、sheet、合并区、freeze pane、行高与列宽
      │  ├─ 页眉/页脚全部、sheet/workbook protection、defined names/print ranges
      │  ├─ 完整 sheet/workbook views、sheet format/properties、theme、tab color
      │  ├─ CalcProperties 13 字段、cell protection 与字体 charset/family/scheme 全闭集
      │  ├─ auto-filter、page setup（含 id）、PrintOptions（含 gridLinesSet）、行/列 page breaks
      │  ├─ PrintPageSetup/PrintOptions ``__attrs__`` 漂移门禁，未知字段失败关闭
      │  ├─ 条件格式、字体 shadow/outline/vertical alignment 与其他归一化可见样式
      │  ├─ 仅合并固定 input/gold 已审计的显式默认差异，第三值 FAIL
      │  ├─ pane/selection 为 GUI 会话导航状态，不纳入 artifact 语义
      │  │  └─ 布局语义仍由 freeze_panes 独立锁定
      │  └─ 忽略本任务唯一允许改动的 row hidden 状态
      └─ HideNARowsObservation
         └─ evaluate_hide_na_rows（5 文件固定分母）
            ├─ +1/+59 extra 只增加 UNEXPECTED_DOCUMENT，不改变分母
            └─ AttemptRunner/RunStore 仅落协议、reason code 和计数

Excel-008 当前的语义裁定点
├─ canonical instruction 保持原文，不向 Agent 透露消歧结论
├─ host-only typed evaluator 固定语义
│  ├─ 隐藏包含字面 ``N/A`` 的完整数据行
│  ├─ 保留该行值/其他内容，其他行可见
│  └─ 无 ``N/A`` 的工作簿不变；filter 或等价 row-hidden 结果均按行可见性评价
├─ runtime binding 绑定 5 input + 5 host-only gold 原始 manifest SHA
├─ no-Agent candidate 仅把已上传 input 作为 guest 动作源
│  └─ 固定 revision 真实 fixture 通过 typed bridge/evaluator 1.0，0 skip
└─ 仍独立保留 image、pipeline-live 与 versioned-live 实机门禁
```

```text
Operation-FileOperate-BatchOperationPPT-003 canonical task
├─ asset_manifest（input，20 项）
│  ├─ runtime.assets.load_asset_manifest
│  ├─ assets fetch/verify：固定 revision + size + SHA-256 + MIME + 闭集
│  └─ OSWorldTaskEnvironment.prepare
│     ├─ host cache verify
│     ├─ upload 到 guest shared
│     ├─ guest SHA-256 与文件闭集复验
│     └─打开 shared 目录作为 Agent 起点
├─ gold_manifest（evaluator identity，32 项）
│  ├─ verified_assets.load_verified_pipeline_implicit_gold_manifest
│  │  └─ 原始 bytes 严格 JSON、固定 task/UID/revision/path→size/SHA/MIME
│  └─ runtime.pipeline_implicit_binding
│     ├─ 分类目录→图片 SHA 集合
│     ├─ source 未分类图片 SHA 集合
│     ├─ ppt1..4→逻辑文档 SHA 映射
│     └─ 与 evaluation.pipeline_implicit 不可变常量逐项相等
└─ preflight_pipeline_implicit_runtime
   └─ PipelineImplicitRuntimeCapability
      ├─ task/protocol 精确身份
      ├─ input/gold 原始字节 SHA-256 机器绑定
      └─ 脱敏 repr，不含路径、摘要或内容

Agent 完成后的 production evidence
└─ PipelineImplicitArtifactEvidenceSource.capture
   ├─ canonical task/protocol 闭集
   ├─ prepare 阶段冻结的 guest shared POSIX 绝对目录
   ├─ controller.collect_artifact_tree_manifest（前后两次一致）
   ├─ controller.collect_file_bytes（no-follow + size/SHA-256）
   └─ image_classification_bridge
      └─ ImageClassificationObservation（不可变 typed observation）
         └─ PipelineImplicitTaskEvaluator
            ├─ 明确忽略 Agent final text
            ├─ evaluate_image_classification 纯函数
            └─ RunStore 只保存协议、reason code 与计数

runtime-support-v1 派生
└─ _derive_pipeline_implicit_runtime_blockers
   ├─ PPT-003：本地组件闭合；历史 797 receipt 仅供可选官方审计，普通 runtime-support 仍保留 pipeline live + versioned live
   ├─ Excel-008 / CombinationDocs-002 / SearchWrite-008：本地组件闭合，保留 pipeline live + versioned live
   ├─ 全局镜像未核验时再保留 image materialization blocker
   └─ 专用 allowlist 仍有 PPT-003 的 1173 B / cbf1f356…8144 历史 receipt，但不作为普通评测门禁
```

```text
Operation-FileOperate-SearchAndWrite-008 canonical task
├─ asset_manifest（input，2 份 XLSX 模板）
│  ├─ runtime.assets.read_manifest_bytes_nofollow
│  ├─ fixed Lee revision + path/size/SHA-256/MIME 闭集
│  └─ OSWorldTaskEnvironment.prepare 的统一 host/guest 资产链
├─ gold_manifest（evaluator-only，2 份 XLSX 答案）
│  ├─ 同一 bounded nofollow stable bytes snapshot
│  └─ strict task/UID/revision/path/size/SHA-256/MIME 机器身份
└─ runtime.pipeline_implicit_binding
   ├─ input/gold 与确定性 builder 逐字节相等
　 ├─ searchwrite_contract 唯一合同源
　 │  ├─ task/UID/evaluation + baseline-v6 + cell-match-v1 protocol
　 │  ├─ 两 manifest raw SHA 及全条目
　 │  └─ 2 baseline digest + 9 ordered coordinate/type/value
　 ├─ strict JSON machine-identity SHA 任一维漂移即失败关闭
   ├─ input/gold metadata blocker 已闭合
　 └─ 真实 2 input + 2 gold fixture：76 pass / 0 skip

production artifact capture
└─ PipelineImplicitArtifactEvidenceSource.capture
   ├─ manifest—nofollow bytes—manifest 原子闭集
   └─ searchwrite_bridge.build_searchwrite_observation
      ├─ 父进程：ZIP/XML 流式预检 + 64 KiB strict JSON IPC
      ├─ multiprocessing ``spawn`` 子进程
      │  ├─ Python ``resource``：CPU/core/fd/file-size（Linux 再加 AS）
      │  ├─ openpyxl（``artifact`` extra；仅在子进程延迟导入）
      │  └─ 固定九个目标单元格 + 模板基线语义投影
      ├─ 父进程 RSS 监控
      │  ├─ Linux ``/proc/<pid>/statm``
      │  └─ macOS ``ctypes`` → ``/usr/lib/libproc.dylib``
      └─ SearchWriteObservation
         └─ pure evaluator → 脱敏计数（忽略 Agent final text）

preflight → environment.prepare 身份穿透
├─ PipelineImplicitRuntimeCapability 只含 task/protocol 与两 manifest SHA
├─ prepare 在 guest I/O 前重新校验 capability
├─ 实际持有的 ResolvedTaskAssets.manifest 直接等于正式 A 字节解析结果
└─ A→B→A/B ABA 竞态在第 0 次 guest upload 前拒绝
```

```text
Operation-FileOperate-CombinationDocs-002 canonical task
├─ asset_manifest（input，DOCX/XLSX/PPTX 各 1 份）
│  ├─ 通用 runtime.assets.AssetManifest 读取、闭集和稳定字节校验
│  ├─ Lee revision 13bf942d... 固定
│  └─ path/size/SHA-256/MIME 与 OOXML ZIP 身份严格匹配
├─ known_negative_manifest（host-side audit-only，历史 HF answer 3 份）
│  ├─ 专属 strict manifest loader，锁定 task/UID/revision/base_path/3-entry 身份
│  ├─ ``manifest_role=audit_known_negative`` 且 ``use_as_pass_oracle=false``
│  ├─ 真实历史 answer 固定预期 FAIL 2/3，不进 guest/RunStore/final
│  └─ 不生成、不分发修正版 gold
└─ formal local core（待与 release/runtime-support 串行派生）
   ├─ PipelineImplicitArtifactEvidenceSource.capture
   │  └─ manifest—nofollow bytes—manifest 原子闭集
   └─ cross_document_bridge.build_cross_document_observation
      ├─ 直接从受控 artifact bytes 解析，不读取 Agent final text
      ├─ 有界 OOXML ZIP/XML 资源门
      │  ├─ 拒绝 CRC 失败、路径碰撞、加密、宏、嵌入件和外部关系
      │  └─ 拒绝 DTD/entity，并限制 member/展开尺寸/压缩比/XML 深度与节点数
      ├─ XLSX：固定 Monthly Data 事实闭集，包含 12 个月度的 profit/customers
      ├─ DOCX：January profit 叙述与 top-three profit 顺序
      ├─ PPTX：January customers 叙述
      └─ CrossDocumentObservation（3 份文档固定分母）
         ├─ missing/已知文档错误 → FAIL
         ├─ extra 只增加 UNEXPECTED_DOCUMENT，不改变分母
         ├─ collision/内部 parser 错误 → ERROR/null
         └─ RunStore 只落协议、reason code 和计数

CombinationDocs-002 当前的事实授权边界
├─ 同批 input XLSX 是 profit/customer 的唯一事实源
├─ 历史 HF answer DOCX 的 December > July > January 只作 known-negative
├─ candidate 在 guest 内仅从已上传 input XLSX 派生 DOCX/PPTX 修正
│  └─ 真实固定 input 通过 typed bridge/evaluator 1.0，XLSX SHA 不变，0 skip
├─ RunVersionVector 直接摘要 known-negative manifest 字节，但不把它当 pass oracle
└─ 仍独立保留 image、pipeline-live 与 versioned-live 实机门禁
```

PPT-003 的 gold manifest 是评价器机器身份与来源证据，不是运行时需要下载
并解析的可变答案文件；纯评价器只使用已经与该 manifest 逐项核对的冻结
SHA-256 映射。input 则复用统一资产下载、缓存、验证和 guest 上传链，不建立
第二套下载协议。所有文件名、相对路径、摘要和原始字节仅存在于 preflight
或 evaluator 的短生命周期内，不能由 Agent 读取，也不得写入 RunStore。
最终 797 身份的 PPT-003 component receipt 已通过原始字节、文件
SHA-256 与 task/environment/component 三身份闭合后进入专用
allowlist；它不使用 Agent final text，只清除 PPT-003 的
pipeline-live blocker，不构成 versioned-live 证据或正式 live 晋级。

SearchWrite-008 已不再使用草案资产；其唯一 typed 合同源、受控
parser、纯 evaluator、两份 baseline 投影与真实 2+2 fixture 已完成
复验，且 production local capability 已正式返回。当前公开
runtime-support 仅保留 pipeline-live 和 versioned-live blocker；当前
仍无 Search 专属 no-Agent candidate，且不因本地 core 闭合而宣称 live。Excel-008 保持原 instruction，但已以 host-only
typed 协议消歧，完成 formal assets、runtime binding、input-only candidate
和固定真实 5+5 fixture 零跳过验证。CombinationDocs-002 已以 input
XLSX 为唯一事实源闭合 input/typed/runtime/candidate；错误 HF answer 只保留
为 audit known-negative 并稳定 FAIL 2/3。两项本地 core 都不替代 component
receipt。仓库中存在的阶段性 parser
或资产文件不等于正式 runtime
support；只有任务专属 canonical、资产、typed capture、纯评价器和
production preflight 同时闭合，且后续完成版本化实机验证后，才能变更
公开支持状态。
