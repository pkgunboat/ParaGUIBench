# 历史无版本冒烟运行：2026-07-29

本文档记录 ParaGUIBench 0.1 preview 的首个成功端到端门禁。记录已移除主机、
网络、开发者路径、模型 endpoint 和凭据值，只保留公开复现所需的稳定身份与
结果。它证明当时的一个具体任务—Agent—环境—评价器组合曾能真实运行，不代表 233
个 canonical 任务都已获得 runtime 支持。

> [!WARNING]
> 该 Run 早于 RunStore v2，没有 source/Agent/evaluator/protocol/environment
> 版本向量。它现已从当前 `live_validated` 计数中降级，只作为
> `LEGACY_UNVERSIONED` 历史冒烟证据，不能用于当前发布就绪或论文复现声明。

| 字段 | 参考结果 |
|---|---|
| Run ID | `run-20260728T170746Z-7457becb` |
| Task ID | `InformationRetrieval-FileSearch-Readonly-001` |
| Agent System | GUI-only Seed18 |
| 步数配置 / 实际步数 | 最大 24 步 / 第 15 步 `finished` |
| 执行终态 | `SUCCEEDED` |
| 评价终态 | `PASSED` |
| 分数 | `1.0` |
| 部署门禁 | `doctor` 10/10 `PASS` |
| 自动化测试 | 141 tests passed |
| 定向 secret 扫描 | `secret_matches=0` |
| 本次 owned container 残留 | 0 |
| RunStore 权限 | 目录 `0700`，文件 `0600` |

## 验证边界

参考验证在 Linux x86-64 上从新的源码部署目录和新的 Python 3.12 虚拟环境
开始，安装 `.[live,dev]` 后执行完整自动化测试、release validator、runtime
support validator 和 repository secret scanner。OSWorld qcow2 位于 checkout
外部缓存；该次验证没有重新下载大型归档，而是在容器启动前对全文件重新计算
SHA-256，并与当时历史 manifest 中记录的 6d reference 摘要一致。
当前 `environments/osworld/image-manifest.json` 已因上游 ZIP 直接解压的
6bf 镜像与该 6d 环境内容不同而改为 fail-closed；本记录不能为当前
manifest 提供可重现来源证据。
任务的四个 download-only 资产同样按逐文件大小和 SHA-256 闭集校验。

`doctor` 在真实运行前同时验证：

1. Python 版本；
2. KVM 可用性；
3. Docker daemon；
4. 固定 digest 的容器镜像；
5. 解压后 qcow2 的完整 SHA-256；
6. 任务资产缓存闭集；
7. controller loopback 端口；
8. VNC loopback 端口；
9. API key 环境变量引用存在；
10. 模型 base URL 环境变量引用是有效 HTTPS URL。

两个凭据相关门禁只输出 `PASS`/`FAIL`，不输出值、长度、摘要或前后缀。成功
Run 完成后又以当前注入的 API key 和 base URL 做精确值扫描，二者在 RunStore
中的总命中数为零。真实
运行日志保存 Agent 步数、终止类型、执行/评价终态和确定性评价摘要，不保存
key、endpoint 值、模型原始响应或完整 final output。执行结束后，runtime 按
本次创建时返回的精确容器 ID 完成清理；owned container 复查结果为零。

本次成功运行使用 CLI 显式配置 `--max-steps 24`，Agent 在第 15 步调用
`finished`，exact 模式经显式别名匹配通过。紧邻该 run 的一次默认 18 步尝试
已完成环境和 Agent 生命周期，但以 `max_steps` 终止并得到正常的
`FAILED / 0.0` 评价；它没有发生基础设施或评价器错误。该对照说明 live 模型
轨迹具有随机性；该记录在当时曾表示部署链成功闭环，但不表示每次采样
必然通过。

## 结论及其限制

该 run 的执行链是：

```text
固定 release task
  -> 资产闭集校验与 guest 上传
  -> disposable OSWorld Docker/KVM session
  -> GUI-only Seed18
  -> exact evaluator
  -> 独立 execution/evaluation 终态
  -> task-scoped RunStore
```

因此，当前可以严格声称：

- 233 个 canonical task definition 已迁入；
- 逐任务 runtime support manifest 已覆盖 233 个任务；
- 上表所列组合在当时取得 `SUCCEEDED` / `PASSED` / `1.0` 的真实结果；
- 因缺少 RunStore v2 版本向量，当前 233 个任务均为 `blocked`，其中 0 个为 `live_validated`。

当前不能据此声称完整 ParaGUI 多 worker 系统、其余任务类别、全部 evaluator、
完整 WebMall 或论文汇总指标已经复现。后续每增加一个 `live_validated` 条目，
都需要通过同样的静态完整性、secret、环境门禁和真实任务评价流程。

复现命令和安全注入方式见
[`docs/deployment/osworld-linux.md`](../deployment/osworld-linux.md)；机器可读支持
状态见
[`benchmark/manifests/runtime-support-v1.json`](../../benchmark/manifests/runtime-support-v1.json)。
