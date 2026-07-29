# Installation

ParaGUIBench 0.1 preview has two installation layers. **Core** installs the
benchmark contracts, framework, evaluators, RunStore, and CLI without GUI
runtime dependencies. **Live OSWorld** adds the model client, image handling,
and HTTP client needed by the currently validated Linux/KVM execution path.

Both layers support Python 3.11–3.13 and use a standard-library virtual
environment. The commands below build a wheel first and then install that wheel
into a new environment; they do not depend on an existing project environment.
Linux and macOS can install Core. A real Live OSWorld run requires Linux
x86-64, Docker, and KVM.

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

This profile verifies `openai`, `Pillow`, and `requests` in addition to the Core
checks. It deliberately does not start Docker, download a VM, access an API, or
run a benchmark task. Continue with
[`docs/deployment/osworld-linux.md`](docs/deployment/osworld-linux.md) to pin and
verify the OSWorld assets and run `paraguibench doctor`.

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
   `PARAGUIBENCH_MODEL_BASE_URL` inside that file. The verifier checks only
   existence, ordinary-file status, ownership, mode, and location; it never
   opens the file.

2. A deployment-platform secret manager. Inject the same two variable names
   directly into the `paraguibench` process. Do not create a repository-local
   file and do not print the process environment to verify injection.

The model identifier, cache roots, VM path, and ports are non-secret runtime
configuration. A model endpoint may still expose internal topology, so it
follows the same non-logging boundary as the API key.

## Contributor validation

Install the same wheel with both declared extras, then run the complete local
gates:

```bash
python3 -m venv .venv-dev
.venv-dev/bin/python -m pip install \
  "paraguibench[live,dev] @ file://${WHEEL_PATH}"

.venv-dev/bin/python -m pytest
.venv-dev/bin/python scripts/benchmark/validate_release.py --repo-root .
.venv-dev/bin/python scripts/benchmark/validate_runtime_support.py --repo-root .
.venv-dev/bin/python scripts/security/scan_repository.py --root .
```

The public workflow repeats this wheel-first process on Python 3.11, 3.12, and
3.13. It never receives a model key and never performs Live OSWorld E2E; real
GUI validation belongs on a controlled Linux/KVM host.

For the exact dependency boundary, see
[`docs/installation/dependency-tree.md`](docs/installation/dependency-tree.md).
For stable failure identifiers and safe remediation, see
[`docs/installation/troubleshooting.md`](docs/installation/troubleshooting.md).
