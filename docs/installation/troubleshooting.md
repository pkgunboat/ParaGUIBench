# Installation troubleshooting

The installation verifiers intentionally print only `PASS <check-id>` or
`FAIL <check-id>`. They suppress package locations, interpreter paths,
environment values, subprocess output, and exception text. This limits
diagnostic detail by design; it also makes the output safe to attach to a
public issue.

| Check | Meaning | Safe remediation |
|---|---|---|
| `python-version` | The interpreter is outside Python 3.11–3.13. | Recreate the venv with a supported interpreter. |
| `package-import` | `paraguibench` is unavailable to that interpreter. | Reinstall the built wheel with that venv's `python -m pip`. |
| `cli-help` | The installed module CLI cannot construct its help command. | Reinstall the wheel; if it persists, report the Python minor version and check ID only. |
| `dependency-openai` | The Live OSWorld model client is unavailable. | Install the wheel with its `live` extra. |
| `dependency-pillow` | Screenshot image support is unavailable. | Install the wheel with its `live` extra. |
| `dependency-requests` | The loopback guest HTTP client is unavailable. | Install the wheel with its `live` extra. |
| `dependency-playwright` | The attach-only Chrome CDP probe client is unavailable. | Install the wheel with its `live` extra; a host browser download is not required. |
| `playwright_dependency` | The live OSWorld doctor cannot resolve the attach-only Playwright client. | Reinstall with `pip install -e '.[live]'`; do not download a separate host browser. |
| `gold_cache` | Required evaluator-only gold is missing, corrupt, linked, non-private, or not byte-identical to its manifest. | For schema v1, run `gold fetch`; for schema v2, follow the controlled private `gold materialize` workflow. Then run `gold verify` with the same external root; never copy gold into the input cache or loosen permissions. |
| `webmall_manifest` | The pinned four-store environment, browser binding, reader script, or protocol identity is inconsistent. | Use an intact checkout at the intended commit; do not edit the manifest, reader script, or digest on the deployment host. |
| `webmall_store_<n>_origin` | One of the four origin references is missing, malformed, or duplicates another store. | Correct the named external deployment binding without printing its value. |
| `webmall_store_<n>_reader_target` | One of the four `wp --ssh=` targets is missing, malformed, or duplicates another store. | Correct the named external binding; for Docker transport also set `WP_CLI_DOCKER_NO_TTY=1`. |
| `webmall_wp_cli` | The `wp` executable is not available to the runner process. | Install WP-CLI through the deployment image and verify only `command -v wp` rather than dumping `PATH`. |
| `webmall_lease_endpoint` | The coordinator URL is absent or unsafe; remote plaintext HTTP is rejected. | Inject `PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL`; use HTTPS remotely or loopback HTTP on one host. |
| `webmall_lease_credential` | The runner lease credential reference is absent or invalid. | Inject `PARAGUIBENCH_WEBMALL_LEASE_TOKEN` through an external `0600` file or secret manager; never print it. |
| `file-exists` | The selected external secret file is absent. | Create it outside the checkout. |
| `file-regular` | The path is not an ordinary non-symlink file. | Replace links or special files with a private ordinary file. |
| `file-owner` | The file is not owned by the current user. | Correct ownership through the host administrator. |
| `file-mode-0600` | Group or other users have access, or the mode differs. | Run `chmod 600` on the external file. |
| `file-outside-checkout` | The file resolves inside the source checkout. | Move it to a user configuration directory outside Git. |

If wheel construction fails while obtaining `hatchling`, check outbound package
index access and retry the build from a new build venv. Do not copy a build
environment from another project.

The Live OSWorld installation profile checks Python dependencies only. Docker,
KVM, image digests, input assets, evaluator-only gold, loopback ports, and credential references are
deployment checks handled by `paraguibench doctor`; see
[`../deployment/osworld-linux.md`](../deployment/osworld-linux.md). Public CI
must stop at installation and repository validation and must not receive a
credential or start a real GUI task.

WebMall adds four origins, four WP-CLI reader targets, and a distributed-lease
coordinator to the same OSWorld browser gate. The complete variable table and
safe coordinator/doctor/run sequence are in
[`../deployment/webmall-linux.md`](../deployment/webmall-linux.md). Doctor does
not contact a store or acquire a lease, so a `PASS` report is not live evidence.

When asking for help, do not attach a secret file, environment dump, pip
configuration containing authenticated indexes, model response, screenshot,
or raw RunStore directory. Share only the stable check IDs, supported Python
minor version, operating-system family, and the repository commit.
