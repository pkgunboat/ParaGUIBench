# OSWorld 任务并行化改造分析报告

## 1. 概述
本报告旨在分析将 OSWorld 现有单体任务（Single-Agent Task）改造为并行任务（Parallel Task）时，在**任务初始化（Config）**和**结果评测（Evaluator）**阶段需要进行的关键修改。基于对 Chrome, GIMP, LibreOffice, VS Code, Thunderbird 等典型任务配置文件的分析，总结出以下改造规范。

## 2. Config 阶段改造规范（文件加载与路径重映射）

### 核心问题
OSWorld 任务普遍使用硬编码的容器本地路径（如 `/home/user/Desktop/`），直接并行化会导致：
1.  **文件隔离失效**：所有 Agent 操作同一路径，无法利用共享文件夹架构。
2.  **并发写入冲突**：多个 Agent 同时读写同名文件（如 `result.xlsx`）。
3.  **Profile 锁死**：特定应用（Thunderbird, Chrome）在共享路径下无法多实例运行。

### 改造对照表

| 应用类型 | 典型操作场景 | 原始配置示例 (JSON) | 并行化改造方案 | 风险等级 |
| :--- | :--- | :--- | :--- | :--- |
| **LibreOffice** (Writer/Calc/Impress) | 下载/打开文档 | `files[].path`: `/home/user/Desktop/doc.docx` | **重映射路径**：将路径修改为共享挂载点下的独立子目录：`/mnt/shared/{task_id}/doc.docx`。 | 高 |
| **GIMP** | 打开/编辑图片 | `parameters.files[].path`: `/home/user/Desktop/img.jpg` | **重映射路径**：同上，指向共享子目录。 | 高 |
| **Thunderbird** | 加载用户配置文件 | `path`: `/home/user/tb-profile.tar.gz` | **复制模式**：先解压到共享目录，再在 Config 阶段 `cp` 到容器本地 `/home/user/`。严禁直接挂载 Profile 目录（会导致 Lock 冲突）。 | 极高 |
| **VS Code** | 安装插件 (VSIX) | `path`: `/home/user/test.vsix` | **建议修改**：源文件放共享目录，但安装过程在容器内进行。 | 中 |
| **Multi-apps** | 复杂工作流 (mkdir) | `command`: `mkdir -p /home/user/Documents/Project` | **重写指令**：必须修改 Shell 命令中的路径参数，确保创建的是任务专属目录。 | 高 |

## 3. Evaluator 阶段改造规范（评测重定向）

### 核心问题
评测器默认在宿主机运行，并通过路径或端口检查 Agent 状态。并行环境下，评测器需要知道“去哪个容器检查”以及“去哪个路径找文件”。

### 改造对照表

| 评测类型 | 典型函数/字段 | 原始配置示例 | 并行化改造方案 |
| :--- | :--- | :--- | :--- |
| **文件一致性** | `compare_table`, `compare_docx` | `result.path`: `/home/user/Desktop/res.docx` | **路径重定向**：修改 `result.path` 指向 Agent 对应的共享子目录 `/mnt/shared/{task_id}/res.docx`。 |
| **Chrome 状态** | `is_expected_active_tab` | 仅含函数名 | **增加上下文**：评测函数调用时需注入 `container_id` 或 `debug_port`，以连接正确的 Chrome 实例。 |
| **CLI 输出** | `vm_terminal_output` | 无路径参数 | **指定容器**：需修改底层 `get_vm_terminal_output` 实现，使其接受 Target Container 参数。 |
| **VLC 配置** | `vlc_config` | `dest`: `vlcrc` | **指定容器**：配置文件通常在容器本地 Home 目录，评测器需通过 `docker cp` 从特定容器提取。 |

## 4. 隐性依赖与特殊处理

### 4.1 Post-config 自动化脚本
*   **现象**：很多任务在 `evaluator.postconfig` 中使用 Python `pyautogui` 模拟 `Ctrl+S` 保存文件。
*   **风险**：如果文件是通过共享路径打开的，`Ctrl+S` 会直接写回共享存储。
*   **对策**：确保每个 Agent 打开的是其专属子目录下的文件，避免多个 Agent 的 `Ctrl+S` 操作互相覆盖。

### 4.2 窗口标题匹配 (Window Title Matching)
*   **现象**：`activate_window` 依赖窗口标题（如 `test.docx - LibreOffice`）。
*   **风险**：如果两个 Agent 打开不同目录下的同名文件，窗口标题可能相同。
*   **对策**：由于每个 Agent 运行在独立的 Docker 容器（且通常有独立的 Xvfb），此问题在容器隔离架构下可忽略；但如果是单机多窗口架构，则必须重命名文件以区分窗口。

## 5. 总结
OSWorld 任务并行化的核心原则是 **“I/O 路径隔离”** 与 **“评测目标定向”**。不能简单拼接 JSON，必须对所有涉及文件读写 (`path`, `file`) 和状态检查 (`func`) 的字段进行针对性的重写（Rewriting）。













