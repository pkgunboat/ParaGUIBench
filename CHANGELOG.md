# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0 releases are previews: task contracts, evaluator semantics, and CLI
surfaces may change between minor versions.

Two claims are tracked separately throughout this file and must not be conflated:
a task being **locally runnable** (assets, environment protocol, and evaluator all
declared) versus **`live_validated`** (versioned live evidence from a real run).
As of the latest entry, all 233 canonical tasks are locally declared and
`live_validated` is 0.

## [Unreleased]

### Fixed

- Sorting evaluation for `Operation-FileOperate-BatchOperationExcel-003` failed
  for every model regardless of agent behavior. Verified against the source
  workbooks: headers sit on row 3 and differ by language
  (`Sales revenue(yuan)` / `营业额（元）`), so the keyword pair `["sales","amount"]`
  matched nothing, while the fixed-column variant read from row 1 and mixed the
  title string into the numeric column. `check_sort_order_by_header_keywords` now
  accepts `header_keyword_groups` (any group may match) and requires a header row
  to hold at least `min_header_cells` non-empty cells, which prevents matching the
  single-cell title row and silently passing on an empty data range.
- Evaluator errors are now separated from model failures. Exceptions raised by
  Operation checks are classified by origin: corrupted agent artifacts
  (`BadZipFile`, missing files) count as FAIL and stay in the denominator, while
  other exceptions are recorded as `evaluator_error` and leave the success-rate
  denominator. Legacy CSVs without a `status` column are detected via the
  `score = -1` sentinel. Outcome priority is unified as
  SKIP → EVALUATOR_ERROR → INTERRUPTED → PASS/FAIL.
- `InformationRetrieval-FileSearch-ReadonlyPPT-004` answers take the form
  `match:2,3,5;unmatch:8` where page numbers are unordered within a group. The
  match mode changed from `exact` to `keyed_numeric_set` so reordering within a
  group is no longer scored as an error.

### Changed

- Baseline advanced to the complete upstream evaluator fix line (dev `028ddd0f`).
  A larger, previously uncommitted fix line from 2026-07-14 to 07-28 superseded
  the two partial lines merged on 2026-08-21; fixes unique to those lines were
  back-ported individually with no regressions. Scope: 150 locked-tree paths,
  139 modified and 11 added. Parity manifest entries went 908 → 919.
- WebNavigate evaluation moved from regex URL matching to host allowlists plus
  path semantic groups (`webnavigate_url_rules.py`,
  `webnavigate_evaluation_router.py`). Judgments are stricter: WebNavigate-001
  accepts only the monthly forecast page, and 011 rejects `search.fda.gov` and
  search-engine result pages. Settings-001 moved from bookmark evaluation to
  OSWorld Chrome profile name evaluation.
- The effective denominator is full again: `skip_eval` tasks went 12 → 0. The
  eight OnlineShopping tasks previously excluded as ambiguous received
  determinate answers through the 2026-07-26 dynamic gold re-annotation, so all
  233 tasks are scored.
- WebMall gold provenance is now recorded per task: `task_revision`,
  `gold_snapshot_id`, `gold_snapshot_path`, and `gold_catalog_sha256`, the last
  defined as the SHA-256 over the four stores' catalog JSON hashes joined as
  `port:hash` in ascending port order, recomputable from the snapshot file.
- Documentation, website, and packaging metadata now consistently state 0.2
  preview; several files still declared 0.1.

### Added

- `CITATION.cff` with the full author list and preferred citation.
- This changelog.

## [0.2] - 2026-08-19

### Added

- The original project's **GUI-Only** and **ParaGUI** methods are migrated in as
  the authoritative implementations, byte-identical to the source project's
  method code and locked by `tests/methods/parity_manifest.json`. Any edit to a
  migrated file fails the parity test and must be declared in
  `docs/methods-provenance.md`.
- Methods service stack and third-party deployment guide
  (`docs/deployment/methods-services.md`), covering the runner's external
  dependencies and their distribution boundaries.
- Top-level original packages: `pipelines`, `mm_agents`, `config_loader`.

### Fixed

- Methods launcher restores the original runners' flat-import context.
- WebMall parallel runner passes `timeout_per_subtask`, preventing GUI workers
  from timing out early.
- WebNavigate runner summary reads `expected_count` from `match_detail`.
- WebMall `extra_docker_env` task alias is created at launch time.
- Agent-mode precedence aligned with the original runners; unknown-category
  handling narrowed.

### Removed

- Unused OCR metric (`easyocr`) from the vendored evaluators.

## [0.1] - 2026-08-15

### Added

- Initial public preview: 233 canonical task contracts published with empty live
  allowlists, locked by `benchmark/manifests/release-v1.json`.
- OSWorld and WebMall runtimes, agent runtime, and evaluation protocol
  (`docs/evaluation/protocol.md`).
- OnlyOffice and WebMall lease deployment contracts.
- Installation validation (`verify_install`, `doctor`), release bundle builder
  with deterministic archives, and repository security scanner.
- User handbook under `docs/`, English `INSTALL.md`, and the project website.

### Notes

- All 233 tasks are marked `blocked` in
  `benchmark/manifests/runtime-support-v1.json`. Early GUI-only Seed18
  single-VM smoke evidence exists but carries no versioned run vector, so it is
  retained only as a historical unversioned record.

[Unreleased]: https://github.com/pkgunboat/ParaGUIBench/compare/v0.2...HEAD
[0.2]: https://github.com/pkgunboat/ParaGUIBench/compare/v0.1...v0.2
[0.1]: https://github.com/pkgunboat/ParaGUIBench/releases/tag/v0.1
