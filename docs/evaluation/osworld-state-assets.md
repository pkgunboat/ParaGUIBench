# OSWorld state 资产与 evaluator-only gold 固定矩阵

本矩阵覆盖 13 个 artifact-family 任务。历史远程草案固定来源身份、相对路径、
guest 用途、期望媒体类型和 evaluator gold logical key；只有完成真实下载和
字节核验的条目才能标为 `integrity_verified`。这些草案共含 71 个 input 和
15 个 remote gold 引用；71 个 input 和 14 个 gold 已完成 size/SHA-256 核验。
Settings-001 的旧远程图像仍保留为 schema-v1 `integrity_unverified` 历史草案，
不得冒充新的正式 gold。该任务的正式 schema-v2 gold 则从已验证的 canonical
MP4 input 私有派生，不使用 remote gold locator。`CombinationDocs-011` 的
`Invoice # 243729.pdf` 同时作为 input 与 gold。

依赖关系保持单向：

```text
canonical legacy URL / strict manifest state ─────┐
ArtifactEvidenceSpec source/evaluator/contract ───┼─> osworld_state_asset_drafts.py
最终 OSWorld source task config（审计输入）───────┘              │
                                                                 ├─> 13 input drafts
                                                                 └─> 13 gold drafts

26 drafts ─> 固定 revision 远端列表/下载 ─> size+SHA+magic/media 核验
          ─> 逐文件 provenance/license 核验 ─> 严格 runtime manifests
          ─> canonical task asset_manifest/gold_manifest ─> versioned live gate
```

`artifact_evidence_specs.py` 决定 source task、source evaluator、source
contract SHA 和 15 个 gold logical key；canonical task 决定公开任务 UID，以及
legacy URL 或 strict manifest 两种互斥资产状态；最终 OSWorld task config 只作为
文件名、guest path 和 evaluator expected path 的审计来源，不成为运行时依赖。
生成器不联网、不读凭据，也不修改 runtime 或 evaluator。

`verified-bytes` 表示已从固定 xlang commit 匿名下载，并通过 production
fetcher、size/SHA-256、MIME/magic、ZIP/OOXML 成员与 CRC 闭集；
`verified-path` 只表示路径已由固定 OSWorld source task config 核对，不声明
字节已经晋升。

| Canonical task | Input | Gold | Input 路径证据 | 草案与正式清单 |
|---|---:|---:|---|---|
| `Operation-FileOperate-BatchOperation-003` | 1 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-BatchOperation-003.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-BatchOperation-003.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-BatchOperation-003.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-BatchOperation-003.json) |
| `Operation-FileOperate-CombinationDocs-009` | 2 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-009.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-009.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-009.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-009.json) |
| `Operation-FileOperate-CombinationDocs-010` | 1 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-010.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-010.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-010.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-010.json) |
| `Operation-FileOperate-CombinationDocs-011` | 4 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-011.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-011.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-011.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-011.json) |
| `Operation-FileOperate-CombinationDocs-012` | 15 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-012.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-012.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-012.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-012.json) |
| `Operation-FileOperate-CombinationDocs-013` | 19 | 2 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-013.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-013.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-013.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-013.json) |
| `Operation-FileOperate-CombinationDocs-014` | 20 | 2 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-014.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-CombinationDocs-014.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-014.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-014.json) |
| `Operation-FileOperate-SearchAndWrite-001` | 1 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-001.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-001.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-SearchAndWrite-001.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-SearchAndWrite-001.json) |
| `Operation-FileOperate-SearchAndWrite-003` | 2 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-003.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-003.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-SearchAndWrite-003.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-SearchAndWrite-003.json) |
| `Operation-FileOperate-SearchAndWrite-005` | 1 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-005.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-005.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-SearchAndWrite-005.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-SearchAndWrite-005.json) |
| `Operation-FileOperate-SearchAndWrite-009` | 1 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-009.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-SearchAndWrite-009.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-SearchAndWrite-009.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-FileOperate-SearchAndWrite-009.json) |
| `Operation-FileOperate-Settings-001` | 2 | 1 | xlang input / private derived v2 gold | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-FileOperate-Settings-001.input.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-FileOperate-Settings-001.json) · [legacy unverified v1 gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-FileOperate-Settings-001.gold.draft.json) · [formal derived gold](../../benchmark/gold/manifests/Operation-FileOperate-Settings-001.json) |
| `Operation-WebOperate-SearchAndWrite-001` | 2 | 1 | xlang / verified-bytes | [input draft](../../benchmark/assets/manifests/osworld-state-drafts/Operation-WebOperate-SearchAndWrite-001.input.draft.json) · [gold draft](../../benchmark/gold/manifests/osworld-state-drafts/Operation-WebOperate-SearchAndWrite-001.gold.draft.json) · [strict input](../../benchmark/assets/manifests/Operation-WebOperate-SearchAndWrite-001.json) · [evaluator gold](../../benchmark/gold/manifests/Operation-WebOperate-SearchAndWrite-001.json) |

全部路径统一固定到 xlang commit
`711e0811642364e7aa8f10a8918367d0b626d578`。旧 Lee/tree 映射经审计确认为
错误路径，已经删除；对应 input 改由固定 OSWorld source task config 提供路径
证据。71 个 input 与 14 个 gold 已完成 size/SHA-256 核验，共 85 条
`integrity_verified`；1 个历史 remote gold 仍为 `integrity_unverified`。该草案条目的
媒体声明只是路径台账，不赋予旧 9.042 秒图像 schema-v2 或正式 gold 身份。

Batch003 在固定 revision 上完成匿名 production fetch/verify。输入
`raw_book.zip` 为 1,091,801 bytes，SHA-256 为
`f4c410119a88653225d8016d2594ae395d5b020e7b40067af0e72f0754b3c22e`；
gold `book.zip` 为 2,935,633 bytes，SHA-256 为
`5d028f5cb57e8f04fd8e5a65370959da91e7c873601bc1fcff9dc8ff5b72005f`。
两份 ZIP 均通过成员安全与 CRC 闭集。新增 8 个任务又通过 production 匿名
fetch/verify，覆盖 30 个 input 与 9 个 evaluator-only gold；39 个对象的
MIME/ZIP/OOXML/CRC 闭集均通过，媒体分布为 PDF 19、PPTX 2、XLSX 12、
DOCX 3、ZIP 1、CSV 1、TXT 1。缓存仅作 download-only 验证，不进入 Git；
gold 缓存与 input 缓存物理、接口分离。

最后 4 个任务的 40 个 input 与 4 个可晋升 gold 也从同一固定
revision 匿名获取；其中 CombinationDocs-011 有 1 份 input/gold 共用字节，
因此共 43 个唯一对象。这些对象通过 production fetcher、size/SHA-256、
PDF/MP4 magic 与 OOXML/ZIP 成员、主类型、CRC 闭集。Settings 的旧
`landscape.png` 只作为已知负例与历史草案，未进入正式 schema-v2 manifest。

xlang 数据卡声明 `Apache-2.0`，该状态已固定到版本化 dataset 台账；它只支持
repository 级来源说明和 `download_only` 策略，不替代逐文件 provenance。
这些任务仍只能按清单下载，不将第三方字节纳入 Git 或对外再分发。

`Settings-001` 的指令与初始 input 均保持不变。正式 schema-v2 manifest 固定
`landscape.mp4` 的 size/SHA-256，并以协议化的 FFmpeg/ffprobe 8.1.1 工具链选取
第一个 PTS 不小于 8.000000 秒的帧：index 240、PTS 8.008000，前帧
PTS 7.974633。产物 PNG 与 decoded-RGB 摘要均被固定，评价阈值统一为 0.90。
旧约 9.042 秒图像的生产指标得分为 0.7960269769984115，因而是必须失败的已知负例。
派生 PNG 只允许同一操作者的私有 host provisioning：不进 Git、guest、Agent 或
RunStore，不公开再分发。manifest 中 Apache-2.0 许可证据只绑定 source dataset
及派生依据，不额外授予派生 PNG 的公开再分发权。

13 个任务已完成 input 资产切换；它们的 strict input manifest、source
start context 与 setup 绑定已全部
闭合，13 个任务同时闭合正式 gold 身份。`Settings-001` 的 canonical
任务现显式声明上述 schema-v2 `gold_manifest`，但它仍不在当前 12 项
component candidate/receipt/promotion 闭集内，因此任何现有 component receipt 都不能为其清除 live 门禁。
历史上 3 个 source start-context 歧义也已由固定 source config 和 task-specific
绑定唯一解析；当前投影不再携带 ambiguity blocker。

其余 12 项已接入不运行 Agent 的 task-scoped component candidate；它在
同一 owned VM 内完成 setup、getter 和真实 gold resolver/media/projection/
metric 链，成功 close 后才能输出脱敏 receipt。正式 component
allowlist 当前仍为空，所以未清除任何 getter/gold/setup blocker。即使
未来 receipt 经人工审核和版本控制加入 allowlist，它也只能清对应
任务的 G/D/S，不会清镜像或 versioned-live blocker。

确定性检查命令为：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-dev/bin/python \
  scripts/benchmark/osworld_state_asset_drafts.py check --repo-root .
```
