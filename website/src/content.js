const repositoryUrl = "https://github.com/pkgunboat/ParaGUIBench";

/**
 * 生成指向主分支公开文档的稳定链接。
 *
 * @param {string} path - 仓库根目录下的文档相对路径。
 * @returns {string} GitHub 上可公开访问的文档地址。
 */
export function repositoryDocumentUrl(path) {
  return `${repositoryUrl}/blob/main/${path}`;
}

export const sharedLinks = {
  repository: repositoryUrl,
  issues: `${repositoryUrl}/issues`,
  install: repositoryDocumentUrl("INSTALL.md"),
  architecture: repositoryDocumentUrl("docs/architecture/dependency-tree.md"),
  tasks: repositoryDocumentUrl("benchmark/provenance/README.md"),
  evaluator: repositoryDocumentUrl("docs/evaluation/protocol.md"),
  artifacts: repositoryDocumentUrl("docs/evaluation/protocol.md") + "#evidence-storage",
  contributing: `${repositoryUrl}/blob/main/CONTRIBUTING.md`,
  license: `${repositoryUrl}/blob/main/LICENSE`,
  deployment: repositoryDocumentUrl("docs/deployment/osworld-linux.md"),
};

export const content = {
  en: {
    languageName: "English",
    languageToggle: "中文",
    menuOpen: "Open navigation",
    menuClose: "Close navigation",
    navigationLabel: "Primary navigation",
    nav: [
      ["Benchmark", "benchmark"],
      ["Architecture", "architecture"],
      ["Tasks", "tasks"],
      ["Quickstart", "quickstart"],
      ["Results", "results"],
      ["Docs", "docs"],
    ],
    github: "GitHub",
    hero: {
      title: "Benchmarking Parallel Execution for GUI Agents",
      description:
        "A 233-task benchmark for planning, coordinating, and evaluating concurrent GUI work across browsers, desktops, and shared resources.",
      primary: "Get started",
      secondary: "Explore 233 tasks",
      status: {
        preview: "v0.1 Preview",
        canonical: "canonical tasks",
        localReady: "local components ready (not live-validated)",
        localIncomplete: "local components incomplete",
        live: "live-validated",
        blocked: "blocked tasks",
      },
      diagram: {
        accessibleLabel:
          "Current public preview runtime and the adaptive round-based ParaGUI reference agent",
        label: "Execution architecture",
        title: "Runtime status and ParaGUI architecture",
        intro:
          "The public package's historical smoke path and the paper's multi-worker reference agent are shown separately; neither is presented as current versioned runtime support.",
        preview: {
          heading: "Current public preview",
          status: "Historical · rerun pending",
          task: "Benchmark task",
          runner: "AttemptRunner",
          environment: "Disposable OSWorld VM",
          environmentMeta: "1 VM",
          agent: "GUI-only Seed18",
          agentMeta: "1 worker",
          capabilities: ["Browser", "Desktop apps", "Task assets"],
          evaluator: "Exact evaluator",
          runStore: "Task-scoped RunStore",
          scope: "1 task · 1 VM · 1 worker",
          note: "Historical unversioned smoke evidence; not counted as live-validated.",
        },
        reference: {
          heading: "ParaGUI reference agent (paper)",
          paperStatus: "Evaluated in the paper",
          packageStatus: "Public multi-worker integration pending",
          task: "User task",
          agentBoundary: "ParaGUI Agent System",
          planner: "Planner",
          plannerDetail: "Instruction + accumulated history H",
          round: "Adaptive round r",
          decision: "Dispatch or finish",
          finishNo: "No · dispatch",
          finishYes: "Yes · terminate",
          dispatch: "Self-contained parallel dispatch (up to N)",
          worker: "Generic GUI worker",
          workerDetail: "Interchangeable executor",
          workerIndexes: ["1", "2", "N"],
          environment: "Isolated Ubuntu desktop",
          environmentDetail: "One Docker container per worker",
          environmentCapability: "Browser + desktop apps",
          sharedDirectory: "Shared directory · /home/user/shared/",
          sharedDirectoryDetail: "Cross-worker artifact channel",
          barrier: "Round barrier",
          barrierDetail: "Text summaries + produced file paths",
          history: "Update history H",
          nextRound: "Next round r + 1",
          finalOutput: "Final answer",
          workerNote: "Interchangeable GUI executors · no direct messaging",
          answerInput: "Final answer",
          environmentInput: "Final environment state",
          evaluator: "Task evaluator",
          evaluatorDetail: "Reads the answer and/or environment state",
          runStore: "RunStore",
        },
        notes: [
          "Browser and desktop apps are environment capabilities—not worker roles.",
          "Workers do not message one another; files pass only through the shared directory.",
          "ParaGUI revises later dispatches after each round; it is not a fixed upfront DAG.",
        ],
      },
    },
    benchmark: {
      title: "Why parallel GUI evaluation?",
      lead:
        "Real GUI work rarely stays inside one interface. It moves between the web, desktop applications, and shared artifacts—often with independent work that can proceed concurrently.",
      body:
        "ParaGUIBench evaluates decomposition, bounded parallel execution, synchronization, and outcome verification as one system problem. It keeps the canonical task definition separate from whether the current public runtime can execute it.",
      sequential: "Sequential execution",
      parallel: "Parallel coordination",
      sequentialNotes: ["One resource at a time", "Longer critical path", "Coordination remains hidden"],
      parallelNotes: ["Independent work overlaps", "Checkpoints expose dependencies", "One task-level verdict"],
      categoriesTitle: "Six benchmark categories",
      categoriesNote: "Counts are generated from the public canonical task taxonomy.",
      previewNote:
        "Public preview: the OSWorld profile and active-tab pure protocols are code-wired, while 15 artifact-state tasks remain blocked; Agent/environment integration, external assets, and versioned live validation remain pending.",
    },
    architecture: {
      title: "Repository module boundaries",
      intro:
        "This view describes ownership in the public source package, not the execution status of the paper system. Each module keeps models, environments, scoring, and evidence on explicit sides of the boundary.",
      link: "Read the public-package architecture",
      modules: [
        {
          name: "Benchmark",
          description: "Release manifests and safe task projections",
          nodes: ["Canonical tasks", "Runtime support manifest", "Agent-safe task view"],
        },
        {
          name: "Framework",
          description: "Provider-neutral execution contracts",
          nodes: ["ExecutionPlan / SubtaskSpec", "SubtaskResult", "Bounded DAGScheduler"],
        },
        {
          name: "Agent Systems",
          description: "Runnable policies and model adapters",
          nodes: ["GUI-only Seed18", "ParaGUI planner adapter", "ParaGUI worker adapter"],
        },
        {
          name: "Runtime / CLI",
          description: "Task lifecycle and composition",
          nodes: ["AttemptRunner", "Environment lifecycle", "Agent + evaluator assembly"],
        },
        {
          name: "Integrations",
          description: "External environment adapters",
          nodes: ["OSWorld controller + Chrome state evidence", "WebMall logical bindings", "Pinned runtime manifests"],
        },
        {
          name: "Evaluation",
          description: "Agent-independent deterministic scoring",
          nodes: ["Answer evaluators", "OSWorld profile / active-tab state evaluators", "WebMall closed-world evaluators"],
        },
        {
          name: "RunStore",
          description: "Task-scoped evidence and privacy",
          nodes: ["Run / task / attempt identity", "Events and artifacts", "Atomic persistence and redaction"],
        },
      ],
      boundaryLabel: "Repository dependency boundaries",
      boundaryTitle: "Boundary rules",
      boundaries: [
        "Framework never creates a VM or calls a model.",
        "Agent Systems do not import evaluators.",
        "Runtime composes the environment, Agent System, and evaluator without exposing gold data to the Agent.",
        "RunStore persists task-scoped evidence outside the checkout and never stores credential values.",
      ],
    },
    tasks: {
      title: "Explore task and runtime support",
      intro:
        "Search public task metadata and inspect the evaluator, environment, assets, and validation boundary for every canonical task.",
      search: "Search task IDs",
      allGroups: "All benchmark categories",
      allEvaluators: "All evaluators",
      allStatuses: "All support statuses",
      clear: "Clear",
      filters: "Filters",
      task: "Task",
      category: "Category",
      evaluator: "Evaluator",
      runtime: "Runtime",
      assets: "Assets",
      validation: "Validation",
      blockers: "Blockers",
      noBlockers: "None",
      noResults: "No tasks match these filters.",
      previous: "Previous",
      next: "Next",
      page: "Page",
      of: "of",
      note:
        "Local readiness covers repository components only; live support status additionally requires a versioned end-to-end run in the real environment.",
      loadError: "The public task index could not be loaded. Please refresh or inspect the manifest in GitHub.",
      loading: "Loading the public task index…",
      blockedSummary: "blocked",
      localReadySummary: "local components ready (not live-validated)",
    },
    quickstart: {
      title: "Get started without guesswork",
      intro:
        "Use the core track for local inspection and development. Live GUI execution adds OSWorld, virtualization, external assets, and a model endpoint.",
      requirements: ["Python 3.11–3.13", "macOS · core tooling", "Linux x86-64 · live runtime"],
      core: "Core",
      live: "OSWorld runtime",
      copy: "Copy",
      copied: "Copied",
      coreLabel: "Local inspection and development",
      liveLabel: "Live task execution",
      coreCode: `git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python scripts/installation/verify_install.py --profile core`,
      liveCode: `# Run on Linux x86-64 after completing the OSWorld prerequisites
python -m pip install -e '.[live,dev]'
python scripts/installation/verify_install.py --profile live-osworld
export PARAGUIBENCH_SERVER_PORT=5527
export PARAGUIBENCH_VNC_PORT=8527
export PARAGUIBENCH_CHROMIUM_PORT=9527
paraguibench doctor --repo-root . \\
  --task-id InformationRetrieval-FileSearch-Readonly-001 \\
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \\
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \\
  --server-port "$PARAGUIBENCH_SERVER_PORT" \\
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \\
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT"`,
      doctor: "Run the installation verifier before your first task.",
      guide: "Full installation guide",
      deployment: "OSWorld deployment guide",
      secret:
        "Keep credentials outside the checkout. The CLI accepts environment-variable references, never secret values as command-line arguments.",
    },
    results: {
      title: "Results, separated from validation status",
      intro:
        "Research findings and public-package readiness answer different questions. We display them side by side so a paper result is never mistaken for a runnable-suite claim.",
      paperTitle: "Reported in the accompanying manuscript",
      success: "46.4%",
      successLabel: "ParaGUI task success rate",
      gain: "+12.9 pp",
      gainLabel: "over the strongest serial baseline in that study",
      paperNote:
        "These are manuscript results. The complete experiment suite and reproduction recipe are not yet live-validated in this preview.",
      ledgerTitle: "Public-preview validation ledger",
      definitions: "Canonical definitions",
      localReady: "Local components ready (not live-validated)",
      live: "Live-validated tasks",
      pending: "Blocked tasks",
      checks: [
        ["OSWorld state evaluators", "Profile evidence and active-tab AT→CDP→AT collection are production-wired; live validation is pending"],
        ["Remaining state migration", "15 artifact-state tasks remain blocked"],
        ["Repository privacy scan", "Passed for the preview checkpoint"],
        ["Clean Linux deployment", "Historical reference slice retained"],
        ["Versioned end-to-end rerun", "Versioned live validation remains pending for all 233 tasks"],
      ],
      scope: "Evidence scope",
    },
    docs: {
      title: "Documentation and reproducibility",
      intro:
        "The public package documents its dependency boundaries, installation tracks, evaluator contracts, and task-scoped run evidence.",
      view: "View guide",
      links: [
        ["Installation", "Core and OSWorld setup", "install"],
        ["Architecture", "Modules and dependency directions", "architecture"],
        ["Task provenance", "Sources and release boundaries", "tasks"],
        ["Evaluator protocol", "Scoring and evidence boundaries", "evaluator"],
        ["Run artifacts", "Task-scoped logs and run records", "artifacts"],
        ["Contributing", "How to improve tasks and runtime support", "contributing"],
        ["License", "Code and third-party asset boundaries", "license"],
      ],
      reproducibility:
        "Every run uses task-scoped logs and artifacts. Credentials, endpoint values, and raw model responses are outside the public run contract.",
    },
    footer: {
      description:
        "An open-source benchmark for parallel GUI-agent execution, coordination, and reproducible evaluation.",
      preview: "Open-source preview · no analytics · static GitHub Pages",
      citation: "Citation pending preprint release",
    },
  },
  "zh-CN": {
    languageName: "简体中文",
    languageToggle: "EN",
    menuOpen: "打开导航",
    menuClose: "关闭导航",
    navigationLabel: "主导航",
    nav: [
      ["基准", "benchmark"],
      ["架构", "architecture"],
      ["任务", "tasks"],
      ["快速开始", "quickstart"],
      ["结果", "results"],
      ["文档", "docs"],
    ],
    github: "GitHub",
    hero: {
      title: "面向 GUI 智能体并行执行的基准评测",
      description:
        "一个包含 233 个任务的基准，用于评价智能体在浏览器、桌面应用与共享资源之间的规划、协调和并发执行能力。",
      primary: "开始使用",
      secondary: "浏览 233 个任务",
      status: {
        preview: "v0.1 预览版",
        canonical: "个 canonical 任务",
        localReady: "个本地组件已闭合任务（非实机验证）",
        localIncomplete: "个本地组件未闭合任务",
        live: "个已版本化真实验证",
        blocked: "个阻塞任务",
      },
      diagram: {
        accessibleLabel: "当前公开预览版运行链路与自适应轮次式 ParaGUI 参考 Agent",
        label: "执行架构",
        title: "运行状态与 ParaGUI 架构",
        intro:
          "本图将公开包的历史冒烟链路与论文多 Worker 参考 Agent 分开展示；两者都不代表当前已完成版本化真实验证。",
        preview: {
          heading: "当前公开预览版",
          status: "历史证据 · 待复验",
          task: "基准任务",
          runner: "AttemptRunner",
          environment: "一次性 OSWorld 虚拟机",
          environmentMeta: "1 台虚拟机",
          agent: "GUI-only Seed18",
          agentMeta: "1 个 Worker",
          capabilities: ["浏览器", "桌面应用", "任务资产"],
          evaluator: "Exact 评价器",
          runStore: "任务级 RunStore",
          scope: "1 个任务 · 1 台虚拟机 · 1 个 Worker",
          note: "这是历史无版本冒烟证据，不计入当前 live-validated。",
        },
        reference: {
          heading: "ParaGUI 参考 Agent（论文）",
          paperStatus: "已在论文实验中评测",
          packageStatus: "公开版多 Worker 集成待完成",
          task: "用户任务",
          agentBoundary: "ParaGUI Agent System",
          planner: "规划器",
          plannerDetail: "任务指令 + 累积历史 H",
          round: "自适应轮次 r",
          decision: "继续分派或结束",
          finishNo: "否 · 分派",
          finishYes: "是 · 终止",
          dispatch: "自包含并行分派（最多 N 个）",
          worker: "通用 GUI Worker",
          workerDetail: "可互换执行器",
          workerIndexes: ["1", "2", "N"],
          environment: "隔离 Ubuntu 桌面",
          environmentDetail: "每个 Worker 独占一个 Docker 容器",
          environmentCapability: "浏览器 + 桌面应用",
          sharedDirectory: "共享目录 · /home/user/shared/",
          sharedDirectoryDetail: "跨 Worker 文件产物通道",
          barrier: "轮次同步屏障",
          barrierDetail: "文本摘要 + 产出文件路径",
          history: "更新历史 H",
          nextRound: "进入下一轮 r + 1",
          finalOutput: "最终答案",
          workerNote: "可互换 GUI 执行器 · Worker 之间不直接通信",
          answerInput: "最终答案",
          environmentInput: "最终环境状态",
          evaluator: "任务评价器",
          evaluatorDetail: "读取答案和/或最终环境状态",
          runStore: "RunStore",
        },
        notes: [
          "浏览器和桌面应用属于环境能力，不是 Worker 类型。",
          "Worker 之间不直接通信；文件只通过共享目录传递。",
          "ParaGUI 会在每轮结束后修订后续分派，并非预先固定的 DAG。",
        ],
      },
    },
    benchmark: {
      title: "为什么需要并行 GUI 评价？",
      lead:
        "真实 GUI 工作很少局限在一个界面内。它会跨越网页、桌面应用和共享产物，其中多项独立工作往往能够同时推进。",
      body:
        "ParaGUIBench 将任务分解、有界并行、同步和结果验证作为一个完整系统问题进行评价，并把 canonical 任务定义与当前公开运行时能否执行该任务严格区分。",
      sequential: "串行执行",
      parallel: "并行协调",
      sequentialNotes: ["每次只处理一种资源", "关键路径更长", "协调能力无法显式评价"],
      parallelNotes: ["独立工作可重叠", "检查点显式表达依赖", "最终形成统一任务级结论"],
      categoriesTitle: "六类基准任务",
      categoriesNote: "数量由公开 canonical 任务分类确定性生成。",
      previewNote:
        "公开预览说明：OSWorld profile 与 active-tab 纯评价协议已完成代码接线，15 个 artifact-state 任务仍处于阻塞；Agent/环境集成、外部资产与版本化真实环境验证仍待执行。",
    },
    architecture: {
      title: "公开仓库模块边界",
      intro:
        "这里描述的是公开源码包的职责归属，而不是论文系统的运行状态。模型、环境、评分和证据分别位于明确的模块边界内。",
      link: "查看公开包架构说明",
      modules: [
        {
          name: "Benchmark",
          description: "发布清单与安全任务投影",
          nodes: ["Canonical 任务", "运行支持清单", "Agent 安全任务视图"],
        },
        {
          name: "Framework",
          description: "与模型提供方无关的执行契约",
          nodes: ["ExecutionPlan / SubtaskSpec", "SubtaskResult", "有界 DAGScheduler"],
        },
        {
          name: "Agent Systems",
          description: "可运行策略与模型适配",
          nodes: ["GUI-only Seed18", "ParaGUI planner adapter", "ParaGUI worker adapter"],
        },
        {
          name: "Runtime / CLI",
          description: "任务生命周期与装配",
          nodes: ["AttemptRunner", "环境生命周期", "Agent + evaluator 装配"],
        },
        {
          name: "Integrations",
          description: "外部环境适配器",
          nodes: ["OSWorld controller + Chrome 状态证据", "WebMall 逻辑绑定", "固定版本运行清单"],
        },
        {
          name: "Evaluation",
          description: "与 Agent 类型无关的确定性评分",
          nodes: ["答案评价器", "OSWorld profile / active-tab 状态评价器", "WebMall 闭集评价器"],
        },
        {
          name: "RunStore",
          description: "任务级证据与隐私边界",
          nodes: ["Run / task / attempt 身份", "事件与产物", "原子持久化与脱敏"],
        },
      ],
      boundaryLabel: "仓库依赖边界",
      boundaryTitle: "边界规则",
      boundaries: [
        "Framework 不创建虚拟机，也不调用模型。",
        "Agent Systems 不导入评价器。",
        "Runtime 装配环境、Agent System 与评价器，但不会把 gold 数据暴露给 Agent。",
        "RunStore 在源码 checkout 外保存任务级证据，并且不写入凭据值。",
      ],
    },
    tasks: {
      title: "浏览任务与运行支持状态",
      intro: "检索公开任务元数据，并查看每个 canonical 任务的评价器、环境、资产和验证边界。",
      search: "搜索任务 ID",
      allGroups: "全部基准类别",
      allEvaluators: "全部评价器",
      allStatuses: "全部支持状态",
      clear: "清除",
      filters: "筛选",
      task: "任务",
      category: "类别",
      evaluator: "评价器",
      runtime: "运行环境",
      assets: "资产",
      validation: "验证状态",
      blockers: "阻塞项",
      noBlockers: "无",
      noResults: "没有符合当前条件的任务。",
      previous: "上一页",
      next: "下一页",
      page: "第",
      of: "页，共",
      note:
        "本地就绪度只表示仓库组件已闭合；真实环境支持状态还必须通过版本化端到端实机复验。",
      loadError: "无法载入公开任务索引。请刷新页面，或在 GitHub 中直接查看 manifest。",
      loading: "正在载入公开任务索引……",
      blockedSummary: "个阻塞任务",
      localReadySummary: "个本地组件已闭合任务（非实机验证）",
    },
    quickstart: {
      title: "不靠猜测完成安装",
      intro:
        "本地检查与开发使用 Core 路径；真实 GUI 执行还需要 OSWorld、虚拟化、外部资产和模型端点。",
      requirements: ["Python 3.11–3.13", "macOS · Core 工具", "Linux x86-64 · 真实运行时"],
      core: "Core",
      live: "OSWorld 运行时",
      copy: "复制",
      copied: "已复制",
      coreLabel: "本地检查与开发",
      liveLabel: "真实任务执行",
      coreCode: `git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python scripts/installation/verify_install.py --profile core`,
      liveCode: `# 请在 Linux x86-64 完成 OSWorld 前置准备后执行
python -m pip install -e '.[live,dev]'
python scripts/installation/verify_install.py --profile live-osworld
export PARAGUIBENCH_SERVER_PORT=5527
export PARAGUIBENCH_VNC_PORT=8527
export PARAGUIBENCH_CHROMIUM_PORT=9527
paraguibench doctor --repo-root . \\
  --task-id InformationRetrieval-FileSearch-Readonly-001 \\
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \\
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \\
  --server-port "$PARAGUIBENCH_SERVER_PORT" \\
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \\
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT"`,
      doctor: "首次运行任务前，请先执行安装验证器。",
      guide: "完整安装说明",
      deployment: "OSWorld 部署说明",
      secret:
        "凭据必须保存在 checkout 外。CLI 仅接收环境变量的引用，不通过命令行参数接收 secret 值。",
    },
    results: {
      title: "论文结果与验证状态分开呈现",
      intro:
        "研究结论与公开包就绪度回答的是两个问题。并列展示可以避免把论文实验结果误读为当前全量任务均可运行。",
      paperTitle: "论文稿件中报告的结果",
      success: "46.4%",
      successLabel: "ParaGUI 任务成功率",
      gain: "+12.9 pp",
      gainLabel: "相对该研究中最强串行基线的提升",
      paperNote:
        "以上为论文实验结果；完整实验套件与复现实验流程尚未在当前预览版中完成真实环境验证。",
      ledgerTitle: "公开预览版验证台账",
      definitions: "Canonical 定义",
      localReady: "本地组件已闭合任务（非实机验证）",
      live: "已真实验证任务",
      pending: "阻塞任务",
      checks: [
        ["OSWorld 状态评价器", "Profile 证据与 active-tab AT→CDP→AT 采集已完成生产接线；live 验证待执行"],
        ["剩余状态迁移", "15 个 artifact-state 任务仍处于阻塞"],
        ["仓库隐私扫描", "当前预览检查点已通过"],
        ["全新 Linux 部署", "保留历史参考切片"],
        ["带版本向量的端到端复验", "233 个任务的版本化真实环境验证仍待执行"],
      ],
      scope: "证据范围",
    },
    docs: {
      title: "文档与可复现性",
      intro: "公开包记录依赖边界、两类安装路径、评价契约和任务级运行证据。",
      view: "查看说明",
      links: [
        ["安装", "Core 与 OSWorld 环境配置", "install"],
        ["架构", "模块划分与依赖方向", "architecture"],
        ["任务来源", "来源与发布边界", "tasks"],
        ["评价协议", "评分逻辑与证据边界", "evaluator"],
        ["运行产物", "任务级日志与运行记录", "artifacts"],
        ["贡献指南", "如何改进任务和运行支持", "contributing"],
        ["许可证", "代码与第三方资产边界", "license"],
      ],
      reproducibility:
        "每次运行均按任务保存日志与产物。凭据、endpoint 值和模型原始响应不属于公开运行记录。",
    },
    footer: {
      description: "面向 GUI 智能体并行执行、协调与可复现评价的开源基准。",
      preview: "开源预览版 · 无分析跟踪 · 静态 GitHub Pages",
      citation: "引用信息将在预印本发布后补充",
    },
  },
};
