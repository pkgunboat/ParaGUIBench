# WebMall distributed-lease v1 部署

该服务仅依赖 Python 标准库，使用 SQLite `BEGIN IMMEDIATE` 为共享四店后端提供跨进程 namespace 单占与持久化 fencing token。代码默认仅监听 `127.0.0.1:8765`；跨主机生产流量必须由 HTTPS 反向代理终止 TLS，不应将 Python HTTP 端口暴露到网络。

先在独立虚拟环境中安装当前仓库，再将 [`paraguibench-webmall-lease.service.example`](paraguibench-webmall-lease.service.example) 按实际安装路径复制到 systemd。服务凭据只能存在 root 可读的 `/etc/paraguibench/webmall-lease.env`：

```text
PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN=<32-to-4089-char-base64url-secret>
```

环境文件权限应为 `0600`，不要将其内容写入 shell history、RunStore、日志或仓库。为生产主机配置 [`nginx-webmall-lease.conf.example`](nginx-webmall-lease.conf.example) 和可信 TLS 证书，客户端 coordinator URL 使用 `https://` 地址。

systemd 样例同时限制服务线程、内存和文件描述符；这些限制与协调器自身
的连接超时和有界并发共同防止本机慢连接无限占用线程。生产部署不应删除
`TasksMax`、`MemoryMax` 或 `LimitNOFILE`，调整前应结合并发 Attempt 上限做压测。

服务启动命令为：

```bash
python -m paraguibench.integrations.webmall.lease_coordinator \
  --database /var/lib/paraguibench-webmall-lease/leases.sqlite3 \
  --host 127.0.0.1 \
  --port 8765
```

SQLite 文件是 fencing 高水位的权威持久状态，不得在服务重启时删除或以空文件替换。备份与恢复必须保留其最新 fencing token；不可同时启动两个指向不同 SQLite 文件的权威实例。`assert-held` 会在 `BEGIN IMMEDIATE` 写锁内重新读取时钟，核对完整 Attempt/owner/lease/fencing 身份，并按原 grant TTL 原子续期。运行器在 baseline、每次可调用 controller 操作与 final evidence 前都会重新确认所有权；任何不确定的网络失败在一次幂等重试后仍会 fail-closed。
