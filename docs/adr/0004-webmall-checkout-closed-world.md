# ADR-0004：WebMall checkout 使用按 logical store 分组的闭集订单协议

- 状态：Accepted（部分 profile/结账状态条款后续由 ADR-0005 取代）
- 日期：2026-08-04

## 背景

历史 checkout evaluator 把任务 gold 压缩为第一个商品，并同时强制全局只能
出现一笔订单、订单中只能有一件商品。这使两个正式任务的正确行为无法表达：

- `Operation-OnlineShopping-EndToEnd-001` 的两个商品来自两个 logical store，
  正确结果是每个 store 各一笔订单；
- `Operation-OnlineShopping-EndToEnd-005` 的两个商品来自同一 logical store，
  正确结果是一笔包含两件商品的订单。

历史实现还只从每个 VM 的 Chrome History 读取最近一个 `order-received` 页面。
这种采样不能证明没有额外订单，也不能覆盖单 VM 顺序完成的跨店两单。

## 决策

新协议按任务语义拆成两个固定 ID：

```text
paraguibench.webmall.checkout.closed-world.v1
paraguibench.webmall.find-and-order.closed-world.v1
```

Evaluator 只接收 logical store 身份和 Attempt 基线之后的完整新增订单证据，不
认识部署 host、端口、VM ID 或 runtime URL。canonical `expected_urls` 按 store
分组，每个 store 恰好对应一笔期望订单；同店全部期望商品必须位于同一订单，
商品和数量按精确多集合比较。商品正式身份是 evidence adapter 通过可信
WooCommerce product ID 解析出的 canonical slug，不使用可变 display label。
缺单、额外订单、额外商品、缺商品或错误数量均为二值失败，正式得分只有
`1.0` 和 `0.0`。

同一 `(logical_store_id, order_identity)` 的完全相同 sighting 可以去重；同一
身份的冲突证据、非法 gold、非法数量或不完整扫描属于 evaluator error，不能
编码成 Agent 零分。评价结果只包含协议 ID、reason code 和计数，不包含商品名、
订单 ID、URL、history URL 或 checkout profile 值。

本协议遵循 ADR-0003：checkout profile 只用于 Agent 填表，Evaluator 不读取
姓名、地址、邮箱、卡号、CVV 或有效期。历史 evaluator 曾检查八个 billing 字段，
但该行为与当前已接受协议和新的合成 persona 不兼容，不能无版本地迁入。如需恢复
billing 评价，必须另建协议版本并重新采集证据。

> 后续决策：[ADR-0005](0005-webmall-checkout-state-and-billing.md) 已完成上述升级，
> 并取代本节的 profile 不评分条款；本 ADR 的按 logical store 分组、订单闭集和
> EndToEnd 报告 AND 语义仍有效。

8 个 Checkout 任务只评价订单闭集。8 个 EndToEnd（历史 FindAndOrder）任务使用
组合协议：先从 Agent 最终报告严格提取 logical product URL 多集合，再与订单闭集
做逻辑 AND；缺失、错误或重复 URL 均为 Agent 失败。这保留了最终 legacy 修复中的
``string AND checkout`` 语义，同时不把部署 origin 暴露给 evaluator core。

## Evidence adapter 门禁

WebMall environment adapter 必须在 Attempt 开始前建立环境 manifest 声明的
完整 logical store universe（当前为四店）订单基线，在 Agent 完成后再次完整
枚举同一 universe 的新增订单，并将 runtime origin 通过 `WebMallURLRegistry`
转成 logical store。不能只扫描 gold 出现的 store，否则会漏掉其它商店中的额外
副作用。只有所有扫描和全局租约 ownership 检查均成功时才能产生
`CheckoutObservationBatch(complete=True)`。

纯 evaluator 完成并不使任务 `live_validated`。16 个任务继续保持 `blocked`，直至
以下条件全部通过：

1. 完整订单 evidence adapter、EndToEnd 最终报告解析和 WebMall
   environment/reset 生命周期接线；
2. fresh-state 基线、额外副作用检测和隐私测试；
3. 权威 case manifest 上的旧行为差异审计；
4. 至少一轮真实环境 live gate，随后再覆盖 16 个任务。

## 后果

- EndToEnd-001 与 EndToEnd-005 的订单部分由同一规则自然表达，不再写
  task-specific 分支；8 个 EndToEnd 统一增加 logical URL 报告闭集。
- evaluator core 只依赖 Python 标准库，不执行网络、浏览器导航或 VM 命令。
- 历史 `legacy.webmall.checkout.v1` 结果不能直接作为新协议 parity 通过证据；两处
  已确认 bug 的行为变化必须记录为有意修复。
- 环境证据闭包尚未完成期间，runtime-support 使用显式 blocker code，网站不能
  将纯单元测试误报为真实可运行。
