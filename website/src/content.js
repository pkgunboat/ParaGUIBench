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
  artifacts: repositoryDocumentUrl("docs/reproduction/reference-run-20260729.md"),
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
      status: ["v0.1 Preview", "233 canonical tasks", "1 live-validated", "232 infrastructure pending"],
      planner: "Planner",
      plannerDetail: "Decompose & dispatch",
      evaluator: "Evaluator",
      evaluatorDetail: "Aggregate & score",
      lanes: [
        ["Browser worker", "Navigate and retrieve"],
        ["Desktop worker", "Operate applications"],
        ["Shared files", "Read and transform"],
      ],
      checkpoints: "Synchronized checkpoints",
      time: "Parallel progress",
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
        "Public preview: all task definitions are present, while full-suite live validation still depends on external runtime assets and services.",
    },
    architecture: {
      title: "Architecture for parallel GUI work",
      intro:
        "Framework services, runnable Agent Systems, environment adapters, and evaluators have explicit boundaries. This keeps orchestration reusable without hiding model- or runtime-specific behavior inside the framework.",
      link: "Read the architecture guide",
      layers: [
        {
          name: "Framework",
          description: "Provider-neutral contracts and scheduling",
          nodes: ["Task Registry", "Planner", "Scheduler", "Worker Runtime"],
        },
        {
          name: "Agent Systems",
          description: "End-to-end and framework-integrated policies",
          nodes: ["End-to-end Agent", "Framework Adapter", "Session Continuity"],
        },
        {
          name: "Evaluation",
          description: "Deterministic scoring and task-scoped evidence",
          nodes: ["OSWorld-compatible Evaluators", "RunStore", "Result Artifacts"],
        },
      ],
      machines: ["Machine 01", "Machine 02", "Machine N"],
      runId: "Shared run context",
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
        "Support status describes this public preview package. It is not a claim that every canonical task has passed end-to-end execution.",
      loadError: "The public task index could not be loaded. Please refresh or inspect the manifest in GitHub.",
      loading: "Loading the public task index…",
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
paraguibench doctor --repo-root . \\
  --task-id InformationRetrieval-FileSearch-Readonly-001 \\
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \\
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \\
  --server-port "$PARAGUIBENCH_SERVER_PORT" \\
  --vnc-port "$PARAGUIBENCH_VNC_PORT"`,
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
      live: "Live-validated task",
      pending: "Infrastructure pending",
      checks: [
        ["Evaluator support declarations", "Tracked per task; remaining migration blockers stay explicit"],
        ["Repository privacy scan", "Passed for the preview checkpoint"],
        ["Clean Linux deployment", "Passed for the reference slice"],
        ["End-to-end smoke run", "Passed for one declared task"],
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
        ["Run artifacts", "Task-scoped logs and reference evidence", "artifacts"],
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
      status: ["v0.1 预览版", "233 个 canonical 任务", "1 个已真实验证", "232 个等待基础设施验证"],
      planner: "规划器",
      plannerDetail: "分解并分派",
      evaluator: "评价器",
      evaluatorDetail: "汇总并评分",
      lanes: [
        ["浏览器 Worker", "导航与检索"],
        ["桌面 Worker", "操作应用程序"],
        ["共享文件", "读取与转换"],
      ],
      checkpoints: "同步检查点",
      time: "并行进度",
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
        "公开预览说明：所有任务定义已迁入；全量真实环境验证仍依赖外部运行资产与服务。",
    },
    architecture: {
      title: "面向并行 GUI 工作的系统架构",
      intro:
        "框架服务、可运行 Agent System、环境适配器和评价器具有明确边界，使协调逻辑可以复用，同时避免把模型或运行时细节隐藏在框架中。",
      link: "查看架构说明",
      layers: [
        {
          name: "Framework",
          description: "与模型提供方无关的契约与调度",
          nodes: ["任务注册表", "规划器", "调度器", "Worker 运行时"],
        },
        {
          name: "Agent Systems",
          description: "端到端策略与框架集成策略",
          nodes: ["端到端 Agent", "Framework Adapter", "会话连续性"],
        },
        {
          name: "Evaluation",
          description: "确定性评分与任务级证据",
          nodes: ["OSWorld 兼容评价器", "RunStore", "结果产物"],
        },
      ],
      machines: ["机器 01", "机器 02", "机器 N"],
      runId: "共享运行上下文",
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
        "支持状态描述的是当前公开预览包，不代表全部 canonical 任务都已经通过端到端真实执行。",
      loadError: "无法载入公开任务索引。请刷新页面，或在 GitHub 中直接查看 manifest。",
      loading: "正在载入公开任务索引……",
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
paraguibench doctor --repo-root . \\
  --task-id InformationRetrieval-FileSearch-Readonly-001 \\
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \\
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \\
  --server-port "$PARAGUIBENCH_SERVER_PORT" \\
  --vnc-port "$PARAGUIBENCH_VNC_PORT"`,
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
      live: "已真实验证任务",
      pending: "等待基础设施",
      checks: [
        ["评价器支持声明", "逐任务记录；尚未闭环的迁移阻塞保持显式"],
        ["仓库隐私扫描", "当前预览检查点已通过"],
        ["全新 Linux 部署", "参考纵向切片已通过"],
        ["端到端冒烟运行", "一个已声明任务通过"],
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
        ["运行产物", "任务级日志与参考证据", "artifacts"],
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
