# Installation dependency tree

This tree is normative for the 0.1 preview installation paths. Package names
and version ranges come from `pyproject.toml`; system prerequisites are not
Python dependencies and are never installed by ParaGUIBench.

```text
ParaGUIBench source checkout
├── build wheel
│   ├── Python 3.11–3.13
│   ├── pip
│   └── hatchling >=1.27              [isolated build backend]
├── Core wheel
│   ├── paraguibench.benchmark
│   ├── paraguibench.framework
│   ├── paraguibench.agents
│   ├── paraguibench.evaluation
│   ├── paraguibench.runstore
│   └── paraguibench CLI
│       └── third-party runtime dependencies: none
├── Live OSWorld extra
│   ├── Core wheel
│   ├── openai >=1.82,<3              [model client; lazy credential read]
│   ├── Pillow >=11,<13               [screenshot dimensions]
│   └── requests >=2.32,<3            [loopback guest controller]
├── Development extra
│   ├── Core wheel
│   └── pytest >=8.3,<9
└── Live OSWorld system boundary      [not installed by pip]
    ├── Linux x86-64
    ├── Docker daemon and pinned container digest
    ├── /dev/kvm access
    ├── pinned OSWorld qcow2 archive and extracted SHA-256
    ├── task-specific, hash-verified asset cache
    └── credential injection
        ├── external owner-only file, mode 0600
        └── deployment secret manager
```

The wheel contains the Python package. Canonical task JSON, release/runtime
manifests, schemas, fixtures, environment manifests, and validators remain
versioned repository resources and are resolved through an explicit
`--repo-root`.

The dependency directions inside the Python package are maintained separately
in [`../architecture/dependency-tree.md`](../architecture/dependency-tree.md).
The public CI installs the built wheel into fresh environments before running
the package tests, so a missing wheel module cannot be masked by an editable
source installation.
