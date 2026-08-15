"""运行装配层版本向量构造测试。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
import paraguibench.runtime.run_versioning as run_versioning_module
from paraguibench.runtime.run_versioning import (
    RunVersioningError,
    _hash_python_package_tree,
    build_run_version_vector,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _copy_versioning_fixture(tmp_path: Path) -> Path:
    """复制构造版本向量所需的最小仓库事实闭包。

    输入参数：
        tmp_path：pytest 提供的隔离临时目录。
    输出返回值：
        包含当前 Python package、schema、manifest、代表任务、资产 manifest、
        环境 manifest 与 pyproject 的临时仓库根。
    """

    root = tmp_path / "repo"
    shutil.copytree(
        _REPO_ROOT / "src" / "paraguibench",
        root / "src" / "paraguibench",
    )
    shutil.copytree(
        _REPO_ROOT / "benchmark" / "schemas",
        root / "benchmark" / "schemas",
    )
    (root / "benchmark" / "manifests").mkdir(parents=True)
    for filename in ("release-v1.json", "runtime-support-v1.json"):
        shutil.copy2(
            _REPO_ROOT / "benchmark" / "manifests" / filename,
            root / "benchmark" / "manifests" / filename,
        )
    task_id = "InformationRetrieval-FileSearch-Readonly-001"
    (root / "benchmark" / "tasks").mkdir()
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json",
        root / "benchmark" / "tasks" / f"{task_id}.json",
    )
    (root / "benchmark" / "assets" / "manifests").mkdir(parents=True)
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
        root / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
    )
    (root / "environments" / "osworld").mkdir(parents=True)
    shutil.copy2(
        _REPO_ROOT / "environments" / "osworld" / "image-manifest.json",
        root / "environments" / "osworld" / "image-manifest.json",
    )
    shutil.copy2(_REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    return root


def _copy_combination_docs_versioning_fixture(tmp_path: Path) -> Path:
    """扩充最小仓库夹具，使其包含 CombinationDocs-015 的双清单。

    输入参数：
        tmp_path：pytest 提供的隔离临时目录。
    输出返回值：
        在既有源码、release 和环境事实闭包上，补齐该任务 canonical JSON、
        input asset manifest 与 evaluator-only gold manifest 的仓库根。
    """

    root = _copy_versioning_fixture(tmp_path)
    task_id = "Operation-FileOperate-CombinationDocs-015"
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json",
        root / "benchmark" / "tasks" / f"{task_id}.json",
    )
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
        root / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
    )
    gold_directory = root / "benchmark" / "gold" / "manifests"
    gold_directory.mkdir(parents=True)
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "gold" / "manifests" / f"{task_id}.json",
        gold_directory / f"{task_id}.json",
    )
    return root


def _copy_batch_operation_versioning_fixture(tmp_path: Path) -> Path:
    """扩充最小仓库夹具，纳入 Batch003 的 input/gold 双清单。

    输入参数：
        tmp_path：pytest 提供的隔离临时目录。
    输出返回值：
        包含当前源码、发布身份、Batch003 canonical 及两份正式 manifest 的
        仓库根；不会复制或读取外部 ZIP 正文。
    """

    root = _copy_versioning_fixture(tmp_path)
    task_id = "Operation-FileOperate-BatchOperation-003"
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json",
        root / "benchmark" / "tasks" / f"{task_id}.json",
    )
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
        root / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
    )
    gold_directory = root / "benchmark" / "gold" / "manifests"
    gold_directory.mkdir(parents=True)
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "gold" / "manifests" / f"{task_id}.json",
        gold_directory / f"{task_id}.json",
    )
    return root


def _copy_combination002_versioning_fixture(tmp_path: Path) -> Path:
    """扩充版本夹具，纳入 Combo-002 input 与 audit-only reference。

    输入参数：tmp_path 提供隔离临时目录。
    输出返回值：已同步 selected release/runtime-support SHA，且只补全
        Combo-002 canonical、input manifest 与 known-negative metadata 的仓库根。
    """

    root = _copy_versioning_fixture(tmp_path)
    task_id = "Operation-FileOperate-CombinationDocs-002"
    task_source = _REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json"
    task_target = root / "benchmark" / "tasks" / f"{task_id}.json"
    shutil.copy2(task_source, task_target)
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
        root / "benchmark" / "assets" / "manifests" / f"{task_id}.json",
    )
    provenance = root / "benchmark" / "provenance" / "pipeline-implicit-known-negative"
    provenance.mkdir(parents=True)
    shutil.copy2(
        _REPO_ROOT
        / "benchmark"
        / "provenance"
        / "pipeline-implicit-known-negative"
        / f"{task_id}.json",
        provenance / f"{task_id}.json",
    )
    release_path = root / "benchmark" / "manifests" / "release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    selected = next(item for item in release["tasks"] if item["task_id"] == task_id)
    selected["sha256"] = hashlib.sha256(task_target.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    support_path = root / "benchmark" / "manifests" / "runtime-support-v1.json"
    support = json.loads(support_path.read_text(encoding="utf-8"))
    support["release_manifest_sha256"] = hashlib.sha256(
        release_path.read_bytes()
    ).hexdigest()
    support_path.write_text(
        json.dumps(support, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def _copy_webmall_versioning_fixture(tmp_path: Path) -> Path:
    """扩充最小仓库夹具，纳入 WebMall 及其 OSWorld 镜像传递闭包。

    输入参数：
        tmp_path：pytest 提供的隔离临时目录。
    输出返回值：
        包含 AddToCart canonical task、WebMall environment manifest 与被其
        SHA 固定的 OSWorld image manifest 的临时仓库根。
    """

    root = _copy_versioning_fixture(tmp_path)
    task_id = "Operation-OnlineShopping-AddToCart-001"
    shutil.copy2(
        _REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json",
        root / "benchmark" / "tasks" / f"{task_id}.json",
    )
    webmall_directory = root / "environments" / "webmall"
    webmall_directory.mkdir(parents=True)
    shutil.copy2(
        _REPO_ROOT / "environments" / "webmall" / "environment-manifest.json",
        webmall_directory / "environment-manifest.json",
    )
    shutil.copy2(
        _REPO_ROOT / "environments" / "webmall" / "wp-order-evidence.php",
        webmall_directory / "wp-order-evidence.php",
    )
    webmall_path = webmall_directory / "environment-manifest.json"
    webmall = json.loads(webmall_path.read_text(encoding="utf-8"))
    webmall["browser_runtime"]["image_manifest_sha256"] = hashlib.sha256(
        (root / "environments/osworld/image-manifest.json").read_bytes()
    ).hexdigest()
    webmall_path.write_text(
        json.dumps(webmall, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def test_version_vector_uses_runtime_support_and_complete_environment_manifest() -> (
    None
):
    """验证运行版本向量来自受校验清单与实际源码树，而不是浮动别名。

    输入参数：
        无；读取仓库内公开的 release、runtime-support、Python 源码和 OSWorld
        image manifest，不访问 Git、网络、凭据或 VM 镜像。
    输出返回值：
        无；任务协议与 runtime-support 精确一致，环境 revision 覆盖整个
        manifest，三个代码 revision 使用当前工作树的完整 SHA-256。
    """

    environment_manifest_path = (
        _REPO_ROOT / "environments" / "osworld" / "image-manifest.json"
    )

    vector = build_run_version_vector(
        repo_root=_REPO_ROOT,
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        environment_manifest_path=environment_manifest_path,
    )

    assert vector.evaluation_protocol == "paraguibench.answer.exact.v1"
    assert vector.environment_protocol == "osworld.desktop.v1"
    assert vector.environment_revision == (
        "manifest-sha256:"
        + hashlib.sha256(environment_manifest_path.read_bytes()).hexdigest()
    )
    assert vector.source_revision.startswith("tree-sha256:")
    assert len(vector.source_revision) == len("tree-sha256:") + 64
    assert vector.agent_code_revision == vector.source_revision
    assert vector.evaluator_revision == vector.source_revision


def test_version_vector_loads_formal_osworld_manifest_through_strict_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证未传入快照时也必须由 OSWorld 专用稳定 loader 授权。

    输入参数：monkeypatch 禁止通用 repository reader 读取
        ``image-manifest.json``，并观测专用 strict loader 调用。
    输出返回值：无；版本向量使用 strict loader 同源 SHA，
        不得回退为 path-check 后的普通 ``read_bytes``。
    """

    environment_path = _REPO_ROOT / "environments/osworld/image-manifest.json"
    original_reader = run_versioning_module._read_repository_file
    original_loader = run_versioning_module.load_osworld_image_manifest_with_sha256
    observed: list[Path] = []

    def guarded_reader(repo_root: Path, path: Path, *, label: str) -> bytes:
        """拒绝通用 reader 触碰 OSWorld 正式清单。"""

        if Path(path).name == "image-manifest.json":
            raise AssertionError("OSWorld manifest 必须由 strict loader 读取")
        return original_reader(repo_root, path, label=label)

    def observed_loader(path: Path) -> object:
        """记录 strict loader 路径并转发真实同源加载。"""

        observed.append(path)
        return original_loader(path)

    monkeypatch.setattr(run_versioning_module, "_read_repository_file", guarded_reader)
    monkeypatch.setattr(
        run_versioning_module,
        "load_osworld_image_manifest_with_sha256",
        observed_loader,
    )

    vector = build_run_version_vector(
        repo_root=_REPO_ROOT,
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        environment_manifest_path=environment_path,
    )

    assert observed == [environment_path]
    assert vector.environment_revision.endswith(original_loader(environment_path)[1])


def test_version_vector_consumes_first_osworld_manifest_snapshot_without_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认 CLI 首次 image 对象可直接固定环境 revision，阻断 A→B。

    输入参数：monkeypatch 把后续环境路径读取替换为不可达哨兵。
    输出返回值：version builder 仍从首次 same-FD SHA/protocol 构造同一
        revision；runtime-support/release 等其他事实读取保持正常。
    """

    environment_path = _REPO_ROOT / "environments/osworld/image-manifest.json"
    image_manifest = load_osworld_image_manifest(environment_path)
    original_reader = run_versioning_module._read_repository_file

    def guarded_reader(
        repo_root: Path,
        path: Path,
        *,
        label: str,
    ) -> bytes:
        """拒绝版本装配重新读取 OSWorld image 路径。

        输入参数：repo_root/path/label 与生产 reader 相同。
        输出返回值：非 image manifest 委托原 reader；image 路径即失败。
        """

        if Path(path).name == "image-manifest.json":
            raise AssertionError("不得重读 environment manifest")
        return original_reader(repo_root, path, label=label)

    monkeypatch.setattr(run_versioning_module, "_read_repository_file", guarded_reader)

    vector = build_run_version_vector(
        repo_root=_REPO_ROOT,
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        environment_manifest_path=environment_path,
        environment_manifest_sha256=image_manifest.manifest_sha256,
        environment_protocol_ids=image_manifest.protocol_ids,
    )

    assert vector.environment_revision == (
        "manifest-sha256:" + str(image_manifest.manifest_sha256)
    )


def test_version_vector_rejects_task_and_environment_manifest_mismatch() -> None:
    """验证 WebMall 协议不能与 OSWorld 镜像摘要拼成自相矛盾的向量。

    输入参数：
        无；使用 canonical WebMall task 与仓库 OSWorld image manifest。
    输出返回值：
        无；公开构造器在产生版本向量前抛出不回显内容的协议错误。
    """

    with pytest.raises(
        RunVersioningError,
        match="WebMall environment manifest 路径无效",
    ):
        build_run_version_vector(
            repo_root=_REPO_ROOT,
            task_id="Operation-OnlineShopping-AddToCart-001",
            environment_manifest_path=(
                _REPO_ROOT / "environments" / "osworld" / "image-manifest.json"
            ),
        )


def test_webmall_version_vector_rejects_stale_nested_osworld_image_sha(
    tmp_path: Path,
) -> None:
    """WebMall Run 版本向量必须校验嵌套 OSWorld image manifest SHA。

    输入参数：
        tmp_path：pytest 提供的 WebMall 传递环境夹具。
    输出返回值：
        无；只改变当前 OSWorld manifest 而不更新 WebMall 内嵌 SHA
        时，公开构造器必须失败关闭。
    """

    root = _copy_webmall_versioning_fixture(tmp_path)
    environment_manifest = (
        root / "environments" / "webmall" / "environment-manifest.json"
    )
    arguments = {
        "repo_root": root,
        "task_id": "Operation-OnlineShopping-AddToCart-001",
        "environment_manifest_path": environment_manifest,
    }
    vector = build_run_version_vector(**arguments)
    assert vector.environment_protocol == "webmall.browser.v1"

    osworld_manifest = root / "environments" / "osworld" / "image-manifest.json"
    osworld_manifest.write_bytes(osworld_manifest.read_bytes() + b"\n")

    with pytest.raises(RunVersioningError, match="browser image|nested"):
        build_run_version_vector(**arguments)


def test_version_vector_rejects_repo_source_different_from_loaded_package(
    tmp_path: Path,
) -> None:
    """验证版本摘要不能记录与当前进程实际导入包不同的 checkout 源码。

    输入参数：
        tmp_path：pytest 提供的临时仓库位置。
    输出返回值：
        无；复制仓中的 Python 文件被修改后，公开构造器必须失败关闭，
        不能把 checkout B 的摘要记到正在执行 wheel/package A 的 Run 中。
    """

    root = _copy_versioning_fixture(tmp_path)
    package_init = root / "src" / "paraguibench" / "__init__.py"
    package_init.write_text(
        package_init.read_text(encoding="utf-8") + "\n# stale checkout\n",
        encoding="utf-8",
    )

    with pytest.raises(RunVersioningError, match="loaded package"):
        build_run_version_vector(
            repo_root=root,
            task_id="InformationRetrieval-FileSearch-Readonly-001",
            environment_manifest_path=(
                root / "environments" / "osworld" / "image-manifest.json"
            ),
        )


def test_version_vector_changes_when_task_asset_manifest_changes(
    tmp_path: Path,
) -> None:
    """验证任务资产来源、revision 或文件摘要变化会改变 Run 身份。

    输入参数：
        tmp_path：pytest 提供的隔离临时仓库。
    输出返回值：
        无；只修改当前 task 引用的 pinned asset manifest 后，源码闭包摘要
        必须变化，防止不同资产内容被归入同一个 Run。
    """

    root = _copy_versioning_fixture(tmp_path)
    environment_manifest = root / "environments" / "osworld" / "image-manifest.json"
    arguments = {
        "repo_root": root,
        "task_id": "InformationRetrieval-FileSearch-Readonly-001",
        "environment_manifest_path": environment_manifest,
    }
    before = build_run_version_vector(**arguments)

    asset_manifest = (
        root
        / "benchmark"
        / "assets"
        / "manifests"
        / "InformationRetrieval-FileSearch-Readonly-001.json"
    )
    payload = json.loads(asset_manifest.read_text(encoding="utf-8"))
    payload["source"]["revision"] = "1" * 40
    asset_manifest.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )

    after = build_run_version_vector(**arguments)

    assert after.source_revision != before.source_revision
    assert after.agent_code_revision != before.agent_code_revision
    assert after.evaluator_revision != before.evaluator_revision


def test_version_vector_changes_when_evaluator_gold_manifest_changes(
    tmp_path: Path,
) -> None:
    """验证 evaluator-only gold manifest 是 Run 版本身份的一部分。

    输入参数：
        tmp_path：pytest 提供的隔离临时仓库。
    输出返回值：
        无；只修改当前任务引用的 gold manifest 后，source、Agent 与
        evaluator revision 均须改变，避免不同预期答案共用同一 Run 身份。
    """

    root = _copy_combination_docs_versioning_fixture(tmp_path)
    environment_manifest = root / "environments" / "osworld" / "image-manifest.json"
    arguments = {
        "repo_root": root,
        "task_id": "Operation-FileOperate-CombinationDocs-015",
        "environment_manifest_path": environment_manifest,
    }
    before = build_run_version_vector(**arguments)

    gold_manifest = (
        root
        / "benchmark"
        / "gold"
        / "manifests"
        / "Operation-FileOperate-CombinationDocs-015.json"
    )
    gold_manifest.write_bytes(gold_manifest.read_bytes() + b"\n")

    after = build_run_version_vector(**arguments)

    assert after.source_revision != before.source_revision
    assert after.agent_code_revision != before.agent_code_revision
    assert after.evaluator_revision != before.evaluator_revision


def test_batch_version_vector_binds_both_strict_manifest_bytes(
    tmp_path: Path,
) -> None:
    """验证 Batch003 的 input 与 evaluator-only gold 都进入 Run 身份。

    输入参数：
        tmp_path：pytest 提供的隔离仓库。
    输出返回值：
        无；依次只改变 input 或 gold manifest bytes 时，version vector 的
        source revision 都必须改变，canonical 不得回退到 legacy URL。
    """

    root = _copy_batch_operation_versioning_fixture(tmp_path)
    task_id = "Operation-FileOperate-BatchOperation-003"
    arguments = {
        "repo_root": root,
        "task_id": task_id,
        "environment_manifest_path": (
            root / "environments" / "osworld" / "image-manifest.json"
        ),
    }
    before = build_run_version_vector(**arguments)
    input_manifest = root / "benchmark" / "assets" / "manifests" / f"{task_id}.json"
    input_manifest.write_bytes(input_manifest.read_bytes() + b"\n")
    after_input = build_run_version_vector(**arguments)
    gold_manifest = root / "benchmark" / "gold" / "manifests" / f"{task_id}.json"
    gold_manifest.write_bytes(gold_manifest.read_bytes() + b"\n")
    after_gold = build_run_version_vector(**arguments)

    assert before.source_revision != after_input.source_revision
    assert after_input.source_revision != after_gold.source_revision


def test_combination002_version_vector_binds_audit_known_negative_metadata(
    tmp_path: Path,
) -> None:
    """验证 Combo-002 Run 身份直接纳入 audit-only reference 字节。

    输入参数：tmp_path 提供 selected release 已同步的隔离仓库。
    输出返回值：只改 known-negative manifest 原始字节时，source、
        Agent 与 evaluator revision 全部变化；该文件仍不是 pass oracle。
    """

    root = _copy_combination002_versioning_fixture(tmp_path)
    arguments = {
        "repo_root": root,
        "task_id": "Operation-FileOperate-CombinationDocs-002",
        "environment_manifest_path": (
            root / "environments" / "osworld" / "image-manifest.json"
        ),
    }
    before = build_run_version_vector(**arguments)
    reference_manifest = (
        root
        / "benchmark"
        / "provenance"
        / "pipeline-implicit-known-negative"
        / "Operation-FileOperate-CombinationDocs-002.json"
    )
    reference_manifest.write_bytes(reference_manifest.read_bytes() + b"\n")

    after = build_run_version_vector(**arguments)

    assert after.source_revision != before.source_revision
    assert after.agent_code_revision != before.agent_code_revision
    assert after.evaluator_revision != before.evaluator_revision


def test_python_package_digest_rejects_symlink_directory(
    tmp_path: Path,
) -> None:
    """验证 package 源码闭集不能忽略符号链接目录中的实现。

    输入参数：
        tmp_path：pytest 提供的隔离 package 和外部源码目录。
    输出返回值：
        无；摘要构造必须拒绝 symlink directory，不得返回一个
        不随外部 evaluator 内容变化的伪稳定 revision。
    """

    package_root = tmp_path / "paraguibench"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    outside = tmp_path / "outside-evaluation"
    outside.mkdir()
    (outside / "evaluator.py").write_text("REVISION = 1\n", encoding="utf-8")
    (package_root / "evaluation").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(RunVersioningError, match="symlink|符号链接"):
        _hash_python_package_tree(package_root, label="test package")
