# ADR-0006：评价器专用固定 gold 与离线解析边界

- 状态：已接受，分阶段实现中（本地代码闭环，真实 VM 待验证）
- 日期：2026-08-04

## 背景

OSWorld artifact evaluator 的 actual 来自一次 Attempt 的 guest，gold 则来自
外部 benchmark 资产。二者具有不同信任关系：任务输入必须提供给 Agent，gold
只能由 evaluator 读取。旧实现直接使用 Hugging Face `resolve/main` URL，并在
评价阶段下载预期文件，因此无法保证复现版本，也可能把网络失败误记为 Agent
失败。

`Operation-FileOperate-CombinationDocs-015` 是首个外部 gold 迁移切片。它的
输入是 `references.docx`，输出与 gold 都是 `references.bib`，但输入资产清单
不能兼任 gold 清单，否则通用 OSWorld 环境会把预期答案上传至 guest。

## 决策

输入资产与评价 gold 使用独立声明、独立缓存根和独立公开接口。canonical task
可分别引用 input asset manifest 与 evaluator-only gold manifest；任何环境准备
代码只能消费前者。

gold manifest 使用两个严格、互不混用的 variant。schema v1 download manifest
必须固定 provider、repository、40 位 commit、canonical POSIX 路径、字节数、
SHA-256、媒体类型、许可证据和上游 evaluator provenance。schema v2
private-derived manifest 则固定 canonical input manifest 字节摘要、唯一 source input
的 size/SHA/media、派生协议与工具链、帧选择证据、输出字节与 decoded-RGB
摘要，以及严格的 source-derived 许可证据。两种解析器都拒绝未知/重复字段、
类型混淆、路径穿越和非规范标识。manifest 本身及其引用必须进入 Run
版本向量闭包。

下载是 schema v1 的显式 provisioning 操作。schema v2 不接受 URL，也不允许
`gold fetch`；它只能在受控私有 provisioning host 上由显式 `gold materialize`
从已验证 input 派生。物化过程持有 nofollow descriptor，对 source、工具、PTS
序列、输出与 RGB 身份 fail-closed，并以 no-clobber 方式发布单链接 mode-0600
文件。运行与评价只允许使用已在 host 私有缓存中完成
大小、摘要和媒体门禁的文件，不得访问网络。resolver 通过 descriptor、nofollow
和普通文件检查读取有界字节；不得向调用方返回 host 路径，也不得把 URL、摘要、
正文或原始异常写入 observation、RunStore 或 CLI 错误。

derived gold 只存在于 evaluator host，不得上传 guest、交给 Agent、写入 RunStore、
提交 Git 或公开再分发。manifest 的 Apache-2.0 许可证据只绑定 source dataset
和派生依据；`private_materialization_only` 不授予派生产物的公开再分发权。

actual 缺失属于正常 Agent 失败并得到 0 分；actual 与可信 gold 不匹配同样属于
正常评价失败。gold 未准备、损坏、媒体不符或读取失败属于 evaluator
`UNAVAILABLE/ERROR`，分数必须为空，不能伪装成 Agent 失败或从统计分母中静默
消失。若显式 verify 或 doctor 已发现 gold 缺失/损坏，run 必须在 VM、模型客户
端构造、向模型服务传递凭据和 RunStore 创建前退出；doctor 可以读取环境变量是否
存在，以一次性列出全部门禁结果。若 doctor 后到评价读取之间发生完整性、媒体或
读取故障，则 Attempt 记录 evaluator `ERROR` 且 `score=None`。

## 影响

从零部署多一个显式 v1 fetch 或 v2 materialize，以及统一的 verify 步骤，
但真实 run 可以断网复现，且 Agent
永远无法接触预期答案。每迁移一种新 artifact family，必须先完成对应容器解析
资源边界；BibTeX 文本只需要有界普通文件读取与严格 UTF-8，Office、PDF、图片和
归档不得复用未受限的通用解包路径。

本 ADR 被证明完成的条件包括：manifest/parser、离线 resolver、CLI
fetch/materialize/verify、doctor/preflight、版本向量、隐私回归、production source 以及
至少一次真实 guest 评价全部通过。仅创建 manifest 或纯 metric 不构成完成。

截至 2026-08-12，v1 固定 commit gold 已通过 CLI 实际下载与离线 resolver；
Settings-001 的 v2 合同已在受控私有 host 重现 8.008 秒产物，并以 0.90
阈值拒绝历史约9.042秒已知负例。这些都只是本地合同证据；
尚无与当前代码/资产身份一致的真实 VM task setup、guest 文件 getter 与
带 RunStore v2 版本向量 Attempt，因此 runtime-support 仍保留 setup/getter/gold/
versioned-live 四项 blocker，本 ADR 不据本地测试宣称真实环境完成。
