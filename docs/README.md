# ParaGUIBench 0.1 用户手册

公开文档只保留安装、部署、评价边界和可运行 Agent 路径。根目录
[INSTALL.md](../INSTALL.md) 是英文安装入口。

| 手册 | 用途 |
|---|---|
| [中文安装](installation/zh-CN.md) | Core / Live 两层安装 |
| [安装排障](installation/troubleshooting.md) | `verify_install` / `doctor` 检查项 |
| [安装依赖树](installation/dependency-tree.md) | wheel extras 与系统前提 |
| [OSWorld Linux 部署](deployment/osworld-linux.md) | 固定镜像、资产、doctor 与首个候选任务 |
| [WebMall Linux 部署](deployment/webmall-linux.md) | 四店、Checkout / EndToEnd |
| [OnlyOffice 单实例](deployment/onlyoffice.md) | 4 个 SearchAndWrite 共享文档任务 |
| [评价协议](evaluation/protocol.md) | 执行结果与评价结果分离、RunStore 边界 |
| [第三方来源](licenses/third-party-sources.md) | 下载/再分发与 live 门禁不可互换 |
| [Qwen GUI worker](agents/qwen.md) | 截图、坐标与动作白名单 |
| [Kimi + Qwen 单 VM](agents/kimi-qwen-single-vm.md) | 实验性串行 ParaGUI，不是多 VM 并行 |
| [架构与依赖方向](architecture/dependency-tree.md) | 模块允许依赖方向；不等于 runtime 支持 |

当前 233 个任务均为 `blocked`，0 个 `live_validated`。一次历史运行或
component PASS 不能改写该口径。
