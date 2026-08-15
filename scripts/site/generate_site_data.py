#!/usr/bin/env python3
"""从 benchmark 固定来源生成 GitHub Pages 使用的公共安全数据。"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


RELEASE_MANIFEST = Path("benchmark/manifests/release-v1.json")
RUNTIME_SUPPORT_MANIFEST = Path("benchmark/manifests/runtime-support-v1.json")
RELEASE_ID = "release-v1"
RUNTIME_SUPPORT_ID = "runtime-support-v1"
CANONICAL_TASK_ROOT = Path("benchmark/tasks")
DEFAULT_OUTPUT = Path("website/public/data/site-data.json")
PUBLIC_TASK_FIELDS = (
    "task_id",
    "category",
    "benchmark_group",
    "source",
    "tag",
    "type",
    "environment_protocol",
    "evaluation_protocol",
    "asset_status",
    "local_readiness_status",
    "support_status",
    "blocker_codes",
)
EXPECTED_BENCHMARK_GROUP_COUNTS = {
    "FileOperation": 42,
    "FileSearch": 12,
    "OnlineShopping": 91,
    "SearchAndWrite": 10,
    "WebNavigation": 13,
    "WebSearch": 65,
}

FIELD_LABELS = {
    "task_id": {"en": "Task ID", "zh-CN": "任务 ID"},
    "category": {"en": "Category", "zh-CN": "任务大类"},
    "benchmark_group": {
        "en": "Benchmark group",
        "zh-CN": "论文任务分组",
    },
    "source": {"en": "Source", "zh-CN": "任务来源"},
    "tag": {"en": "Tag", "zh-CN": "任务标签"},
    "type": {"en": "Type", "zh-CN": "任务类型"},
    "environment_protocol": {
        "en": "Environment",
        "zh-CN": "环境协议",
    },
    "evaluation_protocol": {
        "en": "Evaluation",
        "zh-CN": "评价协议",
    },
    "asset_status": {"en": "Asset status", "zh-CN": "资产状态"},
    "local_readiness_status": {
        "en": "Local readiness",
        "zh-CN": "本地就绪度",
    },
    "support_status": {
        "en": "Live support status",
        "zh-CN": "真实环境支持状态",
    },
    "blocker_codes": {"en": "Blockers", "zh-CN": "阻塞项"},
}

VALUE_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "category": {
        "InformationRetrieval": {
            "en": "Information Retrieval",
            "zh-CN": "信息检索",
        },
        "Operation": {"en": "Operation", "zh-CN": "操作"},
    },
    "benchmark_group": {
        "FileOperation": {
            "en": "File Operation",
            "zh-CN": "文件操作",
        },
        "FileSearch": {"en": "File Search", "zh-CN": "文件检索"},
        "OnlineShopping": {
            "en": "Online Shopping",
            "zh-CN": "在线购物",
        },
        "SearchAndWrite": {
            "en": "Search and Write",
            "zh-CN": "检索并写入",
        },
        "WebNavigation": {
            "en": "Web Navigation",
            "zh-CN": "网页导航",
        },
        "WebSearch": {"en": "Web Search", "zh-CN": "网页检索"},
    },
    "source": {
        "OSWorld": {"en": "OSWorld", "zh-CN": "OSWorld"},
        "VeriGUI": {"en": "VeriGUI", "zh-CN": "VeriGUI"},
        "WebMall": {"en": "WebMall", "zh-CN": "WebMall"},
        "self": {"en": "Self-collected", "zh-CN": "自建"},
        "unspecified": {"en": "Unspecified", "zh-CN": "未标注"},
    },
    "tag": {
        "AddToCart": {"en": "Add to Cart", "zh-CN": "加入购物车"},
        "CheapestOfferSpecificRequirements": {
            "en": "Cheapest Offer with Specific Requirements",
            "zh-CN": "特定要求下的最低报价",
        },
        "CheapestOfferVagueRequirements": {
            "en": "Cheapest Offer with Vague Requirements",
            "zh-CN": "模糊要求下的最低报价",
        },
        "CheapestProductSearch": {
            "en": "Cheapest Product Search",
            "zh-CN": "最低价商品检索",
        },
        "Checkout": {"en": "Checkout", "zh-CN": "结账"},
        "EndToEnd": {"en": "End to End", "zh-CN": "端到端购物"},
        "FileOperate": {"en": "File Operation", "zh-CN": "文件操作"},
        "FileSearch": {"en": "File Search", "zh-CN": "文件检索"},
        "FindCompatibleProducts": {
            "en": "Find Compatible Products",
            "zh-CN": "查找兼容商品",
        },
        "FindSubstitutes": {
            "en": "Find Substitutes",
            "zh-CN": "查找替代商品",
        },
        "ProductsFulfillingSpecificRequirements": {
            "en": "Products Fulfilling Specific Requirements",
            "zh-CN": "满足特定要求的商品",
        },
        "ProductsSatisfyingVagueRequirements": {
            "en": "Products Satisfying Vague Requirements",
            "zh-CN": "满足模糊要求的商品",
        },
        "SingleProductSearch": {
            "en": "Single Product Search",
            "zh-CN": "单商品检索",
        },
        "VisualSearch": {"en": "Visual Search", "zh-CN": "视觉检索"},
        "WebOperate": {"en": "Web Operation", "zh-CN": "网页操作"},
        "WebSearch": {"en": "Web Search", "zh-CN": "网页检索"},
    },
    "type": {
        "QA": {"en": "Question Answering", "zh-CN": "问答"},
        "OSWorld脚本": {"en": "OSWorld Script", "zh-CN": "OSWorld 脚本"},
        "OSWorld脚本改造": {
            "en": "Adapted OSWorld Script",
            "zh-CN": "OSWorld 脚本改造",
        },
        "self": {"en": "Self-defined", "zh-CN": "自定义"},
        "unspecified": {"en": "Unspecified", "zh-CN": "未标注"},
    },
    "environment_protocol": {
        "osworld.chrome.v1": {
            "en": "OSWorld Chrome",
            "zh-CN": "OSWorld Chrome 环境",
        },
        "osworld.desktop.v1": {
            "en": "OSWorld Desktop",
            "zh-CN": "OSWorld 桌面环境",
        },
        "webmall.browser.v1": {
            "en": "WebMall Browser",
            "zh-CN": "WebMall 浏览器环境",
        },
    },
    "evaluation_protocol": {
        "paraguibench.operation.eval-rules.v1": {
            "en": "ParaGUIBench Operation Artifact Rules",
            "zh-CN": "ParaGUIBench 操作产物规则评价",
        },
        "paraguibench.operation.xlsx.hide-na-rows.v1": {
            "en": "Spreadsheet NA Row Hiding",
            "zh-CN": "表格 NA 行隐藏评价",
        },
        "paraguibench.operation.image-classification.sha256.v1": {
            "en": "SHA-256 Image Classification",
            "zh-CN": "SHA-256 图像分类评价",
        },
        "paraguibench.operation.cross-document-facts.v1": {
            "en": "Cross-Document Fact Consistency",
            "zh-CN": "跨文档事实一致性评价",
        },
        "paraguibench.operation.searchwrite-xlsx.v1": {
            "en": "Search-to-Spreadsheet",
            "zh-CN": "检索写入表格评价",
        },
        "legacy.operation.eval-rules.v1": {
            "en": "Legacy Operation Rules",
            "zh-CN": "旧版操作规则评价",
        },
        "legacy.osworld.state.v1": {
            "en": "Legacy OSWorld State",
            "zh-CN": "旧版 OSWorld 状态评价",
        },
        "legacy.pipeline-implicit.v1": {
            "en": "Legacy Implicit Pipeline",
            "zh-CN": "旧版隐式流水线评价",
        },
        "legacy.webmall.bookmark-url-set.v1": {
            "en": "Legacy WebMall Bookmark Set",
            "zh-CN": "旧版 WebMall 书签集合评价",
        },
        "legacy.webmall.cart.v1": {
            "en": "Legacy WebMall Cart",
            "zh-CN": "旧版 WebMall 购物车评价",
        },
        "legacy.webmall.checkout.v1": {
            "en": "Legacy WebMall Checkout",
            "zh-CN": "旧版 WebMall 结账评价",
        },
        "legacy.webnavigate.bookmark.v1": {
            "en": "Legacy Web Navigation Bookmark",
            "zh-CN": "旧版网页导航书签评价",
        },
        "paraguibench.answer.exact.v1": {
            "en": "Exact Answer",
            "zh-CN": "精确答案评价",
        },
        "paraguibench.answer.implicit-structured.v1": {
            "en": "Implicit Structured Answer",
            "zh-CN": "隐式结构化答案评价",
        },
        "paraguibench.answer.keyed-numeric-set.v1": {
            "en": "Keyed Numeric Set",
            "zh-CN": "带键数值集合评价",
        },
        "paraguibench.answer.numeric.v1": {
            "en": "Numeric Answer",
            "zh-CN": "数值答案评价",
        },
        "paraguibench.answer.ordered-structured.v1": {
            "en": "Ordered Structured Answer",
            "zh-CN": "有序结构化答案评价",
        },
        "paraguibench.osworld.chrome-profile-name.v1": {
            "en": "OSWorld Chrome Profile Name",
            "zh-CN": "OSWorld Chrome Profile 名称评价",
        },
        "paraguibench.osworld.chrome-bookmarks.v1": {
            "en": "OSWorld Chrome Bookmarks",
            "zh-CN": "OSWorld Chrome 书签评价",
        },
        "paraguibench.osworld.google-shopping-active-tab.v1": {
            "en": "OSWorld Google Shopping Active Tab",
            "zh-CN": "OSWorld Google Shopping 活动页评价",
        },
        "paraguibench.osworld.artifact-state.v1": {
            "en": "OSWorld Artifact State",
            "zh-CN": "OSWorld 产物状态评价",
        },
        "paraguibench.webmall.checkout.closed-world.v1": {
            "en": "WebMall Closed-World Checkout",
            "zh-CN": "WebMall 结账闭集评价",
        },
        "paraguibench.webmall.find-and-order.closed-world.v1": {
            "en": "WebMall Find-and-Order Closed World",
            "zh-CN": "WebMall 报告与订单闭集评价",
        },
        "paraguibench.webmall.checkout.closed-world.v2": {
            "en": "WebMall Checkout + Billing Closed World",
            "zh-CN": "WebMall 结账与账单资料闭集评价",
        },
        "paraguibench.webmall.find-and-order.closed-world.v2": {
            "en": "WebMall Find-and-Order + Billing Closed World",
            "zh-CN": "WebMall 报告、订单与账单资料闭集评价",
        },
        "paraguibench.webmall.url-multiset.v1": {
            "en": "WebMall URL Multiset",
            "zh-CN": "WebMall URL 多集合评价",
        },
        "paraguibench.webmall.cart.closed-world.v1": {
            "en": "WebMall Cart Closed World",
            "zh-CN": "WebMall 购物车闭集评价",
        },
    },
    "asset_status": {
        "legacy_remote_reference": {
            "en": "Legacy remote reference",
            "zh-CN": "旧版远程资产引用",
        },
        "no_task_assets_declared": {
            "en": "No task assets declared",
            "zh-CN": "未声明任务资产",
        },
        "pinned_download_manifest": {
            "en": "Pinned download manifest",
            "zh-CN": "已固定下载清单",
        },
    },
    "support_status": {
        "blocked": {"en": "Blocked", "zh-CN": "尚未闭环"},
        "live_validated": {
            "en": "Live validated",
            "zh-CN": "真实环境已验证",
        },
    },
    "local_readiness_status": {
        "local_components_incomplete": {
            "en": "Local components incomplete",
            "zh-CN": "本地组件未闭合",
        },
        "local_ready": {
            "en": "Local components ready (not live-validated)",
            "zh-CN": "本地组件已闭合（非实机验证）",
        },
    },
    "blocker_codes": {
        "osworld_vm_image_materialization_unverified": {
            "en": (
                "Verified VM image recipe is awaiting a reproducible "
                "materialization receipt."
            ),
            "zh-CN": "已验证的虚拟机镜像 recipe 正等待可重现物化回执。",
        },
        "legacy_asset_manifest_not_migrated": {
            "en": "Legacy asset manifest not migrated",
            "zh-CN": "旧版资产清单尚未迁移",
        },
        "legacy_evaluator_not_migrated": {
            "en": "Legacy evaluator not migrated",
            "zh-CN": "旧版评价器尚未迁移",
        },
        "osworld_bookmark_start_context_not_migrated": {
            "en": "OSWorld bookmark start context not migrated",
            "zh-CN": "OSWorld 书签任务启动上下文尚未迁移",
        },
        "osworld_artifact_getter_live_validation_not_completed": {
            "en": "OSWorld artifact getter live validation not completed",
            "zh-CN": "OSWorld 产物读取器尚未完成真实环境验证",
        },
        "osworld_artifact_gold_live_validation_not_completed": {
            "en": "OSWorld evaluator gold live validation not completed",
            "zh-CN": "OSWorld 评价器 gold 尚未完成真实环境验证",
        },
        "osworld_artifact_finalize_not_migrated": {
            "en": "OSWorld artifact finalize action not migrated",
            "zh-CN": "OSWorld 产物收尾动作尚未迁移",
        },
        "osworld_task_setup_live_validation_not_completed": {
            "en": "OSWorld task setup live validation not completed",
            "zh-CN": "OSWorld 任务准备协议尚未完成真实环境验证",
        },
        "osworld_source_start_context_ambiguous": {
            "en": "OSWorld source start context is ambiguous",
            "zh-CN": "OSWorld 来源任务启动上下文存在歧义",
        },
        "osworld_artifact_input_path_inferred": {
            "en": "OSWorld artifact input location is inferred",
            "zh-CN": "OSWorld 产物输入位置仅为推断",
        },
        "osworld_artifact_input_license_unverified": {
            "en": "OSWorld artifact input license is unverified",
            "zh-CN": "OSWorld 产物输入许可尚未核验",
        },
        "webmall_cart_reader_reference_live_validation_not_completed": {
            "en": ("WebMall cart reader reference live validation not completed"),
            "zh-CN": "WebMall 购物车读取器尚未完成参考环境真实验证",
        },
        "pipeline_implicit_input_asset_metadata_unverified": {
            "en": "Pipeline input asset metadata unverified",
            "zh-CN": "隐式流水线输入资产元数据未核验",
        },
        "pipeline_implicit_gold_asset_metadata_unverified": {
            "en": "Pipeline gold asset metadata unverified",
            "zh-CN": "隐式流水线 gold 资产元数据未核验",
        },
        "pipeline_implicit_typed_observation_parser_not_migrated": {
            "en": "Pipeline typed observation parser not migrated",
            "zh-CN": "隐式流水线强类型观测解析器尚未迁移",
        },
        "pipeline_implicit_live_validation_not_completed": {
            "en": "Pipeline implicit live validation not completed",
            "zh-CN": "隐式流水线尚未完成真实环境验证",
        },
        "pipeline_implicit_combination_gold_conflict_unresolved": {
            "en": "CombinationDocs gold conflict unresolved",
            "zh-CN": "CombinationDocs gold 冲突尚未解决",
        },
        "operation_word009_010_writer_live_validation_not_completed": {
            "en": "Word-009/010 Writer live validation not completed",
            "zh-CN": "Word-009/010 Writer 真实环境验证尚未完成",
        },
        "operation_word012_abbreviation_semantics_not_migrated": {
            "en": "Word-012 abbreviation semantics contract not migrated",
            "zh-CN": "Word-012 缩写语义契约尚未迁移",
        },
        "combinationdocs003_real_render_validation_not_completed": {
            "en": "CombinationDocs-003 real render validation not completed",
            "zh-CN": "CombinationDocs-003 真实渲染验证尚未完成",
        },
        "versioned_live_validation_not_completed": {
            "en": "Versioned live validation not completed",
            "zh-CN": "未完成带版本向量的真实环境验证",
        },
    },
}


class SiteDataError(RuntimeError):
    """表示站点数据来源不完整、不一致或不符合公开契约。"""


def build_site_data(repo_root: Path) -> dict[str, Any]:
    """构造 GitHub Pages 可公开读取的任务数据。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
    输出返回值：
        仅含公开任务身份与支持状态的 JSON 对象。
    """

    root = repo_root.resolve()
    release = _load_json_object(root / RELEASE_MANIFEST, "release manifest")
    runtime = _load_json_object(
        root / RUNTIME_SUPPORT_MANIFEST,
        "runtime support manifest",
    )
    if release.get("release_id") != RELEASE_ID:
        raise SiteDataError("release manifest 身份无效")
    if runtime.get("manifest_id") != RUNTIME_SUPPORT_ID:
        raise SiteDataError("runtime support manifest 身份无效")
    release_entries = _require_task_list(release, "release manifest")
    runtime_entries = _require_task_list(runtime, "runtime support manifest")
    _require_declared_count(
        release,
        "task_count",
        len(release_entries),
        "release manifest",
    )
    _require_declared_count(
        runtime,
        "canonical_task_count",
        len(runtime_entries),
        "runtime support manifest",
    )
    release_sha256 = _sha256_file(root / RELEASE_MANIFEST)
    if runtime.get("release_manifest_sha256") != release_sha256:
        raise SiteDataError("runtime support 引用的 release 摘要与当前来源不一致")
    release_by_id = _index_by_task_id(release_entries, "release task")
    runtime_by_id = _index_by_task_id(
        runtime_entries,
        "runtime support task",
    )
    if set(release_by_id) != set(runtime_by_id):
        raise SiteDataError("两个输入清单的 canonical task 集合不一致")

    tasks = []
    for task_id in sorted(release_by_id):
        canonical = _load_canonical_metadata(
            root,
            release_by_id[task_id],
            task_id,
        )
        support = runtime_by_id[task_id]
        blocker_codes = support.get("blocker_codes")
        if not isinstance(blocker_codes, list) or not all(
            isinstance(code, str) and code for code in blocker_codes
        ):
            raise SiteDataError("runtime support task 缺少有效的 blocker_codes")
        if len(blocker_codes) != len(set(blocker_codes)):
            raise SiteDataError("runtime support task 含重复 blocker code")
        for blocker_code in blocker_codes:
            _require_public_value(
                blocker_code,
                "blocker_codes",
                "runtime support task",
            )
        category = task_id.split("-", maxsplit=1)[0]
        tasks.append(
            {
                "task_id": task_id,
                "category": _require_public_value(
                    category,
                    "category",
                    "canonical task",
                ),
                "benchmark_group": _derive_benchmark_group(task_id),
                "source": _public_dimension_value(
                    canonical.get("task_source"),
                    "source",
                ),
                "tag": _public_dimension_value(
                    canonical.get("task_tag"),
                    "tag",
                ),
                "type": _public_dimension_value(
                    canonical.get("task_type"),
                    "type",
                ),
                "environment_protocol": _require_public_record_value(
                    support,
                    "environment_protocol",
                    "runtime support task",
                ),
                "evaluation_protocol": _require_public_record_value(
                    support,
                    "evaluation_protocol",
                    "runtime support task",
                ),
                "asset_status": _require_public_record_value(
                    support,
                    "asset_status",
                    "runtime support task",
                ),
                "local_readiness_status": _require_public_record_value(
                    support,
                    "local_readiness_status",
                    "runtime support task",
                ),
                "support_status": _require_public_record_value(
                    support,
                    "support_status",
                    "runtime support task",
                ),
                "blocker_codes": sorted(blocker_codes),
            }
        )

    _validate_benchmark_group_counts(tasks)
    summary = _build_summary(tasks)
    _require_declared_status_counts(
        runtime,
        "local_readiness_status_counts",
        summary["local_readiness_status_counts"],
        "runtime support manifest",
    )
    return {
        "schema_version": 1,
        "dataset_id": "paraguibench-site-data-v1",
        "input_manifests": {
            "release": {
                "id": _require_string(
                    release,
                    "release_id",
                    "release manifest",
                ),
                "sha256": release_sha256,
                "task_count": len(release_entries),
            },
            "runtime_support": {
                "id": _require_string(
                    runtime,
                    "manifest_id",
                    "runtime support manifest",
                ),
                "sha256": _sha256_file(root / RUNTIME_SUPPORT_MANIFEST),
                "task_count": len(runtime_entries),
            },
        },
        "labels": _build_labels(tasks),
        "summary": summary,
        "tasks": tasks,
    }


def serialize_site_data(data: dict[str, Any]) -> bytes:
    """将站点数据编码为稳定、可审阅的 UTF-8 JSON。

    输入参数：
        data：``build_site_data`` 返回的公开数据对象。
    输出返回值：
        key 排序、两空格缩进、以换行结尾的 UTF-8 字节。
    """

    text = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{text}\n".encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    """生成或检查 GitHub Pages 的派生数据文件。

    输入参数：
        argv：可选命令行参数；省略时读取当前进程参数。
    输出返回值：
        生成或检查成功返回 0，来源错误返回 2，数据漂移返回 1。
    """

    parser = argparse.ArgumentParser(
        description="生成 ParaGUIBench GitHub Pages 公共安全数据"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="ParaGUIBench 仓库根目录",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出文件；相对路径以仓库根目录为基准",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查落盘文件是否与来源一致，不写文件",
    )
    arguments = parser.parse_args(argv)
    root = arguments.repo_root.resolve()
    output_path = arguments.output
    if not output_path.is_absolute():
        output_path = root / output_path

    try:
        expected = serialize_site_data(build_site_data(root))
    except SiteDataError as error:
        print(f"站点数据来源校验失败：{error}", file=sys.stderr)
        return 2

    if arguments.check:
        try:
            actual = output_path.read_bytes()
        except OSError:
            print("站点数据缺失或不可读", file=sys.stderr)
            return 1
        if actual != expected:
            print("站点数据已偏离当前来源，请重新生成", file=sys.stderr)
            return 1
        print("站点数据检查通过")
        return 0

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(expected)
    except OSError:
        print("站点数据无法写入", file=sys.stderr)
        return 2
    print("站点数据生成完成")
    return 0


def _index_by_task_id(
    entries: list[dict[str, Any]],
    label: str,
) -> dict[str, dict[str, Any]]:
    """按 task_id 建立索引，并拒绝重复身份。

    输入参数：
        entries：清单中的任务 object 列表。
        label：可安全显示的记录类型。
    输出返回值：
        从 task_id 到原始任务 object 的映射。
    """

    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        task_id = _require_task_id(entry, label)
        if task_id in index:
            raise SiteDataError(f"{label} 含重复 task_id")
        index[task_id] = entry
    return index


def _load_canonical_metadata(
    repo_root: Path,
    release_entry: dict[str, Any],
    expected_task_id: str,
) -> dict[str, Any]:
    """安全加载 release 条目对应的 canonical task。

    输入参数：
        repo_root：已解析的仓库根目录。
        release_entry：release 中的单个任务条目。
        expected_task_id：release 条目声明的任务身份。
    输出返回值：
        canonical task JSON object；调用方只可提取公开白名单字段。
    """

    relative_path = _require_string(release_entry, "path", "release task")
    task_root = (repo_root / CANONICAL_TASK_ROOT).resolve()
    task_path = (repo_root / relative_path).resolve()
    try:
        task_path.relative_to(task_root)
    except ValueError as error:
        raise SiteDataError("release task 指向 canonical 目录之外") from error
    expected_sha256 = _require_string(
        release_entry,
        "sha256",
        "release task",
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise SiteDataError("release task 缺少有效的 sha256")
    if _sha256_file(task_path) != expected_sha256:
        raise SiteDataError("canonical task 内容偏离 release 摘要")
    task = _load_json_object(task_path, "canonical task")
    if task.get("task_id") != expected_task_id:
        raise SiteDataError("canonical task 身份与 release 不一致")
    return task


def _public_dimension_value(value: object, dimension: str) -> str:
    """将 canonical 分类值规范成适合公开筛选的非空字符串。

    输入参数：
        value：canonical task 中的 source、tag 或 type 值。
        dimension：对应的公开维度名称。
    输出返回值：
        经公开白名单确认的字符串；空字符串统一为 ``unspecified``。
    """

    if not isinstance(value, str):
        raise SiteDataError("canonical task 的分类元数据必须是字符串")
    normalized = value if value else "unspecified"
    return _require_public_value(
        normalized,
        dimension,
        "canonical task",
    )


def _derive_benchmark_group(task_id: str) -> str:
    """按照论文口径从 canonical task_id 推导六类任务分组。

    输入参数：
        task_id：已通过公开格式校验的 canonical 任务 ID。
    输出返回值：
        ``WebSearch``、``FileSearch``、``OnlineShopping``、
        ``FileOperation``、``WebNavigation`` 或 ``SearchAndWrite``。
    """

    if "SearchAndWrite" in task_id:
        group = "SearchAndWrite"
    elif task_id.startswith(
        (
            "InformationRetrieval-VisualSearch-",
            "InformationRetrieval-WebSearch-",
        )
    ):
        group = "WebSearch"
    elif task_id.startswith("InformationRetrieval-FileSearch-"):
        group = "FileSearch"
    elif task_id.startswith("Operation-OnlineShopping-"):
        group = "OnlineShopping"
    elif task_id.startswith("Operation-FileOperate-"):
        group = "FileOperation"
    elif task_id.startswith("Operation-WebOperate-"):
        group = "WebNavigation"
    else:
        raise SiteDataError("canonical task 无法映射到论文任务分组")
    return _require_public_value(
        group,
        "benchmark_group",
        "canonical task",
    )


def _validate_benchmark_group_counts(
    tasks: list[dict[str, Any]],
) -> None:
    """断言六类论文分组与固定 233 项 release 口径完全一致。

    输入参数：
        tasks：已完成安全投影的 233 条公开任务行。
    输出返回值：
        无；任一分组数量漂移时抛出不回显任务内容的 ``SiteDataError``。
    """

    actual = dict(sorted(Counter(task["benchmark_group"] for task in tasks).items()))
    if actual != EXPECTED_BENCHMARK_GROUP_COUNTS:
        raise SiteDataError("论文任务分组计数偏离固定 release 口径")


def _require_public_record_value(
    record: dict[str, Any],
    field: str,
    label: str,
) -> str:
    """从记录中读取一个必须位于公开标签白名单的字符串。

    输入参数：
        record：待检查的 JSON object。
        field：字段名，同时也是 ``VALUE_LABELS`` 的维度名。
        label：可安全显示的来源名称。
    输出返回值：
        经白名单确认、可写入公开数据的字符串。
    """

    value = _require_string(record, field, label)
    return _require_public_value(value, field, label)


def _require_public_value(
    value: str,
    dimension: str,
    label: str,
) -> str:
    """确认一个分类值已经过显式双语标注与公开审阅。

    输入参数：
        value：待公开的机器值。
        dimension：``VALUE_LABELS`` 中的分类维度。
        label：可安全显示的来源名称。
    输出返回值：
        原字符串；未知值会被拒绝且不会在异常中回显。
    """

    if value not in VALUE_LABELS.get(dimension, {}):
        raise SiteDataError(f"{label} 含未审阅的 {dimension} 值")
    return value


def _build_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总页面筛选和统计卡片需要的全部计数。

    输入参数：
        tasks：已完成安全投影的公开任务行。
    输出返回值：
        任务总数及各公开维度的确定性计数字典。
    """

    dimensions = (
        "category",
        "benchmark_group",
        "source",
        "tag",
        "type",
        "environment_protocol",
        "evaluation_protocol",
        "asset_status",
        "local_readiness_status",
        "support_status",
    )
    summary: dict[str, Any] = {"task_count": len(tasks)}
    for dimension in dimensions:
        summary[f"{dimension}_counts"] = _sorted_counts(
            task[dimension] for task in tasks
        )
    support_counts = summary["support_status_counts"]
    summary["support_status_counts"] = {
        status: support_counts.get(status, 0)
        for status in sorted(VALUE_LABELS["support_status"])
    }
    local_counts = summary["local_readiness_status_counts"]
    summary["local_readiness_status_counts"] = {
        status: local_counts.get(status, 0)
        for status in sorted(VALUE_LABELS["local_readiness_status"])
    }
    summary["blocker_code_counts"] = _sorted_counts(
        code for task in tasks for code in task["blocker_codes"]
    )
    return summary


def _sorted_counts(values: Any) -> dict[str, int]:
    """按值汇总并稳定排序计数。

    输入参数：
        values：字符串值的可迭代对象。
    输出返回值：
        key 按字典序排列的计数字典。
    """

    return dict(sorted(Counter(values).items()))


def _build_labels(
    tasks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """为全部公开字段和观测到的分类值生成中英双语标签。

    输入参数：
        tasks：已完成安全投影的公开任务行。
    输出返回值：
        ``fields`` 与 ``values`` 两层双语标签映射。
    """

    observed = {
        "category": {task["category"] for task in tasks},
        "benchmark_group": {task["benchmark_group"] for task in tasks},
        "source": {task["source"] for task in tasks},
        "tag": {task["tag"] for task in tasks},
        "type": {task["type"] for task in tasks},
        "environment_protocol": {task["environment_protocol"] for task in tasks},
        "evaluation_protocol": {task["evaluation_protocol"] for task in tasks},
        "asset_status": {task["asset_status"] for task in tasks},
        "local_readiness_status": {task["local_readiness_status"] for task in tasks},
        "support_status": {task["support_status"] for task in tasks},
        "blocker_codes": {code for task in tasks for code in task["blocker_codes"]},
    }
    value_labels = {
        dimension: {
            value: _label_for_value(dimension, value) for value in sorted(values)
        }
        for dimension, values in observed.items()
    }
    return {
        "fields": {field: FIELD_LABELS[field] for field in PUBLIC_TASK_FIELDS},
        "values": value_labels,
    }


def _label_for_value(dimension: str, value: str) -> dict[str, str]:
    """返回某个分类值的双语标签，并为未来新值提供安全回退。

    输入参数：
        dimension：公开分类维度名称。
        value：该维度的稳定机器值。
    输出返回值：
        同时包含 ``en`` 和 ``zh-CN`` 的已审阅非空标签。
    """

    known = VALUE_LABELS.get(dimension, {}).get(value)
    if known is None:
        raise SiteDataError("公开分类值缺少双语标签")
    return known


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    """加载 JSON object，并隐藏本地路径与原始解析异常。

    输入参数：
        path：待读取的 JSON 文件。
        label：可安全显示的来源名称。
    输出返回值：
        解析后的 JSON object。
    """

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SiteDataError(f"{label} 无法读取或不是有效 JSON") from error
    if not isinstance(value, dict):
        raise SiteDataError(f"{label} 顶层必须是 JSON object")
    return value


def _sha256_file(path: Path) -> str:
    """计算来源文件的 SHA-256，供页面数据记录输入摘要。

    输入参数：
        path：待读取的来源文件。
    输出返回值：
        小写十六进制 SHA-256；读取失败时抛出安全错误。
    """

    try:
        content = path.read_bytes()
    except OSError as error:
        raise SiteDataError("输入清单无法读取") from error
    return hashlib.sha256(content).hexdigest()


def _require_task_list(
    manifest: dict[str, Any],
    label: str,
) -> list[dict[str, Any]]:
    """提取并校验清单的任务 object 列表。

    输入参数：
        manifest：已加载的清单 JSON object。
        label：可安全显示的来源名称。
    输出返回值：
        任务 object 列表。
    """

    entries = manifest.get("tasks")
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) for entry in entries
    ):
        raise SiteDataError(f"{label} 的 tasks 必须是 object 列表")
    return entries


def _require_declared_count(
    manifest: dict[str, Any],
    field: str,
    actual_count: int,
    label: str,
) -> None:
    """校验清单声明的任务总数与其条目数量一致。

    输入参数：
        manifest：待校验清单。
        field：清单中的总数字段名。
        actual_count：从 tasks 列表得到的实际数量。
        label：可安全显示的来源名称。
    输出返回值：
        无；声明缺失或不一致时抛出 ``SiteDataError``。
    """

    declared_count = manifest.get(field)
    if (
        not isinstance(declared_count, int)
        or isinstance(declared_count, bool)
        or declared_count != actual_count
    ):
        raise SiteDataError(f"{label} 的 {field} 与任务条目不一致")


def _require_declared_status_counts(
    manifest: dict[str, Any],
    field: str,
    actual_counts: dict[str, int],
    label: str,
) -> None:
    """校验清单根状态计数与每任务安全投影完全一致。

    输入参数：
        manifest：待校验的 runtime-support 清单。
        field：清单根计数字段名。
        actual_counts：从 233 个任务条目重新汇总的完整计数。
        label：可安全显示的来源名称。
    输出返回值：
        无；字段缺失、键闭集、整数类型或计数任一漂移时
        抛出 ``SiteDataError``。
    """

    declared_counts = manifest.get(field)
    if (
        not isinstance(declared_counts, dict)
        or set(declared_counts) != set(actual_counts)
        or any(type(value) is not int for value in declared_counts.values())
        or declared_counts != actual_counts
    ):
        raise SiteDataError(f"{label} 的 local-readiness 根计数与任务投影不一致")


def _require_string(
    record: dict[str, Any],
    field: str,
    label: str,
) -> str:
    """读取必需字符串字段，不在异常中回显其值。

    输入参数：
        record：待检查的 JSON object。
        field：必需字段名。
        label：可安全显示的记录类型。
    输出返回值：
        非空字符串字段值。
    """

    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise SiteDataError(f"{label} 缺少有效的 {field}")
    return value


def _require_task_id(record: dict[str, Any], label: str) -> str:
    """读取并校验可安全公开的 canonical task_id。

    输入参数：
        record：release 或 runtime support 的任务条目。
        label：可安全显示的来源名称。
    输出返回值：
        仅由字母、数字和分段连字符组成的 task_id。
    """

    task_id = _require_string(record, "task_id", label)
    if not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+", task_id):
        raise SiteDataError(f"{label} 含无效 task_id")
    return task_id


if __name__ == "__main__":
    raise SystemExit(main())
