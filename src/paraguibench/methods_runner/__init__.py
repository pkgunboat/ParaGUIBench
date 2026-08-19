"""原项目两个方法（GUI-Only 与 ParaGUI）的权威运行入口。

迁移原则见 docs/methods-provenance.md：Agent 层与原 runner 逐字迁移
（src/parallel_benchmark、src/desktop_env、src/stages），本包只做三件事：

1. 以 runpy 原样装载原 runner，不改动其任何字节；
2. 启动前做凭据与模型环境变量的 fail-fast 检查（只输出变量名与状态，
   不输出任何值）；
3. 提供与原 runner 完全一致的 CLI 透传。
"""
