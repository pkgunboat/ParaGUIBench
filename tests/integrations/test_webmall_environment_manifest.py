"""WebMall 四店环境 manifest、reader 脚本与 origin 绑定测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paraguibench.integrations.webmall.environment_manifest import (
    WebMallEnvironmentManifestError,
    bind_webmall_origins,
    load_webmall_environment_manifest,
    load_webmall_environment_manifest_with_sha256,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"
SCHEMA_PATH = (
    REPO_ROOT / "benchmark" / "schemas" / "webmall-environment-manifest-v1.schema.json"
)


def _candidate_manifest(
    tmp_path: Path,
    *,
    mutate: tuple[str, object] | None = None,
) -> Path:
    """复制正式 manifest 与 reader，并可定向替换一个顶层字段。

    输入参数：
        tmp_path：pytest 提供的隔离环境目录。
        mutate：可选 ``(field, value)`` 顶层字段替换。
    输出返回值：
        可由严格 loader 独立读取的候选 manifest 路径。
    """

    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if mutate is not None:
        raw[mutate[0]] = mutate[1]
    webmall_directory = tmp_path / "webmall"
    webmall_directory.mkdir()
    candidate = webmall_directory / "environment-manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")
    script_name = raw["order_reader"]["script_path"]
    source_script = MANIFEST_PATH.parent / script_name
    (webmall_directory / script_name).write_bytes(source_script.read_bytes())
    osworld_directory = tmp_path / "osworld"
    osworld_directory.mkdir()
    (osworld_directory / "image-manifest.json").write_bytes(
        (REPO_ROOT / "environments" / "osworld" / "image-manifest.json").read_bytes()
    )
    return candidate


def test_reference_webmall_manifest_closes_four_store_environment() -> None:
    """验证正式 manifest 固定四店、镜像、reader、租约与 reset 合同。

    输入参数：
        无；读取仓库内 WebMall 环境 manifest 与版本化 reader 脚本。
    输出返回值：
        无；全部稳定身份与当前审计确认的软件版本精确匹配。
    """

    manifest = load_webmall_environment_manifest(MANIFEST_PATH)

    assert manifest.manifest_id == "webmall.reference-four-stores.v1"
    assert manifest.environment_id == "webmall-woocommerce-four-stores"
    assert manifest.protocol_ids == ("webmall.browser.v1",)
    assert manifest.store_universe_id == "webmall.four-stores.v1"
    assert tuple(store.logical_store_id for store in manifest.stores) == (
        "store-1",
        "store-2",
        "store-3",
        "store-4",
    )
    assert manifest.browser_runtime.required_protocol_id == "osworld.chrome.v1"
    assert manifest.software.wordpress_version == "7.0.2"
    assert manifest.software.woocommerce_version == "9.8.5"
    assert manifest.software.order_storage == "hpos"
    assert "@sha256:" in manifest.service_images.wordpress
    assert "@sha256:" in manifest.service_images.mariadb
    assert manifest.order_reader.protocol_id == (
        "paraguibench.webmall.wp-cli-orders.v2"
    )
    assert len(manifest.order_reader.script_sha256) == 64
    assert manifest.order_reader.max_stdout_bytes == 4 * 1024 * 1024
    assert manifest.cart_reader.protocol_id == (
        "paraguibench.webmall.woocommerce-store-api-cart.v1"
    )
    assert manifest.cart_reader.evidence_protocol_id == (
        "paraguibench.webmall.cart-authoritative-state.v1"
    )
    assert manifest.cart_reader.reader_kind == "woocommerce_store_api"
    assert manifest.cart_reader.endpoint_path == "/wp-json/wc/store/v1/cart"
    assert manifest.cart_reader.same_browser_context_required is True
    assert manifest.cart_reader.stability_read_count == 2
    assert manifest.cart_reader.max_response_bytes == 2 * 1024 * 1024
    assert manifest.cart_reader.max_items == 1024
    assert manifest.cart_reader.max_quantity == 10_000
    assert manifest.cart_reader.timeout_seconds == 10
    assert manifest.cart_reader.reference_live_validation_status == "pending"
    assert manifest.lease.protocol_id == ("paraguibench.webmall.distributed-lease.v1")
    assert manifest.reset.strategy == "attempt_order_baseline_delta"


def test_manifest_loader_binds_parse_and_sha_to_same_bytes() -> None:
    """验证 runtime 能从同一次稳定读取获得解析对象与原始字节摘要。

    输入参数：无；读取正式 WebMall manifest。
    输出返回值：无；对象与普通 loader 等价，SHA 等于该次字节摘要。
    """

    manifest, manifest_sha256 = load_webmall_environment_manifest_with_sha256(
        MANIFEST_PATH
    )

    assert manifest == load_webmall_environment_manifest(MANIFEST_PATH)
    assert manifest_sha256 == hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()


def test_manifest_loader_parses_live_validated_cart_reference_status(
    tmp_path: Path,
) -> None:
    """验证 loader 能表达由独立 component receipt 授权的活性状态。

    输入参数：tmp_path 为 pytest 候选 manifest 隔离目录。
    输出返回值：无；底层 loader 完整保留 ``live_validated``，
        其可信性由上层 receipt 门禁单独判定。
    """

    candidate = _candidate_manifest(tmp_path)
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    raw["cart_reader"]["reference_live_validation_status"] = "live_validated"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    manifest = load_webmall_environment_manifest(candidate)

    assert manifest.cart_reader.reference_live_validation_status == "live_validated"


def test_webmall_browser_binding_tracks_current_osworld_manifest() -> None:
    """验证 WebMall 浏览器合同精确绑定当前 OSWorld manifest。

    输入参数：
        无；读取仓库内 WebMall 与 OSWorld 两份版本化 manifest。
    输出返回值：
        无；WebMall 内记录的 SHA-256 必须与当前 OSWorld manifest
        字节完全一致，避免 preflight 在任务 I/O 前因引用漂移失败。
    """

    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    image_manifest_path = REPO_ROOT / "environments" / "osworld" / "image-manifest.json"
    actual_digest = hashlib.sha256(image_manifest_path.read_bytes()).hexdigest()

    assert raw["browser_runtime"]["image_manifest_sha256"] == actual_digest
    assert load_webmall_environment_manifest(MANIFEST_PATH).manifest_id == (
        "webmall.reference-four-stores.v1"
    )


def test_webmall_manifest_contains_only_binding_names_not_deployment_values() -> None:
    """验证公开 manifest 不写入内网 origin、容器名或租约凭据。

    输入参数：
        无；读取公开 JSON 文本。
    输出返回值：
        无；仅包含环境变量引用，不含已知端口、内网地址或 secret 值。
    """

    text = MANIFEST_PATH.read_text(encoding="utf-8")
    historical_internal_address = ".".join(("10", "1", "110", "114"))

    assert historical_internal_address not in text
    assert all(f":908{index}" not in text for index in range(1, 5))
    assert "PARAGUIBENCH_WEBMALL_STORE_1_ORIGIN" in text
    assert "PARAGUIBENCH_WEBMALL_LEASE_TOKEN" in text
    assert "sk-" not in text


def test_webmall_origin_binding_materializes_agent_urls_without_changing_gold() -> None:
    """验证运行 origin 只进入 Agent registry，canonical logical gold 保持不变。

    输入参数：
        无；使用四个公开测试 origin 作为环境变量值。
    输出返回值：
        无；registry 可物化/反解 URL，manifest 与 logical URL 未被修改。
    """

    manifest = load_webmall_environment_manifest(MANIFEST_PATH)
    environment = {
        store.origin_env: f"https://store-{index}.example.invalid"
        for index, store in enumerate(manifest.stores, start=1)
    }
    logical_url = "webmall://store-2/product/example-product"

    registry = bind_webmall_origins(manifest, environment)
    runtime_url = registry.materialize_url(logical_url)

    assert runtime_url == "https://store-2.example.invalid/product/example-product"
    assert registry.canonicalize_url(runtime_url) == logical_url
    assert logical_url == "webmall://store-2/product/example-product"


def test_webmall_origin_binding_fails_closed_without_disclosing_values() -> None:
    """验证缺失或非法 origin 只产生固定、不含部署值的绑定错误。

    输入参数：
        无；故意省略三个绑定并给唯一值附加私密 sentinel。
    输出返回值：
        无；异常不回显环境变量值、host 或任务 URL。
    """

    manifest = load_webmall_environment_manifest(MANIFEST_PATH)
    environment = {
        manifest.stores[0].origin_env: "https://private-sentinel.example.invalid/path"
    }

    with pytest.raises(WebMallEnvironmentManifestError) as caught:
        bind_webmall_origins(manifest, environment)

    rendered = f"{caught.value!s}|{caught.value!r}"
    assert str(caught.value) == "WEBMALL_ENVIRONMENT_MANIFEST_INVALID"
    assert "private-sentinel" not in rendered
    assert "example.invalid" not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("store_universe_id", "webmall.partial.v1"),
        ("protocol_ids", ["osworld.chrome.v1"]),
        ("unknown", "must-fail"),
    ],
)
def test_webmall_manifest_rejects_wrong_version_scope_and_unknown_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    """验证 manifest 版本、协议、四店 scope 与字段闭集不可漂移。

    输入参数：
        tmp_path：pytest 提供的隔离候选目录。
        field/value：本例定向替换或新增的顶层字段。
    输出返回值：
        无；严格 loader 以固定错误拒绝候选。
    """

    path = _candidate_manifest(tmp_path, mutate=(field, value))

    with pytest.raises(WebMallEnvironmentManifestError):
        load_webmall_environment_manifest(path)


def test_webmall_manifest_rejects_reader_script_drift(tmp_path: Path) -> None:
    """验证 reader 脚本一字节变化会破坏环境完整性绑定。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 与脚本目录。
    输出返回值：
        无；manifest 固定摘要与实际脚本不一致时失败关闭。
    """

    path = _candidate_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    script = path.parent / raw["order_reader"]["script_path"]
    script.write_bytes(script.read_bytes() + b"\n")

    with pytest.raises(WebMallEnvironmentManifestError):
        load_webmall_environment_manifest(path)


@pytest.mark.parametrize("invalid_limit", [4 * 1024 * 1024 - 1, 4 * 1024 * 1024 + 1])
def test_webmall_manifest_requires_single_reader_stdout_limit(
    tmp_path: Path,
    invalid_limit: int,
) -> None:
    """验证 manifest loader 不允许 parser 与 process 使用不同上限。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 目录。
        invalid_limit：低于或高于唯一 4 MiB 协议值的候选上限。
    输出返回值：
        无；断言任何非精确 4 MiB 值均被固定错误拒绝。
    """

    path = _candidate_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["order_reader"]["max_stdout_bytes"] = invalid_limit
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(WebMallEnvironmentManifestError):
        load_webmall_environment_manifest(path)


def test_webmall_environment_schema_declares_closed_nested_objects() -> None:
    """验证公开 JSON Schema 对顶层和全部嵌套对象使用字段闭集。

    输入参数：
        无；读取 WebMall environment v1 schema。
    输出返回值：
        无；每个 object 都声明 ``additionalProperties=false``。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_webmall_environment_schema_freezes_cart_reader_status_gate() -> None:
    """验证公开 schema 明确选择同会话 Store API 与两态门禁。

    输入参数：
        无；读取仓库 WebMall environment v1 schema。
    输出返回值：
        无；Cart reader 的端点、双读、资源上限和闭集状态均固定。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    cart_reader = schema["$defs"]["cart_reader"]

    assert "cart_reader" in schema["required"]
    assert schema["properties"]["cart_reader"] == {"$ref": "#/$defs/cart_reader"}
    assert cart_reader["additionalProperties"] is False
    assert cart_reader["properties"]["reader_kind"]["const"] == (
        "woocommerce_store_api"
    )
    assert cart_reader["properties"]["stability_read_count"]["const"] == 2
    assert cart_reader["properties"]["reference_live_validation_status"] == {
        "type": "string",
        "enum": ["pending", "live_validated"],
    }


def test_webmall_manifest_loader_rejects_symlink(tmp_path: Path) -> None:
    """验证环境 manifest loader 不跟随指向合法文件的符号链接。

    输入参数：
        tmp_path：pytest 提供的隔离 symlink 位置。
    输出返回值：
        无；JSON 解析前即以固定错误失败。
    """

    linked = tmp_path / "environment-manifest.json"
    linked.symlink_to(MANIFEST_PATH)

    with pytest.raises(WebMallEnvironmentManifestError):
        load_webmall_environment_manifest(linked)
