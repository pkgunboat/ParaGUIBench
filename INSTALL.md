# Installation

ParaGUIBench 0.2 preview has two installation layers. **Core** installs the
benchmark contracts, framework, evaluators, RunStore, and CLI without GUI
runtime dependencies. **Live OSWorld** adds the model client, image handling,
HTTP client, and CDP probe client needed by the candidate Linux/KVM execution
path. That path has
historical smoke evidence, but the current manifest has zero versioned
`live_validated` tasks.

Both layers support Python 3.11–3.13 and use a standard-library virtual
environment. The commands below build a wheel first and then install that wheel
into a new environment; they do not depend on an existing project environment.
Linux and macOS can install Core. A real Live OSWorld run requires Linux
x86-64, Docker, and KVM. A WebMall Checkout/EndToEnd run reuses that browser
layer and additionally requires four deployed stores, WP-CLI reader targets,
and the distributed-lease coordinator described below.

## Clone and build one wheel

```bash
git clone https://github.com/pkgunboat/ParaGUIBench.git
cd ParaGUIBench

python3 -m venv .build-venv
.build-venv/bin/python -m pip install --upgrade pip
.build-venv/bin/python -m pip wheel --no-deps --wheel-dir dist .

WHEEL_PATH="$(find "$(pwd)/dist" -type f -name 'paraguibench-*.whl' -print -quit)"
test -n "$WHEEL_PATH"
```

`pip wheel` creates an isolated build environment and installs the build
backend declared in `pyproject.toml`. The produced wheel is the artifact used
by both installation layers. Do not copy packages from an older checkout.

## Layer 1: Core

Core has no third-party runtime dependency:

```bash
python3 -m venv .venv-core
.venv-core/bin/python -m pip install --no-index "$WHEEL_PATH"

.venv-core/bin/python scripts/installation/verify_install.py --profile core
.venv-core/bin/paraguibench --help >/dev/null
```

A valid installation prints only these stable lines:

```text
PASS python-version
PASS package-import
PASS cli-help
PASS profile-core
```

The source checkout remains necessary for canonical task definitions, schemas,
manifests, and repository validators; those benchmark resources are not hidden
inside the Python wheel.

## Layer 2: Live OSWorld

Create a separate environment and ask pip to resolve the wheel's declared
`live` extra:

```bash
python3 -m venv .venv-live
.venv-live/bin/python -m pip install \
  "paraguibench[live] @ file://${WHEEL_PATH}"

.venv-live/bin/python scripts/installation/verify_install.py \
  --profile live-osworld
```

This profile verifies `openai`, `Pillow`, `requests`, and `Playwright` in
addition to the Core checks. Playwright is used only to attach to the guest
Chrome CDP endpoint, so this step does not install or launch a host browser.
The `BatchOperation-001` image getter also imports Pillow inside the guest
through `python3 -I`. Installing the host `live` extra does not satisfy that
boundary: the pinned qcow2 image must make Pillow importable in isolated Python.
The `CombinationDocs-015` single-file getter uses only the guest standard
library, but both tasks remain blocked until their exact guest capabilities pass
their respective live gates.
The verifier deliberately does not start Docker, download a VM, access an API,
or run a benchmark task. Continue with
[`docs/deployment/osworld-linux.md`](docs/deployment/osworld-linux.md) to pin and
verify the OSWorld input assets, separately provision any evaluator-only gold,
and run `paraguibench doctor`. External gold is not a wheel dependency:
schema-v1 tasks use explicit `paraguibench gold fetch`, while schema-v2 tasks
use `gold materialize` on a controlled private provisioning host. `gold verify`,
doctor, and evaluation remain offline.

### Evaluator-only pinned gold

A task may declare both an `asset_manifest` for guest-visible input and a
`gold_manifest` for host evaluator-only expected data. The manifest metadata is
versioned in the checkout, while downloaded or privately derived gold bytes
remain outside Git and the wheel. Keep the two cache roots separate; `doctor`
and `run` never download or derive gold implicitly. See the deployment guide
for the Settings-001 schema-v2 FFmpeg/ffprobe 8.1.1 provisioning workflow.

```bash
export PARAGUIBENCH_GOLD_CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/paraguibench/gold"
install -d -m 700 "$PARAGUIBENCH_GOLD_CACHE_ROOT"

.venv-live/bin/paraguibench gold fetch \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"

.venv-live/bin/paraguibench gold verify \
  --repo-root . \
  --task-id Operation-FileOperate-CombinationDocs-015 \
  --gold-cache-root "$PARAGUIBENCH_GOLD_CACHE_ROOT"
```

Pass the same `--gold-cache-root` to `doctor` and `run`. The `gold_cache`
doctor gate fails closed before VM startup, model-client construction, handing
credentials to a model service, or RunStore creation. Runtime evaluation reopens
and rehashes the private file to preserve the post-doctor integrity boundary.

### WebMall runtime bindings

The Core wheel now contains the standard-library WebMall manifest loader,
WP-CLI evidence adapter, distributed-lease client/coordinator, and closed-world
evaluator. A live task still needs the Live OSWorld browser layer plus external
WordPress/WooCommerce services and the `wp` executable; pip does not create or
reset those services.

The fixed manifest references these exact runner variables:

```text
PARAGUIBENCH_WEBMALL_STORE_1_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_2_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_3_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN
PARAGUIBENCH_WEBMALL_STORE_1_READER_TARGET
PARAGUIBENCH_WEBMALL_STORE_2_READER_TARGET
PARAGUIBENCH_WEBMALL_STORE_3_READER_TARGET
PARAGUIBENCH_WEBMALL_STORE_4_READER_TARGET
PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL
PARAGUIBENCH_WEBMALL_LEASE_TOKEN
WP_CLI_DOCKER_NO_TTY=1
```

The coordinator process reads its matching secret from
`PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN`; its value must equal the runner's
`PARAGUIBENCH_WEBMALL_LEASE_TOKEN`, while the two processes retain separate
environment allowlists. Keep all real values outside the checkout and default
logs. Remote coordinator traffic requires HTTPS; plaintext HTTP is accepted
only for a loopback endpoint.

Follow the copy-paste coordinator, `doctor`, and `run` sequence in
[`docs/deployment/webmall-linux.md`](docs/deployment/webmall-linux.md). Passing
local contract tests or doctor does not change the runtime-support status: the
current WebMall slice remains blocked until a versioned live Attempt succeeds.

## Credentials: external 0600 file or secret manager

Never put credentials in the checkout, a command argument, shell history,
issue, test fixture, or public CI. Choose one of these two injection methods:

1. An external owner-only file. Create it outside the checkout, edit it without
   shell tracing, and verify metadata before sourcing:

   ```bash
   export PARAGUIBENCH_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/paraguibench/secrets.env"
   install -d -m 700 "$(dirname "$PARAGUIBENCH_SECRET_FILE")"
   install -m 600 /dev/null "$PARAGUIBENCH_SECRET_FILE"
   "${EDITOR:-vi}" "$PARAGUIBENCH_SECRET_FILE"

   .venv-live/bin/python scripts/installation/verify_secret_file.py \
     --secret-file "$PARAGUIBENCH_SECRET_FILE" \
     --checkout-root .

   set +x
   . "$PARAGUIBENCH_SECRET_FILE"
   ```

   Define `PARAGUIBENCH_MODEL_API_KEY` and
   `PARAGUIBENCH_MODEL_BASE_URL` inside that file. A WebMall runner additionally
   receives its store bindings, reader targets, coordinator URL, and
   `PARAGUIBENCH_WEBMALL_LEASE_TOKEN` through an external owner-only file or
   deployment manager; the coordinator receives
   `PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN` through its own secret boundary.
   The verifier checks only
   existence, ordinary-file status, ownership, mode, and location; it never
   opens the file.

2. A deployment-platform secret manager. Inject the same two variable names
   directly into the `paraguibench` process. Do not create a repository-local
   file and do not print the process environment to verify injection.

The model identifier, cache roots, VM path, and ports are non-secret runtime
configuration. A model endpoint may still expose internal topology, so it
follows the same non-logging boundary as the API key.

## Contributor validation

Install the same wheel with the public test extras, then run the complete local
gates:

```bash
python3 -m venv .venv-dev
.venv-dev/bin/python -m pip install \
  "paraguibench[live,dev,artifact] @ file://${WHEEL_PATH}"

.venv-dev/bin/python -m pytest
.venv-dev/bin/python scripts/benchmark/validate_release.py --repo-root .
.venv-dev/bin/python scripts/benchmark/validate_runtime_support.py --repo-root .
.venv-dev/bin/python scripts/security/scan_repository.py --root .
```

The `dev` extra carries pytest plus the `python-docx` and `openpyxl` imports the
test suite requires; `artifact` additionally enables the document-format
evaluator tests that otherwise skip.

The public workflow repeats this wheel-first process on Python 3.11, 3.12, and
3.13. It never receives a model or lease credential and never performs Live
OSWorld/WebMall E2E; real GUI validation belongs on a controlled Linux/KVM host.

For the exact dependency boundary, see
[`docs/installation/dependency-tree.md`](docs/installation/dependency-tree.md).
For stable failure identifiers and safe remediation, see
[`docs/installation/troubleshooting.md`](docs/installation/troubleshooting.md).
