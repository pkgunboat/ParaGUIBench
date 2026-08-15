# Security Policy / 安全政策

ParaGUIBench 会启动容器或虚拟机、连接模型服务并保存 GUI Agent 运行记录，因此本文给出凭据隔离和运行日志脱敏的严格推荐实践。普通开源评测的默认合并门槛及其与官方严格审计的边界，以 [REVIEW_POLICY.md](REVIEW_POLICY.md) 为准；真实凭据或私有资产进入 Git、GitHub release 或公开构建产物始终属于阻塞问题。当前开发阶段仅维护最新的 `main` 分支；正式版本发布后，受支持范围将在这里按版本列出。

## Reporting a vulnerability / 报告漏洞

请优先使用仓库 Security 页面中的 **Private vulnerability reporting** 提交漏洞。若该入口尚未启用，请只创建一个不含复现细节、凭据、内部地址或截图的最小公开 issue，用于向维护者索取私密沟通渠道。

报告中可以包含受影响版本、风险、最小复现步骤以及建议修复，但必须先移除 API key、cookie、认证头、个人文件、内部主机和运行日志中的敏感内容。维护者确认问题并准备修复前，请勿公开完整利用方式。

Please use **Private vulnerability reporting** on the repository Security page whenever it is available. If it is not available, open only a minimal public issue asking the maintainers for a private channel; do not disclose exploit details, credentials, private hosts, personal files, or unsanitized logs.

## Credential handling / 凭据处理

- 代码、任务 JSON、配置示例、测试 fixture、截图、日志、Git commit 和命令行参数中禁止出现真实凭据。
- `.env.example` 只声明变量名，真实值必须由仓库外的权限受控文件、部署平台 secret store 或当前进程环境注入。
- 服务器上的凭据文件必须位于 checkout 之外并限制为所有者可读写。部署脚本不得使用 shell tracing，也不得把认证头或带凭据 URL 写到终端。
- 程序只能记录 `Credential Reference` 及其“已配置/未配置”状态，不得记录值、摘要、前后缀或可用于验证凭据的派生信息。
- 一旦凭据可能进入终端、日志、提交或构建产物，应先撤销并轮换，再清理历史；仅删除当前文件不足以解除泄露风险。

## Logging and artifacts / 日志与产物

所有结构化记录都应经过 RunStore 的 allowlist-first 脱敏后再落盘。禁止记录完整环境变量、请求头、cookie、provider client 对象或未经审查的异常对象。截图、视频和下载文件可能包含个人信息或会话状态，必须作为受控 artifact 单独保存，不能默认上传到公共 CI 或 release。

运行一次任务时，应使用合成 sentinel secret 验证 planner、worker、runtime、evaluator、artifact 索引、终端输出和失败堆栈均不包含该值。sentinel 验证通过不代表真实凭据可以进入测试输入。

## Repository gate / 仓库门禁

提交前运行：

```bash
python scripts/security/scan_repository.py --root .
```

扫描器只检查 Git tracked 与非忽略的 untracked 候选文件，不读取进程环境变量，也不在报告中回显命中值。它用于发现高置信度 token、私钥、固定私网地址和开发者绝对路径，不能替代托管平台 secret scanning、依赖漏洞检查、Git 历史审计、许可证审计或人工复核。普通评审中，真实凭据或私有资产命中必须修复；仅涉及私网地址或开发者路径且不影响公开可移植性的规则，按 [REVIEW_POLICY.md](REVIEW_POLICY.md) 归入 legacy strict 检查，后续应从默认 CI 失败条件中拆分。
