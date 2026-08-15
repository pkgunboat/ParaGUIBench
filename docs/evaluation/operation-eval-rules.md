# Operation eval-rules 原生评价闭包

本模块把 runtime-support v1 中 32 个 `legacy.operation.eval-rules.v1` 任务迁为
`paraguibench.operation.eval-rules.v1` 的纯 artifact evaluator 核心。任务目录对完整、
有序的 `eval_rules` canonical JSON 固定 SHA-256；因此检查名、参数、权重、文件模式、
排除模式或内嵌 gold 的任何运行时变化都会在读取 artifact 前失败。32 项共包含 41 条
规则实例和 33 个唯一检查函数；最小传递闭包为 16 个 DOCX、11 个 XLSX、2 个 PPTX
和 4 个目录检查。旧注册表中未被这些任务引用的检查没有迁入。

权威输入来自 2026-07-14 修复工作树。以下摘要记录本次机械提取所依据的文件身份；
公开仓库不依赖这些绝对路径运行。

| 旧源相对路径 | SHA-256 |
|---|---|
| `eval/operation_evaluator.py` | `edc36a5cf73bd60f366de9361c3bc481d53c42d78a20b640549e1cc2a8c696af` |
| `eval/operation_checks/docx_checks.py` | `7bb2988f70632f26041a09d72ecee00bfa99b069d274736e67f3df53226dd2a8` |
| `eval/operation_checks/xlsx_checks.py` | `2c815b362649f0d04bd4c6c88208654f5dcecdb94b14d0b3dc0b134ad1c3d9fb` |
| `eval/operation_checks/pptx_checks.py` | `9e32598867fb5340dd72a84f8d393cf083c4be9e3ece2a9e492cd68f2e44e505` |
| `eval/operation_checks/file_checks.py` | `dd77045c48e492083758aee1146602dd1a2df82676eb8ee618a72ad29eb6d70d` |

公开入口 `evaluate_operation_artifacts` 只接收单 Attempt artifact 根目录和 canonical
task object。分派使用固定家族 registry，不使用 `eval`、`exec`、任意 import 路径或
调用方提供的函数名。返回值只含协议/规则 ID、有限分数、固定原因码以及规则/artifact
计数；文件名、路径、检查原始 reason、artifact 内容与 gold 均不会进入公开结果。
第三方 Office 库只在命中相应 check 后延迟导入，并位于独立的 `operation` optional
extra；目录检查中的文件分组与命名检查可在 Core wheel 下运行，HTML/xlsx 内容一致性
检查仍需延迟导入 openpyxl。

在任何 Office parser 运行前，标准库预检会拒绝 artifact tree symlink、特殊文件、
文件/总量超限，以及 OOXML 中的路径逃逸、ZIP symlink、加密 member、member 数量或
大小超限、总解压量超限、高压缩比、VBA 宏和 DTD/entity。检查闭包不导入网络或
subprocess 客户端，不启动 Office，也不访问外部 relationship。旧日志中可能携带路径的
调用已改为固定消息；macOS 大小写不敏感文件系统上同一图片被大小写 glob 重复计数的
问题改为按设备号/inode 去重。

`Operation-FileOperate-BatchOperationWord-004` 已从 2026-07-13 审计快照恢复为五条
逐文件规则，而不是把七个词放入一条 `*.docx` 规则。恢复后的任务文件 SHA-256 为
`b1022fddf18884b324b40a5fc1e6f9ed444dd9dc719dd217b2380c0ec0aea147`，与审计快照
`source_files.sha256` 的捕获值一致。该检查使用闭集约束：五份文件分别覆盖自身期望词，
且黄色高亮词集合中出现任一非预期词即整条规则零分，避免“全文全黄”假通过。

代码闭包与 canonical 参数现可为 32/32 项产生分数，且已接入正式
runtime 纵向链。Agent 结束后，OSWorld controller 以一次固定
`python -I -c` helper 递归枚举 guest shared 树；helper 逐级使用
`openat/O_NOFOLLOW`，只接受普通文件，对文件数、深度、名称、单文件、
总字节、响应与真实时间设置硬上限，并返回完整的大小/SHA-256 manifest。
host 随后通过现有 nofollow 单文件 getter 逐项读取，复核大小和摘要，并
写入当前 Attempt 独占的临时树。不完整、重复、越界、排序漂移、文件/目录
冲突，以及 macOS 大小写或 Unicode 归一化折叠均在任何评分前失败关闭。

`OperationTaskEvaluator` 不读取 Agent final text，只从存活 environment 取同一
task/protocol 的冻结快照。快照声明的文件数必须与 pure evaluator 实际预检
计数一致；冻结后增删文件直接记为 evaluator ERROR/null。RunStore 只保存
protocol/rule ID、固定 reason code 和规则/artifact 整数计数，不保存临时 host
路径、guest 路径、文件名、内容、gold 或 final text。environment `close` 负责幂等
删除 owned 临时树，单 VM 租约层只透传同一快照。

Word-009/010 的本地 production core 现以 prepare 前 typed baseline 闭合
pre→post 正文保真。`verify_asset_directory` 先确认 host cache 文件闭集；
environment 随后在首次 desktop/upload/guest I/O 之前，以 held-fd
nofollow 重读 manifest 和所有 input，并将任务 ID、协议、正式 manifest
SHA、文件路径/大小/SHA 及 4/5 份 DOCX 分母绑定为不含原文的
`WordTextBaseline`。single-VM 租约只透传该 DTO；runtime adapter 在触发
post artifact capture 前再校验 task/protocol/manifest/路径/数量/内部形状，
任一伪造均固定为 ERROR/null。

typed 投影覆盖 body、header/footer、footnote/endnote/comment、textbox、
hyperlink、field、revision、Math、DrawingML chart/SmartArt 文字与控制节点，
同时固定容器路径、可见性样式、引用列表语义、internal relationship、
root officeDocument 边、可达文字 part ContentType 及 canonical 根 QName。
Word-009 只在文字语义投影中忽略行距属性；Word-010 只允许新增纯
drawing 段落、image relationship 和 media，已有文字、容器、图片关系与媒体仍需
保持。可比较的文字/语义漂移是固定 `TEXT_FIDELITY_MISMATCH` FAIL/0；
baseline 缺失、身份伪造、未知文字载体、外部/逃逸关系、格式或资源不可靠时
为 ERROR/null。Agent final text 不进入评分或持久化。

依赖链为：`canonical task + formal input contract` → `host manifest/cache
nofollow rebind` → `WordTextBaseline` → `OSWorld environment` → `single-VM
lease` → `OperationTaskEvaluator baseline-first validation` → `owned post snapshot`
→ `evaluate_operation_artifacts` → `RunStore fixed outcome`。这是本地 core/runtime
闭包，不是实机晋升证据；现有
`operation_word009_010_writer_live_validation_not_completed` 仍保留，直到固定版本
真实 Writer/LibreOffice 输出在受控 pinned OSWorld/LibreOffice 远程环境中
通过私有 pre/post、对抗与 versioned-live 复验。

Word-012 的本地 production core/runtime 已以固定四 DOCX 的
evaluator-only 逐处语境合同闭合。environment 在首次 guest I/O
前同时绑定任务、协议、正式 manifest SHA、路径/size/SHA 及固定
分母，为每份源 DOCX 生成不含原文或语义映射的期望 typed
快照。快照的 digest/count/relationship/media 复合身份由进程内
HMAC 封存，runtime adapter 先校验 DTO 形状与认证码，再捕获
post artifact；
single-VM 层只透传同一 DTO，Agent final text 始终删除且不参与评分。

专属比较器只接受任务确认的 canonical 括号形式，保留已正确
的展开，修正已知错误展开，并不要求修改非目标缩写。任意/空
括号、全文统一释义、漏展开、删除缩写、改写/换位无关正文、
四文档缺失/额外，以及 hidden/revision/textbox、`w:bdo`/
`w:dir`/`w:rtl` 方向语义或边界空格 `xml:space` 绕过都以
固定 `ABBREVIATION_SEMANTICS_MISMATCH` FAIL/0 失败。baseline 伪造、
OOXML 不可靠或资源/路径安全异常为 ERROR/null；RunStore 只保留固定
原因与计数或异常类型，不落盘文件名、正文、释义、摘要或
baseline repr。该 production evaluator/runtime 闭包已移除历史本地语义
blocker，但不是 live 晋升证据；任务仍需受控真实 Writer/LibreOffice 的
versioned-live Attempt。统一镜像物化 blocker 已由独立审核的正式物化证据
清除，但本地回放仍不得影响任何 live 或 receipt 门禁。

runtime-support 已将这 32 项精确提升为
`paraguibench.operation.eval-rules.v1`；未来未命中固定 task/rule 摘要的
`eval_rules` 任务仍停留在 legacy fallback。当前 32/32 的输入都已绑定固定
download-only manifest，但全部继续是 `blocked`，并保留
`versioned_live_validation_not_completed` 等未完成门禁。Word-004/-005/-006/-007
的 16 份原始 DOCX 已从公开固定 revision 匿名下载，并由目录闭集、大小、
SHA-256、MIME 与 OOXML/ZIP 完整性共同确认，不使用 Agent final text 作为证据。
Word-008/-009/-010/-011 新增的 17 份 DOCX 与 5 张 JPEG 也通过同一固定
revision 的匿名 production fetch/verify；DOCX 通过 OOXML/ZIP、主部件、无 VBA
及安全路径闭集，JPEG 通过严格完整解码，Word-010 保留 `images/` 相对目录。
CombinationDocs-001 与 SearchAndWrite-002/-006 的 6 份 XLSX 及 1 份 DOCX
也已按同一固定 revision 完成路径、大小、摘要与 MIME 闭集，不改动
任何 `eval_rules`。CombinationDocs-004/-005/-006/-007 的 2+5+3+6 份输入
也已绑定同一 revision；其中 005 的 TXT/Markdown/CSV/HTML 由路径后缀
显式固定 MIME，不依赖 libmagic 对 Markdown 或 OOXML ZIP 容器的环境推断。
004 属于 `answer.exact` 协议资产任务；005/-006/-007 才是上述 32 个
`eval-rules` 任务中本批新增固定输入的 3 项，不因资产就绪而改变协议。
Word-003 只固定 3 份原始 DOCX，同目录 3 份 `*_answer.docx`
不进入 manifest/cache/guest；Word-012 固定 4 份 DOCX，不引入历史
`answer_files` 作为 gold，而是由 host-only 逐处语境合同在 pre 阶段生成
typed 期望快照。SearchAndWrite-004 只固定
Lee `benchmark_dataset` 中的输入 XLSX，不引入独立 `answer_files`；
SearchAndWrite-007 只固定 xlang revision `711e0811642364e7aa8f10a8918367d0b626d578`
的 `Conference.xlsx`，同目录 `ConferenceCity Gold.xlsx` 不进入任务输入。
这 9 份 OOXML 已通过 production fetch/verify、MIME、ZIP/CRC、主部件、
无 VBA 与安全成员路径检查；guest shared 在打开前还会枚举全部节点类型，
只允许声明普通文件及其必要普通祖先目录，任何符号链接或特殊节点均失败关闭；
资产就绪不会清除 image/versioned-live 门禁。
CombinationDocs-003 只固定原始 3 份 XLSX 和 1 份 PPTX，不使用或向
guest 暴露 gold。其唯一规则把 `Monthly Data!A1:F16` 与第 3 页插入内容
做源文件相对比较：native table 逐格校验，picture 走 openpyxl + python-pptx +
Pillow 的确定性投影。四文件闭集、三份 XLSX 原摘要和 PPT must-change 在评分前
失败关闭；图片通道仍保留
`combinationdocs003_real_render_validation_not_completed`，直到真实 LibreOffice
输出完成可见性、裁剪、样式与跨版本复验。

CombinationDocs-008 保留上游 3 份 DOCX、`Project_Information.xlsx` 与
`Naming_rules.txt` 的原字节，但 canonical 任务级规范明确屏蔽 TXT 中过时的
PPT/excel_data/双 `v` 示例。评价器以固定分母 3 校验 `output/` 中的
DOCX-only 单 `rev` 命名，同时绑定每份源文档与两份元数据的大小/SHA。
runtime-support 只有在 nofollow 有界读取成功、
`asset_set_id` 与 task 一致，且 Operation 固定输入的 canonical 路径和
manifest SHA 命中专属闭集时才清除
asset blocker；manifest 缺失、损坏、任务间换位、来源/文件字节漂移，
或与 `prepare_script_path` 重新共存都会使生成失败。
本地合成 Office fixture 与 runtime tracer 通过都不等于 114 `live_validated`。

本地验证命令如下：

```bash
python -m pip install -e '.[operation]'
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/evaluation/test_operation_evaluator.py \
  tests/evaluation/test_operation_check_regressions.py \
  tests/integrations/test_osworld_operation_artifacts.py \
  tests/runtime/test_operation_task_evaluator.py \
  tests/runtime/test_operation_attempt_privacy.py
```

测试覆盖 32-task/33-check 机器闭集、32 个可评分任务的真实 Office parser smoke、
Word-004 的 gold=1、noop=0、全文全黄=0、跨文件词错配=0，及脱敏返回、规则篡改、
symlink/ZIP bomb/宏/DTD 等资源边界，受控 guest manifest、大小/摘要漂移、
host 路径折叠、快照增删、final-text 隔离和 RunStore 脱敏，以及旧审计中 HTML
伪导出、PDF 占位、Word 结构、图片身份、颜色、倍率与排序副本的关键回归。
