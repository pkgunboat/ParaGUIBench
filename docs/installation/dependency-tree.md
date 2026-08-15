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
│   ├── paraguibench.runtime
│   │   ├── runtime.gold_assets / runtime.derived_gold
│   │   ├── runtime.osworld_gold
│   │   └── runtime.webmall_binding/doctor/URL/Cart/order environments
│   ├── integrations.webmall
│   │   ├── same-session browser Cart reader [four stores, two consistent reads]
│   │   └── WP-CLI order evidence + lease client/coordinator
│   ├── integrations.model_endpoint
│   │   └── stdlib URL contract: public HTTPS; loopback HTTP allowed
│   ├── integrations.onlyoffice
│   │   └── task-id routing constants only; Flask stays out of Core
│   └── paraguibench CLI
│       ├── gold fetch/verify/materialize
│       ├── model-probe qwen-native        [dispatch only; live extra executes]
│       └── third-party runtime dependencies: none for Core-only commands
├── Live OSWorld extra
│   ├── Core wheel
│   ├── openai >=1.82,<3              [model client; lazy credential read]
│   ├── Pillow >=11,<13               [host screenshot dimensions]
│   ├── requests >=2.32,<3            [loopback guest controller]
│   ├── Playwright >=1.50,<2          [attach-only Chrome CDP probe]
│   └── model-probe qwen-native
│       └── openai + Pillow           [one bounded request; no VM/RunStore]
├── Operation evaluator extra
│   ├── Core wheel
│   ├── openpyxl >=3.1.5,<4            [11 个 XLSX + 1 个 HTML + 003 源表格；延迟导入]
│   ├── python-docx >=1.1.2,<2         [16 个 DOCX checks；延迟导入]
│   ├── python-pptx >=1.0.2,<2         [2 个 PPTX + 003 跨文档 check；延迟导入]
│   └── Pillow >=11,<13                [003 源表格图片投影；延迟导入]
├── OSWorld artifact projection extra
│   ├── Core wheel
│   ├── openpyxl >=3.1.5,<4            [XLSX/CSV raw→typed projection]
│   ├── python-docx >=1.1.2,<2         [DOCX raw→typed projection]
│   ├── python-pptx >=1.0.2,<2         [PPTX raw→typed projection]
│   ├── Pillow >=11,<13                [jointly normalized image projection]
│   ├── pypdf >=5,<7                   [bounded PDF text projection; lazy import]
│   └── SearchWrite-008 XLSX boundary
│       ├── immutable searchwrite_contract             [no third-party dependency]
│       ├── multiprocessing spawn + resource       [Python standard library]
│       ├── openpyxl                                [child-only lazy import]
│       ├── Linux /proc RSS monitor                 [system interface]
│       ├── macOS /usr/lib/libproc.dylib via ctypes [system interface]
│       └── capability→prepare manifest binding      [stdlib SHA-256/JSON]
├── OnlyOffice share extra
│   ├── Core wheel
│   ├── flask >=3,<4                   [share service factory and unit tests]
│   ├── gunicorn >=22,<24              [container: one worker, many threads]
│   └── requests >=2.32,<3             [callback download]
├── Development extra
│   ├── Core wheel
│   └── pytest >=8.3,<9
├── Live OSWorld system boundary      [not installed by pip]
│   ├── Linux x86-64
│   ├── Docker daemon and pinned container digest
│   ├── /dev/kvm access
│   ├── pinned OSWorld qcow2 archive
│   ├── extracted image materialization identity
│   │   ├── schema v2 archive→member→output recipe [6bf default fixed]
│   │   ├── cli.osworld_qcow2_materializer          [唯一正式 python -m 薄入口]
│   │   │   └── canonical implementation main       [单一 strict type identity]
│   │   └── audited reproducible materialization evidence [统一 image gate 已清除]
│   ├── guest Python with Pillow importable under `python -I`
│   │                                      [artifact pixel getter; live-gate prerequisite]
│   ├── task asset mode
│   │   ├── NONE: no cache or shared-directory preparation
│   │   ├── pinned manifest: task-specific, hash-verified asset cache
│   │   └── non-empty legacy reference: fail-closed until migrated
│   ├── evaluator-only gold mode
│   │   ├── NONE: doctor/run do not create or read a gold cache
│   │   ├── pinned download manifest (schema v1)
│   │   │   ├── explicit `paraguibench gold fetch` network provisioning
│   │   │   ├── private 0700 directories and 0600 regular files
│   │   │   └── offline size/SHA/media verification in doctor and evaluator
│   │   └── private derived manifest (schema v2; Settings-001)
│   │       ├── `paraguibench gold materialize` on a controlled private provisioning host
│   │       ├── held canonical input + FFmpeg/ffprobe 8.1.1 + fixed PTS/output/RGB identities
│   │       ├── mode-0600 output; no Git, guest, Agent, RunStore, network, or public redistribution
│   │       └── deployment/evaluator consumes only the offline production resolver
│   └── credential injection
│       ├── external owner-only file, mode 0600
│       └── deployment secret manager
├── Live WebMall system boundary      [adds to Live OSWorld]
    ├── four unique HTTP(S) store origins
    ├── Cart protocol
    │   ├── manifest-selected Store API reader contract
    │   ├── the Agent's attached BrowserContext and one worker identity
    │   ├── complete two-read agreement for every one of the four stores
    │   └── reference deployment live validation gate [pending until probed]
    ├── Checkout/EndToEnd protocols
    │   ├── four unique `wp --ssh=` reader targets
    │   ├── WP-CLI executable + `WP_CLI_DOCKER_NO_TTY=1`
    │   ├── distributed-lease v1 coordinator
    │   │   ├── loopback HTTP or remote HTTPS endpoint
    │   │   └── persistent private SQLite fencing state
    │   └── separated runner/coordinator secret injection
    │       ├── PARAGUIBENCH_WEBMALL_LEASE_TOKEN
    │       └── PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN
    ├── manifest-matched WordPress/WooCommerce/HPOS services
    └── no model/API credentials in versioned files or RunStore
└── Live OnlyOffice system boundary   [adds to local lab Docker; not installed by pip]
    ├── pinned DocumentServer digest
    ├── built share service image [Flask/Gunicorn/requests; no pip at start]
    ├── repo-external state root
    └── exact 4 SearchAndWrite tasks; the other 6 stay on OSWorld/LibreOffice
```

The wheel contains the Python package. Canonical task JSON, release/runtime
manifests, schemas, fixtures, environment manifests, and validators remain
versioned repository resources and are resolved through an explicit
`--repo-root`.

Schema-v1 gold provisioning uses Python standard-library HTTPS and adds no Core
third-party dependency. Schema-v2 private derivation is an explicit provisioning-only
operation that uses pinned FFmpeg/ffprobe 8.1.1 executables outside Core; deployment and
evaluation remain offline. The provisioning host must install the `artifact` or `live`
extra so that Pillow can verify PNG pixels; the CLI imports this boundary lazily, so
Core-only fetch/verify/help paths remain dependency-free. Only manifest metadata is versioned; evaluator gold bytes
remain outside the wheel and Git in a separate host-private cache.

The 14 fixed OSWorld artifact metric contracts are Core, standard-library, no-I/O
value semantics. Spreadsheet, DOCX, PPTX, PDF and image decoders remain in the
evaluator-only evidence adapter: it must verify pinned gold first and inject immutable
typed projections. The metric layer never opens a path, downloads gold or imports an
Office/PDF parser. Image evidence is jointly normalized with the pinned
min-size/Lanczos rule before RGB/HSV bytes enter the pure metric; malformed,
oversized or incomplete typed values fail closed.

The evaluator-only artifact projection layer implements all 11 external raw contracts
across XLSX/CSV, DOCX, PPTX, PDF, PDF archives and images. Install its parsers only on
evaluator hosts that must convert already verified artifact and gold bytes:

```bash
python -m pip install -e '.[artifact]'
```

`pypdf` and the Office/image parsers are imported lazily. A missing dependency,
malformed container, parser failure, incomplete gold binding or resource-limit breach
is an evaluator error; no parser fallback or Agent-provided value is accepted.

The artifact finalizer adds no host Python dependency: it emits only fixed, shell-free
argv from the versioned evidence spec. Its guest boundary relies on the pinned OSWorld
image for Python, LibreOffice and `pyautogui`; directory archiving uses the Python
standard library with no-follow, byte/count/time budgets and atomic replacement. These
10 non-`none` actions are attached to the environment evidence lifecycle and execute
after the Agent but before capture; the three `none` actions perform zero guest I/O.
These dependencies and local lifecycle tests do not make a task runnable by themselves:
verified input/gold, setup and versioned live gates still apply.

The 32 canonical Operation eval-rules tasks use a machine-verifiable closure of
33 checks. Two directory checks remain standard-library only; the HTML conversion
check also needs openpyxl. Install the Office parsers only on evaluator hosts that
need the remaining 31 checks:

```bash
python -m pip install -e '.[operation]'
```

Every OOXML artifact passes a standard-library no-symlink, size, member-count,
compression-ratio, member-path, macro, encrypted-member and DTD/entity preflight
before any optional parser is imported. The optional libraries are used only for
offline parsing; the evaluator never launches Office, follows relationships over the
network or executes embedded code.

The dependency directions inside the Python package are maintained separately
in [`../architecture/dependency-tree.md`](../architecture/dependency-tree.md).
The public CI installs the built wheel into fresh environments before running
the package tests, so a missing wheel module cannot be masked by an editable
source installation.

The Core wheel includes the WebMall logical-URL and checkout contracts, pinned
environment loader, bounded WP-CLI evidence source, baseline/final state
machine, and standard-library lease client/coordinator. These modules add no
Python dependency. The wheel does **not** install WP-CLI, deploy the four
WooCommerce stores, configure Docker/SSH access, terminate TLS, or provide
credentials. Those remain explicit system/deployment boundaries, and no
WebMall task becomes `live_validated` until the versioned real Attempt passes.
