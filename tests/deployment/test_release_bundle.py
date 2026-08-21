"""ParaGUIBench cleanroom release bundle 的行为回归测试。"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import unicodedata
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SCRIPT = REPOSITORY_ROOT / "scripts" / "deployment" / "release_bundle.py"


def _load_bundle_module():
    """功能：通过公开脚本路径加载 release bundle 模块。

    输入参数：无。
    输出返回值：返回已加载的模块对象。
    """

    spec = importlib.util.spec_from_file_location("release_bundle", BUNDLE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 release bundle 模块：{BUNDLE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_file(root: Path, relative_path: str, payload: str) -> None:
    """功能：在合成仓库中写入 UTF-8 测试文件。

    输入参数：``root`` 为仓库根，``relative_path`` 为相对路径，
    ``payload`` 为文本内容。
    输出返回值：无。
    """

    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _initialize_repository(root: Path, *, scanner_exit_code: int = 0) -> None:
    """功能：创建带有可控静态安全门禁的最小 Git 仓库。

    输入参数：``root`` 为目标目录，``scanner_exit_code`` 为合成
    安全扫描脚本的退出码。
    输出返回值：无。
    """

    subprocess.run(
        ["git", "init", "--quiet", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _write_file(
        root,
        "scripts/security/scan_repository.py",
        "RULES = ()\n"
        "MAX_TEXT_FILE_BYTES = 2097152\n"
        "if __name__ == '__main__':\n"
        f"    raise SystemExit({scanner_exit_code})\n",
    )
    _write_file(root, ".gitignore", ".env\n.venv*/\n__pycache__/\nruns/\ncache/\n")


def _rewrite_manifest_and_checksums(
    artifacts,
    manifest: dict[str, object],
) -> None:
    """功能：为敌意验证用例重签 JSON manifest 与 SHA256 sidecar。

    输入参数：``artifacts`` 为真实构建产物，``manifest`` 为已定向篡改的对象。
    输出返回值：无；函数仅用于证明 verifier 不能只依赖 sidecar 摘要。
    """

    archive_payload = artifacts.archive_path.read_bytes()
    archive_sha256 = hashlib.sha256(archive_payload).hexdigest()
    archive_metadata = manifest["archive"]
    assert isinstance(archive_metadata, dict)
    archive_metadata["bytes"] = len(archive_payload)
    archive_metadata["sha256"] = archive_sha256
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    artifacts.manifest_path.write_bytes(manifest_payload)
    artifacts.checksum_path.write_text(
        f"{archive_sha256}  {artifacts.archive_path.name}\n"
        f"{hashlib.sha256(manifest_payload).hexdigest()}  {artifacts.manifest_path.name}\n",
        encoding="ascii",
    )


def _resign_bundle_with_replaced_member(
    bundle,
    artifacts,
    relative_path: str,
    replacement: bytes,
) -> None:
    """功能：替换单个归档成员并重签全部公开摘要元数据。

    输入参数：``bundle`` 为生产模块，``artifacts`` 为已构建三件套，
    ``relative_path`` 为待替换仓库相对路径，``replacement`` 为敌意字节。
    输出返回值：无；仅用于证明 verifier 不能信任被共同重签的清单与 sidecar。
    """

    sources = []
    archive_prefix = f"{bundle.ARCHIVE_ROOT}/"
    with tarfile.open(artifacts.archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            assert member.isreg()
            assert member.name.startswith(archive_prefix)
            extracted = archive.extractfile(member)
            assert extracted is not None
            member_path = member.name[len(archive_prefix) :]
            payload = replacement if member_path == relative_path else extracted.read()
            sources.append(
                bundle.SourceFile(
                    path=member_path,
                    payload=payload,
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
    assert any(source.path == relative_path for source in sources)
    bundle._write_deterministic_archive(artifacts.archive_path, tuple(sources))

    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    entries = manifest["files"]
    assert isinstance(entries, list)
    by_path = {entry["path"]: entry for entry in entries}
    replaced = by_path[relative_path]
    replaced["bytes"] = len(replacement)
    replaced["sha256"] = hashlib.sha256(replacement).hexdigest()
    source_tree = manifest["source_tree"]
    assert isinstance(source_tree, dict)
    source_tree["file_count"] = len(entries)
    source_tree["total_bytes"] = sum(entry["bytes"] for entry in entries)
    source_tree["sha256"] = bundle._source_tree_sha256(
        (entry["path"], entry["bytes"], entry["sha256"]) for entry in entries
    )
    _rewrite_manifest_and_checksums(artifacts, manifest)


def test_builds_and_verifies_cleanroom_bundle_from_dirty_source_tree(
    tmp_path: Path,
) -> None:
    """功能：确认 dirty 工作树可被冻结为可验证且不含私有状态的包。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；任一可观测行为不符合规范时断言失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "pyproject.toml", "[project]\nname='demo'\n")
    _write_file(repository, "src/paraguibench/tracked.py", "VALUE = 1\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "add",
            "pyproject.toml",
            "src/paraguibench/tracked.py",
        ],
        check=True,
    )
    _write_file(repository, "src/paraguibench/untracked.py", "VALUE = 2\n")
    _write_file(repository, "benchmark/tasks/task.json", '{"task_id":"task"}\n')
    _write_file(repository, "configs/examples/README.md", "# Public config policy\n")
    _write_file(repository, "docs/guide.md", "# Guide\n")
    _write_file(repository, "website/src/app.js", "export const ready = true;\n")
    _write_file(repository, ".env.example", "API_KEY=\n")
    _write_file(repository, ".env.local", "API_KEY=private\n")
    _write_file(repository, "docs/.env.example", "API_KEY=private\n")
    _write_file(repository, "runs/private.log", "private run bytes\n")
    _write_file(repository, "cache/private.bin", "private cache bytes\n")
    _write_file(repository, "docs/run_logs/private.md", "private run log\n")
    _write_file(repository, "docs/.cache/private.md", "private cache\n")
    _write_file(repository, "docs/output/private.md", "private output\n")
    _write_file(repository, "benchmark/gold/private.json", '{"private":"gold bytes"}\n')
    _write_file(
        repository, "website/public/credentials.json", '{"credential":"placeholder"}\n'
    )
    token_filename = "".join(("sk", "-proj-", "A" * 32, ".md"))
    _write_file(repository, f"docs/{token_filename}", "credential in filename only\n")

    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    verification = bundle.verify_release_bundle(
        artifacts.archive_path,
        artifacts.manifest_path,
        artifacts.checksum_path,
    )

    assert verification.file_count == artifacts.file_count
    assert verification.archive_sha256 == artifacts.archive_sha256
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    manifest_paths = [entry["path"] for entry in manifest["files"]]
    assert manifest_paths == sorted(manifest_paths)
    assert "src/paraguibench/tracked.py" in manifest_paths
    assert "src/paraguibench/untracked.py" in manifest_paths
    assert "benchmark/tasks/task.json" in manifest_paths
    assert "configs/examples/README.md" in manifest_paths
    assert ".env.example" in manifest_paths
    assert ".env.local" not in manifest_paths
    assert "docs/.env.example" not in manifest_paths
    assert all(not path.startswith(("runs/", "cache/")) for path in manifest_paths)
    assert all("/run_logs/" not in f"/{path}" for path in manifest_paths)
    assert all("/.cache/" not in f"/{path}" for path in manifest_paths)
    assert all("/output/" not in f"/{path}" for path in manifest_paths)
    assert "benchmark/gold/private.json" not in manifest_paths
    assert "website/public/credentials.json" not in manifest_paths
    assert f"docs/{token_filename}" not in manifest_paths

    with tarfile.open(artifacts.archive_path, "r:gz") as archive:
        archive_names = [member.name for member in archive.getmembers()]
    assert archive_names == [f"ParaGUIBench/{path}" for path in manifest_paths]


@pytest.mark.parametrize(
    "payload",
    [
        b"API_KEY=real-value\n",
        b"export API_KEY=\n",
        b"API_KEY=\nAPI_KEY=\n",
        b"INVALID-NAME=\n",
        b"MISSING_EQUALS\n",
        b"API_KEY=\x00\n",
        b"API_KEY=\xff\n",
        b"#" * (64 * 1024 + 1),
    ],
    ids=(
        "non-empty-value",
        "export-prefix",
        "duplicate-name",
        "invalid-name",
        "missing-equals",
        "nul-byte",
        "invalid-utf8",
        "oversized-template",
    ),
)
def test_builder_rejects_unsafe_root_environment_template(
    tmp_path: Path,
    payload: bytes,
) -> None:
    """功能：确认公开根环境模板只能包含注释、空行和唯一空值变量。

    输入参数：``tmp_path`` 为 pytest 临时目录；``payload`` 为非空值、
    export、重复键、非法行、非法编码、NUL 或超限模板之一。
    输出返回值：无；任一不安全模板都必须在创建输出目录前失败关闭。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    (repository / ".env.example").write_bytes(payload)

    with pytest.raises(bundle.ReleaseBundleError, match=r"env\.example"):
        bundle.build_release_bundle(repository, output, name="release-test")

    assert not output.exists()


def test_verifier_rejects_resigned_nonempty_root_environment_template(
    tmp_path: Path,
) -> None:
    """功能：确认共同重签 archive/manifest/sidecar 也不能放宽空模板语义。

    输入参数：``tmp_path`` 为 pytest 提供的临时仓库与输出目录。
    输出返回值：无；verifier 必须独立拒绝带非空值的根环境模板。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    (repository / ".env.example").write_bytes(b"# names only\nAPI_KEY=\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    _resign_bundle_with_replaced_member(
        bundle,
        artifacts,
        ".env.example",
        b"API_KEY=real-value\n",
    )

    with pytest.raises(bundle.ReleaseBundleError, match=r"env\.example"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


@pytest.mark.parametrize("linked_kind", ["archive", "manifest", "checksum"])
def test_verifier_rejects_symlink_release_artifact(
    tmp_path: Path,
    linked_kind: str,
) -> None:
    """功能：确认离线验证器不跟随三件套中的任何符号链接。

    输入参数：``tmp_path`` 为 pytest 临时目录，``linked_kind`` 为被替换的产物类别。
    输出返回值：无；如果 symlink 被当作普通 release 产物接受则失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    original_output = tmp_path / "original"
    linked_output = tmp_path / "linked"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(
        repository, original_output, name="release-test"
    )
    linked_output.mkdir()
    linked_archive = linked_output / artifacts.archive_path.name
    linked_manifest = linked_output / artifacts.manifest_path.name
    linked_checksum = linked_output / artifacts.checksum_path.name
    originals = {
        "archive": artifacts.archive_path,
        "manifest": artifacts.manifest_path,
        "checksum": artifacts.checksum_path,
    }
    linked_paths = {
        "archive": linked_archive,
        "manifest": linked_manifest,
        "checksum": linked_checksum,
    }
    for kind, destination in linked_paths.items():
        if kind == linked_kind:
            destination.symlink_to(originals[kind])
        else:
            shutil.copyfile(originals[kind], destination)

    with pytest.raises(bundle.ReleaseBundleError, match="符号链接"):
        bundle.verify_release_bundle(linked_archive, linked_manifest, linked_checksum)


def test_public_secret_validation_sources_are_not_mistaken_for_credentials(
    tmp_path: Path,
) -> None:
    """功能：确认名称含 secret 的公开验证器源码不会被误删。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；两个必要公开源文件任一缺失时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(
        repository,
        "scripts/installation/verify_secret_file.py",
        "def verify():\n    return True\n",
    )
    _write_file(
        repository,
        "tests/installation/test_verify_secret_file.py",
        "def test_verify():\n    assert True\n",
    )

    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}

    assert "scripts/installation/verify_secret_file.py" in paths
    assert "tests/installation/test_verify_secret_file.py" in paths


def test_methods_services_stack_stays_outside_cleanroom_closure(
    tmp_path: Path,
) -> None:
    """功能：确认方法验证服务栈不进入 cleanroom 闭集，公开部署编排仍进入。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；``deploy/methods-services/`` 出现在 manifest 或
    ``deploy/onlyoffice/compose.yaml`` 缺失时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(
        repository,
        "deploy/methods-services/webmall/demo.sh",
        "# internal methods stack\n",
    )
    _write_file(
        repository,
        "deploy/onlyoffice/compose.yaml",
        "services: {}\n",
    )

    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}

    assert "deploy/methods-services/webmall/demo.sh" not in paths
    assert "deploy/onlyoffice/compose.yaml" in paths


def test_verifier_rejects_duplicate_json_manifest_keys(tmp_path: Path) -> None:
    """功能：确认重复 JSON key 不能通过“后值覆盖前值”混入 release。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；重复 key 清单被接受时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest_payload = artifacts.manifest_path.read_text(encoding="utf-8")
    schema_line = f'  "schema_version": "{bundle.SCHEMA_VERSION}",\n'
    manifest_payload = manifest_payload.replace(
        schema_line,
        schema_line + schema_line,
        1,
    )
    artifacts.manifest_path.write_text(manifest_payload, encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest_payload.encode("utf-8")).hexdigest()
    artifacts.checksum_path.write_text(
        f"{artifacts.archive_sha256}  {artifacts.archive_path.name}\n"
        f"{manifest_sha256}  {artifacts.manifest_path.name}\n",
        encoding="ascii",
    )

    with pytest.raises(bundle.ReleaseBundleError, match="JSON"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


def test_repeated_build_is_byte_identical_despite_source_metadata_changes(
    tmp_path: Path,
) -> None:
    """功能：确认源文件 mtime/mode 变化不改变任何 release 产物字节。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；重建摘要、产物字节或 tar/gzip 元数据漂移时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    _initialize_repository(repository)
    source = repository / "src" / "paraguibench" / "core.py"
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    environment_template = repository / ".env.example"
    environment_template.write_bytes(b"# names only\nAPI_KEY=\n")

    first = bundle.build_release_bundle(repository, first_output, name="release-test")
    source.chmod(0o755)
    os.utime(source, (1_800_000_000, 1_800_000_000))
    environment_template.chmod(0o600)
    os.utime(environment_template, (1_700_000_000, 1_700_000_000))
    second = bundle.build_release_bundle(repository, second_output, name="release-test")

    assert first.archive_sha256 == second.archive_sha256
    assert first.source_tree_sha256 == second.source_tree_sha256
    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.checksum_path.read_bytes() == second.checksum_path.read_bytes()
    gzip_header = first.archive_path.read_bytes()[:10]
    assert int.from_bytes(gzip_header[4:8], "little") == 0
    with tarfile.open(first.archive_path, "r:gz") as archive:
        for member in archive:
            assert member.uid == 0
            assert member.gid == 0
            assert member.mode == 0o644
            assert member.mtime == 0
            assert member.uname == ""
            assert member.gname == ""


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_builder_rejects_non_regular_allowlisted_source(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    """功能：确认白名单路径中的 symlink 和 FIFO 都不能进入 release。

    输入参数：``tmp_path`` 为 pytest 临时目录，``unsafe_kind`` 为合成文件类型。
    输出返回值：无；非普通文件被读取或打包时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    source = repository / "src" / "paraguibench" / "unsafe.py"
    source.parent.mkdir(parents=True)
    if unsafe_kind == "symlink":
        target = repository / "outside.txt"
        target.write_text("outside\n", encoding="utf-8")
        source.symlink_to(target)
    else:
        os.mkfifo(source)

    with pytest.raises(bundle.ReleaseBundleError):
        bundle.build_release_bundle(repository, output, name="release-test")


def test_security_scan_failure_precedes_tree_enumeration_and_output(
    tmp_path: Path,
) -> None:
    """功能：确认静态安全扫描失败时不枚举非法源节点也不创建产物。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；如果扫描不是第一门禁或留下输出则失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository, scanner_exit_code=1)
    unsafe = repository / "src" / "paraguibench" / "unsafe.py"
    unsafe.parent.mkdir(parents=True)
    os.mkfifo(unsafe)

    with pytest.raises(bundle.ReleaseBundleError, match="安全扫描未通过"):
        bundle.build_release_bundle(repository, output, name="release-test")

    assert not output.exists()


@pytest.mark.parametrize(
    "limit_overrides",
    [
        {"max_files": 2},
        {"max_file_bytes": 16},
        {"max_total_bytes": 24},
    ],
)
def test_builder_enforces_file_count_and_byte_limits_before_output(
    tmp_path: Path,
    limit_overrides: dict[str, int],
) -> None:
    """功能：确认文件数、单文件和总字节三类限额均 fail closed。

    输入参数：``tmp_path`` 为 pytest 临时目录，``limit_overrides`` 为本用例收紧的限额。
    输出返回值：无；任一越界源树被打包或留下产物时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, ".gitignore", "")
    _write_file(repository, "src/paraguibench/core.py", "X" * 64)
    limits = bundle.BundleLimits(**limit_overrides)

    with pytest.raises(bundle.ReleaseBundleError, match="上限"):
        bundle.build_release_bundle(
            repository,
            output,
            name="release-test",
            limits=limits,
        )

    assert not output.exists()


@pytest.mark.parametrize("unsafe_path", ["/absolute.py", "../escape.py"])
def test_verifier_rejects_absolute_and_parent_manifest_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    """功能：确认 manifest 中的绝对路径和 ``..`` 路径在解归档前被拒绝。

    输入参数：``tmp_path`` 为 pytest 临时目录，``unsafe_path`` 为合成越界路径。
    输出返回值：无；越界路径能通过离线验证时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = unsafe_path
    manifest["files"].sort(key=lambda entry: entry["path"])
    _rewrite_manifest_and_checksums(artifacts, manifest)

    with pytest.raises(bundle.ReleaseBundleError, match="路径"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


@pytest.mark.parametrize("collision_kind", ["case", "unicode"])
def test_verifier_rejects_casefold_and_unicode_path_collisions(
    tmp_path: Path,
    collision_kind: str,
) -> None:
    """功能：确认大小写折叠和 Unicode NFC 归一化后的路径冲突被拒绝。

    输入参数：``tmp_path`` 为 pytest 临时目录，``collision_kind`` 为冲突类型。
    输出返回值：无；清单能在不同文件系统上解释为同一路径时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    original = next(
        entry
        for entry in manifest["files"]
        if entry["path"] == "src/paraguibench/core.py"
    )
    alias = dict(original)
    if collision_kind == "case":
        alias["path"] = "src/paraguibench/Core.py"
    else:
        original["path"] = "src/paraguibench/café.py"
        alias["path"] = unicodedata.normalize("NFD", "src/paraguibench/café.py")
    manifest["files"].append(alias)
    manifest["files"].sort(key=lambda entry: entry["path"])
    _rewrite_manifest_and_checksums(artifacts, manifest)

    with pytest.raises(bundle.ReleaseBundleError, match="冲突"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.FIFOTYPE])
def test_verifier_rejects_symlink_and_special_tar_members(
    tmp_path: Path,
    member_type: bytes,
) -> None:
    """功能：确认已重签的 tar symlink/FIFO 成员仍无法通过内部结构门禁。

    输入参数：``tmp_path`` 为 pytest 临时目录，``member_type`` 为 tar 非普通类型。
    输出返回值：无；攻击者同时更新归档和 sidecar 后仍必须被拒绝。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    first_path = manifest["files"][0]["path"]
    with artifacts.archive_path.open("wb") as raw_output:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            mtime=0,
            fileobj=raw_output,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
            ) as archive:
                member = tarfile.TarInfo(f"ParaGUIBench/{first_path}")
                member.type = member_type
                member.mode = 0o644
                member.mtime = 0
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.linkname = "target" if member_type == tarfile.SYMTYPE else ""
                archive.addfile(member)
    _rewrite_manifest_and_checksums(artifacts, manifest)

    with pytest.raises(bundle.ReleaseBundleError, match="普通文件"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


def test_verifier_rejects_resigned_nonzero_gzip_mtime(tmp_path: Path) -> None:
    """功能：确认攻击者重签摘要后仍不能改变 gzip header mtime。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；gzip 头的非确定性时间戳未被拒绝时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    archive_payload = bytearray(artifacts.archive_path.read_bytes())
    archive_payload[4:8] = (1_700_000_000).to_bytes(4, "little")
    artifacts.archive_path.write_bytes(archive_payload)
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    _rewrite_manifest_and_checksums(artifacts, manifest)

    with pytest.raises(bundle.ReleaseBundleError, match="mtime"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


def test_verifier_rejects_manifest_top_level_extra_field(tmp_path: Path) -> None:
    """功能：确认 release schema 顶层为闭集而不接受未定义字段。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；扩展字段可绕过当前 schema 时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    manifest["unexpected"] = {}
    _rewrite_manifest_and_checksums(artifacts, manifest)

    with pytest.raises(bundle.ReleaseBundleError, match="schema"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


def test_verifier_rejects_nonfinite_json_number(tmp_path: Path) -> None:
    """功能：确认 Python 默认容忍的 JSON ``NaN`` 不能进入 release schema。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；非标准、非有限数值被解析时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    artifacts = bundle.build_release_bundle(repository, output, name="release-test")
    manifest_payload = artifacts.manifest_path.read_text(encoding="utf-8")
    manifest_payload = manifest_payload.replace(
        f'    "bytes": {artifacts.archive_path.stat().st_size},',
        '    "bytes": NaN,',
        1,
    )
    artifacts.manifest_path.write_text(manifest_payload, encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest_payload.encode("utf-8")).hexdigest()
    artifacts.checksum_path.write_text(
        f"{artifacts.archive_sha256}  {artifacts.archive_path.name}\n"
        f"{manifest_sha256}  {artifacts.manifest_path.name}\n",
        encoding="ascii",
    )

    with pytest.raises(bundle.ReleaseBundleError, match="JSON"):
        bundle.verify_release_bundle(
            artifacts.archive_path,
            artifacts.manifest_path,
            artifacts.checksum_path,
        )


def test_builder_refuses_to_overwrite_existing_release_artifact(tmp_path: Path) -> None:
    """功能：确认同名三件套不会被非原子地覆盖或混用。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；预存产物被替换或新清单被部分写入时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    output.mkdir()
    archive = output / "release-test.tar.gz"
    archive.write_bytes(b"preexisting-sentinel")

    with pytest.raises(bundle.ReleaseBundleError, match="已存在"):
        bundle.build_release_bundle(repository, output, name="release-test")

    assert archive.read_bytes() == b"preexisting-sentinel"
    assert not (output / "release-test.manifest.json").exists()
    assert not (output / "release-test.sha256").exists()


def test_builder_removes_partial_triple_when_manifest_gate_fails(
    tmp_path: Path,
) -> None:
    """功能：确认归档已生成后的 manifest 门禁失败不留下混合产物。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；三件套任一同名文件在失败后残留时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "VALUE = 1\n")
    limits = bundle.BundleLimits(max_manifest_bytes=1)

    with pytest.raises(bundle.ReleaseBundleError, match="JSON 清单"):
        bundle.build_release_bundle(
            repository,
            output,
            name="release-test",
            limits=limits,
        )

    assert output.exists()
    assert not (output / "release-test.tar.gz").exists()
    assert not (output / "release-test.manifest.json").exists()
    assert not (output / "release-test.sha256").exists()


def test_builder_rescans_frozen_bytes_changed_after_repository_scan(
    tmp_path: Path,
) -> None:
    """功能：确认整仓扫描后到冻结前变更的字节仍需通过同规则二次门禁。

    输入参数：``tmp_path`` 为 pytest 提供的临时目录。
    输出返回值：无；扫描子进程退出后才出现的禁止字节进入包时失败。
    """

    bundle = _load_bundle_module()
    repository = tmp_path / "repository"
    output = tmp_path / "output"
    _initialize_repository(repository)
    _write_file(repository, "src/paraguibench/core.py", "SAFE = True\n")
    _write_file(
        repository,
        "scripts/security/scan_repository.py",
        "import re\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "MARKER = ''.join(('FROZEN', '_SECRET', '_MARKER'))\n"
        "RULES = (SimpleNamespace(pattern=re.compile(MARKER)),)\n"
        "MAX_TEXT_FILE_BYTES = 2097152\n"
        "if __name__ == '__main__':\n"
        "    root = Path(sys.argv[sys.argv.index('--root') + 1])\n"
        "    (root / 'src/paraguibench/core.py').write_text(MARKER, encoding='utf-8')\n"
        "    raise SystemExit(0)\n",
    )

    with pytest.raises(bundle.ReleaseBundleError, match="冻结"):
        bundle.build_release_bundle(repository, output, name="release-test")

    assert not output.exists()
