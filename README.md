# ParaGUIBench

[**English**](README.md) | [简体中文](README_zh-CN.md)

Official project repository for **Beyond Sequential Interaction: Benchmarking Parallel
Execution and Coordination for GUI Agents**.

> [!IMPORTANT]
> This is a **0.3 preview**. Beyond the 0.2 method migration (GUI-Only and
> ParaGUI as authoritative implementations, byte-identical and parity-locked),
> 0.3 hardens the evaluation chain: the repaired Excel-002 initial workbooks,
> the corrected Excel-005 answer, and the re-extracted Settings-001 gold are
> published and pinned to an immutable dataset revision; a task-routing defect
> that broke SearchAndWrite-007 evaluation is fixed; and all five pipelines
> (QA, WebMall, WebNavigate, Operation, SearchWrite) have live smoke evidence
> with zero evaluator errors. Full-benchmark validation over the complete task
> set is still pending and will gate the formal release. The formal
> `live_validated` promotion chain (runtime-support manifest) is unchanged: no
> task is marked `live_validated` yet. A published task definition does not
> imply that its environment, assets, evaluator, and Agent System are
> executable in this preview.

## Overview

GUI agents commonly execute long-horizon tasks as a serial
perception--decision--action loop. ParaGUIBench studies whether multiple GUI agents can
coordinate decomposable workloads across isolated desktop instances and reduce the
critical path without weakening task-level evaluation.

The repository separates reusable orchestration from runnable Agent Systems:

- `framework` defines provider-neutral DAG contracts and bounded scheduling mechanics.
- `agents/systems` contains runnable policies. The GUI-only Seed18 vertical slice is the
  first candidate for a versioned live rerun. An experimental single-VM CLI combines a `kimi-k2.6` planner
  with a Qwen 3.7 GUI worker. Multi-VM GUI-Only and ParaGUI are provided by the vendored
  original methods (see "Original methods" below).
- `benchmark` contains the 233 canonical JSON definitions, release integrity records,
  separate guest-visible input-asset and host evaluator-only gold manifests, schemas,
  provenance, and the per-task runtime support manifest.
- `runtime`, `evaluation`, and `integrations` assemble task preparation, disposable
  OSWorld sessions, deterministic evaluation, and environment adapters.
- `runstore` persists one run/task/attempt hierarchy with separate execution and
  evaluation outcomes, atomic writes, and default sanitization.

ParaGUI is the planner--worker agent introduced in the paper. It decomposes a task into a
dependency-aware plan, dispatches ready subtasks to concurrent workers, and synthesizes
their results. The paper reports a **46.4% success rate**, **12.9 percentage points** above
the strongest serial baseline in that study. These are paper results; the complete
experiment suite needed to reproduce them is not yet `live_validated` in this preview.

## Original methods (GUI-Only and ParaGUI)

The original project's two methods are vendored as the **authoritative
implementations**: `src/parallel_benchmark/`, `src/desktop_env/`, `src/stages/`,
`src/pipelines/`, and `src/mm_agents/` are byte-identical to the source project's
method code (locked per file by `tests/methods/parity_manifest.json`; see
[the methods provenance record](docs/methods-provenance.md)). Both methods were
verified end-to-end on a real host in this release: one task per method completed
the full pipeline — VM provisioning via the original runners, the Qwen GUI agent
loop, and typed evaluation — with zero interruptions.

Run them with the original runner interface:

```bash
python -m paraguibench.methods_runner <qa|webmall|webnavigate|self_operation|searchwrite> \
  --agent-mode <plan|gui_only> --gui-agent qwen -n <vms-per-task> ...
```

Credentials and model IDs are injected through environment variables only; the
calibrated IDs and required variables are listed in
[docs/methods-provenance.md](docs/methods-provenance.md). External services
(WebMall stores, OnlyOffice), the VM image story, and task-asset provisioning
for these runners are covered in
[docs/deployment/methods-services.md](docs/deployment/methods-services.md).
The rewritten Agent
Systems under `src/paraguibench/agents/` remain available through the `paraguibench`
CLI as the open-source release surface.

## Release status

| Surface | Preview status |
|---|---|
| Canonical benchmark definitions | 233/233 migrated and covered by `benchmark/manifests/release-v1.json` |
| Runtime support declaration | 233 per-task records in `benchmark/manifests/runtime-support-v1.json` |
| Local component readiness | 233 `local_ready`; 0 `local_components_incomplete` |
| Live-validated task | None; the first versioned rerun is pending |
| Blocked tasks | 233; each records explicit blocker codes in the runtime support manifest |
| First live-gate candidate | GUI-only Seed18, one VM and one worker |
| Experimental Agent code | Qwen 3.7 Flash GUI-only and Kimi+Qwen sequential single-VM ParaGUI are contract-tested and live-executed on one sample, not live-validated |
| Historical deployment | Execution `SUCCEEDED`, evaluation `PASSED`, score `1.0`; legacy unversioned evidence only |
| WebMall Checkout slice | Logical URLs, versioned fixture/environment, WP-CLI order evidence, distributed lease, CLI binding, and native evaluator are integrated locally; no versioned live Attempt has passed |
| CombinationDocs-015 evaluator slice | Native `paraguibench.osworld.artifact-state.v1`, pinned input assets, evaluator-only gold, and CLI/doctor/source wiring are complete locally; the task remains blocked by four manifest-listed runtime and live gates |
| Original methods (GUI-Only / ParaGUI) | Vendored byte-identical from the source project; one task per method verified end-to-end on a real host with zero interruptions; full-suite validation pending before the formal release |
| Remaining release work | Private asset provisioning, real-environment deployment, Agent Systems, suite metrics, licensing review, and category-level live validation |

The runtime support manifest is the authoritative machine-readable statement of two
different readiness layers. `local_readiness_status` describes whether repository-side
components are closed; `support_status` remains the formal live claim. A `local_ready`
task can therefore still be `blocked` until its pinned real-environment gates and
versioned receipt are complete, and the CLI must not present it as live-supported.

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

The package supports Python 3.11--3.13. Ubuntu/Debian hosts need the venv component
first (`sudo apt install python3.12-venv`); without it `python3.12 -m venv` fails on
ensurepip. A live OSWorld run additionally requires Linux
x86-64, Docker, writable `/dev/kvm`, sufficient local storage for the VM image, and an
OpenAI-compatible model service.

```bash
git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[live,methods,dev,artifact]'
python -m pytest
python scripts/benchmark/validate_release.py --repo-root .
python scripts/benchmark/validate_runtime_support.py --repo-root .
python scripts/security/scan_repository.py --root .
```

## Shortest evaluation path

Credentials stay in environment variables, never on the CLI. Public model endpoints
must be HTTPS. A local server may use `http://127.0.0.1:...` or `http://localhost:...`.
Do not put a username, password, query, or fragment in the URL.

```bash
export PARAGUIBENCH_MODEL_API_KEY="..."
export PARAGUIBENCH_MODEL_BASE_URL="https://example.invalid/v1"
export PARAGUIBENCH_MODEL_ID="qwen3.7-flash-2026-07-15"
export PARAGUIBENCH_ASSET_CACHE_ROOT="$HOME/.cache/paraguibench/assets"
export PARAGUIBENCH_GOLD_CACHE_ROOT="$HOME/.cache/paraguibench/gold"
export PARAGUIBENCH_QCOW2_PATH="$HOME/.local/share/paraguibench/Ubuntu.qcow2"
export PARAGUIBENCH_RUNS_ROOT="$HOME/.local/share/paraguibench/runs"
export PARAGUIBENCH_SERVER_PORT=5527
export PARAGUIBENCH_VNC_PORT=8527
export PARAGUIBENCH_CHROMIUM_PORT=9527

# Protocol only; no VM and no task score.
paraguibench model-probe qwen-native

# Real OSWorld needs Linux x86-64, Docker, /dev/kvm, and a qcow2 image.
# macOS can run unit tests; it cannot start the guest VM.
paraguibench doctor \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT"
paraguibench run \
  --repo-root . \
  --task-id InformationRetrieval-FileSearch-Readonly-001 \
  --asset-cache-root "$PARAGUIBENCH_ASSET_CACHE_ROOT" \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT" \
  --qcow2-path "$PARAGUIBENCH_QCOW2_PATH" \
  --server-port "$PARAGUIBENCH_SERVER_PORT" \
  --vnc-port "$PARAGUIBENCH_VNC_PORT" \
  --chromium-port "$PARAGUIBENCH_CHROMIUM_PORT" \
  --runs-root "$PARAGUIBENCH_RUNS_ROOT" \
  --agent-system gui-only \
  --worker qwen \
  --model "$PARAGUIBENCH_MODEL_ID"
```

Point the cache, qcow2, and port variables at your machine; see
[the OSWorld Linux guide](docs/deployment/osworld-linux.md) for provisioning.
Scores come from guest artifacts and typed observations, not Agent final text.
Gold stays host-only.

Model credentials must come from a secret manager or an owner-only file **outside the
checkout**. The CLI reads the environment variable references
`PARAGUIBENCH_MODEL_API_KEY` and `PARAGUIBENCH_MODEL_BASE_URL`; it has no command-line
option for their values. `.env.example` is documentation only and is **not loaded
automatically**.

WebMall runners additionally bind four origins, four WP-CLI reader targets, a
coordinator URL, and a lease credential through manifest-named environment
references. The coordinator receives the matching credential through a separate
variable and process boundary. Do not put any real binding or credential in the
checkout or logs; see the WebMall deployment guide for the complete variable
table and commands.

Four SearchAndWrite tasks (`002`, `004`, `006`, `008`) additionally depend on a
local OnlyOffice DocumentServer plus the ParaGUIBench share service. The other
six SearchAndWrite tasks stay on OSWorld/LibreOffice. This is a single-instance
lab service; passing unit tests does not mean the real editor is up, and it does
not mark those tasks `live_validated`. See
[the OnlyOffice deployment guide](docs/deployment/onlyoffice.md).

For the pinned VM, task input assets, evaluator-only gold provisioning, complete `doctor`
gate, live command, and safe inspection workflow, follow
[the OSWorld Linux deployment guide](docs/deployment/osworld-linux.md).

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
default log contract. Run roots, guest-visible input-asset caches, and evaluator-only gold
caches should remain outside the source checkout and are ignored if created locally by
mistake. Gold stays in a separate host-private cache and is never exposed to the Agent or
default RunStore records.

New Runs persist a six-field source/Agent/evaluator/protocol/environment version vector.
`paraguibench inspect --diagnostics` exposes only that fixed identity and an enumerated
failure stage; it never prints free-form summary details. Tasks that declare no external
files take an explicit zero-asset path, while non-empty legacy asset references remain
fail-closed.

## Documentation

- [User handbook](docs/README.md)
- [Installation guide](INSTALL.md)
- [Installation troubleshooting](docs/installation/troubleshooting.md)
- [OSWorld Linux deployment](docs/deployment/osworld-linux.md)
- [WebMall Linux deployment and Checkout run](docs/deployment/webmall-linux.md)
- [OnlyOffice single-instance deployment](docs/deployment/onlyoffice.md)
- [Qwen 3.7 GUI worker and validation boundary](docs/agents/qwen.md)
- [Kimi + Qwen sequential single-VM ParaGUI](docs/agents/kimi-qwen-single-vm.md)
- [Architecture and dependency directions](docs/architecture/dependency-tree.md)
- [Evaluation protocol and outcome boundaries](docs/evaluation/protocol.md)
- [Benchmark provenance](benchmark/provenance/README.md)
- [Third-party sources and release boundaries](docs/licenses/third-party-sources.md)
- [Safe configuration examples](configs/examples/README.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [GitHub Pages source and local preview](website/README.md)

## Paper and citation

This repository accompanies "Beyond Sequential Interaction: Benchmarking Parallel
Execution and Coordination for GUI Agents". The preprint link and BibTeX entry will
be added once the paper is available on arXiv; until then, machine-readable citation
metadata for the software is in [`CITATION.cff`](CITATION.cff), which GitHub renders
under **Cite this repository**.

## License

Original source code developed for this project is licensed under the
[Apache License 2.0](LICENSE). This license does not automatically cover benchmark data,
task assets, VM/container images, model services, or other third-party material.

ParaGUIBench adapts parts of OSWorld's evaluation protocol and uses tasks or environments
derived from VeriWeb, OSWorld, and WebMall. The fixed upstream OSWorld archive and the
historical reference qcow2 resolve to guest-visible images with different content. The
archive-derived 6bf image is now the open-source default identity, while the historical
6d image remains a separate legacy identity. Its schema-v2 materialization recipe and
output digest are fixed, and frozen cleanroom code has produced independently reviewed
reproducible materialization evidence. Per-task live gates remain fail-closed;
redistribution and layered licensing review also remain open. See
[the OSWorld environment boundary](environments/osworld/README.md) and
[third-party-sources.md](docs/licenses/third-party-sources.md) before running, packaging,
or redistributing any external asset.
