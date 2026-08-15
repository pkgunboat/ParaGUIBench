# Benchmark data provenance

This directory records the origin, deterministic normalizations, and release
decisions for the canonical ParaGUIBench task definitions. Canonical
publication and runtime support are separate: all 233 definitions are present,
while the current runtime support manifest marks all 233 tasks `blocked` and
zero tasks `live_validated`.

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
`scripts/benchmark/migrate_checkout_fixture.py`. The versioned checkout fixture
and the two WebMall schemas define the projection and logging contracts. These canonical and task-
preparation migrations are complete, but the full WebMall service and checkout
evaluator are not yet `live_validated`.

## Assets and runtime-support boundary

Binary task assets, VM images, model weights, caches, logs, and credentials are
not part of the canonical task snapshot. Ten FileSearch tasks now have
download-only asset manifests with a fixed repository revision, file sizes, and
per-file SHA-256 values. Nine generator-managed manifests additionally pin
the verified MIME type of all 30 files and are reproduced by the offline
`scripts/benchmark/readonly_asset_manifests.py` generator/check against the
closed `readonly-file-search-asset-manifest-v1` schema. The upstream Lee dataset
does not declare a verified license, so these records remain `download_only`
with `license_status=unverified`; they are integrity records, not permission to
redistribute the files. All canonical tasks are now locally closed, while their
remaining explicit blocker codes record private provisioning and live-validation work.

Thirty-four Operation FileOperate tasks additionally bind 128 PPTX, DOCX, XLSX,
JPEG, and text inputs to fixed Lee/xlang revisions. Their deterministic generator and
closed schema preserve exact task UID, path, size, SHA-256, MIME, and one-to-five-file
directory closure, extended to ten files only for the paired Word/JPEG task.
Runtime support requires the task-specific canonical path
and complete manifest byte digest; missing, cross-swapped, or tampered manifests
fail closed. BatchOperation-001 retains its artifact-getter live blocker. These
integrity bindings do not migrate evaluator semantics or
constitute live validation, and the files remain `unverified`/`download_only`.

`runtime-support-v1.json` covers the same 233 task IDs as `release-v1.json`.
All 233 records are currently `blocked`. GUI-only Seed18 with
`InformationRetrieval-FileSearch-Readonly-001` is the first versioned rerun
candidate, but its earlier smoke evidence predates RunStore v2 and is classified
as `LEGACY_UNVERSIONED`, not current live support. This manifest prevents the
presence of canonical JSON or a historical run from being misrepresented as
executable support.

## RunStore-v2 live-validation receipts

Live promotion no longer accepts a bare task-ID allowlist. The deterministic
runtime-support generator first derives every evaluator, asset, Cart,
OSWorld-artifact, pipeline, and task-specific Operation blocker. A task can be
`live_validated` only when the OSWorld image gate is ready, that component
blocker list is empty, and a reviewed receipt exists at
`benchmark/provenance/live-validation-receipts/<task_id>.json` with an exact
task-to-file SHA-256 binding. The binding is stored separately from promotion
code in the closed
`benchmark/provenance/live-validation-receipt-allowlist-v1.json` data file and
must be a subset of the current canonical release task IDs. The binding set is
currently empty, so the public count remains zero.

The receipt format is a closed, bounded, no-follow projection of RunStore v2.
Its directory chain and leaf are opened through anchored no-follow descriptors;
the exact physical file set and directory identity are checked before and after
the bounded read.
It persists only schema version, task/run/attempt identities,
`SUCCEEDED`/`PASSED`, a finite score in `[0,1]`, the six-field version vector, and a
promotion-safe component revision. The component revision covers the current
loaded-package-matched Python source tree, the runtime-support promotion guard,
benchmark schemas, release-validated canonical task path, task-specific
input/reference manifests, environment closure, and protocol projection.
References include evaluator gold or an explicitly audit-only known-negative;
the latter is provenance, never a pass oracle. WebMall's
environment closure validates and includes the current nested OSWorld Chrome
image manifest. Only the generated runtime-support status output, receipt
allowlist data, and receipts are excluded to avoid a digest cycle.
Free-form `final_output`, details, events, prompts, responses, paths, hosts, and
credentials are forbidden and never consulted for promotion.

The repository-scoped runtime-support schema evaluator rejects unsupported
future assertion keywords instead of silently accepting semantics it does not
implement. Pending blocker shapes are protocol-specific: Cart, artifact-state,
pipeline-implicit, and ordinary protocols cannot borrow one another's blocker
codes.

WebMall Cart reader reference validation is a separate shared component proof,
not a RunStore-v2 task receipt. Its independent allowlist is
`benchmark/provenance/webmall-cart-component-receipt-allowlist-v1.json`; the
only permitted non-empty key is `webmall-cart-reader-reference-v1`. That entry
must bind four lowercase SHA-256 values: the sanitized receipt bytes and the
current Cart task, environment, and component identities. The mechanically
named receipt is read only from
`benchmark/provenance/webmall-cart-component-receipts/webmall-cart-reader-reference-v1.json`.
The directory may be absent while the allowlist is empty; otherwise its exact
single-file physical closure, directory identity, no-follow path chain, regular
single-link leaf, bounded stable read, duplicate-free JSON, and before/after
bytes are all revalidated.

The Cart task identity closes exactly the seven AddToCart tasks plus
CheapestProductSearch-007 against the canonical release. The environment
identity is reconstructed from the same WebMall and nested OSWorld manifest
snapshots actually supplied to the receipt validator. The receipt-neutral
component identity covers the complete `src/paraguibench` Python tree,
benchmark schemas, promotion guard, release, WebMall reader, and both
environment manifests, while excluding generated runtime-support/site data,
both receipt roots, and both allowlists. This prevents both dependency gaps and
a runtime-support self-reference cycle.

The explicit candidate validation intentionally produces its receipt against
the current checked-in WebMall manifest, including its `pending` advisory
field. The independently allowlisted receipt is the live-validation authority:
the trusted loader does not require a prior, unproven manifest mutation to
`live_validated`, while changing that manifest field alone never clears the
blocker. This keeps candidate output promotable without creating a
manifest/environment/component digest cycle.

Only a currently valid component receipt can remove the shared
`webmall_cart_reader_reference_live_validation_not_completed` blocker from the
exact eight Cart entries. It cannot change the image-materialization state,
which is derived independently from the current image manifest, or remove the
per-task `versioned_live_validation_not_completed` blocker; a generic task
receipt cannot remove the Cart component blocker. The component allowlist is
currently empty. The checked-in projection is 233 `local_ready`, 0
`local_components_incomplete`, 233 `blocked`, and 0 `live_validated`. Neither
receipt format admits or consumes Agent final text, Cart contents, worker IDs,
deployment origins or host endpoints, runtime filesystem paths, credentials,
prompts, or responses. The manifest-bound public OCI image identity remains an
intentional non-sensitive receipt field.

OSWorld artifact setup/getter/gold validation uses a second, task-scoped shared
component proof. Its version-controlled trust anchor is
`benchmark/provenance/osworld-artifact-component-receipt-allowlist-v1.json`,
which is intentionally empty in the checked-in release. Adding a receipt file
alone has no effect. A human reviewer must first verify that the receipt came
from the dedicated `osworld-artifact component-validate` candidate, then add
the exact receipt SHA-256 and all five current identities to the allowlist in a
reviewed version-control change. A generic RunStore-v2 task receipt, ordinary
`PASSED` Attempt, evaluator-only result, user-written artifact, Agent final
text, or component receipt not named by this trust anchor cannot clear any
component blocker.

The allowlist may contain only the twelve artifact-family tasks implemented by
the dedicated no-Agent component candidate. All thirteen artifact-family tasks
now have formal gold identities: `Operation-FileOperate-Settings-001` binds a
strict schema-v2 manifest that privately derives the first video frame whose
PTS is at least 8.000000 seconds from the canonical MP4 input. Its output and
decoded-RGB digests are fixed, and the 0.90 metric threshold rejects the
historical approximately 9.042-second image. This closes the local semantic
contract without making Settings candidate-, receipt-, or promotion-eligible;
those public task sets remain exactly twelve. Each permitted entry binds the
receipt bytes plus task, environment, setup, getter, and gold component
identities. The task identity includes the release-validated task, strict
input manifest, input draft bytes, and formal gold manifest. The
environment identity includes the current OSWorld manifest. Component
identities cover the receipt-neutral production Python/schema/promotion-guard
closure; generated runtime-support/site data, receipt files, and allowlists do
not enter those identities, preventing a regeneration cycle.

The Settings v2 PNG is materialized only on a controlled private provisioning
host from the held, SHA-verified canonical MP4. It is never committed, sent to
the guest or Agent, or written to RunStore. The manifest's Apache-2.0 evidence
binds the source dataset and private derivation basis; it does not grant public
redistribution of the derived PNG. A deployment without the pinned
FFmpeg/ffprobe toolchain receives the already verified mode-0600 output only
through a same-operator private provisioning channel and uses the offline
production resolver.

The dedicated candidate does not construct or run an Agent. Within one
resolved repository and one owned environment it performs strict input/gold
preflight, binds both the OCI digest and extracted qcow2 SHA from the same
current image manifest, copies the qcow2 from a held descriptor to a private
single-link 0400 snapshot, uses an uninjected loopback controller, runs the
production setup/finalizer/getter/gold projection and metric path, closes and
rehashes the owned snapshot, then inspects a versioned RunStore-v2
`SUCCEEDED`/`PASSED` Attempt. Public APIs permanently reject deriving this
receipt from an existing ordinary Attempt or caller-constructed proof object.

A trusted OSWorld artifact component receipt removes only
`osworld_artifact_getter_live_validation_not_completed`,
`osworld_artifact_gold_live_validation_not_completed`, and
`osworld_task_setup_live_validation_not_completed` for its own task. It never
changes the independently manifest-derived image-materialization state, never
removes `versioned_live_validation_not_completed`, and never changes another
task. With the official allowlist empty, the formal projection therefore remains
233 `local_ready`, 0 `local_components_incomplete`, 233 `blocked`, and 0
`live_validated`.

Pipeline-implicit component validation uses a third, independent task-scoped
trust anchor:
`benchmark/provenance/pipeline-implicit-component-receipt-allowlist-v1.json`.
Its only permitted task keys are BatchOperationExcel-008,
BatchOperationPPT-003, CombinationDocs-002, and SearchAndWrite-008. The
checked-in allowlist contains exactly BatchOperationPPT-003. Its 1173-byte
receipt has SHA-256
`cbf1f356c2dda1118490f45434e7f1546344a86a2647f8f40919c631ef458144`
and is retained as optional official audit evidence. Ordinary `doctor` / `run`
and `validate_runtime_support.py` do not consume this receipt; a stale identity
does not fail those commands. No other pipeline task is allowlisted. A receipt
file, ordinary `PASSED` Attempt, evaluator-only result, generic RunStore-v2
receipt, or Agent final text has no effect on ordinary evaluation.

The task identity binds the selected release entry, exact canonical task
bytes, explicitly authorized input and task-scoped reference manifest, and the
typed evaluation protocol. A reference may be evaluator-only gold, or the
CombinationDocs-002 host-side audit known-negative whose schema explicitly
forbids pass-oracle use. The environment identity binds the same held
OSWorld manifest bytes used by execution, the extracted qcow2 digest, and the
digest-pinned OCI image. The receipt-neutral component identity combines both
identities with the complete `src/paraguibench` Python tree, benchmark schemas,
CLI, runtime-support guard, and package configuration. Receipt roots,
allowlists, RunStore output, generated runtime-support, and generated site data
are excluded to avoid self-reference. The loader independently rederives all
three identities, requires receipt/current/allowlist equality, and rechecks the
bounded no-follow single-link physical closure and exact bytes before return.

`paraguibench pipeline-implicit component-validate` is the only public refresh
entry. It accepts no model, credential, Agent, final-text, evaluator,
environment, factory, image, proof, or receipt-output injection. The dedicated
candidate holds one image manifest snapshot, makes an independent `O_EXCL`
0400 qcow2 copy, validates task-specific reference metadata on the host, and
resolves a host-only bundle only for gold-role tasks. It uploads only verified
input assets, captures a production typed observation, and calls the formal
task evaluator. A receipt is constructed only after the owned
environment closes, qcow2 and OCI identities are attested, RunStore reports
`SUCCEEDED`/`PASSED` with score 1.0, two inspections agree, and current
task/image/component identities are rechecked.

BatchOperationPPT-003 has the complete static candidate chain. The local core
for BatchOperationExcel-008 now binds five input plus five host-only evaluator
manifests while leaving its original instruction unchanged; the evaluator
privately enforces whole-row hiding for literal `N/A`. CombinationDocs-002 now
uses its input XLSX as the sole fact source, and retains the erroneous upstream
answer only as a host-side audit known-negative that is expected to fail 2/3;
no corrected gold is generated or distributed. Both candidates upload input
only, ignore Agent final text, and have fixed-revision zero-skip typed-chain
tests. Their selected release hashes, ready-set projection, runtime-support and
site bytes have completed the serialized integration step. The reviewed final
797 BatchOperationPPT-003 component receipt remains the sole dedicated
allowlist entry as optional official audit evidence. Ordinary runtime-support
still keeps its pipeline-live blocker, together with the independent
versioned-live gate. Excel-008, CombinationDocs-002, and SearchAndWrite-008
likewise retain both gates. Settings tasks are outside this receipt protocol.
Even a current pipeline component receipt, when used by the optional official
audit path, removes only its own
`pipeline_implicit_live_validation_not_completed` blocker. It never removes
any still-applicable local blocker or the independent
`versioned_live_validation_not_completed` task receipt gate, and cannot change
the image-materialization state independently derived from the image manifest.
The formal counts remain 233 `local_ready`, 0 `local_components_incomplete`,
233 `blocked`, and 0 `live_validated`; exactly four pipeline-live blockers remain,
while all four evaluation tasks retain their versioned-live blockers. The
no-agent component candidate closed set is the implemented three tasks
(PPT-003, Excel-008, CombinationDocs-002); SearchAndWrite-008 stays in the
evaluation set only.

Two Operation risks retain explicit component blockers. The
`operation_word009_010_writer_live_validation_not_completed` blocker records only
the remaining real Writer gate: the local private pre/post DOCX evidence path and
adversarial preservation checks are complete, but a pinned, versioned Writer run
must prove the same contract before this blocker may be removed. Word-012's
canonical abbreviation transformation, typed evaluator, preservation
constraints, and runtime binding are locally closed; its historical semantics blocker
has been removed. It remains blocked only by the independent versioned-live gate.
The `combinationdocs003_real_render_validation_not_completed` blocker remains
until a pinned LibreOffice run proves that the source-relative table-picture
projection accepts the real GUI output without accepting hidden, cropped,
off-slide, content-altered, or style-drifted evidence. The task has no static
gold presentation; its native-table path is checked directly against the pinned
source range, while the image path remains subject to this real-render gate.

No API key, authentication token, private runtime configuration, internal host
binding, developer absolute path, execution log, VM image, or other binary
benchmark asset is included in this migration.
