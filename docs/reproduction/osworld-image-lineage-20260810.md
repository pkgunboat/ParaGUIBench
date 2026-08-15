# OSWorld 镜像来源链核查（2026-08-10）

本记录区分公开可重现的上游归档与历史论文环境，避免把两个不同的 qcow2 误写成同一条派生链。核查在隔离的参考 Linux 部署机上完成；未读取模型凭据，也未把镜像字节加入源码或 cleanroom 发布包。

固定来源 `xlangai/ubuntu_osworld@a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133` 的 `Ubuntu.qcow2.zip` 为 `12,273,896,463` bytes，SHA-256 为 `b795b6cd4c69b252c1b4f10150a347795555032501b60fd031751ed09b896712`。归档只有一个普通成员 `Ubuntu.qcow2`；直接解压结果为 `24,460,197,888` bytes，SHA-256 为 `6bf667a852b3c307f61d9f09c42559351f45e0607e428b4997becf534cf4d313`。

2026 年 7 月历史实机报告使用的 reference qcow2 来自旧部署缓存，大小为 `23,668,785,152` bytes，SHA-256 为 `6d8056d8b8ea15578969d60c16be2b89f46a41f6dc86ba201e3037902aaca97e`。现有记录没有给出从上述固定 ZIP 生成该文件的确定性步骤；历史脚本只对既有 6d 文件执行无损 zstd 压缩、解压或复制。

两份镜像均为 qcow2、virtual size 均为 50 GiB、无 backing file，`qemu-img check` 均返回成功。使用 QEMU 8.2.2 执行非 strict `qemu-img compare` 仍返回 1，首个 guest-visible 内容差异位于 offset `2,097,664`。因此差异不能解释为仅有 qcow2 allocation 或物理布局不同。

现已裁定把固定 ZIP 直接派生的 6bf 镜像设为新的开源默认
environment identity。历史 6d 镜像仅保留为独立 legacy identity；如需
重现它，仍须先恢复其不可变公开来源或确定性生成 recipe，并确认
重新托管许可。6bf 与 6d 的运行结果必须分层记录，不得混合汇总。

`environments/osworld/image-manifest.json` 已升级为 schema v2，并以闭集 recipe
固定 archive→ZIP local/central member→output 的路径、大小、CRC、extra records
与 SHA-256。`extracted_image.sha256` 现为
`6bf667a852b3c307f61d9f09c42559351f45e0607e428b4997becf534cf4d313`。
2026-08-12 受控主机只读实证 ID
`20260812_114_ubuntu_qcow2_zip_recipe_v1` 的脱敏证据摘要为
`a146640259a85b054ea40cee512bd46eb9fe2393db8fbc70c2eddb227946511c`；
原始证据含部署元数据，不进入仓库或发布包。

当前 `status=verified_reproducible_materialization`：冻结 cleanroom 源码已在
受控 Linux 上通过正式薄 CLI 完整运行版本化安全 materializer，新产物的
大小/SHA、regular/0400/nlink=1/独立 inode、`qemu-img check=0` 与无 backing
均通过复核；原 ZIP、原 6bf、旧诊断产物与非 owned inventory 的前后身份不变。
外置审计 evidence ID 为
`osworld-v2-pending-78d36680-formal-materialization`，SHA-256 为
`855f4dd73021a21e8d8a60b4d8de1d571131492865b62b851cc2000ce3d08b82`；
证据正文含部署元数据，不进入仓库或发布包。由此镜像物化层
`live_run_ready=True`，233 项统一的
`osworld_vm_image_materialization_unverified` 已清除；每任务 component 与
版本化 live Attempt 门禁仍保留，因此正式状态仍是 233 个 `blocked`、0 个
`live_validated`。不得用手工填写本地缓存摘要、文件名、mtime 或历史 6d
冒充正式物化流程。历史 6d 上的 Settings-003 无模型 held-fd PDF smoke 只能
证明旧环境的启动准备链曾工作，不能提升任何任务为 `live_validated`。
