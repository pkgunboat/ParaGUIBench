# Benchmark data provenance

This directory records the origin, deterministic normalizations, and release
decisions for the canonical ParaGUIBench task definitions. Canonical
publication and runtime support are separate: all 233 definitions are present,
while the current runtime support manifest marks one task `live_validated` and
232 tasks `blocked`.

## release-v1 import

- Source repository: `pkgunboat/ParaGUIBench-dev`
- Source branch: `fix/eval-audit-20260714`
- Source base commit: `8d36e1577d18feda8c789fc27529700b7afc5eda`
- Import date: 2026-07-28
- Imported artifacts: 233 UTF-8 JSON task definitions
- Integrity records: `benchmark/manifests/release-v1.json`
- Runtime support records: `benchmark/manifests/runtime-support-v1.json`

The source was an evaluator-audit working-tree snapshot rather than a clean
Git commit. Its task directory contained 101 tracked modifications relative
to the base commit. The per-file SHA-256 values in the release manifest—not
the base commit alone—are therefore the authoritative identity of the imported
snapshot.

The snapshot contained 238 task definitions and one legacy `id_mapping.json`.
Five experimental coding tasks were intentionally excluded:

- `Operation-FileOperate-Coding-001`
- `Operation-FileOperate-Coding-002`
- `Operation-FileOperate-Coding-003`
- `Operation-FileOperate-Coding-004`
- `Operation-FileOperate-Coding-005`

The remaining task files were initially copied byte-for-byte. Each subsequent
normalization is deterministic, updates the release-manifest digest, and has a
check mode or validator coverage. The legacy ID mapping was filtered to the
canonical 233-task set and incorporated into the release manifest.

## Portable canonical normalizations

### WebMall logical URLs

All 91 online-shopping tasks now use `webmall://store-*` logical origins in
instructions and expected URL sets. Deployment-specific origins are resolved
only in memory by the WebMall registry. Canonical task files no longer carry a
fixed deployment host. `scripts/benchmark/logicalize_webmall_urls.py` defines
the deterministic transformation and validation boundary.

### Guest directory binding

The OSWorld task that previously embedded a guest-user absolute path now uses
`${GUEST_SHARED_DIR}` and declares `required_environment_bindings`. Task
materialization accepts only a safe POSIX absolute directory supplied by the
current environment and never treats a task binding as a credential.
`scripts/benchmark/logicalize_guest_paths.py` implements the migration.

### Versioned checkout fixture

On 2026-07-29, 8 WebMall Checkout tasks and 8 EndToEnd tasks were normalized
to reference one project-authored fixture:
`webmall.checkout-profile.synthetic-public.v1`. Repeated inline checkout data
was removed from canonical task files.

The fixture is explicitly synthetic public test data. It uses a reserved
`.invalid` email domain and a test payment number, is marked
`reference_only`, and is pinned by SHA-256 in the release manifest. It must
never be replaced with real personal data, a production payment instrument,
an API key, or deployment credentials.

Task preparation resolves the fixed fixture into three projections:

1. `trusted` holds the hash-verified task and fixture in memory;
2. `agent` renders only the form data required by the task instruction;
3. `audit` persists only fixture identity, schema version, classification, and
   digest—not the profile values or rendered instruction.

The transformation and idempotency check live in
`scripts/benchmark/migrate_checkout_fixture.py`. ADR-0003 and the two WebMall
schemas define the projection and logging contracts. These canonical and task-
preparation migrations are complete, but the full WebMall service and checkout
evaluator are not yet `live_validated`.

## Assets and runtime-support boundary

Binary task assets, VM images, model weights, caches, logs, and credentials are
not part of the canonical task snapshot. The current representative task has a
download-only asset manifest with a fixed repository revision, file sizes, and
per-file SHA-256 values. Other tasks retain explicit blocker codes until their
assets and runtime components are migrated.

`runtime-support-v1.json` covers the same 233 task IDs as `release-v1.json`.
Its current only `live_validated` tuple is GUI-only Seed18 with
`InformationRetrieval-FileSearch-Readonly-001`; the other 232 records are
`blocked`. This manifest prevents the presence of canonical JSON from being
misrepresented as executable support.

No API key, authentication token, private runtime configuration, internal host
binding, developer absolute path, execution log, VM image, or other binary
benchmark asset is included in this migration.
