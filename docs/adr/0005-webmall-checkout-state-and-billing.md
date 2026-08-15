# ADR-0005：WebMall 闭集评价合取结账状态与合成 billing profile

- 状态：Accepted
- 日期：2026-08-04
- 部分取代：ADR-0003 的 profile 不评分条款；ADR-0004 的 profile 不评分条款；v1 的其它 logical-store 闭集与 EndToEnd AND 决策继续有效

## 背景

ADR-0003 建立了版本化的公开合成 checkout fixture 与 trusted/agent/audit
投影，ADR-0004 修复了跨店多单、同店多商品与 EndToEnd 报告 AND 语义。
但是 ADR-0004 当时删除了最终 legacy 修复版已覆盖的 billing 字段检查，也未将
“完成结账”与“信用卡支付”写入正式闭集。这会使商品正确但账单资料、
订单状态或支付方式错误的运行被误判为通过。

合成 fixture 本身已由 release 摘要固定，可以在 evaluator 可信内存中作为目标；
隐私边界应由输入投影与持久化 allowlist 保证，不需要通过放弃正确评分实现。

## 决策

profile、结账状态和支付语义引入两个新协议 ID：

```text
paraguibench.webmall.checkout.closed-world.v2
paraguibench.webmall.find-and-order.closed-world.v2
```

两者的订单部分必须同时满足：

1. 每个 logical store 的新订单集合与期望订单闭集精确相等；
2. 每笔订单的 canonical 商品 slug 与数量多集合精确相等；
3. 结账终态为 `completed`；
4. 支付语义为 `credit_card`；
5. 权威订单的 billing 资料与 `webmall.checkout-profile.synthetic-public.v1`
   的姓名、邮箱、街道、门牌号、邮编、城市、州和国家八个逻辑字段相等。

街道和门牌号在 WooCommerce 的同一 `address_line_1` 中以有界子字符串分别
检查；其余字段只允许 Unicode、大小写、布局空白和已明确声明的国家等价形式差异。
EndToEnd 协议在上述订单合取之外，继续与 Agent 最终报告的 logical URL
多集合做严格 AND。

卡号、CVV 和有效期只是 Agent 填表输入，不进入 evaluator observation。
Profile、支付、商品、订单与 URL 原值只在可信内存中比较；RunStore 只允许
固定 reason code、布尔值与汇总计数。不完整扫描、非法 observation 或无法确定
结账/支付/billing 状态属于 evaluator `ERROR`；完整证据上的不匹配属于 Agent
`FAILED`。

## 版本与迁移边界

ADR-0004 的 `.v1` 保留为“不评分 profile、未强制 completed/credit-card”的
历史协议身份，当前 canonical 16 个任务不再引用 v1。v2 的新 ID 使
版本向量、parity case 和 RunStore 可以明确拒绝旧 observation，避免用同一 ID
表达两种评分语义。任何未包含结账终态、支付语义与 billing 闭包的 observation
都不能通过 v2 parity 或 live gate。

## 后果

- 16 个 Checkout/EndToEnd 任务的纯评价合同与最终 legacy 修复语义恢复一致。
- 生产 evidence adapter 必须从权威 WooCommerce 订单同时提供 canonical 商品、
  checkout 终态、支付语义与上述 8 字段 billing observation，不能仅依赖
  Chrome History，也不得采集卡号、CVV 或有效期。
- 生产 WP-CLI reader、跨主机租约、固定 WebMall 环境/reset manifest、CLI 绑定与
  版本化 live gate 仍未完成，所以 16 个任务继续显式标记为 `blocked`。
