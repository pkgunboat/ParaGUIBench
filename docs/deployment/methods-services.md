# methods_runner 第三方部署指南（服务栈与资源获取）

本页说明在自有 Linux 机器上复现 `python -m paraguibench.methods_runner`
两类方法（GUI-Only / ParaGUI）所需的外部服务、虚拟机镜像与任务素材的
获取方式。方法本身的环境变量矩阵（模型凭据、agent 选择等）见
`docs/methods-provenance.md`；本页只覆盖"代码之外"的部分。

## 总览：三类外部依赖

| 依赖 | 获取方式 | 说明 |
|---|---|---|
| 任务素材（每任务输入文件） | **运行时自动**从 HuggingFace 下载 | runner 内置 `task_data_cache`，按任务 JSON 的 `prepare_script_path` 拉取到 `src/parallel_benchmark/hf_data/`（gitignored），无需手工干预 |
| VM 容器镜像 | **运行时自动**构建 | 首次运行时在公开基础镜像 `happysixd/osworld-docker`（Docker Hub）上本地构建 sshfs/proxy 变体 `happysixd/osworld-docker-sshfs`（该变体不在 Docker Hub 公开列表，构建函数内置于 runner） |
| qcow2 guest 镜像 | **需自行准备** | 见下文"VM 磁盘镜像"，这是目前唯一无法一条命令获取的依赖 |
| WebMall 四店后端 | 本仓库 `deploy/methods-services/` | 仅 `webmall` 类别需要 |
| OnlyOffice + 共享服务 | 本仓库 `deploy/methods-services/` | 仅 `searchwrite` 类别需要 |

## 与 deploy/onlyoffice 的关系

`deploy/onlyoffice/` 是公开 CLI（`paraguibench run`）路径的单实例 OnlyOffice
编排，share server 是开源重写版（`src/paraguibench/integrations/onlyoffice/share_server.py`）。
`deploy/methods-services/` 是原方法验证时使用的服务栈（容器名 `bench-*`，
share server 为原版 `document_sharing_server.py`）。跑 methods_runner 时
应使用后者；两套栈端口默认相同（8080/5050），不要同时启动。

## 服务栈部署（webmall / searchwrite 类别）

前置：Linux x86-64、Docker、Python venv 支持（Ubuntu/Debian 需先
`sudo apt install python3.12-venv`，否则 `python3.12 -m venv` 直接失败；
无 sudo 权限时可改用自带 ensurepip 的 Python 发行版，如 conda base）、
`vm.max_map_count >= 262144`
（Elasticsearch 8.x 要求：`sudo sysctl -w vm.max_map_count=262144`）。

```bash
# 1) 首次：恢复四店数据（自动从 Uni Mannheim 公网下载约 3.5 GB 备份）
bash scripts/deployment/setup_webmall_services.sh          # 自动探测本机 IP
bash scripts/deployment/setup_webmall_services.sh <IP>     # 或显式指定

# 2) 日常启停（数据保留在 Docker 卷）
bash scripts/deployment/start_bench_services.sh
bash scripts/deployment/stop_bench_services.sh

# 3) 验证
curl http://127.0.0.1:9081/            # Shop 1（9081-9084 四店）
curl http://127.0.0.1:5050/healthz     # OnlyOffice share Flask
curl http://127.0.0.1:8080/            # OnlyOffice DocumentServer
```

端口与 host 的权威配置是 `configs/deploy.yaml`（可自行创建，字段与
默认值见 `src/config_loader.py`；缺省时 `config_loader` 用默认值
9081-9084 / 8080 / 5050；也可用 `BENCH_DEPLOY_CONFIG` 指向自定义路径）。

### 换机部署后的任务 URL 改写

91 个 OnlineShopping 任务 JSON 的 `answer` 字段指向打包者原始环境的
部署地址（host:port）。自有环境部署四店后必须改写，否则 webmall 评价器
URL 匹配必然失败：

```bash
python scripts/deployment/rewrite_webmall_task_urls.py --dry-run       # 先演练
python scripts/deployment/rewrite_webmall_task_urls.py \
    --from http://<原始host> --to http://<你的host>                    # 写入
```

三点注意：

- **必须显式传 `--to`**：缺省时脚本从 `configs/deploy.yaml` 读
  `services.webmall.host_ip`，未配置该文件时回落 `http://127.0.0.1`——而
  guest 虚拟机内的 `127.0.0.1` 指向 guest 自身，四店不可达，methods 路径下
  评价必然失败。`--to` 应为本机对 guest 可达的 IP（同一台机器部署时即本机内网 IP）。

- 改写 `src/parallel_benchmark/tasks/` 下的文件会让
  `tests/methods/test_methods_parity.py` 报"偏离迁移基线"——这是**部署期的
  有意偏离**，parity 锁的作用正是让这类偏离可见；`--backup` 可留 `.bak`
  回滚。
- 改动 `deploy/methods-services/onlyoffice/` 下服务代码后需
  `docker restart bench-onlyoffice-share`（容器绑定挂载该目录，内存中是
  启动时加载的代码）。

## VM 磁盘镜像（qcow2）

**当前状态（诚实边界）**：方法验证所用的 guest 镜像（内部称"6d 历史镜像"，
SHA-256 目录前缀 `6d8056d8…`）**没有公开分发渠道**。HuggingFace 数据集
`leeLegendary/Parallel_benchmark` 只包含任务 JSON、每任务输入素材
（`benchmark_dataset/<uid>/`）与答案文件，**不含** `Ubuntu.qcow2.zst` 等
任何镜像归档。

第三方可选路径：

1. **开源默认 6bf archive 镜像**：按 `docs/deployment/osworld-linux.md`
   的"固定 OSWorld 镜像"一节，从 HF dataset `xlangai/ubuntu_osworld`
   下载 `Ubuntu.qcow2.zip`（manifest 锁定 SHA-256
   `b795b6cd…`）并物化。2026-08-28 在 pinned revision 的全新部署实测中，
   该镜像 guest 内 `pyautogui` 在位，click / 截图动作经
   `desktop_env` 动作服务器正常执行（五类管线各 1 任务端到端跑通）；
   打字类动作在该轮冒烟中未被触发，键盘任务依赖方建议先以单个打字任务探针确认。
2. **在 1 的基础上自行定制后另存为基础镜像**：若你的工作负载需要 6bf 之外
   的 guest 依赖（或打字探针失败需要补装），启动一次 guest，
   `pip install pyautogui pyperclip` 等后关机，把 overlay 合并回基础 qcow2
   （或直接修改基础镜像后另存）。此路径未在本仓库验证，欢迎反馈。
3. 等待官方分发：若后续在 HF dataset 补充镜像归档，本页会更新。

镜像身份差异（6d vs 6bf archive guest 内容不同）的背景见 README 的
"镜像身份说明"。

## 任务素材与答案文件的自动获取

方法 runner 不需要手工下载数据集：任务 JSON 的 `prepare_script_path`
以 HuggingFace 数据集 tree URL 的形式编码了素材位置（指向
`leeLegendary/Parallel_benchmark` 的 `benchmark_dataset/<uid>/` 目录），
`src/stages/task_data_cache.py` 会在首次执行任务时自动拉取并缓存
（`rewrite_hf_url` 支持 `BENCH_HF_BASE` 镜像前缀）。离线环境可预先用
`huggingface-cli download
leeLegendary/Parallel_benchmark --repo-type dataset` 整体下载后按
`benchmark_dataset/<uid>/` 结构放入缓存目录。

`self_operation` 的 `--gt-cache-dir` 在空目录下即可工作（GT 在运行期
计算），无需预置缓存。

## 快速核验清单（新机器）

1. `git clone` + `pip install -e '.[live,methods,dev,artifact]'`，然后
   `python -m pytest` 通过（含 parity 锁定测试）。注意 **`methods` extra
   不可省略**：`psutil`/`docker`/`pandas` 等 runner 运行时依赖只在该 extra
   中声明，只装 `[live,dev,artifact]` 会有 1 个 smoke import 测试失败，
   且 `methods_runner` 运行时直接 ImportError。
2. 服务栈按需部署（上文）；`webmall` 前完成 URL 改写。
3. `qcow2` 指向自有镜像；`--vm-memory` 记得带单位（如 `4G`）。
4. 模型凭据走环境变量（矩阵见 `docs/methods-provenance.md`）。
5. **配置部署机本机登录密码**：`BENCH_SSH_PASSWORD` 必须是运行用户在
   **本机（部署机）** 的真实登录密码，一个密码贯穿三层——宿主 `sudo -S docker`
   （容器构建/重建）、runner SSH 到 `127.0.0.1`（已配公钥免密时可跳过密码
   认证，但变量仍须非空）、guest sshfs 回挂共享目录（密码认证到宿主 sshd，
   **无法用密钥绕过**）。建议写入仓库外 0600 文件后 `set -a` source，
   含特殊字符（如反引号）的密码须单引号包裹：``BENCH_SSH_PASSWORD='`'``。
6. 单任务试跑：

```bash
python -m paraguibench.methods_runner qa \
  --agent-mode plan --gui-agent qwen -n 1 -p 1 \
  --vm-memory 4G --vm-cpu-cores 4 --memory-limit-gb 24 \
  --vm-ip 127.0.0.1 --shared-base-dir "$HOME/paragui-shared" \
  --qcow2-path <你的Ubuntu.qcow2> --task-uids <uid> \
  --gui-max-rounds 30 --gui-timeout 1800 --output-json-path out.json
```

五类 runner 的任务选择参数**不统一**，按类别使用（实测口径）：

| 类别 | 选任务参数 | 说明 |
|---|---|---|
| `qa` / `webmall` | `--task-uids <uid,uid…>`（逗号分隔）或 `--task-list-file <文件>` | uid 取任务 JSON 的 `task_uid` 字段；webmall 另有 `--all`（全集），qa 空参即跑全集 |
| `webnavigate` | `--task-ids <id,id…>`（**逗号分隔**，自动补全为完整 task_id）或 `--task-list-file <文件>` | 无 `--task-list` 参数 |
| `self_operation` | `--task-ids <id> <id>…`（**空格分隔**，`nargs="*"`；逗号串会被当成单个 ID）或 `--task-list <文件>` | 用文件更稳 |
| `searchwrite` | 仅 `--task-list-file <文件>`（每行一个 task_id） | 不认 `--task-uids`/`--task-ids` |
| `self_operation` 附加 | `--gt-cache-dir <目录>` | 空目录即可，GT 首次运行自动获取 |

其余完整参数组合与模型环境变量矩阵见 `docs/methods-provenance.md`。
