"""WebMall 协议预检、Agent URL 物化与版本闭包测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import shutil
from typing import Any

import pytest

from paraguibench.benchmark import prepare_release_task
from paraguibench.integrations.webmall import (
    WebMallEnvironmentManifestError,
)
from paraguibench.runtime.webmall_binding import (
    WebMallEvidenceMode,
    WebMallPrivilegedRuntimeBindingError,
    WebMallRuntimeBindingError,
    _safe_repo_file,
    bind_webmall_privileged_runtime,
    preflight_webmall_cart_reference_candidate_runtime,
    preflight_webmall_identity,
    preflight_webmall_runtime,
)
from paraguibench.runtime.webmall_cart_component_receipts import (
    WebMallCartComponentReceiptError,
)
from paraguibench.runtime import webmall_binding as webmall_binding_module


_ORIGINS = {
    "PARAGUIBENCH_WEBMALL_STORE_1_ORIGIN": "https://store-one.example",
    "PARAGUIBENCH_WEBMALL_STORE_2_ORIGIN": "https://store-two.example",
    "PARAGUIBENCH_WEBMALL_STORE_3_ORIGIN": "https://store-three.example",
    "PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN": "https://store-four.example",
}


class _UnusedLeaseTransport:
    """标记 privileged binding 构造阶段不得访问协调器。"""

    def post_json(self, **_: Any) -> dict[str, object]:
        """拒绝构造阶段任何租约 I/O。

        输入参数：
            _：若误调用时的任意参数。
        输出返回值：
            不返回；调用即使测试失败。
        """

        raise AssertionError("privileged binding must not perform lease I/O")


class _UnusedOrderRunner:
    """标记 privileged binding 构造阶段不得启动 WP-CLI。"""

    def run(self, request: object) -> object:
        """拒绝构造阶段任何订单读取。

        输入参数：
            request：若误调用时的进程请求。
        输出返回值：
            不返回；调用即使测试失败。
        """

        del request
        raise AssertionError("privileged binding must not perform order I/O")


def test_identity_preflight_never_requires_deployment_environment() -> None:
    """验证 doctor 可先闭合代码协议，再一次列出所有部署缺口。

    输入参数：
        无；使用真实 Checkout-001，不提供任何环境变量。
    输出返回值：
        无；身份预检返回 WebMall 版本/evaluator 闭包，且仍保留
        logical Agent URL，便于 doctor 后续独立检查四店绑定。
    """

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-Checkout-001",
        environment_bindings={},
    )

    identity = preflight_webmall_identity(
        repo_root=repo_root,
        prepared_task=prepared,
    )

    assert identity.version_vector.environment_protocol == "webmall.browser.v1"
    assert identity.manifest.store_universe_id == "webmall.four-stores.v1"
    assert (
        identity.webmall_manifest_sha256
        == hashlib.sha256(
            (repo_root / "environments/webmall/environment-manifest.json").read_bytes()
        ).hexdigest()
    )
    assert (
        identity.browser_image_manifest_sha256
        == hashlib.sha256(
            (repo_root / "environments/osworld/image-manifest.json").read_bytes()
        ).hexdigest()
    )
    assert "webmall://" in identity.prepared_task.agent_task["instruction"]


def test_identity_preflight_rejects_webmall_manifest_a_to_b_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证首次 WebMall 快照 A 不得与后续正式字节 B 混合。

    输入参数：tmp_path 保存一份另一严格合法 WebMall
        manifest B；monkeypatch 让专用 loader 先返回正式 A，
        在 preflight 返回前复验时返回 B。
    输出返回值：无；版本向量可仅由 A 的两份 same-FD
        SHA 构造，但最终正式快照不再是 A 时必须失败。
    """

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-Checkout-001",
        environment_bindings={},
    )
    real_loader = webmall_binding_module.load_webmall_environment_manifest_with_sha256
    formal_path = repo_root / "environments/webmall/environment-manifest.json"
    manifest_a, sha_a = real_loader(formal_path)
    alternate_root = tmp_path / "environments"
    alternate_webmall = alternate_root / "webmall"
    alternate_osworld = alternate_root / "osworld"
    alternate_webmall.mkdir(parents=True)
    alternate_osworld.mkdir(parents=True)
    shutil.copy2(
        repo_root / "environments/webmall/wp-order-evidence.php",
        alternate_webmall / "wp-order-evidence.php",
    )
    shutil.copy2(
        repo_root / "environments/osworld/image-manifest.json",
        alternate_osworld / "image-manifest.json",
    )
    raw_b = json.loads(formal_path.read_text(encoding="utf-8"))
    raw_b["service_images"]["wordpress"] = "wordpress@sha256:" + "a" * 64
    alternate_manifest_path = alternate_webmall / "environment-manifest.json"
    alternate_manifest_path.write_text(
        json.dumps(raw_b, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_b, sha_b = real_loader(alternate_manifest_path)
    assert (manifest_b, sha_b) != (manifest_a, sha_a)
    loads = iter(((manifest_a, sha_a), (manifest_b, sha_b)))

    monkeypatch.setattr(
        webmall_binding_module,
        "load_webmall_environment_manifest_with_sha256",
        lambda _path: next(loads),
    )

    with pytest.raises(WebMallRuntimeBindingError):
        preflight_webmall_identity(
            repo_root=repo_root,
            prepared_task=prepared,
        )


def test_url_multiset_binding_explicitly_forbids_privileged_order_evidence() -> None:
    """验证 URL 任务在 identity/runtime 两阶段都闭合为无特权。

    输入参数：
        无；使用真实 SingleProductSearch-001 和四个测试 origin。
    输出返回值：
        无；断言协议为 URL multiset，且特权订单证据标记在
        两个阶段均为假。
    """

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-SingleProductSearch-001",
        environment_bindings={},
    )

    identity = preflight_webmall_identity(
        repo_root=repo_root,
        prepared_task=prepared,
    )
    runtime = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepared,
        environment=_ORIGINS,
    )

    assert identity.version_vector.evaluation_protocol == (
        "paraguibench.webmall.url-multiset.v1"
    )
    assert identity.requires_privileged_order_evidence is False
    assert runtime.requires_privileged_order_evidence is False
    assert identity.evidence_mode is WebMallEvidenceMode.REPORTED_URL
    assert runtime.evidence_mode is WebMallEvidenceMode.REPORTED_URL
    assert identity.requires_cart_evidence is False
    assert runtime.requires_cart_evidence is False


def test_cart_binding_selects_browser_cart_evidence_without_privileged_io() -> None:
    """验证 8 个 Cart 任务闭合为独立第三种 WebMall evidence 模式。

    输入参数：无；使用真实 AddToCart-001 与四店合成 origin。
    输出返回值：无；identity/runtime 均选择 Browser Cart，既不读取订单
        特权源，也不退化为 Agent 报告 URL 模式。
    """

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-AddToCart-001",
        environment_bindings={},
    )

    identity = preflight_webmall_identity(
        repo_root=repo_root,
        prepared_task=prepared,
    )
    runtime = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepared,
        environment=_ORIGINS,
    )

    assert identity.version_vector.evaluation_protocol == (
        "paraguibench.webmall.cart.closed-world.v1"
    )
    assert identity.evidence_mode is WebMallEvidenceMode.BROWSER_CART
    assert runtime.evidence_mode is WebMallEvidenceMode.BROWSER_CART
    assert identity.requires_privileged_order_evidence is False
    assert runtime.requires_privileged_order_evidence is False
    assert identity.requires_cart_evidence is True
    assert runtime.requires_cart_evidence is True
    assert identity.cart_reference_validation_verified is False
    assert runtime.cart_reference_validation_verified is False


def test_reference_candidate_can_refresh_stale_receipt_but_normal_runtime_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证过期 receipt 不能进入普通 run，也不会锁死显式刷新命令。

    输入参数：monkeypatch 令 trusted loader 模拟已 allowlist 但
        task/environment/component 向量过期的固定失败。
    输出返回值：无；标准 runtime 失败关闭，仅候选验证
        专属 preflight 可继续且不伪造 receipt 已验证标记。
    """

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-AddToCart-001",
        environment_bindings={},
    )
    loader_calls = 0

    def stale_receipt(_repo_root: Path) -> None:
        """模拟 allowlist 已存在但三层当前身份已漂移。

        输入参数：_repo_root 为标准 preflight 请求验证的仓库。
        输出返回值：不返回；始终抛固定 component receipt 错误。
        """

        nonlocal loader_calls
        loader_calls += 1
        raise WebMallCartComponentReceiptError

    monkeypatch.setattr(
        webmall_binding_module,
        "load_trusted_webmall_cart_reference_receipt",
        stale_receipt,
    )

    with pytest.raises(WebMallCartComponentReceiptError):
        preflight_webmall_runtime(
            repo_root=repo_root,
            prepared_task=prepared,
            environment=_ORIGINS,
        )

    candidate = preflight_webmall_cart_reference_candidate_runtime(
        repo_root=repo_root,
        prepared_task=prepared,
        environment=_ORIGINS,
    )

    assert loader_calls == 1
    assert candidate.evidence_mode is WebMallEvidenceMode.BROWSER_CART
    assert candidate.cart_reference_validation_verified is False


def test_url_multiset_binding_rejects_privileged_runtime_construction() -> None:
    """验证 URL 任务即使被误传入凭据也不得装配订单源。

    输入参数：
        无；使用 URL runtime 与包含合成 reader/lease 值的环境。
    输出返回值：
        无；在任何 WP-CLI 或租约 I/O 前以固定错误失败。
    """

    repo_root = Path(__file__).resolve().parents[2]
    runtime = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepare_release_task(
            repo_root,
            "Operation-OnlineShopping-SingleProductSearch-001",
            environment_bindings={},
        ),
        environment=_ORIGINS,
    )
    deployment = {
        **_ORIGINS,
        **{
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET": (
                f"docker:reference-store-{index}"
            )
            for index in range(1, 5)
        },
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL": ("https://lease.example.invalid"),
        "PARAGUIBENCH_WEBMALL_LEASE_TOKEN": "must-not-be-read",
    }

    with pytest.raises(WebMallPrivilegedRuntimeBindingError):
        bind_webmall_privileged_runtime(
            repo_root=repo_root,
            runtime=runtime,
            environment=deployment,
            attempt_id="attempt-url-001",
            owner_id="worker-url-001",
            lease_transport=_UnusedLeaseTransport(),
            order_runner=_UnusedOrderRunner(),
        )


def test_privileged_binding_snapshots_reader_and_lease_references_without_io() -> None:
    """验证生产 source/session/lease 在模型凭据与 RunStore 前完整装配。

    输入参数：
        无；使用四店 origin/reader target 和合成租约凭据。
    输出返回值：
        无；返回完整四店 evidence session，构造期不启动 WP-CLI、
        不访问协调器，且不在公开 audit 中保存凭据值。
    """

    repo_root = Path(__file__).resolve().parents[2]
    lease_credential = "private-" + ("x" * 32)
    deployment = {
        **_ORIGINS,
        **{
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET": (
                f"docker:reference-store-{index}"
            )
            for index in range(1, 5)
        },
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL": ("https://lease.example.invalid"),
        "PARAGUIBENCH_WEBMALL_LEASE_TOKEN": lease_credential,
    }
    runtime = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepare_release_task(
            repo_root,
            "Operation-OnlineShopping-Checkout-001",
            environment_bindings={},
        ),
        environment=deployment,
    )

    privileged = bind_webmall_privileged_runtime(
        repo_root=repo_root,
        runtime=runtime,
        environment=deployment,
        attempt_id="attempt-001",
        owner_id="worker-host-a-001",
        lease_transport=_UnusedLeaseTransport(),
        order_runner=_UnusedOrderRunner(),
    )

    assert privileged.session.logical_store_ids == (
        "store-1",
        "store-2",
        "store-3",
        "store-4",
    )
    assert privileged.source is not None
    assert privileged.lease is not None
    serialized_audit = json.dumps(runtime.prepared_task.audit_metadata)
    assert lease_credential not in serialized_audit
    assert "lease.example.invalid" not in serialized_audit


def test_privileged_binding_missing_lease_token_uses_fixed_error() -> None:
    """验证特权订单源完整但租约 token 缺失时不泊出其他绑定值。

    输入参数：
        无；故意不提供 manifest 指定的 lease credential。
    输出返回值：
        无；构造在 I/O 前以固定错误码失败。
    """

    repo_root = Path(__file__).resolve().parents[2]
    deployment = {
        **_ORIGINS,
        **{
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET": (
                f"docker:reference-store-{index}"
            )
            for index in range(1, 5)
        },
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL": ("https://lease.example.invalid"),
    }
    runtime = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepare_release_task(
            repo_root,
            "Operation-OnlineShopping-Checkout-001",
            environment_bindings={},
        ),
        environment=deployment,
    )

    with pytest.raises(WebMallPrivilegedRuntimeBindingError) as captured:
        bind_webmall_privileged_runtime(
            repo_root=repo_root,
            runtime=runtime,
            environment=deployment,
            attempt_id="attempt-001",
            owner_id="worker-host-a-001",
            lease_transport=_UnusedLeaseTransport(),
            order_runner=_UnusedOrderRunner(),
        )

    assert str(captured.value) == "WEBMALL_PRIVILEGED_RUNTIME_BINDING_INVALID"
    assert "lease.example.invalid" not in str(captured.value)


def test_preflight_binds_checkout_to_webmall_manifest_without_mutating_gold() -> None:
    """验证 Checkout 在副作用前闭合 manifest、evaluator 与三投影。

    输入参数：
        无；使用仓库内 Checkout-001 和四个合成 origin。
    输出返回值：
        无；Agent instruction 仅见部署 URL，trusted gold 仍为
        logical URL，audit 不含 origin 值，版本向量为 WebMall v2。
    """

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-Checkout-001",
        environment_bindings={},
    )

    binding = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepared,
        environment=_ORIGINS,
    )

    assert binding.version_vector.environment_protocol == "webmall.browser.v1"
    assert (
        binding.version_vector.evaluation_protocol
        == "paraguibench.webmall.checkout.closed-world.v2"
    )
    assert binding.manifest.manifest_id == "webmall.reference-four-stores.v1"
    assert binding.browser_image.environment_id == "osworld-ubuntu-x86_64"
    assert binding.requires_privileged_order_evidence is True
    assert "webmall://" not in binding.prepared_task.agent_task["instruction"]
    assert (
        "https://store-three.example"
        in (binding.prepared_task.agent_task["instruction"])
    )
    assert binding.prepared_task.trusted_task["expected_urls"] == [
        (
            "webmall://store-3/product/"
            "trust-tk-350-wireless-membrane-keyboard-spill-proof-silent-keys-media-keys-black"
        )
    ]
    serialized_audit = json.dumps(binding.prepared_task.audit_metadata)
    assert all(value not in serialized_audit for value in _ORIGINS.values())
    assert set(
        binding.prepared_task.audit_metadata["webmall_environment"][
            "origin_binding_names"
        ]
    ) == set(_ORIGINS)


def test_preflight_rejects_missing_origin_with_fixed_error() -> None:
    """验证任一商店 origin 未绑定时在 Agent/RunStore 前失败。

    输入参数：
        无；故意删除 store-4 的环境变量。
    输出返回值：
        无；异常只包含固定 code，不回显已绑定 origin。
    """

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-Checkout-001",
        environment_bindings={},
    )
    incomplete = dict(_ORIGINS)
    incomplete.pop("PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN")

    with pytest.raises(WebMallEnvironmentManifestError) as captured:
        preflight_webmall_runtime(
            repo_root=repo_root,
            prepared_task=prepared,
            environment=incomplete,
        )

    assert str(captured.value) == "WEBMALL_ENVIRONMENT_MANIFEST_INVALID"
    assert all(value not in str(captured.value) for value in incomplete.values())


def test_safe_repo_file_rejects_parent_directory_symlink(
    tmp_path: Path,
) -> None:
    """验证 manifest 父目录 symlink 不会在 ``resolve`` 后隐身。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；普通文件可解析，指向同一仓库内目录的 symlink
        仍以固定 runtime binding 错误失败关闭。
    """

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    manifest = real_directory / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real_directory, target_is_directory=True)

    assert (
        _safe_repo_file(
            tmp_path.resolve(),
            PurePosixPath("real/manifest.json"),
        )
        == manifest
    )
    with pytest.raises(WebMallRuntimeBindingError):
        _safe_repo_file(
            tmp_path.resolve(),
            PurePosixPath("alias/manifest.json"),
        )
