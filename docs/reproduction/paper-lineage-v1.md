# 论文结果血缘台账 v1（totals-first partial ledger）

本台账先核对论文主表总计与少数消融总计能否追溯到具体结果文件、运行选择规则、源码/配置、评价器和环境版本。它不是论文全部数字的完整血缘图，不判断数字是否“看起来合理”，也不把数值相同误写成可复现。尚未覆盖的分类列、效率分表、并行度表、失败分析和 OSWorld 应用行均在机器可读台账中显式登记为 `MISSING` coverage gap。

机器可读事实源为 `paper-lineage-v1.jsonl`。当前状态枚举如下：

- `VERIFIED_VALUE_MATCH`：候选 artifact 重新聚合后与论文数值逐项一致，但仍可能缺源码、评价器或环境 revision。
- `SOURCE_CANDIDATE`：文件与条件相关，但尚未完成逐任务选择链验证。
- `AMBIGUOUS`：存在多个候选或语义重命名，不能仅按文件名、mtime 或 description 选择。
- `MISSING`：尚未找到生成论文数字的完整 artifact/selection 链。
- `CONFLICT`：候选 artifact 与论文数字或其自述配置冲突。

截至 2026-08-04，N=1、N=5、Kimi worker、Qwen GUI-only 和 Holo3 的候选 artifact 达到 `VERIFIED_VALUE_MATCH`。ParaGUI −SCD 仅是数值匹配，实验语义仍为 `AMBIGUOUS`：早期稿曾把同组数字标作 ParaGUI (Ours)，没有源码/配置快照证明当时确实关闭 SCD。ParaGUI 46.4%、Seed-1.8 27.9%、Claude 33.5%、N=3、plan-seed、plan-kimi、claude-gui、Claude image=3，以及 OSWorld 369 任务结果仍为 `MISSING` 或 `AMBIGUOUS`。

`VERIFIED_VALUE_MATCH` 只表示候选文件中的数值与论文一致，不表示当前评价器下的分数仍然有效，更不是 `REPRODUCIBLE`。这些结果来自评价器审计前或 evaluator revision 不明的历史 Run；审计已知至少会改变 41 个 pass/score 和 43 个任务状态。因此所有历史 claim 当前统一标记为 `legacy_evaluator_unverified`、`post_audit_comparability=false` 和 `NOT_ESTABLISHED`。正式发布前必须补齐：

1. 每个论文 row 的逐任务 chosen attempt 与 replacement reason；
2. 每个 run 时间点对应的完整源码快照，而不是仅写最近 Git commit；
3. evaluator、task/gold、prompt 与环境 manifest 的固定摘要；
4. OSWorld 369 任务结果所在私有归档或挂载盘的原始记录；当前“未找到”只限定于机器台账中的 `private_osworld_results_archive_primary`，不能推断其他主机、挂载目录或已清理目录中不存在。

台账不得保存 API key、含凭据的 base URL、`.env`、未脱敏部署配置或原始模型响应。允许保存字段白名单化的脱敏配置、环境变量名、固定 endpoint 的无凭据身份和配置 digest；凭据值只能记为 `<redacted>` 或存在性标记。

公开台账只保留 `source_location_id`、抽象 `source_locator` 和内容 SHA-256；
内部主机、开发者绝对路径及二者到抽象标识的映射不进入公开仓库。
