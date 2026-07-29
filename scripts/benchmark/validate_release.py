#!/usr/bin/env python3
"""校验 ParaGUIBench canonical benchmark 发布清单。

该脚本仅依赖 Python 标准库，既可供 CI 调用，也可在发布前独立运行。
它不会输出任务正文、答案或环境地址，只报告结构性错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


EXPECTED_RELEASE_ID = "release-v1"
EXPECTED_TASK_COUNT = 233
EXPECTED_FIXTURE_IDS = {
    "webmall.checkout-profile.synthetic-public.v1",
}
CHECKOUT_FIXTURE_ID = "webmall.checkout-profile.synthetic-public.v1"
CHECKOUT_TASK_TAGS = {"Checkout", "EndToEnd"}
EXPECTED_CHECKOUT_TASK_COUNT = 16
CHECKOUT_FIXTURE_SCHEMA_REF = (
    "../../schemas/webmall-checkout-fixture-v1.schema.json"
)
CHECKOUT_TEST_CARD_NUMBER = "4242424242424242"
CHECKOUT_SCHEMA_FILES = {
    "benchmark/schemas/webmall-checkout-fixture-v1.schema.json": (
        "urn:paraguibench:schema:webmall-checkout-fixture:v1"
    ),
    "benchmark/schemas/webmall-checkout-task-v1.schema.json": (
        "urn:paraguibench:schema:webmall-checkout-task:v1"
    ),
}
CHECKOUT_SHIPPING_FIELDS = {
    "name",
    "email",
    "street",
    "house_number",
    "zip",
    "city",
    "state",
    "country",
}
CHECKOUT_PAYMENT_FIELDS = {
    "type",
    "card_number",
    "cvv",
    "expiry_date",
}
EXCLUDED_TASK_IDS = {
    "Operation-FileOperate-Coding-001",
    "Operation-FileOperate-Coding-002",
    "Operation-FileOperate-Coding-003",
    "Operation-FileOperate-Coding-004",
    "Operation-FileOperate-Coding-005",
}


@dataclass(slots=True)
class ValidationResult:
    """保存一次发布校验的结构化结果。

    输入参数：
        task_count：磁盘上成功识别的任务数量。
        errors：发现的错误消息列表。
    输出返回值：
        该数据类本身不执行校验；调用方可读取 ``ok`` 判断是否通过。
    """

    task_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """判断校验是否通过。

        输入参数：
            无。
        输出返回值：
            当且仅当没有发现错误时返回 ``True``。
        """

        return not self.errors


def _load_json(path: Path) -> Any:
    """读取并解析 UTF-8 JSON 文件。

    输入参数：
        path：待解析 JSON 文件的路径。
    输出返回值：
        解析后的 Python 对象。
    """

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _sha256(path: Path) -> str:
    """计算文件的 SHA-256 摘要。

    输入参数：
        path：待计算摘要的文件路径。
    输出返回值：
        小写十六进制 SHA-256 字符串。
    """

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_repo_path(repo_root: Path, relative_path: str) -> Path | None:
    """将清单中的相对路径安全解析到仓库内。

    输入参数：
        repo_root：仓库根目录。
        relative_path：清单声明的仓库相对路径。
    输出返回值：
        路径位于仓库内部时返回解析后的绝对路径，否则返回 ``None``。
    """

    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return candidate


def _validate_checkout_fixture_profile(
    fixture_id: str,
    fixture: dict[str, Any],
    result: ValidationResult,
) -> None:
    """校验 WebMall checkout fixture v1 的公开数据契约。

    输入参数：
        fixture_id：清单声明的 fixture 身份。
        fixture：已解析的 fixture JSON object。
        result：用于累积全部结构性错误的校验结果。
    输出返回值：
        无；版本、用途或 profile 结构不符合协议时追加错误消息。
    """

    expected_scalars = {
        "$schema": CHECKOUT_FIXTURE_SCHEMA_REF,
        "schema_version": 1,
        "fixture_type": "checkout_profile",
        "intended_use": "benchmark_testing_only",
    }
    for field_name, expected_value in expected_scalars.items():
        if fixture.get(field_name) != expected_value:
            result.errors.append(
                f"fixture {fixture_id} 的 {field_name} 不符合 checkout v1 协议"
            )

    profile = fixture.get("profile")
    if not isinstance(profile, dict):
        result.errors.append(f"fixture {fixture_id} 的 profile 必须是 object")
        return

    shipping_address = profile.get("shipping_address")
    if not isinstance(shipping_address, dict):
        result.errors.append(
            f"fixture {fixture_id} 缺少 object 类型 shipping_address"
        )
    else:
        missing_shipping = sorted(
            CHECKOUT_SHIPPING_FIELDS - set(shipping_address)
        )
        if missing_shipping:
            result.errors.append(
                f"fixture {fixture_id} 的 shipping_address 缺少："
                + ", ".join(missing_shipping)
            )
        invalid_shipping = sorted(
            field_name
            for field_name in CHECKOUT_SHIPPING_FIELDS & set(shipping_address)
            if not isinstance(shipping_address[field_name], str)
            or not shipping_address[field_name]
        )
        if invalid_shipping:
            result.errors.append(
                f"fixture {fixture_id} 的 shipping_address 字段必须为非空字符串："
                + ", ".join(invalid_shipping)
            )
        email = shipping_address.get("email")
        if isinstance(email, str) and not email.endswith("@example.invalid"):
            result.errors.append(
                f"fixture {fixture_id} 的 email 必须使用 example.invalid 保留域"
            )

    payment_method = profile.get("payment_method")
    if not isinstance(payment_method, dict):
        result.errors.append(
            f"fixture {fixture_id} 缺少 object 类型 payment_method"
        )
    else:
        missing_payment = sorted(
            CHECKOUT_PAYMENT_FIELDS - set(payment_method)
        )
        if missing_payment:
            result.errors.append(
                f"fixture {fixture_id} 的 payment_method 缺少："
                + ", ".join(missing_payment)
            )
        invalid_payment = sorted(
            field_name
            for field_name in CHECKOUT_PAYMENT_FIELDS & set(payment_method)
            if not isinstance(payment_method[field_name], str)
            or not payment_method[field_name]
        )
        if invalid_payment:
            result.errors.append(
                f"fixture {fixture_id} 的 payment_method 字段必须为非空字符串："
                + ", ".join(invalid_payment)
            )
        if payment_method.get("type") != "credit_card":
            result.errors.append(
                f"fixture {fixture_id} 的 payment_method.type 必须为 credit_card"
            )
        if payment_method.get("card_number") != CHECKOUT_TEST_CARD_NUMBER:
            result.errors.append(
                f"fixture {fixture_id} 的 card_number 必须使用固定公开测试号"
            )


def _validate_checkout_schema_files(
    repo_root: Path,
    result: ValidationResult,
) -> None:
    """校验 checkout v1 的两个公开 JSON Schema 资产。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        result：用于累积全部结构性错误的校验结果。
    输出返回值：
        无；schema 缺失、无效或身份不一致时追加错误消息。
    """

    for relative_path, expected_schema_id in CHECKOUT_SCHEMA_FILES.items():
        schema_path = _resolve_repo_path(repo_root, relative_path)
        if schema_path is None or not schema_path.is_file():
            result.errors.append(f"checkout schema 不存在：{relative_path}")
            continue
        try:
            schema = _load_json(schema_path)
        except (OSError, json.JSONDecodeError) as error:
            result.errors.append(
                f"checkout schema 不是有效 JSON：{relative_path}：{error}"
            )
            continue
        if not isinstance(schema, dict):
            result.errors.append(
                f"checkout schema 根节点必须是 object：{relative_path}"
            )
            continue
        if schema.get("$schema") != (
            "https://json-schema.org/draft/2020-12/schema"
        ):
            result.errors.append(
                f"checkout schema dialect 不正确：{relative_path}"
            )
        if schema.get("$id") != expected_schema_id:
            result.errors.append(
                f"checkout schema 身份不正确：{relative_path}"
            )


def _validate_release_fixtures(
    repo_root: Path,
    manifest: dict[str, Any],
    result: ValidationResult,
) -> dict[str, dict[str, Any]]:
    """校验发布清单固定的版本化 fixture 并返回其内容。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        manifest：已解析的 release-v1 清单。
        result：用于累积全部结构性错误的校验结果。
    输出返回值：
        通过路径、摘要和身份检查后，以 ``fixture_id`` 索引的内容。
    """

    fixture_root_value = manifest.get("fixture_root")
    if not isinstance(fixture_root_value, str) or not fixture_root_value:
        result.errors.append("fixture_root 必须是非空仓库相对路径")
        return {}
    fixture_root = _resolve_repo_path(repo_root, fixture_root_value)
    if fixture_root is None:
        result.errors.append("fixture_root 不得指向仓库外部")
        return {}
    if not fixture_root.is_dir():
        result.errors.append(f"fixture 目录不存在：{fixture_root_value}")
        return {}

    fixture_entries = manifest.get("fixtures")
    if not isinstance(fixture_entries, list):
        result.errors.append("fixtures 必须是列表")
        return {}

    loaded_fixtures: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(fixture_entries):
        if not isinstance(entry, dict):
            result.errors.append(f"fixtures[{index}] 必须是 JSON object")
            continue
        fixture_id = entry.get("fixture_id")
        relative_path = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(fixture_id, str) or not fixture_id:
            result.errors.append(f"fixtures[{index}] 缺少非空 fixture_id")
            continue
        if fixture_id in loaded_fixtures:
            result.errors.append(f"发布清单包含重复 fixture_id：{fixture_id}")
            continue
        if not isinstance(relative_path, str) or not relative_path:
            result.errors.append(f"fixture {fixture_id} 缺少非空 path")
            continue
        fixture_path = _resolve_repo_path(repo_root, relative_path)
        if fixture_path is None:
            result.errors.append(f"fixture {fixture_id} 的 path 指向仓库外部")
            continue
        try:
            fixture_path.relative_to(fixture_root)
        except ValueError:
            result.errors.append(
                f"fixture {fixture_id} 必须位于 fixture_root 内"
            )
            continue
        if not fixture_path.is_file():
            result.errors.append(f"fixture {fixture_id} 引用的文件不存在")
            continue
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            result.errors.append(f"fixture {fixture_id} 缺少有效 sha256")
        elif _sha256(fixture_path) != expected_hash:
            result.errors.append(f"fixture {fixture_id} 的 sha256 不匹配")

        try:
            fixture = _load_json(fixture_path)
        except (OSError, json.JSONDecodeError) as error:
            result.errors.append(f"fixture {fixture_id} 不是有效 JSON：{error}")
            continue
        if not isinstance(fixture, dict):
            result.errors.append(f"fixture {fixture_id} 根节点必须是 JSON object")
            continue
        if fixture.get("fixture_id") != fixture_id:
            result.errors.append(f"fixture {fixture_id} 的内部身份不一致")
        if (
            fixture.get("data_classification")
            != "synthetic_public_test_data"
        ):
            result.errors.append(
                f"fixture {fixture_id} 的 data_classification 必须为 "
                "synthetic_public_test_data"
            )
        if fixture.get("task_storage_policy") != "reference_only":
            result.errors.append(
                f"fixture {fixture_id} 的 task_storage_policy 必须为 "
                "reference_only"
            )
        if fixture_id == CHECKOUT_FIXTURE_ID:
            _validate_checkout_fixture_profile(fixture_id, fixture, result)
        loaded_fixtures[fixture_id] = fixture

    if set(loaded_fixtures) != EXPECTED_FIXTURE_IDS:
        result.errors.append("release-v1 的 fixture 集合与规范不一致")
    return loaded_fixtures


def _validate_checkout_task_references(
    disk_tasks: dict[str, tuple[Path, dict[str, Any]]],
    release_fixtures: dict[str, dict[str, Any]],
    result: ValidationResult,
) -> None:
    """校验 checkout task 仅通过模板引用公开合成 fixture。

    输入参数：
        disk_tasks：以 ``task_id`` 索引的 canonical task。
        release_fixtures：已通过基本发布校验的 fixture 内容。
        result：用于累积全部结构性错误的校验结果。
    输出返回值：
        无；发现内嵌资料、模板或引用错误时追加错误消息。
    """

    checkout_tasks = [
        task
        for _, task in disk_tasks.values()
        if task.get("task_source") == "WebMall"
        and task.get("task_tag") in CHECKOUT_TASK_TAGS
    ]
    if len(checkout_tasks) != EXPECTED_CHECKOUT_TASK_COUNT:
        result.errors.append(
            "WebMall checkout/end-to-end 任务数量应为 "
            f"{EXPECTED_CHECKOUT_TASK_COUNT}，实际为 {len(checkout_tasks)}"
        )

    expected_ref = {
        "binding": "checkout_profile",
        "fixture_id": CHECKOUT_FIXTURE_ID,
    }
    for task in checkout_tasks:
        task_id = task.get("task_id", "<unknown>")
        embedded_fields = sorted(
            field
            for field in ("instruction", "user_details", "payment_info")
            if field in task
        )
        if embedded_fields:
            result.errors.append(
                f"任务 {task_id} 不得内嵌 checkout 字段："
                + ", ".join(embedded_fields)
            )

        instruction_template = task.get("instruction_template")
        if (
            not isinstance(instruction_template, str)
            or instruction_template.count("{{checkout_profile}}") != 1
        ):
            result.errors.append(
                f"任务 {task_id} 的 instruction_template 必须且只能包含一个 "
                "{{checkout_profile}}"
            )

        fixture_ref = task.get("fixture_ref")
        if fixture_ref != expected_ref:
            result.errors.append(
                f"任务 {task_id} 必须引用 {CHECKOUT_FIXTURE_ID}"
            )
        elif fixture_ref["fixture_id"] not in release_fixtures:
            result.errors.append(
                f"任务 {task_id} 引用的 checkout fixture 未通过发布校验"
            )


def validate_release(repo_root: Path, manifest_path: Path) -> ValidationResult:
    """校验 canonical tasks 与发布清单的一致性。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        manifest_path：``release-v1.json`` 清单路径。
    输出返回值：
        包含任务数量和全部结构性错误的 ``ValidationResult``。
    """

    result = ValidationResult()
    try:
        manifest = _load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as error:
        result.errors.append(f"无法读取发布清单：{error}")
        return result

    if not isinstance(manifest, dict):
        result.errors.append("发布清单根节点必须是 JSON object")
        return result

    if manifest.get("release_id") != EXPECTED_RELEASE_ID:
        result.errors.append(
            f"release_id 必须为 {EXPECTED_RELEASE_ID!r}"
        )
    if manifest.get("task_count") != EXPECTED_TASK_COUNT:
        result.errors.append(
            f"task_count 必须为 {EXPECTED_TASK_COUNT}"
        )

    task_root_value = manifest.get("task_root")
    if not isinstance(task_root_value, str) or not task_root_value:
        result.errors.append("task_root 必须是非空仓库相对路径")
        return result

    task_root = _resolve_repo_path(repo_root, task_root_value)
    if task_root is None:
        result.errors.append("task_root 不得指向仓库外部")
        return result
    if not task_root.is_dir():
        result.errors.append(f"任务目录不存在：{task_root_value}")
        return result

    release_fixtures = _validate_release_fixtures(
        repo_root,
        manifest,
        result,
    )
    _validate_checkout_schema_files(repo_root, result)

    task_files = sorted(task_root.glob("*.json"))
    result.task_count = len(task_files)
    if result.task_count != EXPECTED_TASK_COUNT:
        result.errors.append(
            f"磁盘任务数量应为 {EXPECTED_TASK_COUNT}，实际为 {result.task_count}"
        )

    disk_tasks: dict[str, tuple[Path, dict[str, Any]]] = {}
    task_uids: set[str] = set()
    for task_path in task_files:
        try:
            task = _load_json(task_path)
        except (OSError, json.JSONDecodeError) as error:
            result.errors.append(f"{task_path.name} 不是有效 JSON：{error}")
            continue
        if not isinstance(task, dict):
            result.errors.append(f"{task_path.name} 根节点必须是 JSON object")
            continue

        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            result.errors.append(f"{task_path.name} 缺少非空 task_id")
            continue
        if task_id in disk_tasks:
            result.errors.append(f"发现重复 task_id：{task_id}")
            continue
        if task_path.stem != task_id:
            result.errors.append(
                f"文件名与 task_id 不一致：{task_path.name}"
            )
        if task_id in EXCLUDED_TASK_IDS:
            result.errors.append(f"正式发布中包含被排除任务：{task_id}")

        task_uid = task.get("task_uid")
        if not isinstance(task_uid, str) or not task_uid:
            result.errors.append(f"{task_path.name} 缺少非空 task_uid")
        elif task_uid in task_uids:
            result.errors.append(f"发现重复 task_uid：{task_uid}")
        else:
            task_uids.add(task_uid)
        disk_tasks[task_id] = (task_path, task)

    _validate_checkout_task_references(
        disk_tasks,
        release_fixtures,
        result,
    )

    manifest_tasks = manifest.get("tasks")
    if not isinstance(manifest_tasks, list):
        result.errors.append("tasks 必须是列表")
        manifest_tasks = []
    if len(manifest_tasks) != EXPECTED_TASK_COUNT:
        result.errors.append(
            f"清单任务条目应为 {EXPECTED_TASK_COUNT}，实际为 {len(manifest_tasks)}"
        )

    manifest_task_ids: set[str] = set()
    for index, entry in enumerate(manifest_tasks):
        if not isinstance(entry, dict):
            result.errors.append(f"tasks[{index}] 必须是 JSON object")
            continue
        task_id = entry.get("task_id")
        relative_path = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(task_id, str) or not task_id:
            result.errors.append(f"tasks[{index}] 缺少非空 task_id")
            continue
        if task_id in manifest_task_ids:
            result.errors.append(f"清单包含重复 task_id：{task_id}")
            continue
        manifest_task_ids.add(task_id)

        if not isinstance(relative_path, str) or not relative_path:
            result.errors.append(f"清单任务 {task_id} 缺少非空 path")
            continue
        referenced_path = _resolve_repo_path(repo_root, relative_path)
        if referenced_path is None:
            result.errors.append(f"清单任务 {task_id} 的 path 指向仓库外部")
            continue
        if not referenced_path.is_file():
            result.errors.append(f"清单任务 {task_id} 引用的文件不存在")
            continue
        disk_entry = disk_tasks.get(task_id)
        if disk_entry is None:
            result.errors.append(f"清单任务 {task_id} 不在 canonical tasks 中")
        elif disk_entry[0].resolve() != referenced_path:
            result.errors.append(f"清单任务 {task_id} 引用了错误文件")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            result.errors.append(f"清单任务 {task_id} 缺少有效 sha256")
        elif _sha256(referenced_path) != expected_hash:
            result.errors.append(f"清单任务 {task_id} 的 sha256 不匹配")

    disk_task_ids = set(disk_tasks)
    missing_from_manifest = sorted(disk_task_ids - manifest_task_ids)
    extra_in_manifest = sorted(manifest_task_ids - disk_task_ids)
    if missing_from_manifest:
        result.errors.append(
            f"清单遗漏 {len(missing_from_manifest)} 个 canonical task"
        )
    if extra_in_manifest:
        result.errors.append(
            f"清单多出 {len(extra_in_manifest)} 个不存在的 task"
        )

    task_id_mapping = manifest.get("task_id_mapping")
    if not isinstance(task_id_mapping, dict):
        result.errors.append("task_id_mapping 必须是 JSON object")
    else:
        invalid_targets = {
            target
            for target in task_id_mapping.values()
            if not isinstance(target, str) or target not in disk_task_ids
        }
        if invalid_targets:
            result.errors.append(
                f"task_id_mapping 有 {len(invalid_targets)} 个无效目标"
            )
        mapping_targets = {
            target
            for target in task_id_mapping.values()
            if isinstance(target, str)
        }
        if mapping_targets != disk_task_ids:
            result.errors.append(
                "task_id_mapping 的 canonical 目标集合与正式任务集合不一致"
            )

    declared_exclusions = manifest.get("excluded_task_ids")
    if declared_exclusions != sorted(EXCLUDED_TASK_IDS):
        result.errors.append("excluded_task_ids 与 release-v1 规范不一致")

    return result


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。

    输入参数：
        无；参数来自当前进程命令行。
    输出返回值：
        包含仓库根目录和清单路径的 ``argparse.Namespace``。
    """

    default_repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="校验 ParaGUIBench release-v1 benchmark 清单"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=default_repo_root,
        help="ParaGUIBench 仓库根目录",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="发布清单路径；默认使用 benchmark/manifests/release-v1.json",
    )
    return parser.parse_args()


def main() -> int:
    """执行命令行校验并返回进程退出码。

    输入参数：
        无；通过命令行参数确定仓库与清单位置。
    输出返回值：
        校验通过返回 ``0``，否则返回 ``1``。
    """

    args = _parse_args()
    repo_root = args.repo_root.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest is not None
        else repo_root / "benchmark" / "manifests" / "release-v1.json"
    )
    result = validate_release(repo_root, manifest_path)
    if result.ok:
        print(
            f"{EXPECTED_RELEASE_ID} validation passed: "
            f"{result.task_count} canonical tasks"
        )
        return 0

    print(
        f"{EXPECTED_RELEASE_ID} validation failed with "
        f"{len(result.errors)} error(s):",
        file=sys.stderr,
    )
    for error in result.errors:
        print(f"- {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
