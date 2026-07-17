# ParaGUIBench

[**English**](README.md) | [简体中文](README_zh-CN.md)

Official project repository for **Beyond Sequential Interaction: Benchmarking Parallel
Execution and Coordination for GUI Agents**.

> [!IMPORTANT]
> This repository currently serves as the project page. The paper will be linked after its
> arXiv release. The benchmark, infrastructure, evaluation toolkit, and baseline
> implementations are being prepared for public release. No code or benchmark files have
> been released yet.

## Overview

GUI agents typically execute long-horizon tasks as a serial chain of
perception--decision--action cycles. This sequential interaction pattern incurs repeated
large-multimodal-model inference and becomes increasingly inefficient as trajectories grow.
ParaGUIBench studies whether multiple GUI agents can instead coordinate and execute
decomposable workloads in parallel on separate desktop instances.

ParaGUIBench contains three main components:

- **Parallel-native infrastructure.** Each GUI worker operates in an isolated Docker container
  with a full Ubuntu desktop, while all workers share a mounted directory for exchanging
  intermediate artifacts. The infrastructure supports a configurable number of concurrent
  workers and restores each container before every run.
- **Benchmark dataset.** The benchmark contains 233 tasks across six categories in two
  domains. Each task is annotated with its parallel-execution pattern:
  `parallel_independent`, `parallel_dependent`, or `serial`.
- **Evaluation system.** Rule-based evaluators verify textual answers or resulting
  environment states and report task success together with step reduction ratio,
  parallelism degree, and token cost.

We also introduce **ParaGUI**, a planner--worker agent that decomposes long-horizon GUI tasks,
dispatches parallelizable sub-tasks to concurrent workers, aggregates their returned
summaries, and decides whether to continue with another round. On ParaGUIBench, ParaGUI
achieves a **46.4% success rate**, outperforming the strongest serial baseline, Claude Sonnet
4.6, by **12.9 percentage points**. Under the default visual-history configurations, it uses
roughly half that baseline's critical-path steps and less than half its tokens.

## Benchmark at a glance

| Domain | Category | Tasks | Example instruction |
|---|---|---:|---|
| Information retrieval | Web search | 65 | *How many Science magazines were published in 2024 that feature a fish on their cover?* |
| Information retrieval | File search | 12 | *Which AI-related papers in the folder have cartoon-style illustrations?* |
| Operation and manipulation | Online shopping | 91 | *Find the cheapest offer for the Samsung Galaxy S24 Plus; return all tied offers.* |
| Operation and manipulation | File operation | 42 | *Create thematic subfolders and classify the Word, PowerPoint, and Excel files.* |
| Operation and manipulation | Web navigation | 13 | *Open three Tesla model pages for comparison and bookmark each page.* |
| Operation and manipulation | Search and write | 10 | *Fill a table with the top five schools in the 2025 QS rankings and their details.* |
| **Total** |  | **233** |  |

## Planned release

- [ ] arXiv preprint
- [ ] Benchmark tasks and annotations
- [ ] Multi-container environment and setup scripts
- [ ] Evaluation toolkit
- [ ] ParaGUI and baseline implementations
- [ ] Reproduction instructions and dependency documentation

Star or watch this repository to follow release updates.

## Paper

The preprint link will be added here after the paper is available on arXiv.

## Citation

A BibTeX entry will be added together with the arXiv preprint.

## License

Original source code developed for this project is planned for release under the
[Apache License 2.0](LICENSE). This license does not apply to benchmark tasks and
annotations, data, or third-party components and assets; their applicable terms will be
documented separately before release.

ParaGUIBench's multi-container execution manager adapts
[OSWorld](https://github.com/xlang-ai/OSWorld)'s single-VM evaluation backend. Some benchmark
tasks are adapted from VeriWeb, OSWorld, and WebMall, while others are manually constructed
to study parallel execution and cross-worker coordination; all online-shopping tasks use the
WebMall environment. Third-party components remain subject to their respective licenses.
