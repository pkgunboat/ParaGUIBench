# Contributing to ParaGUIBench

ParaGUIBench is currently a `0.1` preview. Contributions are welcome, but a new task
definition is not considered runnable until its assets, environment protocol, evaluator,
and live evidence are all declared independently.

## Before opening a pull request

Create an isolated Python environment with Python 3.11–3.13, install the development
extra, and run the repository gates:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest
python scripts/benchmark/validate_release.py --repo-root .
python scripts/benchmark/validate_runtime_support.py --repo-root .
python scripts/security/scan_repository.py --root .
```

Website changes additionally require Node.js 22:

```bash
cd website
npm ci
npm test
npm run build -- --base /ParaGUIBench/
node scripts/validate-static-site.mjs dist --base /ParaGUIBench/
```

## Contribution boundaries

- Keep provider-neutral scheduling contracts under `framework`.
- Put runnable policies under `agents/systems` and provider SDK integrations under
  `integrations/providers`.
- Do not let Agent implementations import evaluators.
- Keep canonical task definitions, runtime-support declarations, and live evidence as
  separate artifacts.
- Do not add credentials, endpoint values, private network addresses, user-specific
  absolute paths, raw model responses, or unredacted run logs.
- Do not redistribute VM images, datasets, or task assets until their licenses and
  redistribution terms have been reviewed.

For a new dependency, update the relevant dependency tree and explain why the dependency
cannot be implemented with the existing standard-library surface.

## Task and evaluator changes

A task or evaluator pull request should include:

1. the canonical task or protocol change;
2. focused positive, negative, and error-path tests;
3. an updated release or runtime-support manifest when its public contract changes;
4. provenance and license notes for third-party material;
5. live evidence only when the exact task–Agent–environment–evaluator combination was
   executed end to end.

Never mark a task `live_validated` from schema checks, evaluator parity tests, or a
successful installation alone.

## Review scope

Keep pull requests focused. Describe the affected module boundary, tests run, public-data
impact, and whether the change alters runtime support. Maintainers may ask for a smaller
change when code, benchmark data, infrastructure, and documentation are mixed without a
clear dependency.

For questions or proposals, open a GitHub issue before preparing a large migration.
