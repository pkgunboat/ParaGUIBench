# OSWorld Ubuntu 环境边界

ParaGUIBench 不把 VM、容器层或运行时 overlay 提交到 Git。固定来源、归档大小、
SHA-256 和容器 digest 记录在 `image-manifest.json`；部署工具只允许
download/pull，不承担第三方镜像的再分发授权。

首个 live gate 使用单个 disposable Docker/KVM session。控制端口和 VNC 端口
只绑定 `127.0.0.1`，qcow2 以只读 bind mount 注入。runtime 不扫描、不停止也不
复用已有 QEMU 或容器；清理时只使用本次 `docker run` 返回的容器 ID。

`extracted_image.sha256` 是在参考部署机上对解压后 qcow2 全文件逐字节计算并
固定的 SHA-256；它不同于 zip 归档摘要。`doctor` 与 live run 必须在启动容器
前重新计算并匹配该值，不得用文件名、大小、mtime 或“镜像一直没变”的假设
代替完整性证据。

该环境的 controller HTTP 协议源自 OSWorld。新实现只保留 screenshot、
argv-only execute、desktop-path 和受控 upload 等最小接口，移除了旧项目的
SSH-to-self、密码命令拼接和完整环境变量日志路径。来源与许可边界见
`NOTICE` 和 `docs/licenses/third-party-sources.md`。
