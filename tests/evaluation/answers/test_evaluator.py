"""QA answer evaluator 的公开行为回归测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.answers import (
    EvaluationContractError,
    evaluate_qa_answer,
)


def test_exact_mode_accepts_only_canonical_primary_or_declared_alias() -> None:
    """验证 exact 只接受 canonical 等价主答案或显式别名。

    输入参数：
        无；分别提交排版等价别名和把正确答案嵌入额外断言的攻击文本。
    输出返回值：
        无；前者满分通过，后者必须零分失败。
    """

    task = {
        "answer": "Architectural Blueprints—The “4+1” View Model",
        "accepted_answers": ['paper3.pdf'],
        "answer_match_mode": "exact",
    }

    alias = evaluate_qa_answer(task, "<answer>'PAPER3.PDF'</answer>")
    superstring = evaluate_qa_answer(
        task,
        "<answer>paper3.pdf or paper2.pdf</answer>",
    )

    assert (alias.passed, alias.score, alias.match_type) == (
        True,
        1.0,
        "strict_exact_via_alias",
    )
    assert (superstring.passed, superstring.score) == (False, 0.0)


def test_numeric_mode_accepts_equal_decimal_and_declared_unit_only() -> None:
    """验证 numeric 只接受相等 Decimal 及任务白名单单位。

    输入参数：
        无；提交数值等价的带单位答案，以及额外范围、未声明单位两类超集。
    输出返回值：
        无；只有完整语法的白名单单位答案通过。
    """

    task = {
        "answer": "3.0",
        "answer_match_mode": "numeric",
        "accepted_units": ["page", "pages"],
    }

    equal = evaluate_qa_answer(task, "<answer>3 pages</answer>")
    interval = evaluate_qa_answer(task, "<answer>3 or 4 pages</answer>")
    unlisted = evaluate_qa_answer(task, "<answer>3 million</answer>")

    assert (equal.passed, equal.score, equal.match_type) == (
        True,
        1.0,
        "numeric_value",
    )
    assert interval.passed is False
    assert unlisted.passed is False


def test_keyed_numeric_set_is_order_independent_but_rejects_supersets() -> None:
    """验证 keyed numeric set 忽略键和值顺序但精确限制集合。

    输入参数：
        无；分别提交同集合重排、多报页码和重复页码。
    输出返回值：
        无；只有同键同集合的重排答案通过。
    """

    task = {
        "answer": "match:2,3,5;unmatch:8",
        "answer_match_mode": "keyed_numeric_set",
    }

    reordered = evaluate_qa_answer(
        task,
        "<answer>unmatch:8;match:5,2,3</answer>",
    )
    extra = evaluate_qa_answer(
        task,
        "<answer>match:2,3,5,7;unmatch:8</answer>",
    )
    duplicate = evaluate_qa_answer(
        task,
        "<answer>match:2,3,3,5;unmatch:8</answer>",
    )

    assert (reordered.passed, reordered.match_type) == (
        True,
        "keyed_numeric_set",
    )
    assert extra.passed is False
    assert duplicate.passed is False


def test_ordered_structured_requires_same_item_count_and_order() -> None:
    """验证 ordered structured 保留分号项顺序且拒绝缺失/额外项。

    输入参数：
        无；提交 canonical 顺序、反序和带额外步骤的答案。
    输出返回值：
        无；仅逐项同序的一对一答案通过。
    """

    task = {
        "answer": "entry burn; aerodynamic guidance; vertical landing",
        "answer_match_mode": "ordered_structured",
    }

    canonical = evaluate_qa_answer(
        task,
        "<answer>entry burn; aerodynamic guidance; vertical landing</answer>",
    )
    reversed_result = evaluate_qa_answer(
        task,
        "<answer>vertical landing; aerodynamic guidance; entry burn</answer>",
    )
    extra = evaluate_qa_answer(
        task,
        (
            "<answer>entry burn; aerodynamic guidance; vertical landing; "
            "splashdown</answer>"
        ),
    )

    assert (canonical.passed, canonical.score) == (True, 1.0)
    assert reversed_result.passed is False
    assert extra.passed is False


def test_implicit_structured_uses_unordered_one_to_one_f1() -> None:
    """验证无显式模式的分号答案采用无序一对一 F1。

    输入参数：
        无；提交重排答案、重复项和包含额外项的超集。
    输出返回值：
        无；重排满分通过，重复或额外项降低 precision 且不能通过。
    """

    task = {"answer": "Apple; Samsung; Xiaomi"}

    reordered = evaluate_qa_answer(
        task,
        "<answer>Xiaomi; Apple; Samsung</answer>",
    )
    duplicate = evaluate_qa_answer(
        task,
        "<answer>Apple; Apple; Samsung; Xiaomi</answer>",
    )
    extra = evaluate_qa_answer(
        task,
        "<answer>Apple; Samsung; Xiaomi; JBL</answer>",
    )

    assert (reordered.passed, reordered.score) == (True, 1.0)
    assert duplicate.passed is False
    assert duplicate.details["precision"] < 1.0
    assert extra.passed is False
    assert extra.details["precision"] < 1.0


def test_structured_narrow_equivalence_preserves_units_and_word_identity() -> None:
    """验证结构化单项只接受白名单式排版/单复数窄等价。

    输入参数：
        无；预测改变 ``c²`` 排版及规则单复数，并另测同前缀但不同词。
    输出返回值：
        无；排版与单复数变体通过，``smartphones`` 不得误配 ``smarter``。
    """

    task = {"answer": "smartphones; up:2.16 MeV/c²"}

    narrow = evaluate_qa_answer(
        task,
        "<answer>up: 2.16 MeV/c^2; smartphone</answer>",
    )
    wrong_prefix = evaluate_qa_answer(
        task,
        "<answer>up: 2.16 MeV/c²; smarter</answer>",
    )

    assert narrow.passed is True
    assert wrong_prefix.passed is False


def test_interval_equivalence_requires_explicit_narrow_task_policy() -> None:
    """验证误差区间只有在任务声明窄容差和单位时才可等价。

    输入参数：
        无；同一预测分别在显式 1% GeV/MeV policy 和默认禁用策略下评价。
    输出返回值：
        无；显式窄区间通过，默认配置失败。
    """

    enabled_task = {
        "answer": "x:2.16 GeV; status:stable",
        "allow_interval": True,
        "interval_max_relative_width": 0.01,
        "interval_units": ["GeV", "MeV"],
    }
    disabled_task = {"answer": "x:2.16 GeV; status:stable"}
    prediction = "<answer>x:2160±10 MeV; status:stable</answer>"

    enabled = evaluate_qa_answer(enabled_task, prediction)
    disabled = evaluate_qa_answer(disabled_task, prediction)

    assert enabled.passed is True
    assert disabled.passed is False


def test_legacy_single_value_allows_only_safe_wrapper_and_name_nickname() -> None:
    """验证 legacy 单值仅保留可证明安全的包装与姓名昵称兼容。

    输入参数：
        无；测试固定 answer 前缀、参考姓名前缀昵称，以及否定/候选注入。
    输出返回值：
        无；两个窄兼容变体通过，冲突断言与括号候选必须失败。
    """

    company_task = {"answer": "BYD"}
    name_task = {"answer": "Edwin Catmull"}

    wrapped = evaluate_qa_answer(
        company_task,
        "<answer>The answer is BYD</answer>",
    )
    nickname = evaluate_qa_answer(
        name_task,
        "<answer>Edwin (Ed) Catmull</answer>",
    )
    negated = evaluate_qa_answer(
        company_task,
        "<answer>BYD is not the answer</answer>",
    )
    injected = evaluate_qa_answer(
        company_task,
        "<answer>BYD (or Tesla)</answer>",
    )

    assert wrapped.passed is True
    assert nickname.passed is True
    assert negated.passed is False
    assert injected.passed is False


def test_structured_assertion_and_kv_superset_attacks_fail_closed() -> None:
    """验证结构化候选、问号、否定与 KV 超集不能靠存在正确串通过。

    输入参数：
        无；构造 ``or``、问号、否定 value 和追加 value 四类攻击。
    输出返回值：
        无；所有结果均必须失败，且分数不能达到 1。
    """

    entity_task = {"answer": "Apple; Samsung; Xiaomi"}
    kv_task = {"answer": "model:Doogee; status:stable"}
    attacks = (
        (entity_task, "Apple or Samsung; Xiaomi"),
        (entity_task, "Apple?; Samsung; Xiaomi"),
        (kv_task, "model:not Doogee; status:stable"),
        (kv_task, "model:Doogee S99; status:stable"),
    )

    for task, answer in attacks:
        result = evaluate_qa_answer(task, f"<answer>{answer}</answer>")
        assert result.passed is False
        assert result.score < 1.0


def test_contract_errors_never_echo_model_output() -> None:
    """验证无效任务契约异常不会回显模型原文。

    输入参数：
        无；用哨兵模拟可能含敏感内容的模型输出，并触发未知模式错误。
    输出返回值：
        无；异常类型稳定，异常文本中不得出现哨兵或任务答案。
    """

    sentinel = "PRIVATE_MODEL_OUTPUT_SENTINEL"
    task = {
        "answer": "PRIVATE_GOLD_SENTINEL",
        "answer_match_mode": "unsupported-mode",
    }

    with pytest.raises(EvaluationContractError) as captured:
        evaluate_qa_answer(task, sentinel)

    message = str(captured.value)
    assert sentinel not in message
    assert str(task["answer"]) not in message


def test_only_final_selected_answer_can_trigger_abstention() -> None:
    """验证弃答哨兵与普通答案一样遵循最后完整标签语义。

    输入参数：
        无；分别把弃答放在早期标签和最终标签。
    输出返回值：
        无；最终正确答案通过，最终弃答以稳定脱敏类型失败。
    """

    task = {"answer": "BYD", "answer_match_mode": "exact"}

    recovered = evaluate_qa_answer(
        task,
        (
            "<answer>INSUFFICIENT_EVIDENCE</answer>"
            "<answer>BYD</answer>"
        ),
    )
    abstained = evaluate_qa_answer(
        task,
        (
            "<answer>BYD</answer>"
            "<answer>[INSUFFICIENT EVIDENCE]</answer>"
        ),
    )

    assert recovered.passed is True
    assert (abstained.passed, abstained.score, abstained.match_type) == (
        False,
        0.0,
        "agent_abstained",
    )


def test_repaired_legacy_partial_scores_remain_statistically_compatible() -> None:
    """验证两类失败样本保留旧修复的部分分数口径。

    输入参数：
        无；构造结构化 ``or`` 对冲和包含完整参考实体的明确否定。
    输出返回值：
        无；二者仍失败，但分数分别反映三项命中加一个对冲冲突、以及低置信
        冲突诊断，便于历史统计纵向比较。
    """

    disjunction = evaluate_qa_answer(
        {"answer": "Apple; Samsung; Xiaomi"},
        "<answer>Apple or Samsung; Xiaomi</answer>",
    )
    negation = evaluate_qa_answer(
        {"answer": "Poland"},
        "<answer>Poland is not the answer</answer>",
    )

    assert disjunction.passed is False
    assert disjunction.score == pytest.approx(6 / 7)
    assert negation.passed is False
    assert negation.score == 0.5


def test_each_legacy_alias_is_evaluated_by_its_own_shape() -> None:
    """验证主答案与别名可独立采用单值或结构化形态。

    输入参数：
        无；主答案为结构化列表，别名为任务显式声明的单值。
    输出返回值：
        无；完整命中单值别名时必须通过，而不是被主答案形态提前截断。
    """

    task = {
        "answer": "Rocket League; Balatro",
        "accepted_answers": ["Rocket League"],
    }

    result = evaluate_qa_answer(
        task,
        "<answer>Rocket League</answer>",
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.match_type.endswith("_via_alias")
