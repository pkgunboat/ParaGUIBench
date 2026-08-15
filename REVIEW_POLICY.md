# ParaGUIBench Review Policy

本文件定义 ParaGUIBench 的默认代码评审与验收标准。它的目的不是追求理论上最强的防御，而是保证一个公开的 GUI Agent benchmark 能被第三方正常安装、配置、运行并得到可信的评测结果，同时避免维护者的真实凭据或私有资产进入 GitHub 与发布包。

除非在任务开始前显式选择了命名的“官方严格审计” profile，所有贡献均按本文的“普通评测”标准评审。

## 核心目标

默认评审只围绕以下四个目标展开：

1. 第三方可以按照公开文档完成安装、配置、运行和结果检查。
2. task、environment、Agent adapter 与 evaluator 的接口和语义正确，评分可复现。
3. 维护者的真实 API key、cookie、私有资产和不应公开的数据不会被提交到 Git、GitHub release 或公开构建产物。
4. 实现保持清晰、可维护；没有明确故障或需求依据时，不增加防御性状态、依赖、协议和门禁。

以下内容不是普通评测的默认目标：抵御具有本机权限的恶意进程、恶意管理员、恶意代理或依赖注入；企业级 secret 管理；取证级证据链；零信任部署；对每个文件和进程进行密码学身份绑定。这些能力可以作为独立的严格审计 profile 提供，但不得无条件进入普通运行路径。

## 两类验收 profile

### 普通评测（默认，影响合并）

普通评测需要满足：

- README 和安装文档中的主要命令可以直接执行，CLI 参数与实际实现一致。
- task 所需的公开输入、环境准备、Agent 适配器、typed observation 与 evaluator 能形成完整运行链。
- evaluator 使用 guest artifact、结构化 observation 或其他正式输入评分，不使用 Agent final text 代替任务结果。
- benchmark 合同要求 host-only 的 gold 不会作为任务输入暴露给 Agent；这是评测有效性要求。
- tracked 文件、非忽略的待提交文件、测试 fixture、文档、日志样例和发布包中不含真实 API key 或不应公开的私有资产。
- `.env`、本地 secret 文件和私有运行产物保持在 Git 之外；公开示例只使用明显的占位符。
- 正常成功与失败路径不会破坏用户数据，并会清理本项目创建的明显运行资源，使评测可以重复执行。
- 与改动直接相关的测试、生成器和公开清单保持一致。

普通用户如何在自己的机器上保存 key、是否使用代理、是否让本机其他进程读取 key、是否把命令留在 shell history，属于用户自己的运行环境责任，不是本项目普通代码评审的阻塞项。项目不应主动打印真实 key，但普通评审不要求用复杂机制证明 key 在所有本地威胁模型下均不可见。

### 官方严格审计（可选，不默认阻塞）

以下项目只有在任务开始前显式选择了命名的严格审计 profile 时才成为硬门槛；“官方发布”“排行榜”或“受控部署”等场景名称本身不会自动启用整套严格要求：

- versioned live receipt、allowlist、逐字节 provenance 与稳定 SHA 闭包；
- VM terminal、devtools、权限与镜像 hardening 证明；
- `nofollow`、`O_EXCL`、`fsync`、ABA 防护和严格 inode 连续性；
- hostile proxy、恶意本地进程、模块 monkeypatch 或依赖替换防护；
- 一次性 API 预算、精确容器/进程身份与完整资源 inventory；
- 取证级日志、独立 attestation 和全量保护文件复哈希。

严格审计应使用显式命名的 profile、命令或部署流程，例如 `audit-official`，并与普通 `run` 路径分离。严格 profile 的缺失或失败可以记录为风险，但不得自动否定普通评测功能。

既有测试、scanner 或门禁本身也必须按本文分类。若某项检查只验证 `strict_optional` 能力，应移动到严格 profile、降为提示或明确标注适用范围，而不是为了让该检查通过而继续向普通生产路径增加防御代码。测试失败是需要分析的证据，不会自动把其所编码的严格策略升级为普通评测要求。

当前 `scan_repository.py` 同时包含真实 secret 检测以及私网地址、开发者路径等严格规则。在工具和 CI 拆分为默认与严格 profile 之前，后两类无真实 secret、无私有资产的命中属于已知的 legacy tooling mismatch：应通过调整 scanner 规则、测试 fixture 或 CI profile 解决，不得为消除这类红灯向 runtime 增加防御代码。所有贡献仍应运行 scanner，并始终修复真实凭据或私有资产命中。

## 哪些 finding 可以阻塞合并

普通评审中的 blocking finding 必须同时具备高置信度、可复现影响和明确的正常用户路径。典型情况包括：

- 安装、CLI、任务准备、Agent 调用或评分主路径无法工作；
- evaluator 语义错误、结果不稳定，或把 Agent final text 当作正式证据；
- 必需的输入、gold 解析、运行清理或公开清单损坏；
- 真实凭据或私有资产将进入 Git/GitHub/release；
- 改动导致相关测试、生成器、发布包或公共接口回归；
- 正常路径存在明显的数据破坏、无限资源泄漏或不可恢复状态；
- quickstart 的主要命令与代码不一致。

以下 finding 默认属于 `strict_optional`，不能单独阻塞普通贡献：

- 恶意模块预加载、monkeypatch、恶意本地进程或管理员攻击；
- 用户自己的 shell history、代理、endpoint 或凭据文件管理方式；
- 缺少逐依赖 SHA、inode 证明、原子落盘或竞态防护；
- 缺少 VM terminal/devtools hardening attestation；
- 缺少官方 live receipt 或 allowlist，但普通运行与评分链本身可以工作；
- 没有正常使用场景复现的理论 symlink、TOCTOU 或环境变量攻击；
- 纯格式偏好、无实际缺陷支撑的额外负向测试或重复校验。

`strict_optional` 只有在以下情况之一成立时才可升级为 blocking：任务开始前明确选择了命名的严格 profile；已有真实事故或正常路径复现；或该问题会直接破坏普通评测正确性。若属于后两种情况，应根据实际影响直接记录为 `core_blocking`，而不是仅凭场景标签升级。

## 防止过度设计的检查

提出新的防御机制前，作者和 reviewer 应回答：

1. 它防止哪个普通用户可遇到的具体故障？
2. 是否存在最小复现、测试或真实证据？
3. 简单的输入校验、错误提示、文档或单元测试是否已经足够？
4. 它增加了多少代码、依赖、状态、运行步骤和维护成本？
5. 它是否更适合放入可选的官方严格审计 profile？

如果没有具体的普通评测故障，新机制应保持可选或暂不实现。不得仅以“理论上可能发生”为理由叠加重复 loader、重复身份系统、一次性 wrapper 或大规模 attestation。

确有必要新增防御时，应提供：明确的需求或威胁、一个能先失败后通过的最小测试、可测量的完成条件，以及相对于现有机制不可替代的理由。

## Reviewer 输出规范

每条 finding 应包含：

- 分类：`core_blocking` 或 `strict_optional`；
- 置信度与实际影响；
- 最小复现或代码证据；
- 最小修复范围；
- 是否改变普通评测结果或只影响严格审计。

默认只有高置信度的 `core_blocking` finding 可以阻止合并。Reviewer 不应把“可以进一步加固”写成“当前功能不可用”，也不应在修复过程中持续扩大威胁模型。

## 普通评测的完成定义

一项改动在以下条件满足后即可按普通评测标准完成：

- 相关功能与回归测试通过；
- 主要安装、运行和评分命令与文档一致；
- 受影响的 manifest、网站数据或生成产物已用正式生成器更新；
- 仓库与发布候选中没有真实 secret 或不应公开的私有资产；
- reviewer 未发现未解决的 `core_blocking` finding。

`local_ready` 不等于 `live_validated`。缺少官方 live evidence 时应如实保持相应状态，但不能仅据此否定普通用户已经可以运行和评测该任务。反过来，单独存在 evaluator 核心也不能被表述为完整 runtime support。

## 与其他文档的关系

- 明确的任务需求和维护者授权可以选择更严格的 profile 或提高验收门槛，但不能豁免真实凭据、私有资产进入公开仓库或发布包，也不能允许虚假声明 runtime support 或评测结果。
- 任务开始后不得把新增的 `strict_optional` 要求追溯升级为本任务 blocker；新发现的普通功能或评测正确性缺陷仍可直接按 `core_blocking` 处理。
- 本文定义普通贡献的默认合并门槛。
- [SECURITY.md](SECURITY.md) 提供更严格的推荐安全实践；其中超出本文普通威胁模型的项目，在普通评审中按 `strict_optional` 处理。
- [CONTRIBUTING.md](CONTRIBUTING.md) 说明贡献流程和应运行的项目门禁。

如需提高默认门槛，应先更新本文，说明新增门槛解决的真实问题、对普通用户的影响和相应维护成本，而不是在单个 PR 中隐式引入新的安全模型。
