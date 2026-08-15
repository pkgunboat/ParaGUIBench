# ADR-0002：以 Benchmark Task 为单位的 RunStore

- 状态：Accepted
- 日期：2026-07-28

## 背景

旧日志已经部分按任务组织，但 condition 位于任务目录上方，秒级时间戳可能冲突，多进程可能共享 handler，结构化文件直接覆盖，planner、worker、环境和 evaluator 各自写入不同目录且没有统一脱敏 seam。

## 决策

RunStore 使用以下稳定层级：

```text
runs/<run_id>/tasks/<safe_task_id>/attempts/<attempt_id>/
```

- `Run` 固定代码、release manifest、Agent System、配置和环境版本。
- 新 Run 的 `run.json` 必须在顶层保存完整 `RunVersionVector`：source、Agent code、evaluator、evaluation protocol、environment protocol 与 environment manifest revision。协议必须以正整数 `.vN` 结尾；`HEAD`、`latest`、`unknown`、`.v0` 和短摘要均非法。
- 新 Attempt 只能追加到身份一致、版本向量完整的 schema 2.0 Run；legacy Run 只读。Run、Attempt 与 summary 的 schema 和三层 ID 必须在 inspect 时交叉验证，缺文件不能自动视为 legacy。
- `Benchmark Task` 是最终评价的存储边界。
- planner subtask 和全部 worker 记录属于某个 Attempt。
- retry 创建新 Attempt；实验条件变化创建新 Run。
- 每个 producer 独占事件流，禁止多进程并发追加同一文件。
- canonical JSON/JSONL 是事实源；SQLite 索引必须可重建。
- 快照和终态使用同目录临时文件、同步与原子替换。
- Execution Outcome 与 Evaluation Outcome 分开持久化。
- Artifact 先完成写入和 SHA-256 校验，再提交 manifest。
- 安全检查使用 allowlist-only `AttemptInspection`；不得直接把自由格式 summary details 或 event data 暴露为诊断协议。失败阶段是 runtime 保留顶层字段，evaluator details 中的同名键没有控制权。
- `PASSED`/`FAILED` 必须携带有限 score，其他评价状态必须为 `None`；写入端与不可信读取端共用同一交叉约束。
- 只读 inspect 不创建 runs-root，也不收紧用户误传目录的权限。

## 隐私决策

- 所有结构化数据在序列化前统一递归脱敏。
- 不序列化完整 `os.environ`、HTTP headers、cookie、provider client、CLI secrets 或异常对象。
- API key 只记录 Credential Reference 和 `present/absent`。
- prompt、response、截图和下载内容默认采用 `sanitized` 或 `on_failure` 捕获策略。
- 日志目录默认权限为 `0700`，文件默认权限为 `0600`。
- 日志导出是显式操作，导出前必须再次执行 secret scan。

## 后果

- 同一任务可以安全地重复运行和并发重试。
- planner、worker、environment 和 evaluator 的证据可按 Attempt 聚合。
- 统计报表由 canonical 记录派生，不再形成第二事实源。
- 旧日志只通过只读 importer 转换，不长期维护双目录。
- 缺少版本向量的旧 Run 只标记 `LEGACY_UNVERSIONED`，不允许根据当前代码倒填历史 revision。
