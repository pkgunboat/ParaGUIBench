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
| `file-exists` | The selected external secret file is absent. | Create it outside the checkout. |
| `file-regular` | The path is not an ordinary non-symlink file. | Replace links or special files with a private ordinary file. |
| `file-owner` | The file is not owned by the current user. | Correct ownership through the host administrator. |
| `file-mode-0600` | Group or other users have access, or the mode differs. | Run `chmod 600` on the external file. |
| `file-outside-checkout` | The file resolves inside the source checkout. | Move it to a user configuration directory outside Git. |

If wheel construction fails while obtaining `hatchling`, check outbound package
index access and retry the build from a new build venv. Do not copy a build
environment from another project.

The Live OSWorld installation profile checks Python dependencies only. Docker,
KVM, image digests, assets, loopback ports, and credential references are
deployment checks handled by `paraguibench doctor`; see
[`../deployment/osworld-linux.md`](../deployment/osworld-linux.md). Public CI
must stop at installation and repository validation and must not receive a
credential or start a real GUI task.

When asking for help, do not attach a secret file, environment dump, pip
configuration containing authenticated indexes, model response, screenshot,
or raw RunStore directory. Share only the stable check IDs, supported Python
minor version, operating-system family, and the repository commit.
