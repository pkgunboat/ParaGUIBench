#!/usr/bin/env python3
"""生成并校验 ParaGUIBench preview 的逐任务 runtime support 清单。

该工具只读取 canonical release 与任务元数据，不改写任务文件。生成结果
刻意把“任务已经发布”和“任务已经通过真实运行验证”拆成两个独立字段。
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any


MANIFEST_ID = "runtime-support-v1"
RELEASE_ID = "release-v1"
LIVE_VALIDATED_TASK_IDS = {
    "InformationRetrieval-FileSearch-Readonly-001",
}
DEFAULT_RELEASE_PATH = Path("benchmark/manifests/release-v1.json")
DEFAULT_OUTPUT_PATH = Path("benchmark/manifests/runtime-support-v1.json")
SCHEMA_REFERENCE = "../schemas/runtime-support-v1.schema.json"
SCHEMA_PATH = Path("benchmark/schemas/runtime-support-v1.schema.json")
SCHEMA_ID = "urn:paraguibench:schema:runtime-support:v1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class RuntimeSupportError(RuntimeError):
    """表示 runtime support 来源或输出不符合固定 preview 契约。"""


@dataclass(slots=True)
class ValidationResult:
    """保存一次 runtime support 校验的结构化结果。

    输入参数：
        task_count：待校验清单中成功识别的任务数量。
        status_counts：按 ``support_status`` 汇总的条目数。
        errors：不包含任务正文或敏感值的错误消息列表。
    输出返回值：
        数据类本身；调用方可通过 ``ok`` 判断校验是否通过。
    """

    task_count: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """判断校验是否通过。

        输入参数：
            无。
        输出返回值：
            没有发现错误时返回 ``True``，否则返回 ``False``。
        """

        return not self.errors


def build_runtime_support_manifest(repo_root: Path) -> dict[str, Any]:
    """从 canonical release 确定性构造逐任务支持清单。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
    输出返回值：
        可直接序列化的 runtime-support-v1 JSON object；不包含任务正文、
        答案、地址、凭据或运行日志。
    """

    root = repo_root.resolve()
    release_path = root / DEFAULT_RELEASE_PATH
    release = _load_json_object(release_path, "canonical release")
    if release.get("release_id") != RELEASE_ID:
        raise RuntimeSupportError("canonical release_id 不符合预期")
    release_entries = release.get("tasks")
    if not isinstance(release_entries, list):
        raise RuntimeSupportError("canonical release tasks 必须是列表")

    entries = [
        _build_task_entry(
            _load_canonical_task(root, release_entry),
        )
        for release_entry in release_entries
    ]
    entries.sort(key=lambda entry: entry["task_id"])
    return {
        "$schema": SCHEMA_REFERENCE,
        "schema_version": 1,
        "manifest_id": MANIFEST_ID,
        "release_id": RELEASE_ID,
        "release_manifest_sha256": _sha256_file(release_path),
        "canonical_task_count": len(entries),
        "tasks": entries,
    }


def validate_runtime_support_manifest(
    repo_root: Path,
    manifest_path: Path | None = None,
) -> ValidationResult:
    """独立校验落盘清单与 canonical 元数据的确定性推导完全一致。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        manifest_path：待校验清单；省略时使用默认 runtime-support 路径。
    输出返回值：
        包含任务数、状态计数和全部结构性错误的 ``ValidationResult``。
    """

    result = ValidationResult()
    root = repo_root.resolve()
    target_path = (
        manifest_path
        if manifest_path is not None
        else root / DEFAULT_OUTPUT_PATH
    )
    try:
        expected = build_runtime_support_manifest(root)
        actual = _load_json_object(target_path, "runtime support manifest")
    except RuntimeSupportError as error:
        result.errors.append(str(error))
        return result

    _validate_schema_asset(root, actual, result)
    actual_entries = actual.get("tasks")
    if isinstance(actual_entries, list):
        result.task_count = len(actual_entries)
        result.status_counts = dict(
            sorted(
                Counter(
                    entry.get("support_status")
                    for entry in actual_entries
                    if isinstance(entry, dict)
                    and isinstance(entry.get("support_status"), str)
                ).items()
            )
        )
    else:
        result.errors.append("runtime support tasks 必须是列表")
        actual_entries = []

    expected_root_fields = {
        key: value for key, value in expected.items() if key != "tasks"
    }
    actual_root_fields = {
        key: value for key, value in actual.items() if key != "tasks"
    }
    if actual_root_fields != expected_root_fields:
        result.errors.append("runtime support 根元数据与确定性推导不一致")

    _validate_task_entries(
        actual_entries,
        expected["tasks"],
        result,
    )
    return result


def _validate_schema_asset(
    repo_root: Path,
    manifest: dict[str, Any],
    result: ValidationResult,
) -> None:
    """校验对应 JSON Schema 存在且身份与清单引用一致。

    输入参数：
        repo_root：已解析的仓库根目录。
        manifest：待校验 runtime support JSON object。
        result：用于累积错误的校验结果。
    输出返回值：
        无；schema 缺失、无效或身份错误时向 ``result`` 追加错误。
    """

    schema_path = repo_root / SCHEMA_PATH
    try:
        schema = _load_json_object(schema_path, "runtime support schema")
    except RuntimeSupportError as error:
        result.errors.append(str(error))
        return
    if schema.get("$id") != SCHEMA_ID:
        result.errors.append("runtime support schema 身份无效")
    if manifest.get("$schema") != SCHEMA_REFERENCE:
        result.errors.append("runtime support manifest 的 $schema 引用无效")


def _validate_task_entries(
    actual_entries: list[object],
    expected_entries: list[dict[str, Any]],
    result: ValidationResult,
) -> None:
    """逐项比较落盘支持状态与 canonical 确定性推导。

    输入参数：
        actual_entries：落盘清单中的任务条目。
        expected_entries：由 canonical 元数据重新推导的任务条目。
        result：用于累积错误的校验结果。
    输出返回值：
        无；遗漏、重复、误标或字段漂移时追加安全错误消息。
    """

    actual_by_id: dict[str, dict[str, Any]] = {}
    invalid_entry_count = 0
    duplicate_ids: set[str] = set()
    for entry in actual_entries:
        if not isinstance(entry, dict):
            invalid_entry_count += 1
            continue
        task_id = entry.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            invalid_entry_count += 1
            continue
        if task_id in actual_by_id:
            duplicate_ids.add(task_id)
        actual_by_id[task_id] = entry
    if invalid_entry_count:
        result.errors.append(
            f"runtime support 含 {invalid_entry_count} 个无效任务条目"
        )
    if duplicate_ids:
        result.errors.append(
            f"runtime support 含 {len(duplicate_ids)} 个重复 task_id"
        )

    expected_by_id = {
        entry["task_id"]: entry for entry in expected_entries
    }
    missing_ids = set(expected_by_id) - set(actual_by_id)
    unexpected_ids = set(actual_by_id) - set(expected_by_id)
    if missing_ids:
        result.errors.append(
            f"runtime support 遗漏 {len(missing_ids)} 个 canonical task"
        )
    if unexpected_ids:
        result.errors.append(
            f"runtime support 含 {len(unexpected_ids)} 个非 canonical task"
        )

    drifted_ids = [
        task_id
        for task_id in sorted(set(expected_by_id) & set(actual_by_id))
        if actual_by_id[task_id] != expected_by_id[task_id]
    ]
    if drifted_ids:
        result.errors.append(
            f"runtime support 有 {len(drifted_ids)} 个任务状态偏离确定性推导"
        )


def _load_canonical_task(
    repo_root: Path,
    release_entry: object,
) -> dict[str, Any]:
    """加载一个 release 条目指向的 canonical task。

    输入参数：
        repo_root：已解析的仓库根目录。
        release_entry：release ``tasks`` 列表中的单个条目。
    输出返回值：
        task_id 与 release 条目一致的任务 JSON object。
    """

    if not isinstance(release_entry, dict):
        raise RuntimeSupportError("canonical release task 条目必须是 object")
    task_id = release_entry.get("task_id")
    relative_path = release_entry.get("path")
    expected_digest = release_entry.get("sha256")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeSupportError("canonical release task_id 无效")
    if not isinstance(relative_path, str) or not relative_path:
        raise RuntimeSupportError("canonical release task path 无效")
    if (
        not isinstance(expected_digest, str)
        or _SHA256_PATTERN.fullmatch(expected_digest) is None
    ):
        raise RuntimeSupportError("canonical release task SHA-256 无效")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeSupportError("canonical release task path 不得越界")
    task_path = (repo_root / relative).resolve()
    try:
        task_path.relative_to(repo_root)
    except ValueError as error:
        raise RuntimeSupportError("canonical release task path 不得越界") from error
    task = _load_json_object(task_path, "canonical task")
    if _sha256_file(task_path) != expected_digest:
        raise RuntimeSupportError("canonical task 与 release 摘要不一致")
    if task.get("task_id") != task_id:
        raise RuntimeSupportError("canonical task_id 与 release 条目不一致")
    return task


def _build_task_entry(task: dict[str, Any]) -> dict[str, Any]:
    """把一个 canonical task 投影为无敏感值的支持状态条目。

    输入参数：
        task：已验证身份的 canonical task JSON object。
    输出返回值：
        包含环境、评价、资产和 runtime readiness 的稳定元数据。
    """

    task_id = task["task_id"]
    is_live_validated = task_id in LIVE_VALIDATED_TASK_IDS
    evaluation_protocol = _derive_evaluation_protocol(task)
    asset_status = _derive_asset_status(task)
    if is_live_validated:
        support_status = "live_validated"
        support_reason_code = "live_validation_passed"
        blocker_codes: list[str] = []
    else:
        support_status = "blocked"
        support_reason_code = "runtime_components_incomplete"
        blocker_codes = []
        if not evaluation_protocol.startswith("paraguibench."):
            blocker_codes.append("legacy_evaluator_not_migrated")
        if asset_status == "legacy_remote_reference":
            blocker_codes.append("legacy_asset_manifest_not_migrated")
        blocker_codes.append("live_validation_not_completed")

    return {
        "task_id": task_id,
        "canonical_status": "published",
        "environment_protocol": _derive_environment_protocol(task),
        "evaluation_protocol": evaluation_protocol,
        "asset_status": asset_status,
        "support_status": support_status,
        "support_reason_code": support_reason_code,
        "blocker_codes": blocker_codes,
    }


def _derive_environment_protocol(task: dict[str, Any]) -> str:
    """根据稳定任务来源与标签推导所需环境协议。

    输入参数：
        task：canonical task JSON object。
    输出返回值：
        WebMall 浏览器、OSWorld Chrome 或 OSWorld 桌面协议标识。
    """

    if task.get("task_source") == "WebMall":
        return "webmall.browser.v1"
    if task.get("task_tag") in {"WebSearch", "WebOperate"}:
        return "osworld.chrome.v1"
    return "osworld.desktop.v1"


def _derive_evaluation_protocol(
    task: dict[str, Any],
) -> str:
    """从现有 evaluator 元数据推导评价协议，不猜测尚未迁移的能力。

    输入参数：
        task：canonical task JSON object。
    输出返回值：
        已迁移 QA 的原生协议，或带 ``legacy.`` 前缀的待迁移协议。
    """

    if task.get("task_type") == "QA" and task.get("task_source") != "WebMall":
        match_mode = str(
            task.get("answer_match_mode") or "implicit-structured"
        ).strip().lower().replace("_", "-")
        if match_mode == "strict-exact":
            match_mode = "exact"
        return f"paraguibench.answer.{match_mode}.v1"

    evaluator_path = task.get("evaluator_path")
    if isinstance(evaluator_path, str) and evaluator_path:
        normalized_path = evaluator_path.lower()
        path_protocols = (
            ("file_search_readonly", "legacy.file-search-readonly.v1"),
            ("webnavigate_bookmark", "legacy.webnavigate.bookmark.v1"),
            ("string_url_evaluator", "legacy.webmall.bookmark-url-set.v1"),
            ("cart_evaluator", "legacy.webmall.cart.v1"),
            ("checkout_evaluator", "legacy.webmall.checkout.v1"),
        )
        for marker, protocol in path_protocols:
            if marker in normalized_path:
                return protocol
        if "osworld" in normalized_path:
            return "legacy.osworld.state.v1"
        return "legacy.python-reference.v1"

    if isinstance(task.get("eval_rules"), list) and task["eval_rules"]:
        return "legacy.operation.eval-rules.v1"
    match_mode = task.get("answer_match_mode")
    if isinstance(match_mode, str) and match_mode:
        safe_mode = match_mode.strip().lower().replace("_", "-")
        return f"legacy.answer.{safe_mode}.v1"
    return "legacy.pipeline-implicit.v1"


def _derive_asset_status(task: dict[str, Any]) -> str:
    """根据任务中的资产声明推导迁移状态。

    输入参数：
        task：canonical task JSON object。
    输出返回值：
        固定下载清单、legacy 远程引用或未声明任务资产三种状态之一。
    """

    if isinstance(task.get("asset_manifest"), str) and task["asset_manifest"]:
        return "pinned_download_manifest"
    if (
        isinstance(task.get("prepare_script_path"), str)
        and task["prepare_script_path"]
    ):
        return "legacy_remote_reference"
    return "no_task_assets_declared"


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    """读取 UTF-8 JSON object，并避免在错误中回显数据正文。

    输入参数：
        path：待读取文件路径。
        label：用于安全错误消息的逻辑名称。
    输出返回值：
        解析后的 JSON object。
    """

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeSupportError(
            f"{label} 无法解析：{type(error).__name__}"
        ) from None
    if not isinstance(value, dict):
        raise RuntimeSupportError(f"{label} 根节点必须是 object")
    return value


def _sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256。

    输入参数：
        path：待摘要的普通文件。
    输出返回值：
        64 位小写十六进制 SHA-256 字符串。
    """

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_arguments() -> argparse.Namespace:
    """解析命令行参数。

    输入参数：
        无；参数从当前进程命令行读取。
    输出返回值：
        包含子命令和仓库路径的 ``argparse.Namespace``。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("generate", "validate"),
        help="生成或独立校验 runtime-support-v1 清单",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="ParaGUIBench 仓库根目录",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="目标清单路径；相对路径按仓库根目录解析",
    )
    return parser.parse_args()


def main() -> int:
    """执行确定性清单生成命令。

    输入参数：
        无；使用 ``_parse_arguments`` 返回的命令行参数。
    输出返回值：
        生成成功返回 0；契约错误时由异常终止并返回非零。
    """

    arguments = _parse_arguments()
    root = arguments.repo_root.resolve()
    target_path = arguments.manifest
    if target_path is None:
        target_path = root / DEFAULT_OUTPUT_PATH
    elif not target_path.is_absolute():
        target_path = root / target_path

    if arguments.command == "generate":
        manifest = build_runtime_support_manifest(root)
        target_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            "runtime-support-v1 generated: "
            f"tasks={manifest['canonical_task_count']}"
        )
        return 0

    result = validate_runtime_support_manifest(root, target_path)
    if result.ok:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in result.status_counts.items()
        )
        print(
            f"runtime-support-v1 valid: tasks={result.task_count}; {counts}"
        )
        return 0
    for error in result.errors:
        print(f"ERROR: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
