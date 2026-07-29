# 第三方来源与发布边界

本文档是 0.1 preview 的第三方来源台账。只有完成文件级 provenance、许可证
兼容性和必要 notice 核验的内容才能进入公开发布。“来源已固定”“摘要已验证”
与“允许重新分发”是三个不同结论，不能相互替代。

| 组件或资产 | 已知来源 | 发布方式 | 当前证据与开放门禁 |
|---|---|---|---|
| OSWorld controller/protocol 派生实现 | [xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld) | 只保留项目实际使用的最小接口；记录上游来源与本项目修改 | 上游代码仓标注 Apache-2.0；仍需固定逐文件 upstream commit 映射，并核对 notice 与修改说明 |
| OSWorld Ubuntu VM 归档 | `xlangai/ubuntu_osworld`；revision、对象、大小和归档摘要见 `environments/osworld/image-manifest.json` | download-only；qcow2、归档和 overlay 不进入 Git | 解压后 qcow2 SHA-256 已在参考部署对全文件验证，状态为 `verified_on_reference_deployment`；镜像所含软件、数据和再分发边界仍待逐层审计 |
| OSWorld Docker/KVM 容器 | 固定 digest 见 `environments/osworld/image-manifest.json` | pull-only，不随仓库分发 | 可变 tag 已禁止；上游 build recipe、镜像层来源及许可证清单仍是公开发布门禁 |
| 代表 FileSearch 任务资产 | `leeLegendary/Parallel_benchmark`；固定 revision 和逐文件摘要见 `benchmark/assets/manifests/` | download-only；PDF/JPG 不进入 Git | 来源与完整性已固定并用于参考运行；上游未声明明确 license，内容权利和再分发许可仍未闭环 |
| 233 个 canonical task definition | ParaGUIBench 标注，以及来源于或改编自 VeriWeb、OSWorld、WebMall 的任务 | 由 `release-v1.json` 固定任务路径与摘要 | 定义已全部迁入；仍需完成逐任务 provenance、修改状态和 benchmark data license 审计 |
| WebMall logical URL 与 checkout fixture | WebMall 环境语义；fixture 为本项目编写的合成公开测试数据 | 任务仅保存 `webmall://store-*`、fixture reference 和 schema；不打包服务数据库或账户快照 | 91 个任务的 logical URL、16 个 checkout/end-to-end fixture 引用和安全投影已完成；WebMall 服务、商品数据与完整 runtime 的来源/许可和 live validation 尚未闭环 |
| OnlyOffice 与网页/文档语料 | 各服务、数据集和文档原始提供方 | 优先发布构建或获取说明，不默认打包服务数据或文档副本 | 需逐资产确认来源、版本、条款和可公开性 |
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

当前可验证结论仅为：OSWorld 解压镜像摘要已在参考部署匹配，代表任务可按固定
manifest 下载并运行；这不是 VM、容器层或任务资产再分发许可已经完成的声明。
