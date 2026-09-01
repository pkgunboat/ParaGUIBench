# 第三方来源与发布边界

本文档是 0.3 preview 的第三方来源台账。只有完成文件级 provenance、许可证
兼容性和必要 notice 核验的内容才能进入公开发布。“来源已固定”“摘要已验证”
与“允许重新分发”是三个不同结论，不能相互替代。

| 组件或资产 | 已知来源 | 发布方式 | 当前证据与开放门禁 |
|---|---|---|---|
| OSWorld controller/protocol 派生实现 | [xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld) | 只保留项目实际使用的最小接口；记录上游来源与本项目修改 | 上游代码仓标注 Apache-2.0；仍需固定逐文件 upstream commit 映射，并核对 notice 与修改说明 |
| OSWorld `ubuntu_osworld_file_cache` 任务与评价资产 | [xlangai/ubuntu_osworld_file_cache](https://huggingface.co/datasets/xlangai/ubuntu_osworld_file_cache) | schema-v1 资产固定 commit/大小/SHA 后 download-only；Settings-001 schema-v2 gold 只能从已验证 MP4 在同一操作者私有 host 派生；input/gold 缓存分离，字节均不进 Git | 官方 dataset card 当前声明 Apache-2.0；该证据只绑定 source dataset 来源台账和 Settings 的 `derived_from_source_input` 依据，不由 OSWorld 源码许可推导，不替代逐文件 provenance。**2026-08-31 政策修订（对齐 2026-08-26 已执行的发布事实）**：Settings-001 派生 gold PNG 已随该日期的资产修复 commit 进入公开数据集 `answer_files/9b5220d5…/landscape.png` 并被评价器公开拉取；基于上游 MP4 为 Apache-2.0 且数据集卡已带 attribution，该单帧派生 PNG 的公开托管在此授权，早于该日期的 `private_materialization_only`"严禁公开 hosting"表述对这一文件不再适用（该约束对其余私有派生物仍然有效）。gold 字节仍不进 Git/guest/RunStore。该 gold manifest 的 `distribution_policy` 与 `license.distribution` 机器字段仍为旧值，与 `license_status` 一同留待后续发布节点统一翻转，翻转前不作为分发政策依据。`CombinationDocs-015`、`BatchOperation-003` 及其他 v1 资产保持固定 fetch/verify 流程。真实 VM setup/getter/gold 路径仍受 live 门禁，完整性或本地派生闭合均不等于可再分发或 live 支持 |
| OSWorld Ubuntu VM 归档 | `xlangai/ubuntu_osworld`；revision、对象、大小和归档摘要见 `environments/osworld/image-manifest.json` | download-only；qcow2、归档和 overlay 不进入 Git | 固定 ZIP 的直接解压结果为 6bf，而历史 reference 环境为 guest-visible 内容不同的 6d；二者不能继续写成同一来源链。6bf 已裁定为默认环境，其冻结 cleanroom 可重现物化已完成独立审核；这只核验字节派生，不授予重新托管或再分发权，也不替代每任务 live 门禁。固定身份见 `environments/osworld/image-manifest.json` 与 `environments/osworld/README.md` |
| OSWorld Docker/KVM 容器 | [happysixd/osworld-docker](https://hub.docker.com/r/happysixd/osworld-docker)；正式身份 `happysixd/osworld-docker@sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9` | pull-only，不随仓库分发 | 这是 OSWorld 文档使用的公开 QEMU/KVM 包装镜像，不是 guest qcow2，也不是未公开的 sshfs 派生层。可变 tag 已禁止；上游 build recipe、镜像层来源及许可证清单仍是公开发布门禁 |
| 生成器管理的 FileSearch / Operation FileOperate 任务资产 | `leeLegendary/Parallel_benchmark` 与已记录的 xlang 固定源；revision 和逐文件摘要见 `benchmark/assets/manifests/` | download-only；PDF/JPG/PPTX/DOCX/XLSX/文本不进入 Git | 来源与完整性已固定；两个含 Office `~$` 锁文件的 ReadonlyPPT 候选未被迁移。生成器管理的任务资产（2026-08-31 口径：49 个 manifest、193 个文件——OOXML 154、JPEG/JPG 25、PDF 9、文本 5；早前"43 任务 158 文件"为历史口径）按 ZIP 完整性、main content type、无 VBA、无路径异常与严格解码检查固定。2026-08-31 起 `leeLegendary/Parallel_benchmark` 数据集决定整体采用 Apache-2.0，HF 侧 license tag（`license:apache-2.0`）、数据集卡与 LICENSE 全文已于 2026-09-01 生效（两个外部上游 OSWorld/VeriWeb 均已实证 Apache-2.0）；manifest 内 `license_status` 字段的翻转属哈希链变更（48 个 manifest 的字节哈希被生产代码与 gold manifest 共约 55 处钉定，含 `asset_manifest_sha256` 与 runtime-support/prepare/evaluator 内嵌校验），留待后续发布节点统一执行，在此之前该字段不作为许可声明依据 |
| VeriWeb（原名 VeriGUI）任务改编 | [2077AIDataFoundation/VeriWeb](https://huggingface.co/datasets/2077AIDataFoundation/VeriWeb)（arXiv:2508.04026） | 仅改编为 29 个 WebSearch 任务的定义与答案（`task_source=VeriGUI`）；不分发 VeriWeb 原文件或轨迹 | 2026-08-31 经 HF API 实证上游 `license: apache-2.0`、非 gated、公开；改编任务在 Apache-2.0 下随本数据集再分发需带上游 attribution（数据集卡已列） |
| 233 个 canonical task definition | ParaGUIBench 标注，以及来源于或改编自 VeriWeb、OSWorld、WebMall 的任务 | 由 `release-v1.json` 固定任务路径与摘要 | 定义已全部迁入。2026-08-31 完成 task_source 级 provenance 盘点：OSWorld 派生 28、VeriWeb 派生 29、自制 176（含 WebMall 91、self 57）；两个外部上游均为 Apache-2.0。逐任务的修改状态细录仍留待正式发布补齐 |
| WebMall logical URL 与 checkout fixture | WebMall 环境语义；fixture 为本项目编写的合成公开测试数据 | 任务仅保存 `webmall://store-*`、fixture reference 和 schema；不打包服务数据库或账户快照 | 91 个任务的 logical URL、16 个 checkout/end-to-end fixture 引用和安全投影已完成；WebMall 服务、商品数据与完整 runtime 的来源/许可和 live validation 尚未闭环 |
| OnlyOffice DocumentServer | [onlyoffice/documentserver](https://hub.docker.com/r/onlyoffice/documentserver)；正式身份 `onlyoffice/documentserver@sha256:b9e3c35eab182d3de822a53b109b0f27070f6eacea3b1388b9c50d1182f638f2` | pull-only，不随仓库分发；运行状态必须放在仓库外 | 第一版只承诺单实例实验室部署。镜像层许可证与再分发权仍待复核。share service 为本项目源码，容器依赖见 `deploy/onlyoffice/requirements.txt`。单元测试或 compose config 通过不等于任务 live_validated |
| 模型权重与第三方模型 API | 对应模型或服务提供方 | 不随仓库分发；通过 integration adapter 和 Credential Reference 使用 | 禁止提交权重缓存、API key、endpoint 值、请求记录和认证响应 |
| Python 直接及传递依赖 | `pyproject.toml` 与未来锁文件声明的上游包 | 通过包管理器安装，不复制源码 | 当前 extras 见依赖树；release 仍需生成传递依赖许可证清单并人工复核 |

迁移每个第三方候选文件时应记录目标路径、上游仓库 URL、固定 commit、上游
相对路径、原始许可证、copyright/notice、是否修改及修改概要。对于数据、镜像、
模型、网页和文档资产，还必须单独判断下载使用、随论文归档、Git 分发和镜像
再分发的权限；源码许可证不能覆盖这些资产。

以下内容默认不进入公开 Git：真实凭据、cookie、账号快照、私有配置、内部主机
信息、运行日志、原始审计包、VM 镜像、模型权重、数据库转储、下载缓存以及
来源或授权不明确的任务资产。缺失条款时必须延后相关内容，不能用
ParaGUIBench 的 Apache-2.0 `LICENSE` 覆盖。

当前可验证结论仅为：OSWorld 上游归档、其直接派生的 6bf 默认镜像可重现
物化以及历史 6d reference 镜像身份已分别核验；固定上游 ZIP 与历史 6d 的
派生关系仍不成立。已迁移 FileSearch、Operation FileOperate 与
Batch003 任务资产可按固定
manifest 匿名下载并通过本地闭集校验。这不是 VM、容器层或任务资产再分发许可
已经完成的声明。

2026-08-31 数据集 license 审计补充：任务级来源盘点为 OSWorld 派生 28、
VeriWeb 派生 29（上游 Apache-2.0 已实证）、自制 176；`leeLegendary/Parallel_benchmark`
数据集整体决定采用 Apache-2.0，HF 侧 license tag、数据集卡与 LICENSE 全文已于
2026-09-01 生效；数据集内已审计的历史重复文件与探针残留（A 档）已于同日（2026-09-01）清理完毕，
benchmark 内容未受影响（Settings-001 gold URL 与抽查资产哈希复验通过）。B/C 档
遗留项（旧稿 docx、旧任务定义 json、`result_files/` 运行产物、10 个未钉定目录）
待数据集维护者确认后处理；清理不改变任何 manifest 钉定路径（规范内容仅
`benchmark_dataset/` 与 `answer_files/`）。
