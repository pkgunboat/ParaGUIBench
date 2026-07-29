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
- `Benchmark Task` 是最终评价的存储边界。
- planner subtask 和全部 worker 记录属于某个 Attempt。
- retry 创建新 Attempt；实验条件变化创建新 Run。
- 每个 producer 独占事件流，禁止多进程并发追加同一文件。
- canonical JSON/JSONL 是事实源；SQLite 索引必须可重建。
- 快照和终态使用同目录临时文件、同步与原子替换。
- Execution Outcome 与 Evaluation Outcome 分开持久化。
- Artifact 先完成写入和 SHA-256 校验，再提交 manifest。

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
