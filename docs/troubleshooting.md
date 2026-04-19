# Troubleshooting

## 依赖问题

### `ModuleNotFoundError: No module named 'formulas' / 'cssselect' / ...`
没装 requirements。`pip install -r requirements.txt`。

### `benchmarkClient` 缺失
Claude Computer Use agent 依赖 `benchmarkClient.cookbooks.gpt.gpt_computer_use`，该模块未随本仓库分发。如不使用 Claude GUI Agent，可忽略；否则请单独获取并安装。

## 配置问题

### `RuntimeError: SSH 密码未设置`
```bash
export BENCH_SSH_PASSWORD='...'
```

### Pipeline 启动时提示「未配置 resources.hf_repo」
在 `configs/deploy.yaml` 里把 `resources.hf_repo` 填成真实的 HF dataset repo，或改用 `--source local` 模式。

### `configs/deploy.yaml` 里的 `${VAR}` 没展开
占位符只支持 `${VAR}` 与 `${VAR:-default}`，空串算未设置。检查对应环境变量是否 export。

## 运行时

### VM 启动失败、端口占用
```bash
docker ps -a | grep osworld-vm
docker rm -f $(docker ps -a -q --filter "name=osworld-vm")
```
然后重新跑。

### SSHFS 挂载失败（密码有特殊字符）
仓库内 SSH 密码是通过 base64 传入的，可以包含反引号、$ 等特殊字符。如果仍失败，检查宿主机是否允许密码登录：
```
# /etc/ssh/sshd_config
PasswordAuthentication yes
```

### OnlyOffice 文档服务器无法访问
检查：
- `docker compose -f docker/docker-compose.yaml ps` 是否 healthy
- 宿主机防火墙是否放行 `ONLYOFFICE_DOC_PORT`（默认 8080）
- `configs/deploy.yaml.services.onlyoffice.host_ip` 是否对 VM 可达

### WebMall 任务 answer URL 仍是内部 IP
开源版任务 JSON 里保留了原始的 `10.1.110.114:908X` URL 作为答案模板。
部署时请先决定你的 WebMall host，然后在评估前运行：

```bash
python scripts/rewrite_task_urls.py \
    --from http://10.1.110.114 --to http://<your-host>
```

（如果你的 WebMall 也暴露在 `127.0.0.1:9081-9084`，无需改。）

## 资源问题

### HuggingFace 下载限流 / 需要登录
```bash
huggingface-cli login
# 或 export HF_TOKEN=hf_xxx
```

### 磁盘不够
VM 镜像解压后约 25 GB，gt_cache ~ 5 GB，总计 40 GB+。把 `resources.root` 指到大盘即可。

## 日志

每次运行产生 `logs/ablation_<timestamp>/<condition>/<pipeline>_results.json` +
`agent_results/<pipeline>/<task_id>/task.log`。

查看 pipeline 主日志：
```bash
tail -f logs/ablation_<timestamp>/baseline/pipeline.log
```

## 获取帮助

- `python src/pipelines/run_ablation.py --help`
- GitHub Issues：<your-repo-url>/issues
