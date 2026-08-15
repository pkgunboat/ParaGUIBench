"""OSWorld artifact metric 固定 registry 的 source-parity 测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.osworld.artifact_metrics import (
    OSWORLD_ARTIFACT_METRIC_CONTRACTS,
    ArtifactMetricEvaluationError,
    evaluate_artifact_metric,
)


def test_mountain_hash_name_contract_accepts_each_pinned_allowed_name() -> None:
    """验证图片 SHA 到允许文件名的旧最终精确候选语义。

    输入参数：
        无；构造已解析的可信 actual/gold 内存映射与固定 options。
    输出返回值：
        无；断言每个 SHA 的实际字符串都命中各自允许候选并满分。
    """

    result = evaluate_artifact_metric(
        "mountain-file-hash-name-map.v1",
        actual={"a" * 64: "Kilimanjaro.jpg", "b" * 64: "Everest.jpg"},
        gold={
            "a" * 64: ("Kilimanjaro", "Kilimanjaro.jpg"),
            "b" * 64: ("Everest", "Everest.jpg"),
        },
        options={"expect_in_result": True, "result_not_list": True},
    )

    assert result.score == 1.0
    assert result.matched is True
    assert result.reason_code is None


def test_problematic_membership_contract_preserves_source_substring_semantics() -> None:
    """验证目录成员适配后仍忠实保留旧最终子串判断。

    输入参数：
        无；actual 是已安全解析的成员 tuple，其中 include 名称只作为
        ``.bak`` 超串出现，并含一个无关额外成员。
    输出返回值：
        无；断言换行拼接后的原样子串规则仍通过。该行为是 source parity，
        将来若收紧为 exact/闭集必须发布新的 contract 版本。
    """

    result = evaluate_artifact_metric(
        "problematic-invoice-membership.v1",
        actual=("Invoice # 243729.pdf.bak", "unrelated.tmp"),
        gold={
            "include": ("Invoice # 243729.pdf",),
            "exclude": (
                "invoice TII-20220301-90.pdf",
                "Invoice # GES-20220215-82.pdf",
            ),
        },
        options=None,
    )

    assert result.score == 1.0
    assert result.matched is True
    assert result.reason_code is None


def test_problematic_membership_contract_allows_empty_source_rules() -> None:
    """验证 include/exclude 空序列保留源 ``all([])`` 通过语义。

    输入参数：
        无；adapter 提供合法的空成员 tuple，可信 gold 的两类规则也为空。
    输出返回值：
        无；断言换行文本上的空 include/exclude 合取结果为满分。
    """

    result = evaluate_artifact_metric(
        "problematic-invoice-membership.v1",
        actual=(),
        gold={"include": (), "exclude": ()},
        options=None,
    )

    assert result.score == 1.0
    assert result.matched is True
    assert result.reason_code is None


def test_bibtex_contract_collapses_whitespace_with_ignore_blanks_enabled() -> None:
    """验证 BibTeX contract 复现旧最终 ignore_blanks 归一化。

    输入参数：
        无；actual/gold 使用 UTF-8 bytes，并故意采用不同的换行、制表符、
        连续空格和行结构。
    输出返回值：
        无；断言所有空白按旧语义折叠成单空格后精确匹配。
    """

    result = evaluate_artifact_metric(
        "bibtex.ignore-blanks.v1",
        actual=b"@article{a,\t title = {One}}\n\n@book{b}",
        gold=b"  @article{a, title = {One}} @book{b}  ",
        options={"ignore_blanks": True},
    )

    assert result.score == 1.0
    assert result.matched is True
    assert result.reason_code is None


def test_mountain_mapping_with_wrong_agent_value_is_content_mismatch() -> None:
    """验证可解析 mapping 中的错误 Agent 值是 FAIL 而不是 evaluator ERROR。

    输入参数：
        无；actual 保持旧 metric 接受的 mapping 外形，但必需 SHA 对应数字值。
    输出返回值：
        无；断言返回安全零分及固定内容不匹配码，不抛 schema 异常。
    """

    result = evaluate_artifact_metric(
        "mountain-file-hash-name-map.v1",
        actual={"a" * 64: 7},
        gold={"a" * 64: ("Kilimanjaro.jpg",)},
        options={"expect_in_result": True, "result_not_list": True},
    )

    assert result.score == 0.0
    assert result.matched is False
    assert result.reason_code == "CONTENT_MISMATCH"


def test_fixed_boolean_options_reject_integer_lookalikes() -> None:
    """验证 Python 的 ``1 == True`` 不会绕过固定 options schema。

    输入参数：
        无；传入值相等但类型错误的 integer options。
    输出返回值：
        无；断言评价器以固定 options 错误码 fail-closed。
    """

    with pytest.raises(ArtifactMetricEvaluationError) as captured:
        evaluate_artifact_metric(
            "mountain-file-hash-name-map.v1",
            actual={"a" * 64: "Kilimanjaro.jpg"},
            gold={"a" * 64: ("Kilimanjaro.jpg",)},
            options={"expect_in_result": 1, "result_not_list": 1},
        )

    assert captured.value.code == "OPTIONS_SCHEMA_ERROR"


def test_bibtex_boolean_option_rejects_integer_lookalike() -> None:
    """验证 BibTeX contract 也严格固定 ``ignore_blanks`` 的 bool 类型。

    输入参数：
        无；传入整数 ``1``，其相等性与 ``True`` 相同但 schema 不同。
    输出返回值：
        无；断言返回 options schema ERROR 而不是执行 metric。
    """

    with pytest.raises(ArtifactMetricEvaluationError) as captured:
        evaluate_artifact_metric(
            "bibtex.ignore-blanks.v1",
            actual=b"same",
            gold=b"same",
            options={"ignore_blanks": 1},
        )

    assert captured.value.code == "OPTIONS_SCHEMA_ERROR"


def test_invalid_agent_utf8_is_safe_observation_schema_error() -> None:
    """验证 Agent bytes 解码失败既可分类，也不会通过异常链回显 bytes。

    输入参数：
        无；actual 使用无效 UTF-8，gold 与 options 合法。
    输出返回值：
        无；断言固定 observation schema 错误码、静态消息和无 cause 链。
    """

    with pytest.raises(ArtifactMetricEvaluationError) as captured:
        evaluate_artifact_metric(
            "bibtex.ignore-blanks.v1",
            actual=b"\xffprivate",
            gold=b"gold",
            options={"ignore_blanks": True},
        )

    assert captured.value.code == "OBSERVATION_SCHEMA_ERROR"
    assert "private" not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ("contract_id", "actual", "gold", "options", "expected_code"),
    (
        (
            "unregistered.private-contract",
            b"actual",
            b"gold",
            None,
            "CONTRACT_NOT_REGISTERED",
        ),
        (
            "mountain-file-hash-name-map.v1",
            "/private/agent-output.json",
            {"a" * 64: ("allowed.jpg",)},
            {"expect_in_result": True, "result_not_list": True},
            "OBSERVATION_SCHEMA_ERROR",
        ),
        (
            "mountain-file-hash-name-map.v1",
            {"a" * 64: "allowed.jpg"},
            {"private-gold-key": ("allowed.jpg",)},
            {"expect_in_result": True, "result_not_list": True},
            "GOLD_SCHEMA_ERROR",
        ),
        (
            "problematic-invoice-membership.v1",
            ("member",),
            {"include": ("member",), "exclude": ("other",)},
            {"private_option": True},
            "OPTIONS_SCHEMA_ERROR",
        ),
    ),
)
def test_metric_contract_errors_are_distinct_and_do_not_echo_inputs(
    contract_id: str,
    actual: object,
    gold: object,
    options: object,
    expected_code: str,
) -> None:
    """验证 contract/schema/gold/options ERROR 可区分且消息不回显值。

    输入参数：
        contract_id/actual/gold/options：参数化的不可信边界输入。
        expected_code：该边界应产生的固定错误分类码。
    输出返回值：
        无；断言异常分类准确，且静态消息不含测试中的 private 标记。
    """

    with pytest.raises(ArtifactMetricEvaluationError) as captured:
        evaluate_artifact_metric(
            contract_id,
            actual=actual,
            gold=gold,
            options=options,
        )

    assert captured.value.code == expected_code
    assert "private" not in str(captured.value).lower()


@pytest.mark.parametrize(
    ("contract_id", "actual", "gold", "options"),
    (
        (
            "mountain-file-hash-name-map.v1",
            {"a" * 64: "private-wrong-name.jpg"},
            {"a" * 64: ("allowed-name.jpg",)},
            {"expect_in_result": True, "result_not_list": True},
        ),
        (
            "problematic-invoice-membership.v1",
            ("private-other.pdf",),
            {"include": ("required.pdf",), "exclude": ("forbidden.pdf",)},
            None,
        ),
        (
            "bibtex.ignore-blanks.v1",
            b"private wrong text",
            b"gold text",
            {"ignore_blanks": True},
        ),
    ),
)
def test_agent_content_mismatch_returns_safe_zero_instead_of_error(
    contract_id: str,
    actual: object,
    gold: object,
    options: object,
) -> None:
    """验证三类已解析但错误的 Agent 内容统一返回安全 FAIL。

    输入参数：
        contract_id/actual/gold/options：参数化遍历三个固定 contract。
    输出返回值：
        无；断言结果为有限零分和固定原因码，且 repr 不含原始内容标记。
    """

    result = evaluate_artifact_metric(
        contract_id,
        actual=actual,
        gold=gold,
        options=options,
    )

    assert result.score == 0.0
    assert result.matched is False
    assert result.reason_code == "CONTENT_MISMATCH"
    assert "private" not in repr(result).lower()


def test_public_registry_exposes_exact_fixed_non_callable_contract_closure() -> None:
    """验证公共 registry 只暴露任务实际引用的固定闭集。

    输入参数：
        无；读取只读 registry 的公开 contract 元数据。
    输出返回值：
        无；断言 15 task 的 14 个唯一 contract 身份与源 metric
        精确对应，且公共值不携带 callable。
    """

    assert {
        contract_id: contract.metric_id
        for contract_id, contract in OSWORLD_ARTIFACT_METRIC_CONTRACTS.items()
    } == {
        "mountain-file-hash-name-map.v1": "check_direct_json_object",
        "problematic-invoice-membership.v1": "check_include_exclude",
        "bibtex.ignore-blanks.v1": "compare_text_file",
        "pdf-chapter-archive.v1": "compare_archive",
        "speaker-notes.no-shape-no-bullets.v1": "compare_pptx_files",
        "sheet-data.first-sheet.v1": "compare_table",
        "problem-invoice-content.v1": "compare_pdfs",
        "apa7-references.content-only.base-0_6.v1": "compare_references",
        "grf-sheet-print.sheet1.v1": "compare_table",
        "supported-rate-sheet-print.sheet1.v1": "compare_table",
        "docx-content.v1": "compare_docx_files",
        "sheet-data.named-unseen-movies.v1": "compare_table",
        "slide-index-1.frame-00-08.v1": "compare_images",
        "sheet-fuzzy.restaurant-contacts.v1": "compare_table",
    }
    assert not any(
        callable(contract) for contract in OSWORLD_ARTIFACT_METRIC_CONTRACTS.values()
    )
    with pytest.raises(TypeError):
        OSWORLD_ARTIFACT_METRIC_CONTRACTS["injected"] = object()  # type: ignore[index]


def test_mountain_contract_preserves_list_match_and_ignores_extra_keys() -> None:
    """验证 direct JSON v1 保留旧最终的 list 分支与 extra-key 行为。

    输入参数：
        无；必需 SHA 对应一个含允许值的列表，并加入非 SHA 额外字段。
    输出返回值：
        无；断言 gold 的每个必需键命中即通过，额外 actual 键不受罚。
    """

    result = evaluate_artifact_metric(
        "mountain-file-hash-name-map.v1",
        actual={
            "a" * 64: ["wrong.jpg", "allowed.jpg"],
            "extra_metadata": {"ignored": True},
        },
        gold={"a" * 64: ("allowed.jpg",)},
        options={"expect_in_result": True, "result_not_list": True},
    )

    assert result.score == 1.0
    assert result.matched is True


def test_empty_bibtex_actual_and_gold_preserve_source_match() -> None:
    """验证空 actual 与空 gold 保留源 compare_text_file 的相等语义。

    输入参数：
        无；actual 与 gold 都是空 bytes，options 合法。
    输出返回值：
        无；断言空文本经 ignore_blanks 归一化后仍满分匹配。
    """

    result = evaluate_artifact_metric(
        "bibtex.ignore-blanks.v1",
        actual=b"",
        gold=b"",
        options={"ignore_blanks": True},
    )

    assert result.score == 1.0
    assert result.matched is True
    assert result.reason_code is None
