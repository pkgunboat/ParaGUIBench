# OSWorld artifact-family finalizer 依赖树

本文档记录剩余 13 个 legacy OSWorld artifact-state 任务的收尾动作
闭集、来源身份和安全边界。finalizer 已接入单 Attempt environment 的
评价生命周期；本文仍不把代码存在或合成测试误报为 live validation。

```text
runtime.osworld_environment.OSWorldTaskEnvironment       [Agent 后、capture 前]
├── integrations.osworld.artifact_finalizer.OSWorldArtifactFinalizer
│   ├── integrations.osworld.artifact_evidence_specs
│   │   ├── canonical task / source evaluator / source task
│   │   ├── source contract SHA-256 / evidence spec SHA-256
│   │   ├── finalize action ID / canonical options JSON
│   │   └── item, byte, container and finalize timeout limits
│   └── integrations.osworld.controller.execute_with_timeout
│       └── loopback OSWorld agent server /execute
│           ├── shell=false
│           ├── validated argv
│           └── per-call finite timeout + strict CommandResult schema
└── runtime.osworld_artifact_evidence.OSWorldArtifactEvidenceSource
    └── finalize 成功后捕获并评价冻结 artifact
```

用于未来 114 实机的 component candidate 不运行 Agent，也不从
Agent final text 获取任何证据：

```text
cli: osworld-artifact component-validate
└── runtime.osworld_artifact_component_candidate
    ├── release/task + strict input draft/manifest + formal gold preflight
    ├── current OSWorld manifest
    │   ├── OCI image@sha256
    │   └── extracted qcow2 SHA-256
    ├── OSWorldAttestedDockerSession
    │   └── held source FD → private 0400 snapshot → close-time rehash
    ├── production OSWorldController (loopback, trust_env=False)
    ├── setup → finalizer/getter → resolver/media/projection/metric
    ├── owned environment close
    ├── RunStore-v2 allowlist-only SUCCEEDED/PASSED inspection
    └── task-scoped receipt (G/D/S only; no image/versioned promotion)
```

公共 `OSWORLD_ARTIFACT_FINALIZER_TASK_IDS` 精确限定下表 13 项，
`OSWORLD_ARTIFACT_FINALIZER_ACTIONS` 只把已摘要绑定的 spec 投影为任务—动作
映射。任务载荷不能提供或覆盖 argv、路径、窗口标题、过滤器或超时。
runtime 另导出 `OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS`，只包含其中
10 个非 `none` 动作，并由测试断言与 action catalog 精确相等；manifest
不得从纯 evaluator 或 finalizer catalog 自行推断支持。

| canonical task | finalizer action | 源 postconfig 语义 |
|---|---|---|
| `Operation-FileOperate-BatchOperation-003` | `archive-pdf-directory` | 将 `Desktop/book` 直接 `*.pdf` 写入 `book.zip` |
| `Operation-FileOperate-CombinationDocs-009` | `save-active-libreoffice-document` | 严格激活指定 Impress 窗口后 `Ctrl+S` |
| `Operation-FileOperate-CombinationDocs-010` | `none` | 无 postconfig |
| `Operation-FileOperate-CombinationDocs-011` | `none` | 无 postconfig |
| `Operation-FileOperate-CombinationDocs-012` | `save-active-libreoffice-document` | 严格激活指定 Writer 窗口后 `Ctrl+S` |
| `Operation-FileOperate-CombinationDocs-013` | `export-calc-first-sheet-csv` | 用固定 StarCalc 过滤器导出 `GRF-p5y-Sheet1.csv` |
| `Operation-FileOperate-CombinationDocs-014` | `export-calc-first-sheet-csv` | 用同一过滤器导出 `supported_rate-Sheet1.csv` |
| `Operation-FileOperate-SearchAndWrite-001` | `save-active-libreoffice-document` | 严格激活指定 Calc 窗口后 `Ctrl+S` |
| `Operation-FileOperate-SearchAndWrite-003` | `save-active-libreoffice-document` | 严格激活指定 Writer 窗口后 `Ctrl+S` |
| `Operation-FileOperate-SearchAndWrite-005` | `save-active-libreoffice-document` | 严格激活指定 Calc 窗口后 `Ctrl+S` |
| `Operation-FileOperate-SearchAndWrite-009` | `none` | 无 postconfig |
| `Operation-FileOperate-Settings-001` | `save-active-libreoffice-document` | 严格激活指定 Impress 窗口后 `Ctrl+S` |
| `Operation-WebOperate-SearchAndWrite-001` | `save-active-libreoffice-document` | 严格激活指定 Calc 窗口后 `Ctrl+S` |

动作闭集由 1 个 archive、2 个 Calc export、7 个 strict-window save 和
3 个 `none` 组成。这 10 个非空动作均能从冻结的最终源 evaluator
postconfig 唯一恢复，因此 finalizer 本身没有需要猜测的 action。
当前 13 项的 task-specific start context 和 strict input 绑定已全部
闭合；13 项均已闭合正式 gold 身份。`Settings-001` 使用从 canonical MP4
私有派生 8.008 秒帧的 schema-v2 gold 及 0.90 阈值，但当前 component
candidate/receipt/promotion 执行闭集仍精确为其他 12 项。因此本地身份闭合
不会让 Settings 借用现有 component receipt 清除任何 live 门禁。

`archive-pdf-directory` 通过固定 `python3 -I -c` helper 逐级
`O_NOFOLLOW` 打开目录，只接受安全、非隐藏、直接、普通 `.pdf` 成员，
从而保持旧 `*.pdf` 不匹配点文件的语义；随后复核 inode/大小，应用数量、
单项和总字节上限，并以同目录临时文件原子替换目标 ZIP。符号链接、空闭集、
竞态替换或超限都使整个动作失败。

`save-active-libreoffice-document` 先以固定 argv 执行
`env DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=... wmctrl -Fa <pinned-title>`。
`-F` 保留源 contract 的大小写敏感完整标题匹配，且 controller 必须看到
`returncode == 0`。它不使用旧 `activate_window(strict=False)`，也不依赖在
Linux 上可能吞掉 `wmctrl` 失败的 setup endpoint。只有激活成功后，第二个固定
helper 才按每任务摘要绑定的 `activation_settle_seconds` 等待，发送
`Ctrl+S`，再按 `post_save_settle_seconds` 等待落盘。七项前后等待分别恢复
旧 postconfig 的 0.5–5 秒与 0.5–1 秒值；激活、前等待、保存和后等待共享同一
monotonic 总超时，任一步失败都不会继续。

`export-calc-first-sheet-csv` 在固定 helper 内逐级 nofollow 打开并持有输出
目录 fd，再 nofollow 打开 workbook inode。在同一目录 fd 下创建 mode 0700
的随机私有目录，把已打开 workbook 按单项/总字节上限流式复制为快照；
LibreOffice 只读取该私有快照并只写私有目录。转换返回零后，helper 要求目录
闭集精确等于快照和唯一 `-Sheet1.csv`，nofollow 打开并复核 CSV，再流式复制
到目标目录随机 staging。提交前会再次复核输入 inode、canonical 输出目录
身份和私有闭集，最后以 fd-relative `os.replace` 原子替换目标并 `fsync`。
因此失败不会预删旧 CSV，输入/目标/目录换链不会被跟随，额外 Sheet sidecar
也不会进入评分目录；复制、转换、复核和清理由 guest 与 host 两层总 deadline
共同约束。

所有失败只上抛固定 `ARTIFACT_FINALIZE_*` 机器错误码，不带 argv、
guest 路径、窗口标题、stdout 或 stderr。finalizer 不接受、不读取且不持久化
Agent final text。合成门禁位于
`tests/integrations/test_osworld_artifact_finalizer.py`；它覆盖动作闭集、真实本地 ZIP
生成、隐藏项与符号链接拒绝、严格窗口命令及前后等待、非零返回码立即关闭、
Calc 输入/输出/目录换链和额外 sidecar、旧证据原子保留、脱敏错误以及
controller/路径的零 I/O 预检。
