# CombinationDocs-008 DOCX-only 规范修复

`Operation-FileOperate-CombinationDocs-008` 的上游任务文本要求重命名
Word 与 PPT，但固定 revision
`13bf942dfab6f9d71f16f0958f1edd8b436c7afa` 的实际输入只有三份
DOCX、`Project_Information.xlsx` 和 `Naming_rules.txt`，不含 PPTX。
`Naming_rules.txt` 还包含三项已知冲突：它引用不存在的
`excel_data.xlsx`，允许 `.docx or .pptx`，并将已带前导 `v`
的 Version 代入 `rev{Version}`，会产生 `revv1.0`。仓库不改写上游
缓存，也不把 2026-07-02 后期合成的 PPTX 冒充原始输入。

用户确认的任务级规范作为上述冲突的显式 erratum：以实际
`Project_Information.xlsx` 为项目数据源，只处理三份 DOCX；在拼接
`rev` 前删除 Version 的唯一前导 `v`；保持文档字节不变，
并将结果移入 artifact 根目录下的 `output/`。正式输出闭集为：

```text
Naming_rules.txt
Project_Information.xlsx
output/p-2026-001_multi_modal_agent_rev1.0.docx
output/p-2026-002_gui_benchmark_study_rev2.1.docx
output/p-2026-003_parallel_execution_rev3.5.docx
```

根级 XLSX/TXT 必须与 input manifest 中的 size/SHA-256 完全相同；
每份新名 DOCX 必须与自己对应的旧名源文档字节完全相同。
因此该任务不需要独立 ground-truth 文档：evaluator 直接对固定输入
身份做 source-relative 验证，不读取 Agent final text，也不生成或修复
PPTX。

## Fail-closed 评价合同

任务仍使用固定 `check_named_files_exist` 公开检查身份，但其
canonical `rename_contract` 启用专属闭集路径。下列任一条件成立
即整条规则零分或在上层安全预检阶段 ERROR：

- 三份目标 DOCX 任一缺失，或出现任何额外普通文件（包括位于
  额外目录中的文件）；
- 保留旧 DOCX 名称、使用 `revv` 名称、生成 PPTX，或将新名文档
  放在 `output/` 以外；
- 两份文档内容调包、任何文档内容改写，或 XLSX/TXT 元数据改写；
- DOCX 不是有效 ZIP/OPC 包，或缺失正确 Content Type、package
  relationship、WordprocessingML `document/body`；
- 规则参数、输入 manifest 身份或 canonical rule-set SHA-256 漂移。

公开结果的文档评价分母固定为 3，不会因删除输出而缩小；
内部 reason 仅使用固定值，不返回 artifact 路径、文件名、摘要或
文档内容。当前 Operation guest manifest 只枚举普通文件；空目录不是
runtime 证据，因此本合同不宣称可端到端检测额外空目录。

## 依赖关系与验证

```text
canonical task JSON
├─ asset_manifest -> 5-file pinned input identities
├─ instruction -> DOCX-only / single rev / output folder erratum
└─ eval_rules.rename_contract
   ├─ 3 source DOCX identities -> 3 output DOCX paths
   ├─ XLSX/TXT preserved identities
   └─ check_named_files_exist
      ├─ Operation artifact-tree security preflight
      ├─ exact regular-file path closed set
      ├─ nofollow size/SHA-256 verification
      └─ stdlib DOCX OPC/OOXML validation
```

专属本地门禁：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-dev/bin/python -m pytest -q -p no:cacheprovider \
  tests/evaluation/test_operation_combinationdocs008_docx_only.py
```
