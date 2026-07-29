# ADR-0001：公开仓库 module 布局

- 状态：Accepted
- 日期：2026-07-28

## 背景

旧开发仓库将任务、评价器、五类 pipeline、ParaGUI planner、GUI-only Agent、模型 adapter、OSWorld 派生代码和运行产物集中在 `src/parallel_benchmark`、`src/pipelines`、`src/stages` 与 `src/mm_agents` 中。多个 module 的 interface 相互泄漏，难以独立测试、发布和说明许可证来源。

论文与公开 README 将 ParaGUIBench 和 ParaGUI 作为并列贡献，因此 Agent 不能继续作为 benchmark runner 的内部实现细节。

## 决策

首版采用一个 Python distribution，并建立以下一级 module：

```text
benchmark data
src/paraguibench/
├── benchmark
├── evaluation
├── framework
├── agents
├── runtime
├── runstore
├── integrations
└── cli
```

- `framework` 保存可复用的 planner–worker 调度机制。
- `agents/systems/paragui` 保存 ParaGUI 策略及其完整装配。
- `agents/systems/gui_only` 保存可独立运行的 GUI-only Agent implementation。
- `baseline` 只在实验配置中出现，不作为代码分类。
- `evaluation` 不导入具体 Agent implementation。
- `framework` 不导入具体 Agent、任务类别或 evaluator。
- 第三方环境和模型通过 `integrations` adapter 接入。
- 运行记录统一写入一级 `runstore` module。

## 后果

- 一个安装包降低首期开源和部署复杂度。
- module 的依赖方向允许未来把 ParaGUI 拆成独立 distribution。
- 旧目录需要按职责拆分，不能保留兼容性的双实现。
- 迁移期间通过 contract test 和旧/新同输入 parity 验证行为。
