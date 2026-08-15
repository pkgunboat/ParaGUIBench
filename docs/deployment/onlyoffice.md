# OnlyOffice 共享文档服务

ParaGUIBench 的 SearchAndWrite 任务并不都使用 OnlyOffice。只有以下 4 项把
编辑后的正式文档放在共享文档服务中：

- `Operation-FileOperate-SearchAndWrite-002`
- `Operation-FileOperate-SearchAndWrite-004`
- `Operation-FileOperate-SearchAndWrite-006`
- `Operation-FileOperate-SearchAndWrite-008`

其余 6 项继续使用 OSWorld 桌面中的 LibreOffice：

- `Operation-FileOperate-SearchAndWrite-001`
- `Operation-FileOperate-SearchAndWrite-003`
- `Operation-FileOperate-SearchAndWrite-005`
- `Operation-FileOperate-SearchAndWrite-007`
- `Operation-FileOperate-SearchAndWrite-009`
- `Operation-WebOperate-SearchAndWrite-001`

不得根据“任务是否包含 xlsx 资产”推断路由。SearchAndWrite-007 有 xlsx 输入，
但仍是 OSWorld 任务。

## 第一版范围

当前只承诺单实例实验室部署：一个 DocumentServer 加一个 share service。多实例
和大规模并发要等单实例 E2E 通过后再做。share service 使用 Gunicorn 单 worker
多线程，状态文件原子写入，数据根通过 `ONLYOFFICE_SHARE_DATA_DIR` 注入到仓库外。

单元测试通过只验证 API 与 4/6 分流，不等于真实 DocumentServer 可用，也不等于
这 4 个任务已经 `live_validated`。本轮不写 live receipt，也不把任务标成
live-validated。

## 启动、健康检查、停止与重置

命令、端口合同和外部状态目录见
[deploy/onlyoffice/README.md](../../deploy/onlyoffice/README.md)。

宿主浏览器默认访问：

- DocumentServer：`http://127.0.0.1:8080`
- share service：`http://127.0.0.1:5050`

guest Chrome 以后需要一份 guest 可达的宿主地址；把它放在仓库外的环境变量里，
不要把真实内网地址或 JWT secret 写进 Git。

## 镜像与代码身份

DocumentServer 的正式身份是：

`onlyoffice/documentserver@sha256:b9e3c35eab182d3de822a53b109b0f27070f6eacea3b1388b9c50d1182f638f2`

share service 由 `deploy/onlyoffice/Dockerfile.share` 构建，依赖固定为 Flask、
Gunicorn 和 requests，容器启动时不再临时 `pip install`。

本机若没有 pinned digest，不要让 compose 自动拉取。本地 smoke 可以使用已有镜像
或只启动 share service；那只是实验室连通性检查，不能替换正式镜像身份。
