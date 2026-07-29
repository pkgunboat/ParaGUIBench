# ADR-0003：WebMall checkout 的版本化合成 fixture

- 状态：Accepted
- 日期：2026-07-29

## 背景

首批导入的 8 个 Checkout 与 8 个 EndToEnd 任务在 `instruction`、
`user_details` 和 `payment_info` 中重复保存同一份结账资料。即使这些值用于
测试而非真实交易，重复内嵌仍会产生三个问题：任务定义与测试身份耦合；完整
canonical task 容易被运行日志直接序列化；后续替换测试资料时需要同时改写
16 个任务且难以确认版本一致。

checkout Agent 必须在执行时看到表单所需信息，因此不能把这些值简单标为
secret 后完全删除。需要把“Agent 执行可见”与“canonical task、审计日志默认
可见”分开建模。

## 决策

release-v1 采用一个由项目维护的公开合成 fixture：

```text
benchmark/fixtures/webmall/checkout-profile-v1.json
```

fixture 固定声明：

- `fixture_id = webmall.checkout-profile.synthetic-public.v1`
- `data_classification = synthetic_public_test_data`
- `task_storage_policy = reference_only`
- `intended_use = benchmark_testing_only`

资料使用显式测试身份、`.invalid` 邮箱和测试支付号，不得替换为真实个人资料、
生产支付工具、API key 或部署凭据。`release-v1.json` 记录 fixture 路径与
SHA-256；任何内容变化都形成新的 fixture 版本，不能原地改变 v1 的语义。

16 个 canonical task 删除 `instruction`、`user_details` 与 `payment_info`，
改为：

```json
{
  "instruction_template": "... {{checkout_profile}} ...",
  "fixture_ref": {
    "binding": "checkout_profile",
    "fixture_id": "webmall.checkout-profile.synthetic-public.v1"
  }
}
```

`reference_only` 表示 task JSON 只保存引用，并不表示 Agent 无法使用该资料。
runtime 集成必须建立三个明确投影：

1. `trusted` 投影持有已通过 release 哈希校验的 canonical task 与 fixture；
2. `agent` 投影把 `{{checkout_profile}}` 确定性渲染为完成表单所需的 instruction；
3. `audit` 投影只保存 task/fixture 身份、schema version、数据分类和哈希，不
   保存地址、邮箱、卡号、CVV 或渲染后的完整 instruction。

v1 的渲染顺序固定为姓名、邮箱、街道、门牌号、邮编、城市、州、国家、卡号、
CVV、有效期。runtime 遇到未固定摘要的 fixture、未知 `fixture_id`、重复或缺失
模板 token 时必须 fail closed。Evaluator 继续只依据已购买商品的 logical URL
评价，不读取 checkout profile。

两个公开 schema 分别描述 fixture 和 task overlay：

```text
benchmark/schemas/webmall-checkout-fixture-v1.schema.json
benchmark/schemas/webmall-checkout-task-v1.schema.json
```

统一 release validator 额外检查 fixture 路径/摘要/身份、合成数据分类、
`reference_only` 策略、profile 必需结构、16 个任务的模板引用以及禁止内嵌
字段。迁移脚本可重复执行，并在 `--check` 模式下作为 CI 幂等性门禁。

## 后果

- canonical task 不再复制 checkout profile，修改边界与版本身份清晰。
- Agent 仍可在真实 WebMall 表单中完成结账，评价协议不变。
- RunStore 可以只接受 `audit` 投影，避免因序列化完整 task 而复制支付字段。
- fixture 是公开测试数据而非 secret，但日志仍默认不记录其具体值，降低后续
  数据替换或误配置时的风险。
- runtime 在支持这 16 个任务前必须实现 fixture resolution、模板渲染与三个
  投影；直接把 `instruction_template` 作为 Agent instruction 属于协议错误。
