# CombinationDocs-003 源表格相对评价协议

`Operation-FileOperate-CombinationDocs-003` 不使用 answer PPT 或静态
ground-truth 文件。正式输入只来自
`leeLegendary/Parallel_benchmark@13bf942dfab6f9d71f16f0958f1edd8b436c7afa`
的固定目录 `benchmark_dataset/2654f880-dd6b-4f8c-9f88-aebe2bfa51be`，
download-only manifest 精确声明三份 XLSX 和一份 PPTX。评价后的
artifact 树必须仍是这四个根级路径：

```text
McDonalds_Monthly_Data.xlsx       原字节保真，评价事实源
McDonalds_powerpoint_report.pptx  必须改动，唯一输出文档
store1.xlsx                       原字节保真，无对应幻灯片的干扰项
store2.xlsx                       原字节保真，无对应幻灯片的干扰项
```

任务要求把 `McDonalds_Monthly_Data.xlsx` 的
`Monthly Data!A1:F16` 插入 `McDonalds_powerpoint_report.pptx` 第 3 页。
允许两种严格通道：可编辑 native PowerPoint table 必须在 16×6
矩阵中逐格匹配源值，仅 `A1:F1` 合并，并保留标题、蓝色白字
表头、普通数据格和灰色合计行的对齐、字号、字色与粗体语义；
table 的列宽、行高必须按固定源投影比例分配，内部 grid 总尺寸必须
与 graphicFrame extent 一致，非空格边距不得裁掉可见文本；
picture 必须与 evaluator 从同一固定 XLSX 即时生成的 canonical PNG
原始字节完全相同。图片通道只接受不透明 RGB PNG、官方 internal
image relationship、`image/png` ContentType 和无 effect/ext/crop/flip/rotation
的 QName 允许列表；shape 保持 2:1 自然纵横比，完全位于第 3 页
原内容区，水平与垂直尺寸均不小于该区域的 70%。

五页顺序、页尺寸、原 placeholder 类型/索引/边界、关键文本及其他页
的 shape 闭集必须保持不变；第 3 页插入形状必须是 z-order 最后的
唯一 table 或 picture。任一页隐藏、页外/微缩/拉伸内容、额外遮挡形状、
单数字或单字符漂移、空白/随机/蜜雪冰城图片，以及三份 XLSX
任一字节改动都以固定原因失败。Operation runtime 只读冻结 artifact
快照，不读 Agent final text；RunStore 不保存文件名、路径、单元格值、
图像字节或摘要。

依赖闭包如下：

```text
canonical task JSON
├─ pinned input manifest (4 files; no gold_manifest)
├─ Operation catalog digest
└─ check_combinationdocs003_source_table_insert
   ├─ openpyxl -> Monthly Data!A1:F16 values/visibility/merge contract
   ├─ python-pptx -> 5-slide placeholder/text/shape/native-table contract
   ├─ Pillow -> deterministic RGB projection + canonical PNG bytes
   └─ stdlib/hashlib + Operation preflight -> path/SHA/OOXML/resource limits
```

当前必须保留
`combinationdocs003_real_render_validation_not_completed` blocker。Pillow 内置字体与
PNG encoder 在允许版本范围内仍可能改变 canonical 字节，而 LibreOffice
“复制单元格→粘贴为图片”可能生成不同 PNG 编码、替换而非保留
`OBJECT` placeholder，或使用不同的无效果 `p:pic` 结构。只有在
受控的 pinned OSWorld/LibreOffice 远程环境中完成真实 GUI 正例，
确认 append-placeholder 与 replace-placeholder 终态，并重放空白、错表、单字符、
错页、遮挡、效果、透明和关系类型负例后，才能移除该 blocker。
移除时还必须将 Pillow 精确版本、默认字体字节摘要及 canonical PNG
字节 SHA 纳入 run version vector 与实机 receipt，不得只依赖宽版本范围。
本地合成通过、native table 通道通过或 evaluator core 可达均不等于
`live_validated`。
