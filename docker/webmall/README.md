# WebMall 商城服务

WebMall pipeline 需要 4 个独立的商城后端实例（模拟不同店铺），对应端口 `9081-9084`。

## 镜像

`docker-compose.yaml` 中引用的镜像名为 `benchmark/webmall:latest`，这是占位名。
首次部署前请按下述任一方式准备真实镜像：

### 选项 A：自行构建

基于 [WebShop](https://github.com/princeton-nlp/WebShop) 或类似的 mock 电商项目，
按 `SHOP_ID` env var 加载不同商品 seed 数据，监听 80 端口。构建镜像后：

```bash
docker build -t benchmark/webmall:latest ./your-webmall-source
```

### 选项 B：使用上游镜像

若有公开的 WebMall 镜像，修改 `docker-compose.yaml` 的 `image:` 行即可。

## 任务素材

每个店铺挂载 `./webmall/tasks/` 目录作为任务配置来源，`webmall_assets/` 作为商品数据卷，由
`scripts/download_resources.py` 拉取到 `resources/webmall_assets/`。`docker-compose.yaml`
使用绝对路径 `./webmall/data/shop<N>` 挂载数据卷；首次启动前创建空目录即可，服务会
在卷内自动初始化。

## 任务 JSON 里的 URL

任务 JSON 文件（例如 `Operation-OnlineShopping-Checkout-002.json`）中的
`answer` 字段包含形如 `http://10.1.110.114:9082/product/...` 的 URL，这是
benchmark 维护者环境下的原始 host。部署到自有环境后，运行：

```bash
python scripts/rewrite_task_urls.py \
    --from http://10.1.110.114 --to http://<your-host>
```

或在 `configs/deploy.yaml.services.webmall.host_ip` 改成 `10.1.110.114` 并通过
DNS/hosts 把该 IP 映射到本机。推荐前者。
