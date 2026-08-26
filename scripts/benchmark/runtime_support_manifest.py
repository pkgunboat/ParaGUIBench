#!/usr/bin/env python3
"""生成并校验 ParaGUIBench preview 的逐任务 runtime support 清单。

该工具只读取 canonical release 与任务元数据，不改写任务文件。生成结果
刻意把“任务已发布”、“本地组件已闭合”和“已通过真实运行验证”
拆成三个独立层级。
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifestError,
    load_osworld_image_manifest,
)
from paraguibench.integrations.osworld.task_prepare import (
    OSWORLD_BOOKMARK_START_CONTEXT_SPECS,
)
from paraguibench.runstore import RunVersionVector
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runstore.versioning import validate_run_version_vector
from paraguibench.runtime.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
    ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
    ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
    ArtifactFamilyTaskPrepareCapabilityError,
    inspect_artifact_family_task_prepare_capability,
)
from paraguibench.runtime.assets import (
    AssetManifestError,
    TaskAssetMode,
    load_asset_manifest_bytes,
    read_manifest_bytes_nofollow,
    resolve_task_assets,
)
from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    GoldManifestError,
    load_gold_asset_manifest,
)
from paraguibench.runtime.osworld_environment import (
    OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS,
)
from paraguibench.runtime.osworld_gold import (
    OSWorldGoldBindingError,
    TaskGoldMode,
    bind_osworld_task_gold,
)
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
)
from paraguibench.runtime.osworld_artifact_component_receipts import (
    OSWorldArtifactComponentReceiptError,
    load_trusted_osworld_artifact_component_receipts,
)
from paraguibench.runtime.pipeline_implicit_binding import (
    PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS,
    PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS,
    PipelineImplicitRuntimeBlockedError,
    PipelineImplicitRuntimeManifestError,
    preflight_pipeline_implicit_local_runtime,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    PIPELINE_IMPLICIT_COMPONENT_TASK_IDS,
    PipelineImplicitComponentReceiptError,
    load_trusted_pipeline_implicit_component_receipts,
)
from paraguibench.runtime.run_versioning import (
    RunVersioningError,
    validate_loaded_package_matches_repository,
)
from paraguibench.runtime.webmall_cart_component_receipts import (
    WEBMALL_CART_COMPONENT_TASK_IDS,
    WebMallCartComponentReceiptError,
    has_current_webmall_cart_component_receipt,
)


MANIFEST_ID = "runtime-support-v1"
RELEASE_ID = "release-v1"
LIVE_VALIDATION_RECEIPT_ROOT = Path("benchmark/provenance/live-validation-receipts")
LIVE_VALIDATION_RECEIPT_ALLOWLIST_PATH = Path(
    "benchmark/provenance/live-validation-receipt-allowlist-v1.json"
)
OSWORLD_ARTIFACT_STATE_PROTOCOL_ID = "paraguibench.osworld.artifact-state.v1"
OSWORLD_CHROME_BOOKMARKS_PROTOCOL_ID = "paraguibench.osworld.chrome-bookmarks.v1"
OPERATION_PROTOCOL_ID = "paraguibench.operation.eval-rules.v1"
WEBMALL_CART_PROTOCOL_ID = "paraguibench.webmall.cart.closed-world.v1"
WEBMALL_CART_READER_LIVE_BLOCKER = (
    "webmall_cart_reader_reference_live_validation_not_completed"
)
OSWORLD_VM_IMAGE_LIVE_BLOCKER = "osworld_vm_image_materialization_unverified"
VERSIONED_LIVE_VALIDATION_BLOCKER = "versioned_live_validation_not_completed"
OPERATION_WORD009_010_TEXT_FIDELITY_BLOCKER = (
    "operation_word009_010_writer_live_validation_not_completed"
)
COMBINATIONDOCS003_REAL_RENDER_BLOCKER = (
    "combinationdocs003_real_render_validation_not_completed"
)
LOCAL_READY_STATUS = "local_ready"
LOCAL_COMPONENTS_INCOMPLETE_STATUS = "local_components_incomplete"
_LIVE_ONLY_BLOCKER_CODES = frozenset(
    {
        OSWORLD_VM_IMAGE_LIVE_BLOCKER,
        VERSIONED_LIVE_VALIDATION_BLOCKER,
        "osworld_artifact_getter_live_validation_not_completed",
        "osworld_artifact_gold_live_validation_not_completed",
        "osworld_task_setup_live_validation_not_completed",
        WEBMALL_CART_READER_LIVE_BLOCKER,
        "pipeline_implicit_live_validation_not_completed",
        OPERATION_WORD009_010_TEXT_FIDELITY_BLOCKER,
        COMBINATIONDOCS003_REAL_RENDER_BLOCKER,
    }
)
_OPERATION_WORD_TEXT_FIDELITY_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperationWord-009",
        "Operation-FileOperate-BatchOperationWord-010",
    }
)
_COMBINATIONDOCS003_TASK_ID = "Operation-FileOperate-CombinationDocs-003"
_NATIVE_PIPELINE_IMPLICIT_BINDINGS: dict[str, dict[str, str]] = {
    "Operation-FileOperate-BatchOperationExcel-008": {
        "protocol_id": "paraguibench.operation.xlsx.hide-na-rows.v1",
        "task_uid": "1c73128f-a5ef-4a97-97ce-ef427d6d46b4",
        "task_type": "OSWorld脚本改造",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
    "Operation-FileOperate-BatchOperationPPT-003": {
        "protocol_id": ("paraguibench.operation.image-classification.sha256.v1"),
        "task_uid": "e544ee0f-90e6-43a4-9958-6b74e88d94a6",
        "task_type": "self",
        "task_source": "",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
    "Operation-FileOperate-CombinationDocs-002": {
        "protocol_id": "paraguibench.operation.cross-document-facts.v1",
        "task_uid": "6bf5b1c9-a2a2-4901-bbe3-631a33da45e8",
        "task_type": "self",
        "task_source": "",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
    "Operation-FileOperate-SearchAndWrite-008": {
        "protocol_id": "paraguibench.operation.searchwrite-xlsx.v1",
        "task_uid": "65a4848d-b4b2-4173-8308-a0213fdafbd0",
        "task_type": "",
        "task_source": "self",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
}
_PIPELINE_IMPLICIT_PROTOCOL_IDS = frozenset(
    binding["protocol_id"] for binding in _NATIVE_PIPELINE_IMPLICIT_BINDINGS.values()
)
_PIPELINE_IMPLICIT_BASE_BLOCKERS = (
    "pipeline_implicit_input_asset_metadata_unverified",
    "pipeline_implicit_gold_asset_metadata_unverified",
    "pipeline_implicit_typed_observation_parser_not_migrated",
    "pipeline_implicit_live_validation_not_completed",
)
_PIPELINE_IMPLICIT_COMBINATION_TASK_ID = "Operation-FileOperate-CombinationDocs-002"

if len(_NATIVE_PIPELINE_IMPLICIT_BINDINGS) != 4:
    raise RuntimeError(
        "pipeline-implicit runtime-support binding 必须精确包含 4 个任务"
    )
_NATIVE_OPERATION_RULE_SET_SHA256: dict[str, str] = {
    "Operation-FileOperate-BatchOperationExcel-001": "7ba4387d043eeeae413d7c35a6f101783864c2c68cf3ce32d57ec3c17dd65663",
    "Operation-FileOperate-BatchOperationExcel-002": "8b47e536c77e915622d4f79aebf12727471a0047be6007ae225e2743efaa92c0",
    "Operation-FileOperate-BatchOperationExcel-003": "2eb56fc32fd9964afc7f30e46e7adbbbca6a168cdc072de6d3316ca23bb776aa",
    "Operation-FileOperate-BatchOperationExcel-004": "b4ccc6729af6380a851113a874ee7725c0cdbaf002dc32d4fb03079165f0ce05",
    "Operation-FileOperate-BatchOperationExcel-005": "85095e19de51d538b44d8b9be8f9146bad1b8833915b201f564bcb21c7744fb1",
    "Operation-FileOperate-BatchOperationExcel-006": "4dd0a1469fbecdc9fd3e75dbda14e89ecf1ce76930b2e1461771d691cbb94ac1",
    "Operation-FileOperate-BatchOperationExcel-007": "3d3637d6a00fe0e5de6b8a2d4417e1f8767dde42d38c11ba6fb8265df4c7f7d6",
    "Operation-FileOperate-BatchOperationExcel-009": "84fa6d19a7490d7e0adb3c21d6200820ec8de68cb993031eb705babe66278045",
    "Operation-FileOperate-BatchOperationPPT-001": "aa0b13ebb169a67e2e513afda02d22feb5be94d70605775c56d383c6b2f6d336",
    "Operation-FileOperate-BatchOperationPPT-002": "89299c1f6a81900eeef9b8719ccd4749faf7fee421559d0b73b32cebe01efba3",
    "Operation-FileOperate-BatchOperationWord-001": "6103a7ca8e8c68d310e0fe90cca6422a233015280235922e61ed98778c320f67",
    "Operation-FileOperate-BatchOperationWord-002": "a60c3ff5ea873da994050bfbb66e4976e34ea6753f1bfd02c180d2634dfbd39e",
    "Operation-FileOperate-BatchOperationWord-003": "67d0fd311be4744e3de9cb4a7f58421f20d9176eb73df8680604ccdeaed97053",
    "Operation-FileOperate-BatchOperationWord-004": "d767ba1e4e0435867d9839f3fa50a45fb6afc22b5114bcbe80c1f6434fa35626",
    "Operation-FileOperate-BatchOperationWord-005": "42a40fe933cf760cfbfab601490e0b2e09a5001745ceb201cc5a4c94ca055757",
    "Operation-FileOperate-BatchOperationWord-006": "e16b1cf865ac3f1d6de1a97079e9df9eb4205153dbda04587234bb435227beb0",
    "Operation-FileOperate-BatchOperationWord-007": "aa9df68a8d3dc5bacac0e2cf26f76bfc2b8506632ec06f19695620b76cd89fcd",
    "Operation-FileOperate-BatchOperationWord-008": "cce5379cff1de951bd889b1c328deb0dcbf0516e6a1d1e53b1ef6d96dd9ea0d1",
    "Operation-FileOperate-BatchOperationWord-009": "ed52a2b2c36d9acdeb311bfb35930fbd3cf4b4cccb1d4b10c8acbb55fc0f3b14",
    "Operation-FileOperate-BatchOperationWord-010": "055e07b07f7e0ed14c9edd98617f412c5183769f30d2c65eb3dce00dfcc48c01",
    "Operation-FileOperate-BatchOperationWord-011": "04b531d9a39e9bbee96321910c71def5d6dfaba50f4e7df5095f3a49b5b53f0b",
    "Operation-FileOperate-BatchOperationWord-012": "820b6ad7d13ed6ed4d00e3368ba97b303b76de7cfe4f1439947c0f3b5bb8266b",
    "Operation-FileOperate-CombinationDocs-001": "44e86fe50a887dd3cc0bc6224fa473a28a65dbb2f6d27561c4e5fc6b5a7e6081",
    "Operation-FileOperate-CombinationDocs-003": "dfc80df353362a973f46032ccec3cc18ee7f35863ef742c423d7184ed6c8fde4",
    "Operation-FileOperate-CombinationDocs-005": "6eb08fc94126018bab2be5b1c9674f72526ea2cbb9ad01989c8a941cab420a5a",
    "Operation-FileOperate-CombinationDocs-006": "ac40e56a157c7472240e5cd2d4bed0c705c476210e0757d383a655ccbca95f4b",
    "Operation-FileOperate-CombinationDocs-007": "9f843194fe26ed2d768b9aa442b438c5dc75dea6188a6f8b37408e3fcf540637",
    "Operation-FileOperate-CombinationDocs-008": "6ae54533cbdbe8341da64d87730fb62269557a0103542be4b7cf4589eedef8b6",
    "Operation-FileOperate-SearchAndWrite-002": "713eb9822f9ed0f9129fa12f14086157730c0bd7b600ecd5e47709caa57b0f29",
    "Operation-FileOperate-SearchAndWrite-004": "17395aba1543a92c7f2359179b43abbfc618f33231c420fa807d688f904b3ae1",
    "Operation-FileOperate-SearchAndWrite-006": "8347b8fc1b73ebf46a769fac79674323d0e76058e3fe31eba56e377b02ac2c7a",
    "Operation-FileOperate-SearchAndWrite-007": "f479853597b4b47065c072c640e81b0d03a45b4b4c486b6ef49fb902ef5f3db7",
}
NATIVE_OPERATION_TASK_IDS: frozenset[str] = frozenset(_NATIVE_OPERATION_RULE_SET_SHA256)

if len(NATIVE_OPERATION_TASK_IDS) != 32:
    raise RuntimeError("Operation runtime-support binding 必须精确包含 32 个任务")

# 只有与专属 deterministic builder 字节精确一致的 Operation manifest 才能
# 清除 legacy asset blocker；该绑定同时阻止路径换位、UID 漂移和文件集篡改。
_BATCH_OPERATION_OFFICE_ASSET_MANIFEST_SHA256: dict[str, str] = {
    "Operation-FileOperate-BatchOperation-001": (
        "8a18f0c2751da9186fe62e8cae26d8d273d78b2d0c7180366b06dfead8c4b610"
    ),
    "Operation-FileOperate-BatchOperationExcel-001": (
        "8dae2c991df40eabcd529e330048da91d5008449a3645e23244b0fe9f027934e"
    ),
    "Operation-FileOperate-BatchOperationExcel-002": (
        "3f46763cbac0a1c7c55ed426c4c155f1fa788ce31ad64a151e445219eb4f69be"
    ),
    "Operation-FileOperate-BatchOperationExcel-003": (
        "3e0e9070ad0e4513886b496d03e01eb92ad84a774a0eed09e930e1a25b4aeaca"
    ),
    "Operation-FileOperate-BatchOperationExcel-004": (
        "d7852446f1c034a9af71366d2e0f77d91e8531bfda9fb2c1ceb95fb1f0c67d01"
    ),
    "Operation-FileOperate-BatchOperationExcel-005": (
        "98365b4fef8e68887d1879a2d9b7cf97b6a9741f295dea19c04800ca3ecb9104"
    ),
    "Operation-FileOperate-BatchOperationExcel-006": (
        "f9849e848bb365e556cf798b826caf6d91451ad6e0ad5c1b310c86de28b87974"
    ),
    "Operation-FileOperate-BatchOperationExcel-007": (
        "276dc21f1c2f521a2a3a8c68a3dafbfaf3daa4a5234701de30256c8630405967"
    ),
    "Operation-FileOperate-BatchOperationExcel-009": (
        "9cde3891b88c37d2ac1c6046761591c2cf7d6487fc6b10f7c750239d00573320"
    ),
    "Operation-FileOperate-BatchOperationPPT-001": (
        "dea5970777d5732303486a2ec858d73fde6df06c871a7306f65e1e41d58d7399"
    ),
    "Operation-FileOperate-BatchOperationPPT-002": (
        "e4241bca16e0a2b920625f26d7801c9b11ca9a7c01d4e7d8ad40ac6c630a01d5"
    ),
    "Operation-FileOperate-BatchOperationWord-001": (
        "8847df74b6d7bd1cfbf129c45480c9555f49c75b96c8a196f10e86ac92e18879"
    ),
    "Operation-FileOperate-BatchOperationWord-002": (
        "cbfbbb8edce107ee81fd74af881e7b5b9092b2ad4c8725d59303b512cfa1e1a1"
    ),
    "Operation-FileOperate-BatchOperationWord-003": (
        "bc508df6be6541e6ff5f9d1425f46a668de43ecda6f36090c6e2aa6844d75a48"
    ),
    "Operation-FileOperate-BatchOperationWord-004": (
        "8fc890b395499af997b7496242da28a9cc35bc4440b4e7e397cc246aa7de700c"
    ),
    "Operation-FileOperate-BatchOperationWord-005": (
        "f951fa12d7a524858279c8978125475ff98de083154b0e4a5b2c8dca1514e265"
    ),
    "Operation-FileOperate-BatchOperationWord-006": (
        "388413cec240bfa0dc97478af1e260a30dbe3ee28a84caab0320e3eed6177864"
    ),
    "Operation-FileOperate-BatchOperationWord-007": (
        "989bffde2a1899eab0a2191317087b2a7b1339afa823ee0a66d0d6e5e9411ff0"
    ),
    "Operation-FileOperate-BatchOperationWord-008": (
        "e5dd06083c3abd051056a727dfa7b9f331c16e0a8c7fb6ce04300b8551d3b2c5"
    ),
    "Operation-FileOperate-BatchOperationWord-009": (
        "81f25a195e5c367987c408a2acacfb9da562b8f225e5e442f1f7895112214919"
    ),
    "Operation-FileOperate-BatchOperationWord-010": (
        "1743cbe45191cdf675d92153ac2a4b075393b4f41da929a1759b6c38cc533697"
    ),
    "Operation-FileOperate-BatchOperationWord-011": (
        "e99de3032f1ef6e0fcef14d5e450fe508028e91777a6dc95782ba52bbe920ff6"
    ),
    "Operation-FileOperate-BatchOperationWord-012": (
        "00b56d5ab84094a98e70156f399881792fe01a649b945284705f79ec050bf1f2"
    ),
    "Operation-FileOperate-CombinationDocs-001": (
        "6f191b0729d1fd89f8b20287788416d3a2f8c38a53225d929c8d1de2fd222811"
    ),
    "Operation-FileOperate-CombinationDocs-003": (
        "9f6b932bd2162cc7636df914ff633383728d41b570300d4454f4f03f2a82d963"
    ),
    "Operation-FileOperate-CombinationDocs-004": (
        "057512623cc1e0cc3a0148d207c0f09d6fc4ffa2fa396b0b1025b97027b2b25c"
    ),
    "Operation-FileOperate-CombinationDocs-005": (
        "f1694992c8056a3f9c1cbe8328268e76348469b43b73795a2a25a0a7d556106e"
    ),
    "Operation-FileOperate-CombinationDocs-006": (
        "a644f91ccfdb3bbeacc86a81a5c471e25f21c8acc568e224a0b0759ae7e346c6"
    ),
    "Operation-FileOperate-CombinationDocs-007": (
        "6b5fa2baf3475fd4232c2bc9833d871ff2747217430069fb61ec442e03e10c04"
    ),
    "Operation-FileOperate-CombinationDocs-008": (
        "bd90fbc92d134df55fd0bbf2060c7ccf4cfacb1198e586f73193728480af8eb8"
    ),
    "Operation-FileOperate-SearchAndWrite-002": (
        "92985c1d73b655b3e01c71733b9440e982b0a488b1a7d0d63848c2a7071d87c0"
    ),
    "Operation-FileOperate-SearchAndWrite-004": (
        "a9c519d203e6e6480952037dcb156d8cb691063d9f5a6b848df5c3e141c54581"
    ),
    "Operation-FileOperate-SearchAndWrite-006": (
        "bf1946896285176bb23913244ecea835ce018e3fb42ecf6d48cfbd990c24cdbf"
    ),
    "Operation-FileOperate-SearchAndWrite-007": (
        "cc78e3e3ca07d0559ab6bc69bf49464bb31221d951f9310ba9bb2e4058e2e320"
    ),
}
if len(_BATCH_OPERATION_OFFICE_ASSET_MANIFEST_SHA256) != 34:
    raise RuntimeError("Operation 固定输入 manifest SHA 绑定必须精确包含 34 个任务")
NATIVE_WEBMALL_CART_TASK_IDS: frozenset[str] = WEBMALL_CART_COMPONENT_TASK_IDS

if len(NATIVE_WEBMALL_CART_TASK_IDS) != 8:
    raise RuntimeError("WebMall Cart runtime-support binding 必须精确包含 8 个任务")
_NATIVE_OSWORLD_ARTIFACT_BINDINGS: dict[str, dict[str, str]] = {
    "Operation-FileOperate-BatchOperation-001": {
        "task_uid": "4b987de4-a022-4078-8f50-8f34a39115e6",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/ce2b64a2-ddc1-4f91-8c7d-a88be7121aac.json"
        ),
    },
    "Operation-FileOperate-BatchOperation-003": {
        "task_uid": "c919165f-cdfb-413a-8e00-424a0a133620",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/5df7b33a-9f77-4101-823e-02f863e1c1ae.json"
        ),
    },
    "Operation-FileOperate-CombinationDocs-009": {
        "task_uid": "4fb43529-485f-4385-a6e8-b861bb562b5f",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/eb303e01-261e-4972-8c07-c9b4e7a4922a.json"
        ),
    },
    "Operation-FileOperate-CombinationDocs-010": {
        "task_uid": "a1cd6a49-f077-4ae0-88db-5414ef18089c",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/aceb0368-56b8-4073-b70e-3dc9aee184e0.json"
        ),
    },
    "Operation-FileOperate-CombinationDocs-011": {
        "task_uid": "60ed834a-2f51-4e3b-9b0b-6ed9c24249a4",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/337d318b-aa07-4f4f-b763-89d9a2dd013f.json"
        ),
    },
    "Operation-FileOperate-CombinationDocs-012": {
        "task_uid": "a92f8e87-36b0-4da1-aa72-f7b753011488",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e.json"
        ),
    },
    "Operation-FileOperate-CombinationDocs-013": {
        "task_uid": "3d514057-efd2-44b9-98dd-4b092ac2828a",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/3d514057-efd2-44b9-98dd-4b092ac2828a.json"
        ),
    },
    "Operation-FileOperate-CombinationDocs-014": {
        "task_uid": "f5e1b40b-ea38-4d9f-9cf6-11f1dff5f2cc",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/881deb30-9549-4583-a841-8270c65f2a17.json"
        ),
    },
    "Operation-FileOperate-CombinationDocs-015": {
        "task_uid": "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
        ),
    },
    "Operation-FileOperate-SearchAndWrite-001": {
        "task_uid": "e9e7bcf6-92da-4ff0-aaea-821099370093",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/e9e7bcf6-92da-4ff0-aaea-821099370093.json"
        ),
    },
    "Operation-FileOperate-SearchAndWrite-003": {
        "task_uid": "51d7a7fe-e659-4de0-8345-c2c04da90373",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/51d7a7fe-e659-4de0-8345-c2c04da90373.json"
        ),
    },
    "Operation-FileOperate-SearchAndWrite-005": {
        "task_uid": "dce61462-cf48-42d9-9466-5a0171aa5d12",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/dce61462-cf48-42d9-9466-5a0171aa5d12.json"
        ),
    },
    "Operation-FileOperate-SearchAndWrite-009": {
        "task_uid": "14b28a49-e101-4458-835e-2067823ddefb",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/14b28a49-e101-4458-835e-2067823ddefb.json"
        ),
    },
    "Operation-FileOperate-Settings-001": {
        "task_uid": "9b5220d5-f1f0-4db9-902d-ad41aae4d775",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": (
            "eval/osworld_scripts/9b5220d5-f1f0-4db9-902d-ad41aae4d775.json"
        ),
    },
    "Operation-WebOperate-SearchAndWrite-001": {
        "task_uid": "d017201e-a098-46ab-86be-6c99d263ecff",
        "task_type": "OSWorld脚本",
        "task_source": "OSWorld",
        "task_tag": "WebOperate",
        "evaluator_path": (
            "eval/osworld_scripts/d017201e-a098-46ab-86be-6c99d263ecff.json"
        ),
    },
}
_OSWORLD_ARTIFACT_RUNTIME_BLOCKERS: dict[str, tuple[str, ...]] = {
    "Operation-FileOperate-BatchOperation-001": (
        "osworld_artifact_getter_live_validation_not_completed",
    ),
    "Operation-FileOperate-CombinationDocs-015": (
        "osworld_artifact_getter_live_validation_not_completed",
        "osworld_artifact_gold_live_validation_not_completed",
        "osworld_task_setup_live_validation_not_completed",
    ),
}

if len(_NATIVE_OSWORLD_ARTIFACT_BINDINGS) != 15:
    raise RuntimeError(
        "OSWorld artifact runtime-support binding 必须精确包含 15 个任务"
    )
if (
    set(_OSWORLD_ARTIFACT_RUNTIME_BLOCKERS) | set(ARTIFACT_FAMILY_TASK_PREPARE_SPECS)
) != set(_NATIVE_OSWORLD_ARTIFACT_BINDINGS):
    raise RuntimeError("OSWorld artifact runtime blocker 必须覆盖完整绑定闭集")
_NATIVE_OSWORLD_BOOKMARK_BINDINGS: dict[str, dict[str, str]] = {
    "Operation-WebOperate-Settings-002": {
        "task_uid": "ef47625b-cd1b-46ca-a16c-b0ac0c99c2cc",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-Settings-003": {
        "task_uid": "bc69ee94-cf90-4cc4-a6ed-4266daa71706",
        "task_source": "OSWorld",
        "task_type": "OSWorld脚本",
    },
    "Operation-WebOperate-WebNavigate-001": {
        "task_uid": "49be33a6-666a-4f17-8f96-54ecf6fca25e",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-WebNavigate-002": {
        "task_uid": "9bc31d45-a51c-45c9-95de-b30d8bc67f79",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-WebNavigate-003": {
        "task_uid": "22e76d4d-0b1f-4c51-ab58-8ae41cbee9b7",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-WebNavigate-004": {
        "task_uid": "a1d0e68a-6dd0-402b-8d6a-713c152c19dc",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-WebNavigate-005": {
        "task_uid": "0f931391-7dd0-46ea-a492-13f064056d99",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-WebNavigate-007": {
        "task_uid": "1c100df8-4a3e-4680-be7e-3f5e2e26b22f",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-WebNavigate-008": {
        "task_uid": "eb1ad6e6-b3cc-49e6-a633-a012ae38f56e",
        "task_source": "",
        "task_type": "self",
    },
    "Operation-WebOperate-WebNavigate-010": {
        "task_uid": "a93c6823-7716-40a2-91e1-17dabbaf7d0c",
        "task_source": "OSWorld",
        "task_type": "OSWorld脚本改造",
    },
    "Operation-WebOperate-WebNavigate-011": {
        "task_uid": "38b185ab-d01d-4c97-a58e-d8d5ab4bec7b",
        "task_source": "OSWorld",
        "task_type": "OSWorld脚本改造",
    },
}
if not set(OSWORLD_BOOKMARK_START_CONTEXT_SPECS).issubset(
    _NATIVE_OSWORLD_BOOKMARK_BINDINGS
):
    raise RuntimeError("Bookmark start-context spec 必须属于原生 Bookmark 绑定")
DEFAULT_RELEASE_PATH = Path("benchmark/manifests/release-v1.json")
DEFAULT_OUTPUT_PATH = Path("benchmark/manifests/runtime-support-v1.json")
SCHEMA_REFERENCE = "../schemas/runtime-support-v1.schema.json"
SCHEMA_PATH = Path("benchmark/schemas/runtime-support-v1.schema.json")
SCHEMA_ID = "urn:paraguibench:schema:runtime-support:v1"
OSWORLD_IMAGE_MANIFEST_PATH = Path("environments/osworld/image-manifest.json")
WEBMALL_ENVIRONMENT_MANIFEST_PATH = Path(
    "environments/webmall/environment-manifest.json"
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_COMPONENT_REVISION_PATTERN = re.compile(r"component-sha256:[0-9a-f]{64}")
MAX_LIVE_RECEIPT_BYTES = 16 * 1024
MAX_LIVE_RECEIPT_ALLOWLIST_BYTES = 64 * 1024
MAX_PROMOTION_COMPONENT_FILE_BYTES = 16 * 1024 * 1024
_PROMOTION_COMPONENT_DOMAIN = b"paraguibench-promotion-component-v1\0"
_WEBMALL_ENVIRONMENT_CLOSURE_DOMAIN = b"paraguibench-webmall-environment-closure-v1\0"
_LIVE_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "run_id",
        "attempt_id",
        "execution_outcome",
        "evaluation_outcome",
        "score",
        "version_vector",
        "promotion_component_revision",
    }
)
_LIVE_RECEIPT_VERSION_VECTOR_FIELDS = frozenset(
    {
        "source_revision",
        "agent_code_revision",
        "evaluator_revision",
        "evaluation_protocol",
        "environment_protocol",
        "environment_revision",
    }
)
_PROJECT_SCHEMA_SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "required",
        "additionalProperties",
        "properties",
        "minItems",
        "maxItems",
        "uniqueItems",
        "prefixItems",
        "items",
        "contains",
        "minContains",
        "maxContains",
        "allOf",
        "if",
        "then",
        "else",
    }
)


class RuntimeSupportError(RuntimeError):
    """表示 runtime support 来源或输出不符合固定 preview 契约。"""


@dataclass(slots=True)
class ValidationResult:
    """保存一次 runtime support 校验的结构化结果。

    输入参数：
        task_count：待校验清单中成功识别的任务数量。
        local_readiness_status_counts：按 ``local_readiness_status`` 汇总的条目数。
        status_counts：按 ``support_status`` 汇总的条目数。
        errors：不包含任务正文或敏感值的错误消息列表。
    输出返回值：
        数据类本身；调用方可通过 ``ok`` 判断校验是否通过。
    """

    task_count: int = 0
    local_readiness_status_counts: dict[str, int] = field(default_factory=dict)
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


@dataclass(frozen=True, slots=True)
class CanonicalTaskRecord:
    """保留 release 已验证的 task 内容与仓库相对路径。

    输入参数：
        task：摘要与 task ID 均已通过 release 校验的 JSON object。
        relative_path：release 条目已验证的安全仓库相对路径。
    输出返回值：
        数据类本身；promotion component revision 直接使用
        ``relative_path``，不再从 task ID 猜测文件位置。
    """

    task: dict[str, Any]
    relative_path: Path


def build_runtime_support_manifest(repo_root: Path) -> dict[str, Any]:
    """从 canonical release 确定性构造逐任务支持清单。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
    输出返回值：
        可直接序列化的 runtime-support-v1 JSON object；不包含任务正文、
        答案、地址、凭据或运行日志。
    """

    root = repo_root.resolve()
    _validate_loaded_runtime_package_matches_repository(root)
    release_path = root / DEFAULT_RELEASE_PATH
    release = _load_json_object(release_path, "canonical release")
    if release.get("release_id") != RELEASE_ID:
        raise RuntimeSupportError("canonical release_id 不符合预期")
    release_entries = release.get("tasks")
    if not isinstance(release_entries, list):
        raise RuntimeSupportError("canonical release tasks 必须是列表")
    image_live_run_ready = _load_osworld_image_live_run_ready(root)
    try:
        webmall_cart_component_ready = has_current_webmall_cart_component_receipt(root)
    except WebMallCartComponentReceiptError:
        raise RuntimeSupportError(
            "WebMall Cart component receipt 与当前仓库身份不一致"
        ) from None
    try:
        osworld_artifact_component_ready_task_ids = (
            load_trusted_osworld_artifact_component_receipts(root)
        )
    except OSWorldArtifactComponentReceiptError:
        raise RuntimeSupportError(
            "OSWorld artifact component receipt 与当前仓库身份不一致"
        ) from None
    try:
        pipeline_implicit_component_ready_task_ids = (
            load_trusted_pipeline_implicit_component_receipts(root)
        )
    except PipelineImplicitComponentReceiptError:
        # 历史 / 过期 receipt 只供可选官方审计；普通 runtime-support
        # 不因此失败，只是不把对应任务标成 component-ready。
        pipeline_implicit_component_ready_task_ids = frozenset()

    canonical_records = [
        _load_canonical_task(root, release_entry) for release_entry in release_entries
    ]
    canonical_task_ids = frozenset(
        record.task["task_id"] for record in canonical_records
    )
    if len(canonical_task_ids) != len(canonical_records):
        raise RuntimeSupportError("canonical release task_id 必须唯一")
    trusted_receipt_allowlist = _load_trusted_live_validation_receipt_allowlist(
        root,
        canonical_task_ids=canonical_task_ids,
    )

    entries = [
        _build_task_entry(
            root,
            record.task,
            image_live_run_ready=image_live_run_ready,
            webmall_cart_component_ready=webmall_cart_component_ready,
            osworld_artifact_component_ready_task_ids=(
                osworld_artifact_component_ready_task_ids
            ),
            pipeline_implicit_component_ready_task_ids=(
                pipeline_implicit_component_ready_task_ids
            ),
            trusted_receipt_allowlist=trusted_receipt_allowlist,
            canonical_task_relative_path=record.relative_path,
        )
        for record in canonical_records
    ]
    entries.sort(key=lambda entry: entry["task_id"])
    local_readiness_counts = Counter(
        entry["local_readiness_status"] for entry in entries
    )
    return {
        "$schema": SCHEMA_REFERENCE,
        "schema_version": 1,
        "manifest_id": MANIFEST_ID,
        "release_id": RELEASE_ID,
        "release_manifest_sha256": _sha256_file(release_path),
        "canonical_task_count": len(entries),
        "local_readiness_status_counts": {
            status: local_readiness_counts.get(status, 0)
            for status in (
                LOCAL_COMPONENTS_INCOMPLETE_STATUS,
                LOCAL_READY_STATUS,
            )
        },
        "tasks": entries,
    }


def _validate_loaded_runtime_package_matches_repository(repo_root: Path) -> None:
    """将 Run 版本化的 loaded-package 身份门禁接入清单 build。

    输入参数：
        repo_root：即将被 runtime-support 投影和摘要的仓库根。
    输出返回值：
        无；当前进程实际 import 的 ``paraguibench`` Python 闭集与
        ``repo_root/src/paraguibench`` 逐文件摘要一致时正常返回。
    异常：
        RuntimeSupportError：任一 package 树无效或摘要不一致；
            错误不回显安装路径。
    """

    try:
        validate_loaded_package_matches_repository(repo_root)
    except RunVersioningError:
        raise RuntimeSupportError(
            "loaded package 与 repository package 源码不一致"
        ) from None


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
        manifest_path if manifest_path is not None else root / DEFAULT_OUTPUT_PATH
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
        result.local_readiness_status_counts = dict(
            sorted(
                Counter(
                    entry.get("local_readiness_status")
                    for entry in actual_entries
                    if isinstance(entry, dict)
                    and isinstance(entry.get("local_readiness_status"), str)
                ).items()
            )
        )
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
    actual_root_fields = {key: value for key, value in actual.items() if key != "tasks"}
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
    schema_errors = _validate_runtime_support_schema_instance(schema, manifest)
    result.errors.extend(
        f"runtime support JSON Schema instance invalid: {error}"
        for error in schema_errors[:32]
    )
    if len(schema_errors) > 32:
        result.errors.append(
            "runtime support JSON Schema instance invalid: error limit exceeded"
        )


def _validate_runtime_support_schema_instance(
    schema: dict[str, Any],
    manifest: object,
) -> list[str]:
    """执行 runtime-support-v1 当前 schema 所需关键字的项目级实例校验。

    输入参数：
        schema：仓库内 ``runtime-support-v1.schema.json`` 的 JSON object。
        manifest：待校验的 runtime-support JSON 值。
    输出返回值：
        只包安全 JSON 位置与关键字名的错误列表；空列表表示
        实例通过。本函数仅实现当前项目 schema 实际使用的
        ``$ref/type/const/enum/pattern/required/additionalProperties/properties``、
        数值边界与 array/conditional 关键字，不声称是通用
        JSON Schema 实现。
    """

    errors: list[str] = []
    _validate_project_schema_keyword_closure(
        schema,
        location="$schema",
        errors=errors,
    )
    if errors:
        return errors
    _validate_project_schema_node(
        root_schema=schema,
        node_schema=schema,
        instance=manifest,
        location="$",
        errors=errors,
    )
    _validate_runtime_support_order_semantics(manifest, errors=errors)
    return errors


def _validate_project_schema_keyword_closure(
    node_schema: object,
    *,
    location: str,
    errors: list[str],
) -> None:
    """校验项目级 schema 只使用 evaluator 已实现的关键字闭集。

    输入参数：
        node_schema：当前 schema 节点。
        location：不含实例值的稳定 schema 位置。
        errors：用于累积安全错误的列表。
    输出返回值：
        无；任一未知关键字以 ``unsupported-keyword`` 失败关闭。

    说明：
        本函数只递归 schema 位置，不把 ``properties`` 下的字段名
        或 ``enum`` 中的实例候选值误当作 schema 关键字。
    """

    if not isinstance(node_schema, dict):
        errors.append(f"{location}: schema-node")
        return
    if set(node_schema) - _PROJECT_SCHEMA_SUPPORTED_KEYWORDS:
        errors.append(f"{location}: unsupported-keyword")
    if "$ref" in node_schema and set(node_schema) - {
        "$ref",
        "title",
        "description",
    }:
        errors.append(f"{location}: ref-sibling")

    for mapping_keyword in ("$defs", "properties"):
        mapping = node_schema.get(mapping_keyword)
        if isinstance(mapping, dict):
            for field_name, child_schema in mapping.items():
                _validate_project_schema_keyword_closure(
                    child_schema,
                    location=f"{location}.{mapping_keyword}.{field_name}",
                    errors=errors,
                )
    for child_keyword in ("items", "contains", "if", "then", "else"):
        child_schema = node_schema.get(child_keyword)
        if isinstance(child_schema, dict):
            _validate_project_schema_keyword_closure(
                child_schema,
                location=f"{location}.{child_keyword}",
                errors=errors,
            )
    for sequence_keyword in ("prefixItems", "allOf"):
        sequence = node_schema.get(sequence_keyword)
        if isinstance(sequence, list):
            for index, child_schema in enumerate(sequence):
                _validate_project_schema_keyword_closure(
                    child_schema,
                    location=f"{location}.{sequence_keyword}[{index}]",
                    errors=errors,
                )


def _validate_project_schema_node(
    *,
    root_schema: dict[str, Any],
    node_schema: object,
    instance: object,
    location: str,
    errors: list[str],
) -> None:
    """递归执行 runtime-support schema 当前使用的有限关键字集。

    输入参数：
        root_schema：用于解析本地 ``#`` reference 的完整 schema。
        node_schema：当前递归节点，必须是 JSON object。
        instance：当前待校验 JSON 值。
        location：不包含实例值的稳定 JSON 位置。
        errors：用于累积安全错误的列表。
    输出返回值：
        无；违反项追加到 ``errors``。
    """

    if not isinstance(node_schema, dict):
        errors.append(f"{location}: schema-node")
        return
    reference = node_schema.get("$ref")
    if reference is not None:
        resolved = _resolve_local_schema_reference(root_schema, reference)
        if resolved is None:
            errors.append(f"{location}: $ref")
            return
        _validate_project_schema_node(
            root_schema=root_schema,
            node_schema=resolved,
            instance=instance,
            location=location,
            errors=errors,
        )
        return

    expected_type = node_schema.get("type")
    if isinstance(expected_type, str) and not _matches_json_schema_type(
        instance,
        expected_type,
    ):
        errors.append(f"{location}: type")
        return
    if "const" in node_schema and not _json_values_equal(
        instance,
        node_schema["const"],
    ):
        errors.append(f"{location}: const")
    enum_values = node_schema.get("enum")
    if isinstance(enum_values, list) and not any(
        _json_values_equal(instance, candidate) for candidate in enum_values
    ):
        errors.append(f"{location}: enum")

    if isinstance(instance, str):
        min_length = node_schema.get("minLength")
        max_length = node_schema.get("maxLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            errors.append(f"{location}: minLength")
        if isinstance(max_length, int) and len(instance) > max_length:
            errors.append(f"{location}: maxLength")
        pattern = node_schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, instance) is not None
            except re.error:
                errors.append(f"{location}: schema-pattern")
            else:
                if not matches:
                    errors.append(f"{location}: pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = node_schema.get("minimum")
        maximum = node_schema.get("maximum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"{location}: minimum")
        if isinstance(maximum, (int, float)) and instance > maximum:
            errors.append(f"{location}: maximum")

    if isinstance(instance, dict):
        required = node_schema.get("required")
        if isinstance(required, list):
            for field_name in required:
                if not isinstance(field_name, str) or field_name not in instance:
                    errors.append(f"{location}: required")
        properties = node_schema.get("properties")
        if isinstance(properties, dict):
            if node_schema.get("additionalProperties") is False:
                unexpected = set(instance) - set(properties)
                if unexpected:
                    errors.append(f"{location}: additionalProperties")
            for field_name, field_schema in properties.items():
                if field_name in instance:
                    _validate_project_schema_node(
                        root_schema=root_schema,
                        node_schema=field_schema,
                        instance=instance[field_name],
                        location=f"{location}.{field_name}",
                        errors=errors,
                    )

    if isinstance(instance, list):
        minimum = node_schema.get("minItems")
        maximum = node_schema.get("maxItems")
        if isinstance(minimum, int) and len(instance) < minimum:
            errors.append(f"{location}: minItems")
        if isinstance(maximum, int) and len(instance) > maximum:
            errors.append(f"{location}: maxItems")
        if node_schema.get("uniqueItems") is True:
            serialized: list[str] = []
            try:
                serialized = [
                    json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    for item in instance
                ]
            except (TypeError, ValueError):
                errors.append(f"{location}: uniqueItems")
            if len(serialized) != len(set(serialized)):
                errors.append(f"{location}: uniqueItems")
        prefix_items = node_schema.get("prefixItems")
        prefix_count = 0
        if isinstance(prefix_items, list):
            prefix_count = len(prefix_items)
            for index, item_schema in enumerate(prefix_items):
                if index >= len(instance):
                    break
                _validate_project_schema_node(
                    root_schema=root_schema,
                    node_schema=item_schema,
                    instance=instance[index],
                    location=f"{location}[{index}]",
                    errors=errors,
                )
        item_schema = node_schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance[prefix_count:], start=prefix_count):
                _validate_project_schema_node(
                    root_schema=root_schema,
                    node_schema=item_schema,
                    instance=item,
                    location=f"{location}[{index}]",
                    errors=errors,
                )
        contains_schema = node_schema.get("contains")
        if isinstance(contains_schema, dict):
            match_count = sum(
                not _project_schema_node_has_errors(
                    root_schema=root_schema,
                    node_schema=contains_schema,
                    instance=item,
                )
                for item in instance
            )
            minimum_contains = node_schema.get("minContains", 1)
            maximum_contains = node_schema.get("maxContains")
            if isinstance(minimum_contains, int) and match_count < minimum_contains:
                errors.append(f"{location}: minContains")
            if isinstance(maximum_contains, int) and match_count > maximum_contains:
                errors.append(f"{location}: maxContains")

    all_of = node_schema.get("allOf")
    if isinstance(all_of, list):
        for child_schema in all_of:
            _validate_project_schema_node(
                root_schema=root_schema,
                node_schema=child_schema,
                instance=instance,
                location=location,
                errors=errors,
            )
    if_schema = node_schema.get("if")
    if isinstance(if_schema, dict):
        branch_name = (
            "then"
            if not _project_schema_node_has_errors(
                root_schema=root_schema,
                node_schema=if_schema,
                instance=instance,
            )
            else "else"
        )
        branch = node_schema.get(branch_name)
        if isinstance(branch, dict):
            _validate_project_schema_node(
                root_schema=root_schema,
                node_schema=branch,
                instance=instance,
                location=location,
                errors=errors,
            )


def _project_schema_node_has_errors(
    *,
    root_schema: dict[str, Any],
    node_schema: dict[str, Any],
    instance: object,
) -> bool:
    """在不污染主错误列表的情况下判断 ``if``/``contains`` 子 schema。

    输入参数：
        root_schema/node_schema/instance：与主递归校验器相同的根、节点和实例。
    输出返回值：
        子 schema 产生任一错误时返回 ``True``。
    """

    child_errors: list[str] = []
    _validate_project_schema_node(
        root_schema=root_schema,
        node_schema=node_schema,
        instance=instance,
        location="$candidate",
        errors=child_errors,
    )
    return bool(child_errors)


def _resolve_local_schema_reference(
    root_schema: dict[str, Any],
    reference: object,
) -> object | None:
    """解析当前项目 schema 使用的本地 JSON Pointer reference。

    输入参数：
        root_schema：完整 runtime-support schema。
        reference：待解析 ``$ref`` 值。
    输出返回值：
        成功时返回被引用节点；非本地引用或节点缺失时返回
        ``None``。
    """

    if not isinstance(reference, str) or not reference.startswith("#/"):
        return None
    current: object = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _matches_json_schema_type(instance: object, expected_type: str) -> bool:
    """判断实例是否属于当前 schema 实际使用的 JSON 类型。

    输入参数：
        instance：待判断 JSON 值。
        expected_type：``object/array/string/integer/number/boolean/null`` 之一。
    输出返回值：
        类型精确匹配时返回 ``True``；特别地，Boolean 不得伪装成
        integer/number。
    """

    matchers = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float))
        and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "null": lambda value: value is None,
    }
    matcher = matchers.get(expected_type)
    return False if matcher is None else matcher(instance)


def _json_values_equal(left: object, right: object) -> bool:
    """以 JSON 类型语义比较 ``const``/``enum`` 值，避免 bool 与 0/1 混同。

    输入参数：
        left/right：待比较 JSON 值。
    输出返回值：
        数值类型可按 JSON 语义互比；其余值需 Python 类型与值都一致。
    """

    numeric = (int, float)
    if (
        isinstance(left, numeric)
        and not isinstance(left, bool)
        and isinstance(right, numeric)
        and not isinstance(right, bool)
    ):
        return left == right
    return type(left) is type(right) and left == right


def _validate_runtime_support_order_semantics(
    manifest: object,
    *,
    errors: list[str],
) -> None:
    """补充 JSON Schema 难以表达的 blocker 顺序与双层就绪语义。

    输入参数：
        manifest：待检查 runtime-support 实例。
        errors：主 schema 实例校验的安全错误列表。
    输出返回值：
        无；镜像 blocker 非首位、blocked 条目未以 versioned blocker 结尾，
        live 条目携带 blocker，或 local-readiness 与完整 blocker
        分类不一致时追加错误。
    """

    if not isinstance(manifest, dict) or not isinstance(manifest.get("tasks"), list):
        return
    for index, entry in enumerate(manifest["tasks"]):
        if not isinstance(entry, dict):
            continue
        blockers = entry.get("blocker_codes")
        if not isinstance(blockers, list):
            continue
        location = f"$.tasks[{index}].blocker_codes"
        if OSWORLD_VM_IMAGE_LIVE_BLOCKER in blockers and (
            not blockers or blockers[0] != OSWORLD_VM_IMAGE_LIVE_BLOCKER
        ):
            errors.append(f"{location}: image-blocker-order")
        if entry.get("support_status") == "blocked" and (
            not blockers or blockers[-1] != VERSIONED_LIVE_VALIDATION_BLOCKER
        ):
            errors.append(f"{location}: versioned-blocker-order")
        if entry.get("support_status") == "live_validated" and blockers:
            errors.append(f"{location}: live-blocker-closure")
        expected_local_readiness = _derive_local_readiness_status(blockers)
        if entry.get("local_readiness_status") != expected_local_readiness:
            errors.append(
                f"$.tasks[{index}].local_readiness_status: "
                "local-readiness-classification"
            )

    actual_local_counts = Counter(
        entry.get("local_readiness_status")
        for entry in manifest["tasks"]
        if isinstance(entry, dict)
        and isinstance(entry.get("local_readiness_status"), str)
    )
    expected_local_counts = {
        status: actual_local_counts.get(status, 0)
        for status in (
            LOCAL_COMPONENTS_INCOMPLETE_STATUS,
            LOCAL_READY_STATUS,
        )
    }
    if manifest.get("local_readiness_status_counts") != expected_local_counts:
        errors.append("$.local_readiness_status_counts: local-readiness-counts")


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
        result.errors.append(f"runtime support 含 {invalid_entry_count} 个无效任务条目")
    if duplicate_ids:
        result.errors.append(f"runtime support 含 {len(duplicate_ids)} 个重复 task_id")

    expected_by_id = {entry["task_id"]: entry for entry in expected_entries}
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
) -> CanonicalTaskRecord:
    """加载一个 release 条目指向的 canonical task。

    输入参数：
        repo_root：已解析的仓库根目录。
        release_entry：release ``tasks`` 列表中的单个条目。
    输出返回值：
        同时保留 task JSON object 与 release 已验证安全相对路径的
        ``CanonicalTaskRecord``。
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
    return CanonicalTaskRecord(task=task, relative_path=relative)


def _build_task_entry(
    repo_root: Path,
    task: dict[str, Any],
    *,
    image_live_run_ready: bool,
    webmall_cart_component_ready: bool = False,
    osworld_artifact_component_ready_task_ids: frozenset[str] = frozenset(),
    pipeline_implicit_component_ready_task_ids: frozenset[str] = frozenset(),
    trusted_receipt_allowlist: dict[str, str] | None = None,
    canonical_task_relative_path: Path | None = None,
) -> dict[str, Any]:
    """把一个 canonical task 投影为无敏感值的支持状态条目。

    输入参数：
        repo_root：包含已版本化任务与资产草案的仓库根目录。
        task：已验证身份的 canonical task JSON object。
        image_live_run_ready：版本化 OSWorld image manifest 是否已经
            固定可重现的 guest 镜像物化结果。
        webmall_cart_component_ready：独立 Cart component receipt 是否已经
            与当前 task/environment/component 三层身份重新验证。
        osworld_artifact_component_ready_task_ids：专属 allowlist 与当前
            task/environment/setup/getter/gold 五层身份复验后的
            task-scoped G/D/S receipt 闭集。
        pipeline_implicit_component_ready_task_ids：专属 allowlist 与当前
            task/environment/component 三层身份复验后的任务闭集；
            只能清理对应任务的 pipeline-live blocker。
        trusted_receipt_allowlist：已按 canonical release 收紧的
            task→receipt SHA-256 闭集；直接调用未传入时从仓库读取。
        canonical_task_relative_path：release 已验证 task 相对路径；
            仅直接测试调用可省略并回退到标准路径。
    输出返回值：
        包含环境、评价、资产和 runtime readiness 的稳定元数据。
    """

    task_id = task["task_id"]
    evaluation_protocol = _derive_evaluation_protocol(task)
    asset_status = _derive_asset_status(repo_root, task)
    component_blockers = _derive_runtime_component_blockers(
        repo_root,
        task,
        evaluation_protocol=evaluation_protocol,
        asset_status=asset_status,
        webmall_cart_component_ready=webmall_cart_component_ready,
        osworld_artifact_component_ready_task_ids=(
            osworld_artifact_component_ready_task_ids
        ),
        pipeline_implicit_component_ready_task_ids=(
            pipeline_implicit_component_ready_task_ids
        ),
    )
    receipt_ready = False
    if image_live_run_ready and not component_blockers:
        receipt_ready = _has_trusted_live_validation_receipt(
            repo_root,
            task=task,
            evaluation_protocol=evaluation_protocol,
            environment_protocol=_derive_environment_protocol(task),
            asset_status=asset_status,
            trusted_receipt_allowlist=trusted_receipt_allowlist,
            canonical_task_relative_path=canonical_task_relative_path,
        )
    is_live_validated = (
        image_live_run_ready and not component_blockers and receipt_ready
    )
    if is_live_validated:
        support_status = "live_validated"
        support_reason_code = "live_validation_passed"
        blocker_codes: list[str] = []
    else:
        support_status = "blocked"
        blocker_codes = []
        if not image_live_run_ready:
            blocker_codes.append(OSWORLD_VM_IMAGE_LIVE_BLOCKER)
        blocker_codes.extend(component_blockers)
        blocker_codes.append(VERSIONED_LIVE_VALIDATION_BLOCKER)
        local_readiness_status = _derive_local_readiness_status(blocker_codes)
        support_reason_code = (
            "live_validation_pending"
            if image_live_run_ready
            and evaluation_protocol.startswith("paraguibench.")
            and local_readiness_status == LOCAL_READY_STATUS
            else "runtime_components_incomplete"
        )

    if is_live_validated:
        local_readiness_status = LOCAL_READY_STATUS

    return {
        "task_id": task_id,
        "canonical_status": "published",
        "environment_protocol": _derive_environment_protocol(task),
        "evaluation_protocol": evaluation_protocol,
        "asset_status": asset_status,
        "local_readiness_status": local_readiness_status,
        "support_status": support_status,
        "support_reason_code": support_reason_code,
        "blocker_codes": blocker_codes,
    }


def _derive_local_readiness_status(blocker_codes: list[object]) -> str:
    """根据正式 blocker 闭集派生与 live 状态独立的本地就绪度。

    输入参数：
        blocker_codes：任务已按稳定顺序派生的完整 blocker code 列表。
    输出返回值：
        所有 blocker 都只需真实环境证据时返回 ``local_ready``；
        出现任何本地组件 blocker 或未知 code 时失败关闭为
        ``local_components_incomplete``。
    """

    if all(
        isinstance(blocker_code, str) and blocker_code in _LIVE_ONLY_BLOCKER_CODES
        for blocker_code in blocker_codes
    ):
        return LOCAL_READY_STATUS
    return LOCAL_COMPONENTS_INCOMPLETE_STATUS


def _derive_runtime_component_blockers(
    repo_root: Path,
    task: dict[str, Any],
    *,
    evaluation_protocol: str,
    asset_status: str,
    webmall_cart_component_ready: bool,
    osworld_artifact_component_ready_task_ids: frozenset[str] = frozenset(),
    pipeline_implicit_component_ready_task_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """在任何 live receipt 判定之前派生全部任务组件 blocker。

    输入参数：
        repo_root：包含正式 runtime capability 与资产清单的仓库根。
        task：已通过 release 路径、摘要和 task ID 校验的任务。
        evaluation_protocol：从 canonical task 确定性派生的评价协议。
        asset_status：从 canonical task 与固定 manifest 派生的资产状态。
        webmall_cart_component_ready：当前独立 Cart component receipt
            是否已通过三层身份与物理闭集验证。
        osworld_artifact_component_ready_task_ids：已通过专属物理闭集、
            外置 SHA 和五层 current 身份复验的 12-task 子集。
        pipeline_implicit_component_ready_task_ids：已通过专属物理闭集、
            外置 SHA 和三层 current 身份复验的 3-task candidate 子集。
    输出返回值：
        按 evaluator、asset、artifact/bookmark、Cart、pipeline、
        Operation 专项的稳定顺序返回 blocker code；绝不读取
        receipt 或 Agent 最终文本。
    """

    if not isinstance(webmall_cart_component_ready, bool):
        raise RuntimeSupportError("WebMall Cart component readiness 必须是布尔值")
    if (
        not isinstance(osworld_artifact_component_ready_task_ids, frozenset)
        or not osworld_artifact_component_ready_task_ids
        <= OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
    ):
        raise RuntimeSupportError("OSWorld artifact component readiness 闭集无效")
    if (
        not isinstance(pipeline_implicit_component_ready_task_ids, frozenset)
        or not pipeline_implicit_component_ready_task_ids
        <= PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
    ):
        raise RuntimeSupportError("pipeline implicit component readiness 闭集无效")
    blockers: list[str] = []
    if not evaluation_protocol.startswith("paraguibench."):
        blockers.append("legacy_evaluator_not_migrated")
    if (
        asset_status == "legacy_remote_reference"
        and evaluation_protocol not in _PIPELINE_IMPLICIT_PROTOCOL_IDS
    ):
        blockers.append("legacy_asset_manifest_not_migrated")
    if evaluation_protocol == OSWORLD_ARTIFACT_STATE_PROTOCOL_ID:
        blockers.extend(
            _derive_osworld_artifact_runtime_blockers(
                repo_root,
                task,
                component_ready_task_ids=(osworld_artifact_component_ready_task_ids),
            )
        )
    if evaluation_protocol == OSWORLD_CHROME_BOOKMARKS_PROTOCOL_ID:
        blockers.extend(_derive_osworld_bookmark_runtime_blockers(task))
    if (
        evaluation_protocol == WEBMALL_CART_PROTOCOL_ID
        and not webmall_cart_component_ready
    ):
        blockers.append(WEBMALL_CART_READER_LIVE_BLOCKER)
    if evaluation_protocol in _PIPELINE_IMPLICIT_PROTOCOL_IDS:
        blockers.extend(
            _derive_pipeline_implicit_runtime_blockers(
                repo_root,
                task,
                component_ready_task_ids=(pipeline_implicit_component_ready_task_ids),
            )
        )

    task_id = task.get("task_id")
    if task_id in _OPERATION_WORD_TEXT_FIDELITY_TASK_IDS:
        blockers.append(OPERATION_WORD009_010_TEXT_FIDELITY_BLOCKER)
    if task_id == _COMBINATIONDOCS003_TASK_ID:
        blockers.append(COMBINATIONDOCS003_REAL_RENDER_BLOCKER)
    if len(blockers) != len(set(blockers)):
        raise RuntimeSupportError("runtime 组件 blocker 派生了重复项")
    return blockers


def _has_trusted_live_validation_receipt(
    repo_root: Path,
    *,
    task: dict[str, Any],
    evaluation_protocol: str,
    environment_protocol: str,
    asset_status: str,
    trusted_receipt_allowlist: dict[str, str] | None = None,
    canonical_task_relative_path: Path | None = None,
) -> bool:
    """判断当前任务是否存在经摘要固定的 RunStore-v2 receipt。

    输入参数：
        repo_root：包含公开 receipt 证据目录的仓库根。
        task：当前已验证身份的 canonical task。
        evaluation_protocol：当前派生评价协议。
        environment_protocol：当前派生环境协议。
        asset_status：当前派生资产状态。
        trusted_receipt_allowlist：已验证的 task→receipt SHA-256 闭集；
            省略时从固定数据文件读取。
        canonical_task_relative_path：release 已验证的 task 相对路径。
    输出返回值：
        仅在 task→receipt SHA 闭集及全部当前身份门禁通过时
        返回 ``True``。allowlist 没有当前任务时返回 ``False``。
    """

    allowlist = (
        _load_trusted_live_validation_receipt_allowlist(repo_root)
        if trusted_receipt_allowlist is None
        else trusted_receipt_allowlist
    )
    task_id = task["task_id"]
    if task_id not in allowlist:
        return _load_trusted_live_validation_receipt(
            repo_root,
            task_id=task_id,
            expected_evaluation_protocol=evaluation_protocol,
            expected_environment_protocol=environment_protocol,
            expected_environment_revision="",
            expected_component_revision="",
            trusted_receipt_allowlist=allowlist,
        )

    expected_component_revision = _derive_promotion_component_revision(
        repo_root,
        task=task,
        evaluation_protocol=evaluation_protocol,
        environment_protocol=environment_protocol,
        asset_status=asset_status,
        canonical_task_relative_path=canonical_task_relative_path,
    )
    expected_environment_revision = _derive_current_environment_revision(
        repo_root,
        environment_protocol=environment_protocol,
    )
    return _load_trusted_live_validation_receipt(
        repo_root,
        task_id=task_id,
        expected_evaluation_protocol=evaluation_protocol,
        expected_environment_protocol=environment_protocol,
        expected_environment_revision=expected_environment_revision,
        expected_component_revision=expected_component_revision,
        trusted_receipt_allowlist=allowlist,
    )


def _load_trusted_live_validation_receipt_allowlist(
    repo_root: Path,
    *,
    canonical_task_ids: frozenset[str] | None = None,
) -> dict[str, str]:
    """读取与 guard 代码分离的可信 receipt SHA-256 闭集。

    输入参数：
        repo_root：包含固定 allowlist 数据文件的仓库根。
        canonical_task_ids：可选的 canonical release task ID 闭集；
            build 入口传入后，allowlist 不得含非 canonical task。
    输出返回值：
        字段、task 身份与完整小写 SHA-256 均已校验的新字典。
    异常：
        RuntimeSupportError：路径/字节/JSON 无效，字段不闭合，
            或出现非 canonical task、非法 task ID 或非法摘要。
    """

    payload = _read_repository_component_file(
        repo_root,
        LIVE_VALIDATION_RECEIPT_ALLOWLIST_PATH,
        label="live receipt allowlist",
        maximum_bytes=MAX_LIVE_RECEIPT_ALLOWLIST_BYTES,
    )
    try:
        document = json.loads(
            payload,
            parse_constant=lambda _value: (_raise_invalid_receipt_json()),
            object_pairs_hook=_build_closed_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise RuntimeSupportError("live receipt allowlist JSON 无效") from None
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "receipts"}
        or document.get("schema_version") != 1
        or not isinstance(document.get("receipts"), dict)
    ):
        raise RuntimeSupportError("live receipt allowlist 字段闭集无效")
    allowlist = dict(document["receipts"])
    _validate_trusted_live_receipt_allowlist_entries(allowlist)
    if canonical_task_ids is not None and not set(allowlist).issubset(
        canonical_task_ids
    ):
        raise RuntimeSupportError("live receipt allowlist 含非 canonical task")
    return allowlist


def _validate_trusted_live_receipt_allowlist_entries(
    allowlist: object,
) -> None:
    """校验内存中 task→receipt SHA-256 allowlist 的值闭集。

    输入参数：
        allowlist：从固定 JSON 数据或已验证 build 上下文传入的值。
    输出返回值：
        无；所有 task ID 和 SHA-256 均合法时正常返回。
    异常：
        RuntimeSupportError：非字典、非法 task ID 或非完整小写 SHA-256。
    """

    if not isinstance(allowlist, dict):
        raise RuntimeSupportError("live receipt allowlist 无效")
    for candidate_task_id, digest in allowlist.items():
        try:
            validate_identifier("task_id", candidate_task_id)
        except (TypeError, ValueError):
            raise RuntimeSupportError(
                "live receipt allowlist task identity 无效"
            ) from None
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise RuntimeSupportError("live receipt allowlist SHA-256 无效")


def _load_trusted_live_validation_receipt(
    repo_root: Path,
    *,
    task_id: str,
    expected_evaluation_protocol: str,
    expected_environment_protocol: str,
    expected_environment_revision: str,
    expected_component_revision: str,
    trusted_receipt_allowlist: dict[str, str] | None = None,
) -> bool:
    """从物理闭集中读取并验证一份最小脱敏 live receipt。

    输入参数：
        repo_root：公开 receipt 目录所属的仓库根。
        task_id：由 canonical release 确定的当前任务 ID。
        expected_evaluation_protocol：当前评价协议身份。
        expected_environment_protocol：当前环境协议身份。
        expected_environment_revision：当前完整环境 manifest 摘要。
        expected_component_revision：排除活性状态自引用后的当前
            task/component 闭包摘要。
        trusted_receipt_allowlist：已验证 task→receipt SHA-256 闭集；
            省略时从固定仓库数据文件读取。
    输出返回值：
        allowlist 中没有当前 task 时返回 ``False``；完整字节摘要、
        字段闭集、成功终态、有限分数、六字段版本向量与当前身份
        全部匹配时返回 ``True``。
    异常：
        RuntimeSupportError：allowlist、receipt 物理闭集、JSON 或任一身份
            无效；错误不回显 receipt 内容。
    """

    allowlist = (
        _load_trusted_live_validation_receipt_allowlist(repo_root)
        if trusted_receipt_allowlist is None
        else trusted_receipt_allowlist
    )
    _validate_trusted_live_receipt_allowlist_entries(allowlist)

    expected_digest = allowlist.get(task_id)
    payload = _read_live_receipt_from_anchored_closed_root(
        repo_root,
        allowlisted_task_ids=frozenset(allowlist),
        target_task_id=task_id if expected_digest is not None else None,
    )
    if expected_digest is None:
        return False
    if payload is None:
        raise RuntimeSupportError("live receipt root 缺失")
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise RuntimeSupportError("live receipt SHA-256 与 allowlist 不一致")
    try:
        receipt = json.loads(
            payload,
            parse_constant=lambda _value: (_raise_invalid_receipt_json()),
            object_pairs_hook=_build_closed_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise RuntimeSupportError("live receipt JSON 无效") from None
    _validate_live_receipt_payload(
        receipt,
        task_id=task_id,
        expected_evaluation_protocol=expected_evaluation_protocol,
        expected_environment_protocol=expected_environment_protocol,
        expected_environment_revision=expected_environment_revision,
        expected_component_revision=expected_component_revision,
    )
    return True


def _read_live_receipt_from_anchored_closed_root(
    repo_root: Path,
    *,
    allowlisted_task_ids: frozenset[str],
    target_task_id: str | None,
) -> bytes | None:
    """通过 nofollow dirfd 链读取并前后复验 receipt 物理闭集。

    输入参数：
        repo_root：待校验仓库根。
        allowlisted_task_ids：已验证的 receipt task ID 闭集。
        target_task_id：本次需读取的 allowlisted task；当前 task
            未入 allowlist 时为 ``None``，但仍校验目录闭集。
    输出返回值：
        目标 task 的稳定有界字节；未指定 target，或空 allowlist
        且 receipt 根尚未创建时返回 ``None``。
    异常：
        RuntimeSupportError：平台不支持 nofollow dirfd、路径链无效、
            文件集与 allowlist 不等，或目录/文件在读取期间漂移。
    """

    receipt_directory = _open_live_receipt_root_directory(
        repo_root,
        allowlisted_task_ids=allowlisted_task_ids,
    )
    if receipt_directory is None:
        return None
    expected_names = {f"{candidate}.json" for candidate in allowlisted_task_ids}
    try:
        before_directory = _validate_anchored_receipt_directory_closed_set(
            receipt_directory,
            expected_names=expected_names,
        )
        if target_task_id is None:
            return None
        target_name = f"{target_task_id}.json"
        if target_name not in expected_names:
            raise RuntimeSupportError("live receipt target 不属于 allowlist")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        try:
            receipt_descriptor = os.open(
                target_name,
                flags,
                dir_fd=receipt_directory,
            )
        except OSError:
            raise RuntimeSupportError("live receipt 无法从锚定目录打开") from None
        try:
            payload = _read_bounded_stable_regular_descriptor(
                receipt_descriptor,
                maximum_bytes=MAX_LIVE_RECEIPT_BYTES,
                label="live receipt",
            )
        finally:
            os.close(receipt_descriptor)
        after_directory = _validate_anchored_receipt_directory_closed_set(
            receipt_directory,
            expected_names=expected_names,
        )
        if before_directory != after_directory:
            raise RuntimeSupportError("live receipt root 读取期间不稳定")
        return payload
    finally:
        os.close(receipt_directory)


def _open_live_receipt_root_directory(
    repo_root: Path,
    *,
    allowlisted_task_ids: frozenset[str],
) -> int | None:
    """以 ``openat`` 形式逐段打开 receipt 根的 nofollow 目录链。

    输入参数：
        repo_root：已选择的仓库根。
        allowlisted_task_ids：决定目录缺失是否可视为初始空状态。
    输出返回值：
        最终 receipt 目录的已打开 descriptor；空 allowlist 且目录
        尚不存在时返回 ``None``。调用方负责关闭 descriptor。
    异常：
        RuntimeSupportError：平台无安全 flags，仓库根或任一中间节点
            不是 nofollow 普通目录。
    """

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise RuntimeSupportError("live receipt root 平台缺少 nofollow dirfd")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    try:
        current_descriptor = os.open(repo_root.resolve(), flags)
    except OSError:
        raise RuntimeSupportError("live receipt repository root 无法安全打开") from None
    try:
        for part in LIVE_VALIDATION_RECEIPT_ROOT.parts:
            try:
                next_descriptor = os.open(
                    part,
                    flags,
                    dir_fd=current_descriptor,
                )
            except FileNotFoundError:
                if not allowlisted_task_ids:
                    os.close(current_descriptor)
                    return None
                raise RuntimeSupportError("live receipt root 缺失") from None
            except OSError:
                raise RuntimeSupportError(
                    "live receipt root path contains symlink 或非目录"
                ) from None
            try:
                os.close(current_descriptor)
            except OSError:
                try:
                    os.close(next_descriptor)
                except OSError:
                    pass
                raise RuntimeSupportError("live receipt root dirfd 交接失败") from None
            current_descriptor = next_descriptor
        return current_descriptor
    except BaseException:
        try:
            os.close(current_descriptor)
        except OSError:
            pass
        raise


def _validate_anchored_receipt_directory_closed_set(
    directory_descriptor: int,
    *,
    expected_names: set[str],
) -> tuple[int, int, int, int]:
    """在同一 dirfd 上验证 receipt 文件集与目录身份稳定。

    输入参数：
        directory_descriptor：通过 nofollow 目录链打开的 receipt 根。
        expected_names：由 allowlisted task ID 机械派生的文件名闭集。
    输出返回值：
        ``(dev, inode, mtime_ns, ctime_ns)`` 稳定目录身份元组。
    异常：
        RuntimeSupportError：枚举失败，节点为 symlink/特殊文件，
            文件名不等于 allowlist，或扫描期间目录漂移。
    """

    try:
        before = os.fstat(directory_descriptor)
        with os.scandir(directory_descriptor) as iterator:
            entries = list(iterator)
        for entry in entries:
            entry_status = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(entry_status.st_mode):
                raise RuntimeSupportError("live receipt path is symlink")
            if not stat.S_ISREG(entry_status.st_mode):
                raise RuntimeSupportError("live receipt root 含非普通文件")
        if {entry.name for entry in entries} != expected_names:
            raise RuntimeSupportError(
                "live receipt root closed set 与 allowlist 不一致"
            )
        after = os.fstat(directory_descriptor)
    except RuntimeSupportError:
        raise
    except OSError:
        raise RuntimeSupportError("live receipt root 无法稳定枚举") from None
    fields = ("st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns")
    before_identity = tuple(getattr(before, field) for field in fields)
    after_identity = tuple(getattr(after, field) for field in fields)
    if before_identity != after_identity:
        raise RuntimeSupportError("live receipt root 扫描期间不稳定")
    return after_identity


def _read_bounded_stable_regular_file_nofollow(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    """用单一 nofollow fd 有界读取前后身份稳定的普通文件。

    输入参数：
        path：已经物理目录闭集检查的目标文件。
        maximum_bytes：允许读取的最大字节数。
        label：仅用于不含外部值的安全错误区域名。
    输出返回值：
        完整、未超限且读取前后 dev/inode/size/mtime/ctime 一致的字节。
    异常：
        RuntimeSupportError：路径为 symlink/非普通文件、超限、短读或读取期间
            身份发生变化。
    """

    if not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise RuntimeSupportError(f"{label} size limit 无效")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        if path.is_symlink():
            raise RuntimeSupportError(f"{label} path is symlink") from None
        raise RuntimeSupportError(f"{label} 无法打开") from None
    try:
        return _read_bounded_stable_regular_descriptor(
            descriptor,
            maximum_bytes=maximum_bytes,
            label=label,
        )
    finally:
        os.close(descriptor)


def _read_bounded_stable_regular_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    """从已锚定 descriptor 有界读取前后身份稳定的普通文件。

    输入参数：
        descriptor：调用方已以 nofollow 语义打开的文件 descriptor。
        maximum_bytes：允许读取的最大字节数。
        label：仅用于不含外部值的安全错误区域名。
    输出返回值：
        完整、未超限，且读取前后 dev/inode/size/mtime/ctime
        一致的原始字节。
    异常：
        RuntimeSupportError：descriptor 非普通文件、超限、短读或
            读取期间身份发生变化。本函数不关闭 descriptor。
    """

    if not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise RuntimeSupportError(f"{label} size limit 无效")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeSupportError(f"{label} 不是普通文件")
        if before.st_size < 1 or before.st_size > maximum_bytes:
            raise RuntimeSupportError(f"{label} size 超出限制")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise RuntimeSupportError(f"{label} 发生短读")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeSupportError(f"{label} size 读取漂移")
        after = os.fstat(descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field) for field in stable_fields
        ):
            raise RuntimeSupportError(f"{label} 读取期间不稳定")
        return b"".join(chunks)
    except OSError:
        raise RuntimeSupportError(f"{label} 读取失败") from None


def _raise_invalid_receipt_json() -> None:
    """为 ``json.loads(parse_constant=...)`` 统一拒绝 NaN/Infinity。

    输入参数：
        无；非标准 token 值故意不传入，避免错误回显。
    输出返回值：
        不返回；始终抛出 ``ValueError``。
    """

    raise ValueError("non-finite JSON constant")


def _build_closed_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """在 JSON 解码期间构造 object，并拒绝被默认解码器覆盖的重复 key。

    输入参数：
        pairs：``json.loads`` 按字节顺序提供的 key/value 序列。
    输出返回值：
        key 闭合且唯一时返回普通字典。
    异常：
        ValueError：同一 JSON object 出现重复 key；错误不回显 key 或值。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_live_receipt_payload(
    receipt: object,
    *,
    task_id: str,
    expected_evaluation_protocol: str,
    expected_environment_protocol: str,
    expected_environment_revision: str,
    expected_component_revision: str,
) -> None:
    """校验 receipt 最小字段闭集、终态与当前组件身份。

    输入参数：
        receipt：从摘要固定字节解码的不可信 JSON 值。
        task_id：文件名与 canonical release 同时固定的任务 ID。
        expected_evaluation_protocol/expected_environment_protocol：当前协议身份。
        expected_environment_revision：当前环境 manifest 完整摘要。
        expected_component_revision：当前 promotion-safe 组件闭包摘要。
    输出返回值：
        无；全部契约通过时正常返回。
    异常：
        RuntimeSupportError：任一字段、身份、终态、分数或版本向量无效。
    """

    if not isinstance(receipt, dict) or set(receipt) != _LIVE_RECEIPT_FIELDS:
        raise RuntimeSupportError("live receipt fields 不符合最小闭集")
    if receipt.get("schema_version") != "2.0":
        raise RuntimeSupportError("live receipt schema 不是 RunStore v2")
    if receipt.get("task_id") != task_id:
        raise RuntimeSupportError("live receipt task identity 不一致")
    for field_name in ("run_id", "attempt_id"):
        try:
            validate_identifier(field_name, receipt.get(field_name))
        except (TypeError, ValueError):
            raise RuntimeSupportError(
                "live receipt run/attempt identity 无效"
            ) from None
    if (
        receipt.get("execution_outcome") != "SUCCEEDED"
        or receipt.get("evaluation_outcome") != "PASSED"
    ):
        raise RuntimeSupportError("live receipt outcome 必须是 SUCCEEDED/PASSED")
    score = receipt.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise RuntimeSupportError("live receipt score 必须是 [0,1] 有限数值")
    if isinstance(score, float) and not math.isfinite(score) or not 0 <= score <= 1:
        raise RuntimeSupportError("live receipt score 必须位于 [0,1]")

    vector_payload = receipt.get("version_vector")
    if (
        not isinstance(vector_payload, dict)
        or set(vector_payload) != _LIVE_RECEIPT_VERSION_VECTOR_FIELDS
    ):
        raise RuntimeSupportError("live receipt version vector fields 无效")
    try:
        vector = RunVersionVector(**vector_payload)
        validate_run_version_vector(vector)
    except (TypeError, ValueError):
        raise RuntimeSupportError("live receipt version vector 无效") from None
    if not (
        vector.source_revision
        == vector.agent_code_revision
        == vector.evaluator_revision
    ):
        raise RuntimeSupportError("live receipt version vector code identity 不一致")
    if (
        vector.evaluation_protocol != expected_evaluation_protocol
        or vector.environment_protocol != expected_environment_protocol
        or vector.environment_revision != expected_environment_revision
    ):
        raise RuntimeSupportError("live receipt version vector current identity 不一致")
    component_revision = receipt.get("promotion_component_revision")
    if (
        not isinstance(component_revision, str)
        or _COMPONENT_REVISION_PATTERN.fullmatch(component_revision) is None
        or component_revision != expected_component_revision
    ):
        raise RuntimeSupportError("live receipt component revision 与当前闭包不一致")


def _derive_current_environment_revision(
    repo_root: Path,
    *,
    environment_protocol: str,
) -> str:
    """从当前完整环境 manifest 字节派生 receipt 必须匹配的 revision。

    输入参数：
        repo_root：包含 OSWorld/WebMall 环境 manifest 的仓库根。
        environment_protocol：runtime-support 当前任务的环境协议。
    输出返回值：
        ``manifest-sha256:<digest>`` 格式的完整字节摘要。
    异常：
        RuntimeSupportError：协议无法映射到固定 manifest，或文件不安全。
    """

    if environment_protocol == "webmall.browser.v1":
        environment_files = _load_webmall_environment_closure(repo_root)
        digest = hashlib.sha256(_WEBMALL_ENVIRONMENT_CLOSURE_DOMAIN)
        for relative_path, payload in environment_files:
            digest.update(relative_path.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(payload).digest())
        return "manifest-sha256:" + digest.hexdigest()
    if environment_protocol not in {"osworld.desktop.v1", "osworld.chrome.v1"}:
        raise RuntimeSupportError("live receipt environment protocol 无对应 manifest")
    payload = _read_repository_component_file(
        repo_root,
        OSWORLD_IMAGE_MANIFEST_PATH,
        label="live receipt environment manifest",
    )
    return "manifest-sha256:" + hashlib.sha256(payload).hexdigest()


def _load_webmall_environment_closure(
    repo_root: Path,
) -> tuple[tuple[Path, bytes], tuple[Path, bytes]]:
    """读取并校验 WebMall 对 OSWorld Chrome 镜像的传递环境闭包。

    输入参数：
        repo_root：包含 WebMall 与 OSWorld 环境 manifest 的仓库根。
    输出返回值：
        稳定顺序的 ``((webmall_path, bytes), (osworld_path, bytes))``；
        返回的两份原始字节均已被 WebMall 嵌套 SHA 绑定。
    异常：
        RuntimeSupportError：任一 manifest 无效，嵌套路径/协议不是固定值，
            或 OSWorld 字节与 WebMall 声明的 SHA-256 不一致。
    """

    webmall_payload = _read_repository_component_file(
        repo_root,
        WEBMALL_ENVIRONMENT_MANIFEST_PATH,
        label="WebMall environment manifest",
    )
    osworld_payload = _read_repository_component_file(
        repo_root,
        OSWORLD_IMAGE_MANIFEST_PATH,
        label="WebMall browser image manifest",
    )
    try:
        webmall = json.loads(
            webmall_payload,
            parse_constant=lambda _value: (_raise_invalid_receipt_json()),
            object_pairs_hook=_build_closed_json_object,
        )
        osworld = json.loads(
            osworld_payload,
            parse_constant=lambda _value: (_raise_invalid_receipt_json()),
            object_pairs_hook=_build_closed_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise RuntimeSupportError("WebMall browser image manifest JSON 无效") from None
    browser_runtime = (
        webmall.get("browser_runtime") if isinstance(webmall, dict) else None
    )
    protocol_ids = osworld.get("protocol_ids") if isinstance(osworld, dict) else None
    if (
        not isinstance(browser_runtime, dict)
        or browser_runtime.get("kind") != "osworld_chrome"
        or browser_runtime.get("image_manifest_ref") != "../osworld/image-manifest.json"
        or browser_runtime.get("required_protocol_id") != "osworld.chrome.v1"
        or not isinstance(protocol_ids, list)
        or "osworld.chrome.v1" not in protocol_ids
    ):
        raise RuntimeSupportError("WebMall browser image 传递身份无效")
    expected_digest = browser_runtime.get("image_manifest_sha256")
    if (
        not isinstance(expected_digest, str)
        or _SHA256_PATTERN.fullmatch(expected_digest) is None
        or hashlib.sha256(osworld_payload).hexdigest() != expected_digest
    ):
        raise RuntimeSupportError("WebMall browser image SHA-256 与当前环境不一致")
    return (
        (WEBMALL_ENVIRONMENT_MANIFEST_PATH, webmall_payload),
        (OSWORLD_IMAGE_MANIFEST_PATH, osworld_payload),
    )


def _derive_promotion_component_revision(
    repo_root: Path,
    *,
    task: dict[str, Any],
    evaluation_protocol: str,
    environment_protocol: str,
    asset_status: str,
    canonical_task_relative_path: Path | None = None,
) -> str:
    """构造不含 runtime 活性输出与 receipt allowlist 的任务组件闭包摘要。

    输入参数：
        repo_root：包含当前源码、schema、task、资产与环境文件的根。
        task：已通过 canonical release 摘要验证的任务 object。
        evaluation_protocol/environment_protocol：当前确定性协议投影。
        asset_status：当前确定性资产状态投影。
        canonical_task_relative_path：release 已验证的 task 仓库相对路径。
    输出返回值：
        ``component-sha256:<digest>``。摘要覆盖公开 Python 源码闭集、
        全部 benchmark schema、当前 task、其专属 input/gold manifest、
        当前环境 manifest 与协议投影；故意排除 runtime-support 活性
        输出和 receipt 自身，避免晋升造成摘要循环。
    异常：
        RuntimeSupportError：任一闭包路径越界、含 symlink/特殊节点、超限或
            task 资产引用无效。
    """

    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeSupportError("promotion component task identity 无效")
    relative_paths = _collect_promotion_component_paths(
        repo_root,
        task=task,
        environment_protocol=environment_protocol,
        canonical_task_relative_path=canonical_task_relative_path,
    )
    digest = hashlib.sha256(_PROMOTION_COMPONENT_DOMAIN)
    projection = json.dumps(
        {
            "task_id": task_id,
            "evaluation_protocol": evaluation_protocol,
            "environment_protocol": environment_protocol,
            "asset_status": asset_status,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest.update(projection)
    digest.update(b"\0")
    for relative_path in relative_paths:
        payload = _read_repository_component_file(
            repo_root,
            relative_path,
            label="promotion component file",
        )
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return "component-sha256:" + digest.hexdigest()


def _collect_promotion_component_paths(
    repo_root: Path,
    *,
    task: dict[str, Any],
    environment_protocol: str,
    canonical_task_relative_path: Path | None = None,
) -> list[Path]:
    """枚举 promotion-safe revision 的仓库内普通文件闭集。

    输入参数：
        repo_root：待枚举仓库根。
        task：当前 canonical task，用于加入 task/input/gold 专属路径。
        environment_protocol：用于选择 OSWorld 或 WebMall 完整环境 manifest。
        canonical_task_relative_path：release 已验证 task 仓库相对路径；
            省略时仅为直接测试回退到标准路径。
    输出返回值：
        按 POSIX 相对路径排序且去重的文件列表。
    异常：
        RuntimeSupportError：src/schema 树不存在、含 symlink/特殊节点，
            或 task 文件引用不安全。
    """

    root = repo_root.resolve()
    task_id = task["task_id"]
    task_relative_path = canonical_task_relative_path or (
        Path("benchmark/tasks") / f"{task_id}.json"
    )
    task_relative_path = _validate_component_relative_path(
        task_relative_path.as_posix()
    )
    candidates: list[Path] = [
        Path("pyproject.toml"),
        Path("scripts/benchmark/runtime_support_manifest.py"),
        task_relative_path,
    ]
    candidates.extend(
        _collect_regular_tree_files(
            root,
            Path("src/paraguibench"),
            suffix=".py",
            label="promotion source tree",
        )
    )
    candidates.extend(
        _collect_regular_tree_files(
            root,
            Path("benchmark/schemas"),
            suffix=".json",
            label="promotion schema tree",
        )
    )
    for field_name in ("asset_manifest", "gold_manifest"):
        reference = task.get(field_name)
        if reference is None:
            continue
        if not isinstance(reference, str) or not reference:
            raise RuntimeSupportError("promotion component manifest reference 无效")
        candidates.append(_validate_component_relative_path(reference))
    if environment_protocol == "webmall.browser.v1":
        _load_webmall_environment_closure(repo_root)
        candidates.extend(
            [WEBMALL_ENVIRONMENT_MANIFEST_PATH, OSWORLD_IMAGE_MANIFEST_PATH]
        )
    elif environment_protocol in {"osworld.desktop.v1", "osworld.chrome.v1"}:
        candidates.append(OSWORLD_IMAGE_MANIFEST_PATH)
    else:
        raise RuntimeSupportError("promotion component environment protocol 无效")
    return sorted(set(candidates), key=lambda item: item.as_posix())


def _collect_regular_tree_files(
    repo_root: Path,
    relative_root: Path,
    *,
    suffix: str,
    label: str,
) -> list[Path]:
    """不跟随链接地枚举一棵树中指定后缀的普通文件。

    输入参数：
        repo_root：仓库根。
        relative_root：树根的仓库相对路径。
        suffix：进入摘要闭集的文件后缀。
        label：安全错误区域名。
    输出返回值：
        稳定排序的仓库相对文件路径。
    异常：
        RuntimeSupportError：树缺失、含 symlink 或任一节点非普通文件/目录。
    """

    tree_root = repo_root / relative_root
    try:
        root_status = tree_root.lstat()
    except OSError:
        raise RuntimeSupportError(f"{label} 缺失") from None
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        raise RuntimeSupportError(f"{label} root 无效")
    files: list[Path] = []
    try:
        walker = os.walk(tree_root, topdown=True, followlinks=False)
        for current_raw, directory_names, file_names in walker:
            current = Path(current_raw)
            for name in directory_names:
                status = (current / name).lstat()
                if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
                    raise RuntimeSupportError(f"{label} 含 symlink 或特殊目录")
            for name in file_names:
                path = current / name
                status = path.lstat()
                if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
                    raise RuntimeSupportError(f"{label} 含 symlink 或特殊文件")
                if path.suffix == suffix:
                    files.append(path.relative_to(repo_root))
    except OSError:
        raise RuntimeSupportError(f"{label} 无法枚举") from None
    if not files:
        raise RuntimeSupportError(f"{label} 文件闭集为空")
    return sorted(files, key=lambda item: item.as_posix())


def _validate_component_relative_path(value: str) -> Path:
    """把 task 中的 manifest 引用收紧为安全仓库相对路径。

    输入参数：
        value：canonical task 中的 input/gold manifest 字符串。
    输出返回值：
        不含空段、``.``、``..`` 或反斜杠的相对 ``Path``。
    异常：
        RuntimeSupportError：路径为绝对、可穿越或不是规范 POSIX 形式。
    """

    if "\\" in value:
        raise RuntimeSupportError("promotion component manifest path 无效")
    relative = Path(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value
    ):
        raise RuntimeSupportError("promotion component manifest path 无效")
    return relative


def _read_repository_component_file(
    repo_root: Path,
    relative_path: Path,
    *,
    label: str,
    maximum_bytes: int = MAX_PROMOTION_COMPONENT_FILE_BYTES,
) -> bytes:
    """验证仓库相对路径链后有界稳定读取普通文件。

    输入参数：
        repo_root：已选择的仓库根。
        relative_path：由生产常量或已收紧 task 引用产生的相对路径。
        label：不含外部值的安全错误区域名。
        maximum_bytes：该类闭集文件的最大字节数。
    输出返回值：
        未超限且读取前后稳定的文件字节。
    异常：
        RuntimeSupportError：路径越界、中间链含 symlink，或目标读取无效。
    """

    root = repo_root.resolve()
    if not isinstance(relative_path, Path):
        raise RuntimeSupportError(f"{label} 路径无效")
    try:
        relative_path = _validate_component_relative_path(relative_path.as_posix())
    except RuntimeSupportError:
        raise RuntimeSupportError(f"{label} 路径无效") from None
    try:
        candidate = root / relative_path
        candidate.relative_to(root)
    except ValueError:
        raise RuntimeSupportError(f"{label} 路径越界") from None
    current = root
    for part in relative_path.parts[:-1]:
        current = current / part
        try:
            status = current.lstat()
        except OSError:
            raise RuntimeSupportError(f"{label} 路径缺失") from None
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise RuntimeSupportError(f"{label} 路径含 symlink 或非目录")
    return _read_bounded_stable_regular_file_nofollow(
        candidate,
        maximum_bytes=maximum_bytes,
        label=label,
    )


def _derive_pipeline_implicit_runtime_blockers(
    repo_root: Path,
    task: dict[str, Any],
    *,
    component_ready_task_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """按 production preflight 投影 pipeline 组件门禁，不推断 live 状态。

    输入参数：
        repo_root：包含正式/草案清单与 runtime source 的仓库根。
        task：已经 release 身份校验的 canonical task。
        component_ready_task_ids：已通过专属 receipt 的任务闭集；
            仅作为 pipeline-live blocker 的任务级开关。
    输出返回值：
        四个当前 production-ready 任务只返回 pipeline 首次
        live 复验码；formal-only 兼容分支仅在故障注入或未来
        不完整任务中验证 typed blocker 必须精确匹配。其余未闭合项
        返回完整 metadata/parser/live 门禁，CombinationDocs 再追加
        gold conflict。该函数不读取 Agent final text，也不把组件
        完成误报为 live。
    异常：
        RuntimeSupportError：注册为 production-ready 的任务未通过实际
            manifest/typed/evaluator 机器身份门禁。
    """

    if (
        not isinstance(component_ready_task_ids, frozenset)
        or not component_ready_task_ids <= PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
    ):
        raise RuntimeSupportError("pipeline implicit component readiness 闭集无效")
    task_id = task.get("task_id")
    component_ready = task_id in component_ready_task_ids
    if task_id in PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS:
        try:
            capability = preflight_pipeline_implicit_local_runtime(
                repo_root=repo_root,
                task=task,
            )
        except PipelineImplicitRuntimeManifestError:
            raise RuntimeSupportError(
                "pipeline-implicit 正式 runtime capability 无效"
            ) from None
        except PipelineImplicitRuntimeBlockedError:
            raise RuntimeSupportError(
                "pipeline-implicit 正式 runtime capability 被阻断"
            ) from None
        if capability is None or capability.task_id != task_id:
            raise RuntimeSupportError(
                "pipeline-implicit 正式 runtime capability 身份无效"
            )
        return (
            []
            if component_ready
            else ["pipeline_implicit_live_validation_not_completed"]
        )

    if task_id in PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS:
        try:
            preflight_pipeline_implicit_local_runtime(
                repo_root=repo_root,
                task=task,
            )
        except PipelineImplicitRuntimeBlockedError as error:
            expected = ("pipeline_implicit_typed_observation_parser_not_migrated",)
            if error.blocker_codes != expected:
                raise RuntimeSupportError(
                    "pipeline-implicit 正式资产 capability 阻断码无效"
                ) from None
            blockers = list(error.blocker_codes)
            if not component_ready:
                blockers.append("pipeline_implicit_live_validation_not_completed")
            return blockers
        except PipelineImplicitRuntimeManifestError:
            raise RuntimeSupportError(
                "pipeline-implicit 正式资产 capability 无效"
            ) from None
        raise RuntimeSupportError(
            "pipeline-implicit 未完成 parser 的任务不得返回 runtime capability"
        )

    blockers = list(_PIPELINE_IMPLICIT_BASE_BLOCKERS)
    if task_id == _PIPELINE_IMPLICIT_COMBINATION_TASK_ID:
        blockers.append("pipeline_implicit_combination_gold_conflict_unresolved")
    if component_ready:
        blockers.remove("pipeline_implicit_live_validation_not_completed")
    return blockers


def _load_osworld_image_live_run_ready(repo_root: Path) -> bool:
    """读取公开镜像清单并返回统一 live 物化门禁。

    输入参数：
        repo_root：包含版本化 OSWorld image manifest 的仓库根目录。
    输出返回值：
        schema v2 固定 recipe 已取得可重现物化回执时返回 ``True``；
        recipe 已固定但回执尚未受控纳入，或 legacy manifest 尚无完整
        recipe 时返回 ``False``，供全部任务派生同一 fail-closed blocker。
    异常：
        RuntimeSupportError：镜像清单本身无法解析或身份无效。
    """

    try:
        manifest = load_osworld_image_manifest(repo_root / OSWORLD_IMAGE_MANIFEST_PATH)
    except OSWorldImageManifestError:
        raise RuntimeSupportError(
            "OSWorld image manifest 无法派生 runtime support"
        ) from None
    return manifest.live_run_ready


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
        match_mode = (
            str(task.get("answer_match_mode") or "implicit-structured")
            .strip()
            .lower()
            .replace("_", "-")
        )
        if match_mode == "strict-exact":
            match_mode = "exact"
        return f"paraguibench.answer.{match_mode}.v1"

    if (
        task.get("task_type") == "QA"
        and task.get("task_source") == "WebMall"
        and task.get("evaluator_path") == "evaluators/string_url_evaluator.py"
    ):
        return "paraguibench.webmall.url-multiset.v1"

    if _matches_native_webmall_cart_task(task):
        return WEBMALL_CART_PROTOCOL_ID

    pipeline_implicit_protocol = _match_pipeline_implicit_protocol(task)
    if pipeline_implicit_protocol is not None:
        return pipeline_implicit_protocol

    evaluation_mode = task.get("evaluation_mode")
    if (
        evaluation_mode == "osworld_profile_state"
        and task.get("profile_state_adapter") == "chrome_profile_name_v1"
        and task.get("vm_aggregation") == "any_complete"
    ):
        return "paraguibench.osworld.chrome-profile-name.v1"
    if (
        evaluation_mode == "osworld_active_tab"
        and task.get("active_tab_adapter") == "google_shopping_selected_filters_v1"
        and task.get("vm_aggregation") == "any_complete"
    ):
        return "paraguibench.osworld.google-shopping-active-tab.v1"

    artifact_binding = _NATIVE_OSWORLD_ARTIFACT_BINDINGS.get(task.get("task_id"))
    if artifact_binding is not None and all(
        task.get(field) == value for field, value in artifact_binding.items()
    ):
        return OSWORLD_ARTIFACT_STATE_PROTOCOL_ID

    bookmark_binding = _NATIVE_OSWORLD_BOOKMARK_BINDINGS.get(task.get("task_id"))
    if bookmark_binding is not None and all(
        task.get(field) == value
        for field, value in {
            **bookmark_binding,
            "task_tag": "WebOperate",
            "evaluator_path": "eval/webnavigate_bookmark_evaluator.py",
        }.items()
    ):
        return OSWORLD_CHROME_BOOKMARKS_PROTOCOL_ID

    if _matches_native_operation_task(task):
        return OPERATION_PROTOCOL_ID

    evaluator_path = task.get("evaluator_path")
    if isinstance(evaluator_path, str) and evaluator_path:
        normalized_path = evaluator_path.lower()
        path_protocols = (
            ("file_search_readonly", "legacy.file-search-readonly.v1"),
            ("webnavigate_bookmark", "legacy.webnavigate.bookmark.v1"),
            ("string_url_evaluator", "legacy.webmall.bookmark-url-set.v1"),
            ("cart_evaluator", "legacy.webmall.cart.v1"),
            (
                "checkout_evaluator",
                (
                    "paraguibench.webmall.find-and-order.closed-world.v2"
                    if task.get("task_tag") == "EndToEnd"
                    else "paraguibench.webmall.checkout.closed-world.v2"
                ),
            ),
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


def _matches_native_webmall_cart_task(task: dict[str, Any]) -> bool:
    """按固定任务闭集与完整 Cart 元数据判定原生评价绑定。

    输入参数：
        task：已通过 release 路径、文件摘要与 task_id 验证的任务 object。
    输出返回值：
        任务身份和 WebMall Cart 合同字段全部命中时返回 ``True``；
        未知任务或任一元数据漂移时返回 ``False``，继续使用 legacy fallback。
    """

    task_id = task.get("task_id")
    return (
        isinstance(task_id, str)
        and task_id in NATIVE_WEBMALL_CART_TASK_IDS
        and task.get("task_type") == "QA"
        and task.get("task_source") == "WebMall"
        and task.get("answer_type") == "cart"
        and task.get("evaluator_path") == "evaluators/cart_evaluator.py"
        and isinstance(task.get("expected_urls"), list)
        and bool(task["expected_urls"])
    )


def _match_pipeline_implicit_protocol(task: dict[str, Any]) -> str | None:
    """按固定四任务闭集与全部身份字段匹配原生协议。

    输入参数：
        task：已经 release 路径、SHA-256 和 task_id 校验的任务。
    输出返回值：
        task_uid/type/source/tag/evaluator_path 全部精确命中时返回
        专属原生协议；未知任务或任一字段漂移时返回 ``None``，
        使调用方继续走 legacy fail-closed fallback。
    """

    task_id = task.get("task_id")
    binding = (
        _NATIVE_PIPELINE_IMPLICIT_BINDINGS.get(task_id)
        if isinstance(task_id, str)
        else None
    )
    if binding is None:
        return None
    if not all(
        task.get(field) == expected
        for field, expected in binding.items()
        if field != "protocol_id"
    ):
        return None
    return binding["protocol_id"]


def _matches_native_operation_task(task: dict[str, Any]) -> bool:
    """根据固定 task ID 与完整 eval-rules 摘要判定原生 Operation 绑定。

    输入参数：
        task：已通过 canonical release 路径、文件 SHA-256 与 task ID
            验证的任务 JSON object。
    输出返回值：
        task ID、FileOperate 标签、空 legacy evaluator path 与完整有序
        ``eval_rules`` canonical SHA-256 全部命中时返回 ``True``；
        未知或漂移任务返回 ``False`` 并继续走 legacy fallback。
    """

    task_id = task.get("task_id")
    if not isinstance(task_id, str):
        return False
    expected_digest = _NATIVE_OPERATION_RULE_SET_SHA256.get(task_id)
    rules = task.get("eval_rules")
    if (
        expected_digest is None
        or task.get("task_tag") != "FileOperate"
        or task.get("evaluator_path") != ""
        or not isinstance(rules, list)
        or not rules
        or not all(isinstance(rule, dict) for rule in rules)
    ):
        return False
    try:
        serialized = json.dumps(
            rules,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return False
    return hashlib.sha256(serialized).hexdigest() == expected_digest


def _derive_osworld_artifact_runtime_blockers(
    repo_root: Path,
    task: dict[str, Any],
    *,
    component_ready_task_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """返回已权威绑定 artifact task 的专用 runtime 阻塞项。

    输入参数：
        repo_root：用于验证 task-prepare draft 的仓库根目录。
        task：已通过 canonical release 路径、摘要和 task_id
            验证的任务 JSON object。
        component_ready_task_ids：专属 component loader 完整验证的
            task-scoped 闭集；非该闭集成员的任何 receipt 不生效。
    输出返回值：
        按稳定顺序返回当前 artifact getter/gold/finalize
        证据链仍未闭环的 blocker code。未在权威绑定闭集的
        task 返回空列表，不会因共用 evaluator path 误提升。
    """

    task_id = task.get("task_id")
    if not isinstance(task_id, str):
        return []
    if (
        not isinstance(component_ready_task_ids, frozenset)
        or not component_ready_task_ids <= OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
    ):
        raise RuntimeSupportError("OSWorld artifact component task 闭集无效")
    established_blockers = _OSWORLD_ARTIFACT_RUNTIME_BLOCKERS.get(task_id)
    if established_blockers is not None:
        return list(established_blockers)
    prepare_spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS.get(task_id)
    if prepare_spec is None:
        return []
    try:
        capability = inspect_artifact_family_task_prepare_capability(
            repo_root=repo_root,
            task=task,
        )
    except ArtifactFamilyTaskPrepareCapabilityError:
        raise RuntimeSupportError(
            "OSWorld artifact task-prepare capability 无法验证"
        ) from None
    if capability is None:
        raise RuntimeSupportError("OSWorld artifact task-prepare capability 绑定缺失")

    blockers = [
        "osworld_artifact_getter_live_validation_not_completed",
        "osworld_artifact_gold_live_validation_not_completed",
    ]
    if (
        prepare_spec.finalize_action_id != "none"
        and task_id not in OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS
    ):
        blockers.append("osworld_artifact_finalize_not_migrated")
    blockers.append("osworld_task_setup_live_validation_not_completed")
    capability_projection = (
        (
            ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
            "osworld_source_start_context_ambiguous",
        ),
        (
            ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
            "osworld_artifact_input_path_inferred",
        ),
        (
            ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
            "osworld_artifact_input_license_unverified",
        ),
    )
    blockers.extend(
        public_code
        for internal_code, public_code in capability_projection
        if internal_code in capability.blocker_ids
    )
    if task_id == "Operation-FileOperate-Settings-001":
        _validate_settings_derived_gold_contract(repo_root, task)
    if task_id in component_ready_task_ids:
        blockers = [
            blocker
            for blocker in blockers
            if blocker
            not in {
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
            }
        ]
    return blockers


def _validate_settings_derived_gold_contract(
    repo_root: Path,
    task: dict[str, Any],
) -> None:
    """验证 Settings 本地就绪确实绑定严格 v2 derived gold。

    输入参数：repo_root 为当前仓库根；task 为 release 已验证的 canonical
        Settings 任务对象。
    输出返回值：task 引用、严格 manifest 类型、input manifest 反向绑定与
        evidence spec logical key 全部闭合时返回 ``None``。
    异常：RuntimeSupportError：任一字段、路径、字节身份或语义绑定漂移；
        错误不回显路径、摘要或原始 JSON。
    """

    expected_reference = (
        "benchmark/gold/manifests/Operation-FileOperate-Settings-001.json"
    )
    if task.get("gold_manifest") != expected_reference:
        raise RuntimeSupportError("Settings derived gold manifest 引用无效")
    try:
        manifest = load_gold_asset_manifest(repo_root / expected_reference)
        if type(manifest) is not DerivedGoldAssetManifest:
            raise RuntimeSupportError("Settings derived gold contract 类型无效")
        input_manifest_payload = read_manifest_bytes_nofollow(
            repo_root / manifest.asset_manifest
        )
        if (
            hashlib.sha256(input_manifest_payload).hexdigest()
            != manifest.asset_manifest_sha256
        ):
            raise RuntimeSupportError("Settings input manifest 字节身份无效")
        input_manifest = load_asset_manifest_bytes(input_manifest_payload)
        source_assets = tuple(
            asset
            for asset in input_manifest.files
            if asset.path == manifest.source_input.path
        )
        if (
            input_manifest.asset_set_id != manifest.asset_set_id
            or len(source_assets) != 1
            or (
                source_assets[0].path,
                source_assets[0].size,
                source_assets[0].sha256,
                source_assets[0].media_type,
            )
            != (
                manifest.source_input.path,
                manifest.source_input.size,
                manifest.source_input.sha256,
                manifest.source_input.media_type,
            )
        ):
            raise RuntimeSupportError("Settings source input 反向绑定无效")
        bound = bind_osworld_task_gold(
            "Operation-FileOperate-Settings-001",
            manifest,
            task_uid=(
                task.get("task_uid") if isinstance(task.get("task_uid"), str) else None
            ),
            evaluator_path=(
                task.get("evaluator_path")
                if isinstance(task.get("evaluator_path"), str)
                else None
            ),
            asset_manifest_reference=(
                task.get("asset_manifest")
                if isinstance(task.get("asset_manifest"), str)
                else None
            ),
        )
    except (AssetManifestError, GoldManifestError, OSWorldGoldBindingError):
        raise RuntimeSupportError("Settings derived gold contract 无法验证") from None
    if bound.mode is not TaskGoldMode.PRIVATE_DERIVED_MANIFEST:
        raise RuntimeSupportError("Settings derived gold contract 类型无效")


def _derive_osworld_bookmark_runtime_blockers(
    task: dict[str, Any],
) -> list[str]:
    """返回原生 Bookmark 任务仍未闭环的专用 runtime blocker。

    输入参数：
        task：已通过 canonical release 路径、摘要和 task_id 验证的任务。
    输出返回值：
        无启动上下文的任务返回空列表；有上下文但生产 spec
        尚未接入时返回独立 blocker；已接入任务必须与 spec
        身份、manifest 和相对资产完全一致。
    异常：
        RuntimeSupportError：canonical task 与已接入 spec 发生漂移。
    """

    task_id = task.get("task_id")
    if not isinstance(task_id, str):
        return []
    context = task.get("agent_start_context")
    if context is None:
        return []
    spec = OSWORLD_BOOKMARK_START_CONTEXT_SPECS.get(task_id)
    if spec is None:
        return ["osworld_bookmark_start_context_not_migrated"]
    expected_context = {
        "type": spec.context_type,
        "asset_relative_path": spec.asset_relative_path,
        "open_with": spec.open_with,
        "target": spec.target,
    }
    expected_identity = {
        "task_id": spec.task_id,
        "task_uid": spec.task_uid,
        "task_source": spec.task_source,
        "task_type": spec.task_type,
        "task_tag": spec.task_tag,
        "asset_manifest": spec.asset_manifest,
        "evaluator_path": spec.evaluator_path,
        "agent_start_context": expected_context,
    }
    if any(task.get(key) != value for key, value in expected_identity.items()):
        raise RuntimeSupportError(
            "Bookmark start-context canonical 身份与生产 spec 不一致"
        )
    return []


def _derive_asset_status(repo_root: Path, task: dict[str, Any]) -> str:
    """根据任务中的资产声明推导迁移状态。

    输入参数：
        repo_root：包含 canonical task 与固定资产 manifest 的仓库根。
        task：canonical task JSON object。
    输出返回值：
        固定下载清单、legacy 远程引用或未声明任务资产三种状态之一。
    """

    if "asset_manifest" in task:
        if "prepare_script_path" in task or "prepare_exclude_patterns" in task:
            raise RuntimeSupportError("canonical 固定资产不得与 legacy 来源字段共存")
        task_id = task.get("task_id")
        expected_digest = _BATCH_OPERATION_OFFICE_ASSET_MANIFEST_SHA256.get(task_id)
        if expected_digest is None:
            try:
                resolved = resolve_task_assets(repo_root, task)
            except AssetManifestError:
                raise RuntimeSupportError(
                    "canonical 固定资产 manifest 无法验证"
                ) from None
            if resolved.mode is not TaskAssetMode.PINNED_DOWNLOAD_MANIFEST:
                raise RuntimeSupportError("canonical 固定资产模式无效")
            manifest = resolved.manifest
        else:
            expected_reference = f"benchmark/assets/manifests/{task_id}.json"
            if task.get("asset_manifest") != expected_reference:
                raise RuntimeSupportError("Office 固定资产 manifest 路径漂移")
            try:
                payload = read_manifest_bytes_nofollow(repo_root / expected_reference)
                manifest = load_asset_manifest_bytes(payload)
            except AssetManifestError:
                raise RuntimeSupportError("Office 固定资产 manifest 无法验证") from None
            if hashlib.sha256(payload).hexdigest() != expected_digest:
                raise RuntimeSupportError("Office 固定资产 manifest 字节漂移")
        if manifest is None or manifest.asset_set_id != task.get("task_id"):
            raise RuntimeSupportError("canonical task 与资产集合身份不一致")
        return "pinned_download_manifest"
    if isinstance(task.get("prepare_script_path"), str) and task["prepare_script_path"]:
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
        raise RuntimeSupportError(f"{label} 无法解析：{type(error).__name__}") from None
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
        print(f"runtime-support-v1 generated: tasks={manifest['canonical_task_count']}")
        return 0

    result = validate_runtime_support_manifest(root, target_path)
    if result.ok:
        counts = ", ".join(
            f"{status}={count}" for status, count in result.status_counts.items()
        )
        print(f"runtime-support-v1 valid: tasks={result.task_count}; {counts}")
        return 0
    for error in result.errors:
        print(f"ERROR: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
