"""OSWorld artifact evidence spec 版本化目录的公共契约测试。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from paraguibench.integrations.osworld.artifact_evidence_specs import (
    ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID,
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
    ArtifactEvidenceSpecError,
    canonical_artifact_evidence_spec_json,
    project_inline_artifact_metric_inputs,
    validate_artifact_evidence_spec,
)
from paraguibench.evaluation.osworld.artifact_metrics import (
    evaluate_artifact_metric,
)


_EXPECTED_SOURCE_IDENTITIES = {
    "Operation-FileOperate-BatchOperation-001": (
        "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
        "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
        "28fdb8cb9b84390cfd642e1670d15aa4a5179a6931fa8986495fdd8bece2501c",
    ),
    "Operation-FileOperate-BatchOperation-003": (
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "0456405408bdb3d305b10dac904cba7fbc556f041417bef5530387a736cfd517",
    ),
    "Operation-FileOperate-CombinationDocs-009": (
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "bc73a485042a3878b972e5fa14b9841cef85cfc79ef6c42274c5b68aaef1670b",
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "1e04563701fde1335a57c6540c5e9919472fd36b1d2ad1c0d5ae75fb5a1b1387",
    ),
    "Operation-FileOperate-CombinationDocs-011": (
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "846c0629ec2dde2f18a34807e9c0b899260fff5de22fd0cd710d5df3170e94f7",
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "4780cfb96a299a1e8b30ab369fe767150164ee6140dd747ca7e017ecbe8bc948",
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        "3d514057-efd2-44b9-98dd-4b092ac2828a",
        "7e287123-70ca-47b9-8521-47db09b69b14",
        "99468cac2c1677f2ddda08f8289b97890f01cf39a24417668c494b77e52c4ed3",
    ),
    "Operation-FileOperate-CombinationDocs-014": (
        "881deb30-9549-4583-a841-8270c65f2a17",
        "881deb30-9549-4583-a841-8270c65f2a17",
        "f8bb09a70d6733f65bbe1e03e6d8c7a7366671cff70bf688450bba34fbcd809d",
    ),
    "Operation-FileOperate-CombinationDocs-015": (
        "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "df67aebb-fb3a-44fd-b75b-51b6012df509",
        "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c",
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        "e9e7bcf6-92da-4ff0-aaea-821099370093",
        "c7c1e4c3-9e92-4eba-a4b8-689953975ea4",
        "8ff91da03ef3013c0abe4bac318a6c9ddaa5a6271cf6ed4652ffe7f8b6f73539",
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        "51d7a7fe-e659-4de0-8345-c2c04da90373",
        "da52d699-e8d2-4dc5-9191-a2199e0b6a9b",
        "8485b90d63965980bae26b44093cafa2c4dbd4b1971354f9b9b14dd12b7ed6a1",
    ),
    "Operation-FileOperate-SearchAndWrite-005": (
        "dce61462-cf48-42d9-9466-5a0171aa5d12",
        "67890eb6-6ce5-4c00-9e3d-fb4972699b06",
        "f031f50bac3ab93f3dd1894b9cea737a2246c798ba2daf6a2b777c0239365855",
    ),
    "Operation-FileOperate-SearchAndWrite-009": (
        "14b28a49-e101-4458-835e-2067823ddefb",
        "3e3fc409-bff3-4905-bf16-c968eee3f807",
        "8a440569b160bd2b7295ec4b006a83e002e2b578559c06a4f96d8265902189bf",
    ),
    "Operation-FileOperate-Settings-001": (
        "9b5220d5-f1f0-4db9-902d-ad41aae4d775",
        "47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5",
        "5f3ebcf626c74ac25b31c54c186166064c8a62edec23a87efbf1655a854ff66d",
    ),
    "Operation-WebOperate-SearchAndWrite-001": (
        "d017201e-a098-46ab-86be-6c99d263ecff",
        "d1acdb87-bb67-4f30-84aa-990e56a09c92",
        "2262ca74a553975a89efff303a8731a9cafee598f9a0e2174562fa2f034e35c4",
    ),
}

_EXPECTED_SOURCE_TO_RUNTIME_LOCATORS = {
    "Operation-FileOperate-BatchOperation-001": ((("Pictures",), ("shared",)),),
    "Operation-FileOperate-BatchOperation-003": (
        (("Desktop/book/book.zip",), ("Desktop/book/book.zip",)),
    ),
    "Operation-FileOperate-CombinationDocs-009": (
        (
            ("Desktop/lecture1-2021-with-ink.pptx",),
            ("Desktop/lecture1-2021-with-ink.pptx",),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        (("exam/grades.xlsx",), ("exam/grades.xlsx",)),
    ),
    "Operation-FileOperate-CombinationDocs-011": (
        (
            ("Desktop/problematic/Invoice # 243729.pdf",),
            ("Desktop/problematic/Invoice # 243729.pdf",),
        ),
        (("Desktop/problematic",), ("Desktop/problematic",)),
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        (
            ("Desktop/students work/case study.docx",),
            ("Desktop/students work/case study.docx",),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        (
            ("Desktop/GRF-p5y.xlsx", "Desktop/GRF-p5y-Sheet1.csv"),
            ("Desktop/GRF-p5y.xlsx", "Desktop/GRF-p5y-Sheet1.csv"),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-014": (
        (
            (
                "Documents/Fundings/supported_rate.xlsx",
                "Documents/Fundings/supported_rate-Sheet1.csv",
            ),
            (
                "Documents/Fundings/supported_rate.xlsx",
                "Documents/Fundings/supported_rate-Sheet1.csv",
            ),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-015": (
        (("Desktop/references.bib",), ("Desktop/references.bib",)),
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        (
            ("Desktop/Professor_Contact.xlsx",),
            ("Desktop/Professor_Contact.xlsx",),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        (
            ("Desktop/book_list_result.docx",),
            ("Desktop/book_list_result.docx",),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-005": (
        (
            ("Desktop/best_awards_acl.xlsx",),
            ("Desktop/best_awards_acl.xlsx",),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-009": (
        (("Desktop/movies.xlsx",), ("Desktop/movies.xlsx",)),
    ),
    "Operation-FileOperate-Settings-001": (
        (
            ("Desktop/Robotic_Workshop_Infographics.pptx",),
            ("Desktop/Robotic_Workshop_Infographics.pptx",),
        ),
    ),
    "Operation-WebOperate-SearchAndWrite-001": (
        (("Desktop/MUST_VISIT.xlsx",), ("Desktop/MUST_VISIT.xlsx",)),
    ),
}

_EXPECTED_EVIDENCE_SPEC_SHA256 = {
    "Operation-FileOperate-BatchOperation-001": (
        "b76e30c2e64800da5ae577ac67af6cee25eda2725c714156a553e59dd53b9150"
    ),
    "Operation-FileOperate-BatchOperation-003": (
        "84303456acc9f0599267ff2a1e6f740286fec8b04a13c1a9e1965af1240a7a5d"
    ),
    "Operation-FileOperate-CombinationDocs-009": (
        "3511d41332419b4ca879ea5741b4258f277a0b63cf1ac15be850d2986ce93f56"
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        "8241669808219dcbbd9bd65a63ee39a46ec39f5e8bafd27241e2548a6cf8d6f4"
    ),
    "Operation-FileOperate-CombinationDocs-011": (
        "8e80a8128160c7c6adc3016d4ce8e991c8f9ff2233ba4120472c40b5d521e50f"
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        "2ce71d4781c77115037556f3febcf09733910a951b04926a2ca2f6bd2aa6384c"
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        "1dd2ea7eca7fd520ed23fef47e878315500942ab14f642f667a3b64fc861f577"
    ),
    "Operation-FileOperate-CombinationDocs-014": (
        "b63da4802e018afd24c162b1d910feeff8ef1751e37e5357b282267df445d715"
    ),
    "Operation-FileOperate-CombinationDocs-015": (
        "76ad950add00f112d76c659bdaab86ac1edef413e633ef90522e0c7ec0451658"
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        "98df5f39a8106c20971cfdbe8d8c178f1b91ea79413e9ee97cb068415f298fbd"
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        "9dd438373a51ed6364500dbe84d51d1d6e752e03ca68b6605529cac0d609c9df"
    ),
    "Operation-FileOperate-SearchAndWrite-005": (
        "a2449644490d5094442e9e86d48f75c9bdbc22faa08f4417041502070c6f3a4e"
    ),
    "Operation-FileOperate-SearchAndWrite-009": (
        "79ea0c485668dee08c26cbc55da5e1e6320fcbf5209a201eb3bb88053b39922a"
    ),
    "Operation-FileOperate-Settings-001": (
        "b3a011b545d82bdf381e1159a02119e29be79bf549792f347ba6ef96d471c9bd"
    ),
    "Operation-WebOperate-SearchAndWrite-001": (
        "f594a7b5e06a43d9cb7109c9bba4c9c19e14d3c96dbe1439069c2e616e14b6d6"
    ),
}


def test_batch_operation_spec_is_canonical_and_self_authenticating() -> None:
    """验证首个 artifact 任务的取证规格具备稳定摘要。

    输入参数：
        无；通过公共 catalog 取山峰图片批量改名任务。
    输出返回值：
        无；断言 task/rule/source 身份、相对定位器、受限
        getter 以及 canonical JSON SHA-256 均完整绑定。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-001"]

    assert spec.schema_id == ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID
    assert spec.rule_id == (
        "paraguibench.osworld.artifact-rule.Operation-FileOperate-BatchOperation-001.v1"
    )
    assert spec.source_evaluator_id == "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac"
    assert spec.source_task_id == "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac"
    assert spec.source_contract_sha256 == (
        "28fdb8cb9b84390cfd642e1670d15aa4a5179a6931fa8986495fdd8bece2501c"
    )
    assert spec.finalize_action_id == "none"
    assert spec.artifact_slots[0].source_locator_relative_paths == ("Pictures",)
    assert spec.artifact_slots[0].locator_relative_paths == ("shared",)
    assert spec.artifact_slots[0].getter_kind == "image-directory-hash-manifest"
    assert (
        json.loads(spec.artifact_slots[0].getter_options_json)["symlink_policy"]
        == "nofollow-fail-closed"
    )

    canonical_json = canonical_artifact_evidence_spec_json(spec)
    payload = json.loads(canonical_json)
    assert payload["schema_id"] == ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID
    assert "evidence_spec_sha256" not in payload
    assert (
        spec.evidence_spec_sha256
        == hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    )


def test_catalog_pins_all_fifteen_final_source_identities() -> None:
    """验证取证 catalog 无遗漏地绑定 15 个最终源 evaluator。

    输入参数：
        无；预期表来自本地最终 OSWorld evaluator JSON
        以及既有 artifact-state rule catalog。
    输出返回值：
        无；断言 task 闭集与 evaluator/task/contract SHA
        三重身份逐项相等。
    """

    actual = {
        task_id: (
            spec.source_evaluator_id,
            spec.source_task_id,
            spec.source_contract_sha256,
        )
        for task_id, spec in OSWORLD_ARTIFACT_EVIDENCE_SPECS.items()
    }

    assert actual == _EXPECTED_SOURCE_IDENTITIES


def test_catalog_explicitly_pins_source_to_shared_locator_adaptation() -> None:
    """验证 15 个任务均显式区分源 home 路径与真实 shared 路径。

    输入参数：
        无；预期表同时固定源 evaluator 相对路径和在动态
        guest home 下实际取证的 ``shared`` 相对路径。
    输出返回值：
        无；断言固定 adaptation ID 、每个槽位的路径映射及
        多文件顺序精确匹配，子目录结构不被无条件展平。
    """

    actual = {
        task_id: tuple(
            (
                slot.source_locator_relative_paths,
                slot.locator_relative_paths,
            )
            for slot in spec.artifact_slots
        )
        for task_id, spec in OSWORLD_ARTIFACT_EVIDENCE_SPECS.items()
    }

    assert actual == _EXPECTED_SOURCE_TO_RUNTIME_LOCATORS
    assert OSWORLD_ARTIFACT_EVIDENCE_SPECS[
        "Operation-FileOperate-BatchOperation-001"
    ].artifact_slots[0].source_path_adaptation_id == (
        "paraguibench.osworld.source-home-to-shared.v1"
    )
    assert {
        slot.source_path_adaptation_id
        for task_id, spec in OSWORLD_ARTIFACT_EVIDENCE_SPECS.items()
        if task_id != "Operation-FileOperate-BatchOperation-001"
        for slot in spec.artifact_slots
    } == {"paraguibench.osworld.source-path-identity.v1"}


def test_catalog_freezes_all_canonical_evidence_spec_digests() -> None:
    """验证 15 条 canonical JSON 的 SHA-256 已作为发布契约固定。

    输入参数：
        无；读取公共 catalog 中每个 spec 的摘要。
    输出返回值：
        无；断言 getter、locator、limits、finalize、metric options、
        inline rules 或 gold keys 的任何变化都会使固定摘要测试失败。
    """

    assert {
        task_id: spec.evidence_spec_sha256
        for task_id, spec in OSWORLD_ARTIFACT_EVIDENCE_SPECS.items()
    } == _EXPECTED_EVIDENCE_SPEC_SHA256


def test_catalog_has_no_empty_metric_slot_or_external_gold_location() -> None:
    """验证规格不会产生“取了证但没有评分”或未确认 gold 位置。

    输入参数：
        无；遍历 15 条规格的所有槽位和 canonical JSON。
    输出返回值：
        无；断言每个槽位都有 metric，外部 gold 只以逻辑 key
        出现，规格不含 HTTP URL 或绝对 guest 路径。
    """

    for spec in OSWORLD_ARTIFACT_EVIDENCE_SPECS.values():
        assert all(slot.metrics for slot in spec.artifact_slots)
        canonical_json = canonical_artifact_evidence_spec_json(spec)
        assert "http://" not in canonical_json
        assert "https://" not in canonical_json
        assert '"/home/' not in canonical_json


def test_batch_operation_metric_pins_the_confirmed_inline_rule() -> None:
    """验证山峰图片 metric 绑定已确认的内联 gold 规则。

    输入参数：
        无；从公共 catalog 取首个任务的唯一 metric。
    输出返回值：
        无；断言 metric/contract/阈值与源 JSON 一致，
        内联规则 canonical SHA 精确匹配，且未虚构外部 gold。
    """

    metric = (
        OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-001"]
        .artifact_slots[0]
        .metrics[0]
    )
    getter_options = json.loads(
        OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-001"]
        .artifact_slots[0]
        .getter_options_json
    )

    assert (
        metric.metric_id,
        metric.contract_id,
        metric.score_threshold,
        json.loads(metric.options_json),
        metric.expected_kind,
        metric.gold_keys,
    ) == (
        "check_direct_json_object",
        "mountain-file-hash-name-map.v1",
        1.0,
        {},
        "inline-rule",
        (),
    )
    assert metric.expected_options_json is not None
    assert hashlib.sha256(metric.expected_options_json.encode("utf-8")).hexdigest() == (
        "5257a0dacbc75cfa6b282298f93ddeab48793eb270430da9e2acfcd536ddad7f"
    )
    assert getter_options == {
        "content_detection": "pillow-open-no-suffix-filter",
        "digest_algorithm": "sha256",
        "hash_projection": "pillow-image-tobytes",
        "duplicate_digest_policy": "last-observed-entry-wins",
        "member_selection": "all-direct-members",
        "symlink_policy": "nofollow-fail-closed",
    }


def test_mountain_inline_rule_projects_to_metric_gold_and_options() -> None:
    """验证内联 source rules 以固定方式投影为 metric 输入。

    输入参数：
        无；取山峰图片 metric spec，通过公共纯函数生成
        ``gold`` 和 ``options``。
    输出返回值：
        无；断言 expected 映射与两个 bool flag 被分离，并能
        直接调用固定 artifact metric contract 得到满分。
    """

    metric = (
        OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-001"]
        .artifact_slots[0]
        .metrics[0]
    )
    gold, options = project_inline_artifact_metric_inputs(metric)
    actual = {digest: candidates[0] for digest, candidates in gold.items()}

    assert metric.metric_input_projection_id == (
        "inline-rule.expected-as-gold.flags-as-options.v1"
    )
    assert options == {
        "expect_in_result": True,
        "result_not_list": True,
    }
    result = evaluate_artifact_metric(
        metric.contract_id,
        actual=actual,
        gold=gold,
        options=options,
    )
    assert result.score == 1.0


def test_validator_rejects_non_relative_slot_locator_without_echoing_it() -> None:
    """验证任务输入不能将绝对路径或路径穿越注入 getter。

    输入参数：
        无；复制一条可信 catalog spec，仅将槽位定位器
        替换为包含私密片段的路径穿越值。
    输出返回值：
        无；断言公共验证器 fail-closed，且错误不回显
        不可信 locator。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-CombinationDocs-015"]
    private_locator = "../../private/references.bib"
    unsafe_slot = replace(
        spec.artifact_slots[0],
        locator_relative_paths=(private_locator,),
    )
    unsafe_spec = replace(spec, artifact_slots=(unsafe_slot,))

    with pytest.raises(ArtifactEvidenceSpecError) as caught:
        validate_artifact_evidence_spec(unsafe_spec)

    assert private_locator not in str(caught.value)


def test_validator_rejects_finalize_action_outside_the_fixed_allowlist() -> None:
    """验证取证规格不能携带任务提供的任意收尾命令。

    输入参数：
        无；将已注册 spec 的 action ID 替换为模拟不可信值。
    输出返回值：
        无；断言验证器拒绝任意 action，且错误不回显
        不可信值。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-003"]
    unsafe_action = "run-private-shell-command"

    with pytest.raises(ArtifactEvidenceSpecError) as caught:
        validate_artifact_evidence_spec(replace(spec, finalize_action_id=unsafe_action))

    assert unsafe_action not in str(caught.value)


def test_validator_rejects_arbitrary_command_in_finalize_options() -> None:
    """验证 allowlist action 的 options 也不能被替换成任意命令。

    输入参数：
        无；保留已允许的归档 action ID，仅把 options 替换为
        包含私密片段的 command 对象。
    输出返回值：
        无；断言验证器拒绝非法 options，且不回显 command。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-003"]
    private_command = "read-private-file"
    unsafe_spec = replace(
        spec,
        finalize_options_json=json.dumps({"command": private_command}),
    )

    with pytest.raises(ArtifactEvidenceSpecError) as caught:
        validate_artifact_evidence_spec(unsafe_spec)

    assert private_command not in str(caught.value)


def test_validator_rejects_getter_kind_outside_the_fixed_allowlist() -> None:
    """验证 collector 只能从固定 getter registry 选择取证方式。

    输入参数：
        无；复制已注册 spec 并注入模拟任意 getter ID。
    输出返回值：
        无；断言验证器 fail-closed，且错误不包含该 ID。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-Settings-001"]
    unsafe_getter = "execute-task-provided-script"
    unsafe_slot = replace(spec.artifact_slots[0], getter_kind=unsafe_getter)

    with pytest.raises(ArtifactEvidenceSpecError) as caught:
        validate_artifact_evidence_spec(replace(spec, artifact_slots=(unsafe_slot,)))

    assert unsafe_getter not in str(caught.value)


def test_validator_rejects_non_positive_evidence_limits() -> None:
    """验证文件、容器、文本和超时约束不能退化为无界值。

    输入参数：
        无；将一条正常 spec 的总字节上限置零。
    输出返回值：
        无；断言公共验证器拒绝非正数上限。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-CombinationDocs-013"]
    unsafe_limits = replace(spec.limits, max_total_bytes=0)

    with pytest.raises(ArtifactEvidenceSpecError):
        validate_artifact_evidence_spec(replace(spec, limits=unsafe_limits))


def test_validator_bounds_text_for_base64_http_envelope() -> None:
    """验证合法文本上限始终能装入 controller 的 16 MiB 包络。

    输入参数：
        无；在精确安全边界与高一字节处分别构造 evidence limits。
    输出返回值：
        无；12,579,840 bytes 可接受，再多一字节必须在 guest I/O 前
        失败关闭，不能运行时误记成 read_error。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-CombinationDocs-015"]
    safe_limits = replace(spec.limits, max_text_bytes=12_579_840)
    unsafe_limits = replace(spec.limits, max_text_bytes=12_579_841)

    validate_artifact_evidence_spec(replace(spec, limits=safe_limits))
    with pytest.raises(ArtifactEvidenceSpecError):
        validate_artifact_evidence_spec(replace(spec, limits=unsafe_limits))


def test_validator_rejects_fractional_count_and_byte_limits() -> None:
    """验证计数与字节上限不能用可通过数值比较的浮点数绕过。

    输入参数：
        无；将成员数与总字节数分别替换为正的有限小数。
    输出返回值：
        无；两个规格都必须 fail-closed，避免 getter 接到非整数资源参数。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-001"]
    for field_name, value in (
        ("max_items", 1.5),
        ("max_total_bytes", 1024.5),
    ):
        unsafe_limits = replace(spec.limits, **{field_name: value})
        with pytest.raises(ArtifactEvidenceSpecError):
            validate_artifact_evidence_spec(replace(spec, limits=unsafe_limits))


@pytest.mark.parametrize(
    "field_name",
    ("max_items", "getter_timeout_seconds"),
)
def test_validator_rejects_unrepresentable_large_evidence_limits(
    field_name: str,
) -> None:
    """验证无法转换为有限浮点的超大整数只产生固定领域错误。

    输入参数：
        field_name：分别覆盖整数计数与允许浮点的 timeout 字段。
    输出返回值：
        无；验证器抛 ``ArtifactEvidenceSpecError``，不泄出内部
        ``OverflowError`` 或回显注入值。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-001"]
    unsafe_value = 10**400
    unsafe_limits = replace(
        spec.limits,
        **{field_name: unsafe_value},
    )

    with pytest.raises(ArtifactEvidenceSpecError) as caught:
        validate_artifact_evidence_spec(replace(spec, limits=unsafe_limits))

    assert str(unsafe_value) not in str(caught.value)


@pytest.mark.parametrize(
    "field_name",
    ("getter_timeout_seconds", "finalize_timeout_seconds"),
)
def test_validator_rejects_sub_millisecond_evidence_timeouts(
    field_name: str,
) -> None:
    """验证 timeout 不会小到被跨平台计时器舍入为禁用。

    输入参数：
        field_name：getter 与 finalize 两个允许浮点的超时字段。
    输出返回值：
        无；小于 1ms 的 timeout 必须在 spec 门禁失败，1ms 边界可接受。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-001"]
    invalid_limits = replace(spec.limits, **{field_name: 0.000_999})
    valid_limits = replace(spec.limits, **{field_name: 0.001})

    with pytest.raises(ArtifactEvidenceSpecError):
        validate_artifact_evidence_spec(replace(spec, limits=invalid_limits))
    validate_artifact_evidence_spec(replace(spec, limits=valid_limits))


def test_pdf_archive_spec_pins_safe_finalize_getter_and_metric_contract() -> None:
    """验证 PDF 章节归档任务的完整取证语义。

    输入参数：
        无；从公共 catalog 取 BatchOperation-003。
    输出返回值：
        无；断言收尾动作仅固定 PDF 目录与 ZIP 输出，
        getter 读取单一相对路径，metric 只引用稳定 gold key。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-BatchOperation-003"]
    slot = spec.artifact_slots[0]
    metric = slot.metrics[0]

    assert spec.finalize_action_id == "archive-pdf-directory"
    assert json.loads(spec.finalize_options_json) == {
        "input_directory_relative_path": "Desktop/book",
        "member_suffix": ".pdf",
        "output_relative_path": "Desktop/book/book.zip",
    }
    assert (slot.getter_kind, slot.locator_relative_paths) == (
        "file",
        ("Desktop/book/book.zip",),
    )
    assert (
        metric.metric_id,
        metric.contract_id,
        json.loads(metric.options_json),
        metric.expected_kind,
        metric.expected_options_json,
        metric.gold_keys,
    ) == (
        "compare_archive",
        "pdf-chapter-archive.v1",
        {"file_type": "pdf"},
        "gold-assets",
        None,
        ("osworld-gold:5df7b33a-9f77-4101-823e-02f863e1c1ae:expected:0:v1",),
    )


def test_presentation_notes_spec_pins_save_action_and_pptx_options() -> None:
    """验证讲者备注 PPTX 任务固定窗口保存与比较选项。

    输入参数：
        无；从公共 catalog 取 CombinationDocs-009。
    输出返回值：
        无；断言仅保存固定 Impress 窗口，取固定 PPTX，
        并保留源 evaluator 的 shape/bullet 选项。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-CombinationDocs-009"]
    slot = spec.artifact_slots[0]
    metric = slot.metrics[0]

    assert spec.finalize_action_id == "save-active-libreoffice-document"
    assert json.loads(spec.finalize_options_json) == {
        "activation_settle_seconds": 5.0,
        "application": "impress",
        "post_save_settle_seconds": 1.0,
        "strict_window_title": ("lecture1-2021-with-ink.pptx - LibreOffice Impress"),
    }
    assert slot.locator_relative_paths == ("Desktop/lecture1-2021-with-ink.pptx",)
    assert (
        metric.metric_id,
        metric.contract_id,
        json.loads(metric.options_json),
        metric.gold_keys,
    ) == (
        "compare_pptx_files",
        "speaker-notes.no-shape-no-bullets.v1",
        {"examine_shape": False, "examine_bullets": False},
        ("osworld-gold:eb303e01-261e-4972-8c07-c9b4e7a4922a:expected:0:v1",),
    )


@pytest.mark.parametrize(
    (
        "task_id",
        "activation_settle_seconds",
        "post_save_settle_seconds",
    ),
    (
        ("Operation-FileOperate-CombinationDocs-009", 5.0, 1.0),
        ("Operation-FileOperate-CombinationDocs-012", 0.5, 0.5),
        ("Operation-FileOperate-SearchAndWrite-001", 0.5, 0.5),
        ("Operation-FileOperate-SearchAndWrite-003", 0.5, 0.5),
        ("Operation-FileOperate-SearchAndWrite-005", 0.5, 1.0),
        ("Operation-FileOperate-Settings-001", 0.5, 1.0),
        ("Operation-WebOperate-SearchAndWrite-001", 0.5, 1.0),
    ),
)
def test_save_specs_pin_the_legacy_activation_settle(
    task_id: str,
    activation_settle_seconds: float,
    post_save_settle_seconds: float,
) -> None:
    """验证七个保存任务逐项冻结旧实现的前后稳定等待。

    输入参数：
        task_id：使用 strict-save 的 canonical 任务标识。
        activation_settle_seconds：从可信旧实现恢复的固定等待秒数。
        post_save_settle_seconds：发送 Ctrl+S 后的固定等待总秒数。
    输出返回值：
        无；断言等待只来自受摘要保护的 finalize options。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]

    options = json.loads(spec.finalize_options_json)

    assert options["activation_settle_seconds"] == activation_settle_seconds
    assert options["post_save_settle_seconds"] == post_save_settle_seconds


@pytest.mark.parametrize(
    ("task_id", "source_task_id"),
    (
        (
            "Operation-FileOperate-CombinationDocs-010",
            "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        ),
        (
            "Operation-FileOperate-SearchAndWrite-001",
            "c7c1e4c3-9e92-4eba-a4b8-689953975ea4",
        ),
        (
            "Operation-FileOperate-SearchAndWrite-005",
            "67890eb6-6ce5-4c00-9e3d-fb4972699b06",
        ),
    ),
)
def test_first_sheet_table_specs_pin_identical_source_options(
    task_id: str,
    source_task_id: str,
) -> None:
    """验证三个首工作表数据任务共享同一受版本控制的选项。

    输入参数：
        task_id/source_task_id：参数化的 canonical task 与源 UUID。
    输出返回值：
        无；断言 ``sheet_data`` 索引、metric contract 和逻辑
        gold key 都与最终源 JSON 一致。
    """

    metric = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id].artifact_slots[0].metrics[0]

    assert (
        metric.metric_id,
        metric.contract_id,
        json.loads(metric.options_json),
        metric.gold_keys,
    ) == (
        "compare_table",
        "sheet-data.first-sheet.v1",
        {"rules": [{"type": "sheet_data", "sheet_idx0": 0, "sheet_idx1": "EI0"}]},
        (f"osworld-gold:{source_task_id}:expected:0:v1",),
    )


def test_problem_invoice_spec_pins_two_slots_and_inline_membership_projection() -> None:
    """验证问题发票任务的 PDF 与目录成员必须独立取证。

    输入参数：
        无；取 CombinationDocs-011 的两个槽位及各自 metric。
    输出返回值：
        无；断言 PDF 使用第一个外部 gold，目录规则使用
        已确认 inline gold 且可直接调用固定 membership metric。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-CombinationDocs-011"]
    pdf_metric = spec.artifact_slots[0].metrics[0]
    membership_metric = spec.artifact_slots[1].metrics[0]

    assert (
        pdf_metric.metric_id,
        pdf_metric.contract_id,
        json.loads(pdf_metric.options_json),
        pdf_metric.gold_keys,
    ) == (
        "compare_pdfs",
        "problem-invoice-content.v1",
        {},
        ("osworld-gold:337d318b-aa07-4f4f-b763-89d9a2dd013f:expected:0:v1",),
    )
    assert membership_metric.metric_input_projection_id == (
        "inline-rule.as-gold.no-options.v1"
    )
    assert membership_metric.expected_options_json is not None
    assert hashlib.sha256(
        membership_metric.expected_options_json.encode("utf-8")
    ).hexdigest() == (
        "98fd1221d8db597b023ebc11ed07bde048f7840e689ee08e0ded6c255df70419"
    )
    gold, options = project_inline_artifact_metric_inputs(membership_metric)
    result = evaluate_artifact_metric(
        membership_metric.contract_id,
        actual=("Invoice # 243729.pdf",),
        gold=gold,
        options=options,
    )
    assert result.score == 1.0


@pytest.mark.parametrize(
    ("task_id", "metric_id", "contract_id", "options", "source_task_id"),
    (
        (
            "Operation-FileOperate-CombinationDocs-012",
            "compare_references",
            "apa7-references.content-only.base-0_6.v1",
            {"content_only": True, "reference_base_result": 0.6},
            "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        ),
        (
            "Operation-FileOperate-SearchAndWrite-003",
            "compare_docx_files",
            "docx-content.v1",
            {},
            "da52d699-e8d2-4dc5-9191-a2199e0b6a9b",
        ),
        (
            "Operation-FileOperate-CombinationDocs-015",
            "compare_text_file",
            "bibtex.ignore-blanks.v1",
            {"ignore_blanks": True},
            "df67aebb-fb3a-44fd-b75b-51b6012df509",
        ),
    ),
)
def test_single_document_specs_pin_metric_options_and_logical_gold(
    task_id: str,
    metric_id: str,
    contract_id: str,
    options: dict[str, object],
    source_task_id: str,
) -> None:
    """验证 APA DOCX、结果 DOCX 与 BibTeX 任务的单文档 metric。

    输入参数：
        task_id/metric_id/contract_id/options/source_task_id：参数化的
        源 evaluator 语义。
    输出返回值：
        无；断言 metric 名称、版本 contract、原始 options 与
        不含 URL/size/SHA 的逻辑 gold key 精确固定。
    """

    metric = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id].artifact_slots[0].metrics[0]

    assert (
        metric.metric_id,
        metric.contract_id,
        json.loads(metric.options_json),
        metric.metric_input_projection_id,
        metric.gold_keys,
    ) == (
        metric_id,
        contract_id,
        options,
        "gold-assets.with-evaluator-options.v1",
        (f"osworld-gold:{source_task_id}:expected:0:v1",),
    )


@pytest.mark.parametrize(
    (
        "task_id",
        "contract_id",
        "source_task_id",
        "input_relative_path",
        "output_directory_relative_path",
    ),
    (
        (
            "Operation-FileOperate-CombinationDocs-013",
            "grf-sheet-print.sheet1.v1",
            "7e287123-70ca-47b9-8521-47db09b69b14",
            "Desktop/GRF-p5y.xlsx",
            "Desktop",
        ),
        (
            "Operation-FileOperate-CombinationDocs-014",
            "supported-rate-sheet-print.sheet1.v1",
            "881deb30-9549-4583-a841-8270c65f2a17",
            "Documents/Fundings/supported_rate.xlsx",
            "Documents/Fundings",
        ),
    ),
)
def test_workbook_bundle_specs_pin_two_gold_assets_and_csv_finalize(
    task_id: str,
    contract_id: str,
    source_task_id: str,
    input_relative_path: str,
    output_directory_relative_path: str,
) -> None:
    """验证两个 workbook/CSV bundle 任务固定双 gold 顺序与转换动作。

    输入参数：
        task_id/contract_id/source_task_id：参数化任务、metric
        contract 与源 UUID。
        input_relative_path/output_directory_relative_path：固定 LibreOffice
        转换的输入和输出目录。
    输出返回值：
        无；断言 finalize 不接受任意命令，两个 gold key 按源
        expected 顺序固定，并保留 ``sheet_print`` options。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]
    metric = spec.artifact_slots[0].metrics[0]

    assert spec.finalize_action_id == "export-calc-first-sheet-csv"
    assert json.loads(spec.finalize_options_json) == {
        "input_relative_path": input_relative_path,
        "output_directory_relative_path": output_directory_relative_path,
    }
    assert (
        metric.metric_id,
        metric.contract_id,
        json.loads(metric.options_json),
        metric.gold_keys,
    ) == (
        "compare_table",
        contract_id,
        {
            "rules": [
                {
                    "type": "sheet_print",
                    "sheet_idx0": "RNSheet1",
                    "sheet_idx1": "ENSheet1",
                }
            ]
        },
        (
            f"osworld-gold:{source_task_id}:expected:0:v1",
            f"osworld-gold:{source_task_id}:expected:1:v1",
        ),
    )


def test_movies_workbook_spec_pins_named_sheet_data_rule() -> None:
    """验证电影任务仅比较固定 ``unseen_movies`` 工作表。

    输入参数：
        无；取 SearchAndWrite-009 的唯一 metric spec。
    输出返回值：
        无；断言源 sheet_data 名称对、版本 contract 与 gold key
        被精确固定。
    """

    metric = (
        OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-FileOperate-SearchAndWrite-009"]
        .artifact_slots[0]
        .metrics[0]
    )

    assert (
        metric.metric_id,
        metric.contract_id,
        json.loads(metric.options_json),
        metric.gold_keys,
    ) == (
        "compare_table",
        "sheet-data.named-unseen-movies.v1",
        {
            "rules": [
                {
                    "type": "sheet_data",
                    "sheet_idx0": "RNunseen_movies",
                    "sheet_idx1": "ENunseen_movies",
                }
            ]
        },
        ("osworld-gold:3e3fc409-bff3-4905-bf16-c968eee3f807:expected:0:v1",),
    )


def test_slide_background_spec_pins_extraction_index_and_continuous_threshold() -> None:
    """验证幻灯片背景图取证与 0.90 连续分阈值均被固定。

    输入参数：
        无；取 Settings-001 的槽位和唯一 metric。
    输出返回值：
        无；断言 getter 只提取源 JSON 指定的 slide index，
        metric options 和显式通过阈值均为 0.90，gold key 固定为
        私有派生清单的 v2 身份。
    """

    slot = OSWORLD_ARTIFACT_EVIDENCE_SPECS[
        "Operation-FileOperate-Settings-001"
    ].artifact_slots[0]
    metric = slot.metrics[0]

    assert slot.getter_kind == "pptx-slide-background-image"
    assert json.loads(slot.getter_options_json) == {"slide_index": 1}
    assert (
        metric.metric_id,
        metric.contract_id,
        metric.score_threshold,
        json.loads(metric.options_json),
        metric.gold_keys,
    ) == (
        "compare_images",
        "slide-index-1.frame-00-08.v1",
        0.90,
        {"score_threshold": 0.90},
        ("osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v2",),
    )


def test_restaurant_workbook_spec_pins_full_fuzzy_table_options() -> None:
    """验证餐厅联系人表格的复合 fuzzy 规则未被简化。

    输入参数：
        无；取 WebOperate-SearchAndWrite-001 的 metric spec。
    输出返回值：
        无；通过 canonical SHA 固定 exact/fuzzy/includes 范围、
        阈值、归一化和字符清理参数，并固定逻辑 gold key。
    """

    metric = (
        OSWORLD_ARTIFACT_EVIDENCE_SPECS["Operation-WebOperate-SearchAndWrite-001"]
        .artifact_slots[0]
        .metrics[0]
    )

    assert (
        metric.metric_id,
        metric.contract_id,
        metric.gold_keys,
    ) == (
        "compare_table",
        "sheet-fuzzy.restaurant-contacts.v1",
        ("osworld-gold:d1acdb87-bb67-4f30-84aa-990e56a09c92:expected:0:v1",),
    )
    assert hashlib.sha256(metric.options_json.encode("utf-8")).hexdigest() == (
        "1a441877a83db07b1b6797c35e9b9d9197b575056fe8e721a96965fba33578dd"
    )


def test_osworld_integration_package_exports_the_evidence_spec_api() -> None:
    """验证 runtime adapter 不需依赖私有模块路径即可读取规格。

    输入参数：
        无；从 ``paraguibench.integrations.osworld`` 包级公共面导入
        catalog、spec 类型、canonicalizer 和 inline 投影器。
    输出返回值：
        无；断言包级导出与定义模块中的公共对象是同一实例。
    """

    from paraguibench.integrations.osworld import (
        OSWORLD_ARTIFACT_EVIDENCE_SPECS as exported_catalog,
        ArtifactEvidenceSpec as exported_spec_type,
        canonical_artifact_evidence_spec_json as exported_canonicalizer,
        project_inline_artifact_metric_inputs as exported_projector,
    )
    from paraguibench.integrations.osworld.artifact_evidence_specs import (
        ArtifactEvidenceSpec,
    )

    assert exported_catalog is OSWORLD_ARTIFACT_EVIDENCE_SPECS
    assert exported_spec_type is ArtifactEvidenceSpec
    assert exported_canonicalizer is canonical_artifact_evidence_spec_json
    assert exported_projector is project_inline_artifact_metric_inputs
