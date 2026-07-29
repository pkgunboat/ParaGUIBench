# ParaGUIBench

[**English**](README.md) | [简体中文](README_zh-CN.md)

Official project repository for **Beyond Sequential Interaction: Benchmarking Parallel
Execution and Coordination for GUI Agents**.

> [!IMPORTANT]
> This is a **0.1 preview**, not the complete benchmark runtime. All 233 canonical task
> definitions have been migrated, but the runtime support manifest currently marks only
> GUI-only Seed18 with `InformationRetrieval-FileSearch-Readonly-001` as
> `live_validated`. A published task definition does not imply that its environment,
> assets, evaluator, and Agent System are executable in this preview.

## Overview

GUI agents commonly execute long-horizon tasks as a serial
perception--decision--action loop. ParaGUIBench studies whether multiple GUI agents can
coordinate decomposable workloads across isolated desktop instances and reduce the
critical path without weakening task-level evaluation.

The repository separates reusable orchestration from runnable Agent Systems:

- `framework` defines provider-neutral DAG contracts and bounded scheduling mechanics.
- `agents/systems` contains runnable policies. The GUI-only Seed18 vertical slice is the
  current live gate; ParaGUI planner--worker components are under active integration.
- `benchmark` contains the 233 canonical JSON definitions, release integrity records,
  task assets manifests, schemas, provenance, and the per-task runtime support manifest.
- `runtime`, `evaluation`, and `integrations` assemble task preparation, disposable
  OSWorld sessions, deterministic evaluation, and environment adapters.
- `runstore` persists one run/task/attempt hierarchy with separate execution and
  evaluation outcomes, atomic writes, and default sanitization.

ParaGUI is the planner--worker agent introduced in the paper. It decomposes a task into a
dependency-aware plan, dispatches ready subtasks to concurrent workers, and synthesizes
their results. The paper reports a **46.4% success rate**, **12.9 percentage points** above
the strongest serial baseline in that study. These are paper results; the complete
experiment suite needed to reproduce them is not yet `live_validated` in this preview.

## Release status

| Surface | Preview status |
|---|---|
| Canonical benchmark definitions | 233/233 migrated and covered by `benchmark/manifests/release-v1.json` |
| Runtime support declaration | 233 per-task records in `benchmark/manifests/runtime-support-v1.json` |
| Live-validated task | `InformationRetrieval-FileSearch-Readonly-001` only |
| Blocked tasks | 232; each records explicit blocker codes in the runtime support manifest |
| Live-validated Agent System | GUI-only Seed18, one VM and one worker |
| Reference deployment | Execution `SUCCEEDED`, evaluation `PASSED`, score `1.0` |
| WebMall portability | Logical URLs, guest-directory binding, and versioned synthetic checkout fixture completed; full WebMall runtime is not live-validated |
| Remaining release work | Additional assets, environment adapters, evaluators, Agent Systems, suite metrics, licensing, and category-level live validation |

The runtime support manifest is the authoritative machine-readable statement of what can
run today. Entries marked `blocked` remain useful canonical benchmark definitions, but the
CLI must not present them as supported.

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

## Quick start from a source checkout

The package supports Python 3.11--3.13. A live OSWorld run additionally requires Linux
x86-64, Docker, writable `/dev/kvm`, sufficient local storage for the VM image, and an
OpenAI-compatible model service.

```bash
git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[live,dev]'
python -m pytest
python scripts/benchmark/validate_release.py --repo-root .
python scripts/benchmark/validate_runtime_support.py --repo-root .
python scripts/security/scan_repository.py --root .
```

Model credentials must come from a secret manager or an owner-only file **outside the
checkout**. The CLI reads the environment variable references
`PARAGUIBENCH_MODEL_API_KEY` and `PARAGUIBENCH_MODEL_BASE_URL`; it has no command-line
option for their values. `.env.example` is documentation only and is **not loaded
automatically**.

For the pinned VM, task assets, ten-check `doctor`, live command, and safe inspection
workflow, follow [the OSWorld Linux deployment guide](docs/deployment/osworld-linux.md).
The sanitized successful deployment record is
[reference-run-20260729.md](docs/reproduction/reference-run-20260729.md).

## Run records and privacy

RunStore uses stable `run_id`, `task_id`, and `attempt_id` boundaries:

```text
<runs-root>/<run_id>/
├── run.json
└── tasks/<task_id>/attempts/<attempt_id>/
    ├── task.json
    ├── summary.json
    ├── events/
    └── artifacts/
```

Directories are created with mode `0700` and files with mode `0600`. Execution and
evaluation outcomes remain independent, so an evaluator failure cannot be misreported as
an Agent execution failure. Persisted records use allowlists and sanitization; credentials,
endpoint values, raw model responses, and full checkout fixture values are outside the
default log contract. Run roots and asset caches should remain outside the source
checkout and are ignored if created locally by mistake.

## Documentation

- [OSWorld Linux deployment](docs/deployment/osworld-linux.md)
- [Reference live run](docs/reproduction/reference-run-20260729.md)
- [Architecture and dependency directions](docs/architecture/dependency-tree.md)
- [Benchmark provenance](benchmark/provenance/README.md)
- [Third-party sources and release boundaries](docs/licenses/third-party-sources.md)
- [Safe configuration examples](configs/examples/README.md)

## Paper and citation

The preprint link and BibTeX entry will be added after the paper is available on arXiv.

## License

Original source code developed for this project is licensed under the
[Apache License 2.0](LICENSE). This license does not automatically cover benchmark data,
task assets, VM/container images, model services, or other third-party material.

ParaGUIBench adapts parts of OSWorld's evaluation protocol and uses tasks or environments
derived from VeriWeb, OSWorld, and WebMall. The OSWorld image digest has been verified on
the reference deployment, but its redistribution and layered licensing review remains
open. See [third-party-sources.md](docs/licenses/third-party-sources.md) before packaging or
redistributing any external asset.
