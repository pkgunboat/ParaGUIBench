# OSWorld Ubuntu 环境边界

ParaGUIBench 不把 VM、容器层或运行时 overlay 提交到 Git。固定来源、归档大小、
SHA-256 和容器 digest 记录在 `image-manifest.json`；部署工具只允许
download/pull，不承担第三方镜像的再分发授权。

首个 live gate 使用单个 disposable Docker/KVM session。控制端口和 VNC 端口
只绑定 `127.0.0.1`，qcow2 以只读 bind mount 注入。runtime 不扫描、不停止也不
复用已有 QEMU 或容器；清理时只使用本次 `docker run` 返回的容器 ID。

已选择固定 HF ZIP 直接派生的 6bf 镜像作为新的开源默认
environment identity；历史 6d reference 镜像只能作为独立 legacy
identity，二者不得混合记录。`image-manifest.json` 已升级为
schema v2，固定 archive→ZIP local/central member→output 的路径、
大小、CRC、extra records 和 SHA-256；`extracted_image.sha256` 为
`6bf667a852b3c307f61d9f09c42559351f45e0607e428b4997becf534cf4d313`。

当前 `status=verified_reproducible_materialization`：冻结 cleanroom 源码已在
受控 Linux 上完整执行 manifest-driven materializer，并通过独立审计，
因此镜像物化层 `live_run_ready=True`。该结论只清除统一的镜像物化 blocker；
每任务 component、版本化 Attempt、receipt 与 allowlist 门禁仍独立失败关闭，
当前仍为 233 个 `blocked`、0 个 `live_validated`。不得用文件名、大小、mtime
或“镜像一直没变”的假设替代 manifest 身份与正式物化流程。
2026-08-12 受控主机只读 recipe 实证 ID 为
`20260812_114_ubuntu_qcow2_zip_recipe_v1`，脱敏证据摘要为
`a146640259a85b054ea40cee512bd46eb9fe2393db8fbc70c2eddb227946511c`；
原始证据含部署元数据，不进入仓库或发布包。固定身份见同目录
`image-manifest.json`，许可与再分发边界见
`docs/licenses/third-party-sources.md`。
正式物化外置审计 evidence ID 为
`osworld-v2-pending-78d36680-formal-materialization`，SHA-256 为
`855f4dd73021a21e8d8a60b4d8de1d571131492865b62b851cc2000ce3d08b82`；
仓库只记录该脱敏锚，不复制含部署元数据的证据正文。

该环境的 controller HTTP 协议源自 OSWorld。新实现只保留 screenshot、
argv-only execute、desktop-path 和受控 upload 等最小接口，移除了旧项目的
SSH-to-self、密码命令拼接和完整环境变量日志路径。来源与许可边界见
`NOTICE` 和 `docs/licenses/third-party-sources.md`。
