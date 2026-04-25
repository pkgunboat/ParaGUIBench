# PEB → PGB Master Table 迁移记录

**执行时间**: 2026-04-23
**执行者**: Claude (via Anthropic Claude Code)
**脚本**: `<PEB_REPO>/ubuntu_env/examples/pipeline_v2/import_to_paraguibench.py`
**日志**: `/tmp/pgb_import3.log`

## 1. 迁移动机

`parallel-efficient-benchmark`（以下简称 PEB）在 2026-04-17 ~ 04-19 期间完成了 5 个消融条件 × 51 任务的 ablation 实验。该数据的污染情况已经过完整审计（详见下文"污染审计"）。
ParaGUIBench（以下简称 PGB）作为重构后的项目继承了 PEB 的实验体系并修复了若干 pipeline 包装层 bug。为支持后续在 PGB 上扩展消融实验、并复用 PEB 已完成的数据，这次把 PEB 的 5 条件 ablation 结果迁移到 PGB 的 master table。

## 2. 迁移范围

| PEB condition | PEB run_timestamp | 任务数 | PGB 目标 condition |
|---|---|---|---|
| baseline | ablation_20260418_112729 | 51 | **baseline_n5** |
| gui_gpt54 | ablation_20260417_124140 | 51 | **gui_gpt54_n5** |
| gui_kimi | ablation_20260418_020026 | 51 | **gui_kimi_n5** |
| plan_kimi | ablation_20260419_012401 (qa+webmall 部分) + ablation_20260419_111620 (operation/searchwrite/webmall/webnavigate 剩余部分) | 51 | **plan_kimi_n5** |
| plan_seed18 | ablation_20260419_224001 | 51 | **plan_seed18_n5** |

**共 5 conditions × 51 tasks = 255 行新记录**。

## 3. 命名策略：为什么加 `_n5` 后缀

PGB 原有 `condition=baseline` 的 `vms_per_task=3`（51 行，来自 ablation_20260420_184625 等）。
PEB 迁入的 5 条件 `vms_per_task=5`，并行度不同。两套基线语义不等价，不能直接共享 `baseline` 名字。

统一给所有 PEB 迁入的 condition 加 `_n5` 后缀：
- 避免与 PGB 现有 `baseline(n=3)` 冲突
- 明确指示并行度维度
- 未来 PGB 若要跑自己的 `plan_kimi(n=3)` 或 `baseline(n=5)` 都不会歧义

## 4. 迁移前后 master.csv 对比

**迁移前** (总 51 行):
```
baseline     run_ts=20260420_184625   rows=47
baseline     run_ts=20260421_203530   rows=3
baseline     run_ts=20260421_212631   rows=1
```

**迁移后** (总 306 行, 净增 255 行):
```
baseline          run_ts=20260420_184625   rows=47  ← PGB 原有
baseline          run_ts=20260421_203530   rows=3   ← PGB 原有
baseline          run_ts=20260421_212631   rows=1   ← PGB 原有
baseline_n5       run_ts=20260418_112729   rows=51  ← PEB 新增
gui_gpt54_n5      run_ts=20260417_124140   rows=51  ← PEB 新增
gui_kimi_n5       run_ts=20260418_020026   rows=51  ← PEB 新增
plan_kimi_n5      run_ts=20260419_012401   rows=17  ← PEB 新增 (qa 部分)
plan_kimi_n5      run_ts=20260419_111620   rows=34  ← PEB 新增 (其他 pipeline)
plan_seed18_n5    run_ts=20260419_224001   rows=51  ← PEB 新增
```

plan_kimi_n5 的 17 + 34 = 51 任务来自两段 run 互补拼接：
- 012401 完成了 qa（17 个），中途中断；webmall 只跑了 2 个
- 111620 补跑了 operation(9) + searchwrite(3) + webmall(20) + webnavigate(2) = 34
- 重复的 2 个 webmall 被 111620 后 import 覆盖，这是 `master_tool.upsert_results` 的标准行为

## 5. 污染审计结论

基于"任务问题排查记录 (2026-04-23)"的 5 类 bug 清单，对 PEB 迁入的 5×51 数据做了逐项审计：

| Bug 类型 | 是否污染 PEB 数据 | 证据 |
|---|---|---|
| HF tree 下载失败 fallback 下 HTML | **否** | 5 个 run 的所有 task.log 里 `[HF tree] API 调用失败` 和 `[direct] 直接下载` 日志均 0 命中；4 条出现 "HTML" 字样的 execution_record 全部是 agent 自主规划"xlsx→html"或访问"amazon.com/help.html"的无关上下文 |
| SearchWrite OnlyOffice 共享链接未注入 | **是**（仅 1 个任务 × 2 条件） | `plan_kimi_n5` / `plan_seed18_n5` 下的 `Operation-FileOperate-SearchAndWrite-004` 的 task.log 里 `:5000/share/onlyoffice` 命中 0；该任务为 OnlyOffice xlsx 类（有 eval_rules，无 evaluator_path） |
| OSWorld SearchWrite config 字段未执行 | **是**（2 个任务 × 2 条件） | `Operation-FileOperate-SearchAndWrite-001` 和 `Operation-WebOperate-SearchAndWrite-001` 的 evaluator JSON 含 `config` 字段（启动 chrome+socat、下载 `Professor_Contact...`、`restaurants.txt`+`MUST_VISIT.xlsx`），PEB 的 `osworld_evaluator.py` 只实现了 postconfig 处理，顶层 config 字段从未执行 |
| WebNavigate 未重新打开 Chrome | 否 | baseline/gui_kimi 下 WebNavigate-001/002 均通过，实际运行层未阻塞 |
| WebMall `shop1/9081` 维护页 | N/A | 用户已确认无需排查 |

**污染记录共 6 条**（已在 master.csv 里 `needs_rerun=true` 标记）：

| condition | pipeline | task_id | 污染原因 tag |
|---|---|---|---|
| plan_kimi_n5 | searchwrite | Operation-FileOperate-SearchAndWrite-001 | osworld-config-not-executed |
| plan_kimi_n5 | searchwrite | Operation-FileOperate-SearchAndWrite-004 | onlyoffice-share-url-not-injected |
| plan_kimi_n5 | searchwrite | Operation-WebOperate-SearchAndWrite-001 | osworld-config-not-executed |
| plan_seed18_n5 | searchwrite | Operation-FileOperate-SearchAndWrite-001 | osworld-config-not-executed |
| plan_seed18_n5 | searchwrite | Operation-FileOperate-SearchAndWrite-004 | onlyoffice-share-url-not-injected |
| plan_seed18_n5 | searchwrite | Operation-WebOperate-SearchAndWrite-001 | osworld-config-not-executed |

这 6 条记录的 `note` 字段包含：
`origin=peb/ablation_<ts>; polluted:<reason>;fixed-in-pgb`

## 6. 未污染数据的可用性

其余 255 − 6 = 249 条记录对应 agent 真实能力，可以直接用于：
- 跨 condition 对比（plan agent / gui agent 消融）
- 并行度对比（需注意 PGB baseline(n=3) vs baseline_n5(n=5) 的代码版本差异，见下）
- 污染排除后的整体 pass rate 分析

**已知的"可比性注意点"**：
PEB 迁入数据是**修复前的代码**跑的，PGB 原有 baseline(n=3) 是**修复后的代码**跑的。对非 SearchWrite 类任务（qa/webmall/webnavigate/operation），两套代码在 agent 执行路径上基本一致（修复集中在 pipeline 包装层，未改动 agent 推理逻辑和 prompt），所以实际对比时受影响很小。但在写论文或做正式对比时，**建议至少在 PGB 上重跑一次 baseline(n=5) 做锚点校准**。

## 7. 需要重跑的实验

**必须重跑（6 个 task-condition 组合）**:
| condition | pipeline | task_id |
|---|---|---|
| plan_kimi (n=5) | searchwrite | Operation-FileOperate-SearchAndWrite-001 |
| plan_kimi (n=5) | searchwrite | Operation-FileOperate-SearchAndWrite-004 |
| plan_kimi (n=5) | searchwrite | Operation-WebOperate-SearchAndWrite-001 |
| plan_seed18 (n=5) | searchwrite | Operation-FileOperate-SearchAndWrite-001 |
| plan_seed18 (n=5) | searchwrite | Operation-FileOperate-SearchAndWrite-004 |
| plan_seed18 (n=5) | searchwrite | Operation-WebOperate-SearchAndWrite-001 |

在 PGB 上重跑时，相关 bug 已在以下文件修复：
- `src/pipelines/searchwrite_pipeline.py` — 共享链接注入、OSWorld/OnlyOffice 分流
- `src/parallel_benchmark/eval/osworld_evaluator.py` — OSWorld config 字段执行
- `src/stages/self_operation_pipeline/run_self_operation_pipeline_parallel.py` — task_uid 透传

**可选重跑（校准锚点，非必须）**:
- `baseline_n5` 在 PGB 上用修复后代码重跑一次，用于校准跨版本影响

## 8. 回滚方案

如需回滚本次迁移：

```bash
# 1. 恢复 master.csv
cd ~/code/ParaGUIBench
mv logs/master_table/master.csv.pre_peb_import.bak logs/master_table/master.csv

# 2. 删除导入的 run 目录
rm -rf logs/ablation_20260417_124140 \
       logs/ablation_20260418_020026 \
       logs/ablation_20260418_112729 \
       logs/ablation_20260419_012401 \
       logs/ablation_20260419_111620 \
       logs/ablation_20260419_224001

# 3. 重建报告
python src/pipelines/master_tool.py rebuild-reports
```

## 9. 数据来源

- PEB 原始 logs（**未被修改**）：
  `<PEB_REPO>/ubuntu_env/logs/ablation_2026041*/`
- PEB master.csv：
  `<PEB_REPO>/ubuntu_env/logs/master_table/master.csv`
- PEB 迁移脚本：
  `<PEB_REPO>/ubuntu_env/examples/pipeline_v2/import_to_paraguibench.py`

## 10. 相关文档

- 本次迁移的审计依据：`docs/task_issue_audit_20260423.md`（PGB 侧）
- 用户偏好配置：`CLAUDE.md`

## 11. 注意事项

- `.gitignore` 把 `logs/` 整个排除，因此本次迁移产生的 `master.csv` 和 `ablation_*/` 目录都**不在 git 跟踪范围**。后续跨机器协作时若需要让其他人看到 master table，需要手动同步或调整 gitignore。
- 备份文件 `master.csv.pre_peb_import.bak` 保留在 `logs/master_table/` 下，建议确认迁移结果稳定后再归档或删除。
