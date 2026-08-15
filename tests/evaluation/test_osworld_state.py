"""OSWorld Chrome profile 与 Google Shopping 活动页评价协议测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.osworld import (
    CHROME_PROFILE_NAME_PROTOCOL_ID,
    GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
    ChromeProfileNameObservation,
    GoogleShoppingActiveTabObservation,
    OSWorldStateEvaluationError,
    evaluate_chrome_profile_name_observations,
    evaluate_google_shopping_active_tab_observations,
)


def _shopping_observation(
    **overrides: object,
) -> GoogleShoppingActiveTabObservation:
    """构造一个满足 Google Shopping 闭集协议的合成快照。

    输入参数：
        overrides：需要覆盖的不可变 observation 字段。
    输出返回值：
        可直接交给纯评价器的单时点活动页状态。
    """

    values: dict[str, object] = {
        "url": (
            "https://www.google.com/search?tbm=shop&hl=en&gl=us&q=drip+coffee+maker"
        ),
        "locale": "en-US",
        "filter_surface_observed": True,
        "selection_enumeration_complete": True,
        "selection_evidence": "semantic_google_filter_state_list",
        "selected_filter_labels": (
            "Black",
            "$25 - $60",
            "On sale",
        ),
        "blocked_reason": "",
    }
    values.update(overrides)
    return GoogleShoppingActiveTabObservation(**values)  # type: ignore[arg-type]


def test_profile_name_uses_exact_state_and_any_complete_vm_aggregation() -> None:
    """验证 Chrome profile 按精确字符串评价且允许任一完整 VM 通过。

    输入参数：
        无；第一台 VM 大小写错误，第二台 VM 精确等于 ``Thomas``。
    输出返回值：
        无；聚合结果通过，且详情只包含协议、原因码和计数。
    """

    result = evaluate_chrome_profile_name_observations(
        (
            ChromeProfileNameObservation(profile_name="thomas"),
            ChromeProfileNameObservation(profile_name="Thomas"),
        ),
        expected_name="Thomas",
    )

    assert result.protocol_id == CHROME_PROFILE_NAME_PROTOCOL_ID
    assert result.passed is True
    assert result.score == 1.0
    assert result.reason_codes == ()
    assert result.evaluated_vm_count == 2
    assert "Thomas" not in repr(result)


def test_profile_name_wrong_value_is_agent_failure_not_evaluator_error() -> None:
    """验证可完整读取但值错误的 profile 状态正常记零分。

    输入参数：
        无；使用与 gold 不相等的完整 observation。
    输出返回值：
        无；返回稳定失败原因，不泄露实际或期望 profile 名称。
    """

    result = evaluate_chrome_profile_name_observations(
        (ChromeProfileNameObservation(profile_name="private-name"),),
        expected_name="Thomas",
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.reason_codes == ("PROFILE_NAME_MISMATCH",)
    assert "private-name" not in repr(result)
    assert "Thomas" not in repr(result)


def test_profile_name_missing_from_valid_preferences_is_agent_failure() -> None:
    """验证合法 Preferences 缺少 profile.name 时按最终口径记零分。

    输入参数：
        无；``complete=True`` 表示文件已可靠解析，但目标字段不存在。
    输出返回值：
        无；这是 Agent 未完成状态，不是 evaluator 基础设施错误。
    """

    result = evaluate_chrome_profile_name_observations(
        (ChromeProfileNameObservation(profile_name=None, complete=True),),
        expected_name="Thomas",
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.reason_codes == ("PROFILE_NAME_MISMATCH",)


def test_profile_name_incomplete_observation_is_evaluator_error() -> None:
    """验证无法可靠读取 Preferences 时不会伪装成 Agent 零分。

    输入参数：
        无；构造明确不完整的 profile observation。
    输出返回值：
        无；公开入口抛固定类型 evaluator error。
    """

    with pytest.raises(OSWorldStateEvaluationError, match="完整"):
        evaluate_chrome_profile_name_observations(
            (
                ChromeProfileNameObservation(
                    profile_name=None,
                    complete=False,
                ),
            ),
            expected_name="Thomas",
        )


def test_google_shopping_requires_query_and_exact_closed_filter_set() -> None:
    """验证 URL 查询与筛选闭集必须在同一活动页快照中同时成立。

    输入参数：
        无；使用最终修复版允许的查询短语和三个精确筛选项。
    输出返回值：
        无；活动页协议满分通过。
    """

    result = evaluate_google_shopping_active_tab_observations(
        (_shopping_observation(),)
    )

    assert result.protocol_id == GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID
    assert result.passed is True
    assert result.score == 1.0
    assert result.reason_codes == ()
    assert result.missing_state_count == 0
    assert result.unexpected_state_count == 0


def test_google_shopping_normalizes_layout_dash_but_not_semantics() -> None:
    """验证只消除价格区间的 Unicode 排版差异，不启用模糊匹配。

    输入参数：
        无；价格筛选使用 en dash 与窄空格，语义仍为同一精确标签。
    输出返回值：
        无；规范化后通过。
    """

    result = evaluate_google_shopping_active_tab_observations(
        (
            _shopping_observation(
                selected_filter_labels=(
                    "Black",
                    "$25\u202f–\u202f$60",
                    "On sale",
                )
            ),
        )
    )

    assert result.passed is True


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        (
            {"selected_filter_labels": ("Black", "$25 - $60")},
            "FILTER_STATE_MISMATCH",
        ),
        (
            {
                "selected_filter_labels": (
                    "Black",
                    "$25 - $60",
                    "On sale",
                    "Free shipping",
                )
            },
            "FILTER_STATE_MISMATCH",
        ),
        (
            {
                "url": (
                    "https://www.google.com/search?tbm=shop&q=drip+coffee+maker&q=wrong"
                )
            },
            "SEARCH_QUERY_MISMATCH",
        ),
        (
            {"url": ("https://example.com/search?tbm=shop&q=drip+coffee+maker")},
            "WRONG_ACTIVE_PAGE",
        ),
    ],
)
def test_google_shopping_wrong_complete_state_is_zero(
    overrides: dict[str, object],
    reason_code: str,
) -> None:
    """验证缺失、额外、重复 query 与错误主机均属于正常 Agent 失败。

    输入参数：
        overrides：需要制造的活动页状态错误。
        reason_code：期望出现的固定失败原因码。
    输出返回值：
        无；结果为零分而不是 evaluator error。
    """

    result = evaluate_google_shopping_active_tab_observations(
        (_shopping_observation(**overrides),)
    )

    assert result.passed is False
    assert result.score == 0.0
    assert reason_code in result.reason_codes


@pytest.mark.parametrize(
    "overrides",
    [
        {"selection_enumeration_complete": False},
        {"filter_surface_observed": False},
        {"selection_evidence": "partial_filter_surface"},
        {"locale": "zh-CN"},
        {"blocked_reason": "google_captcha"},
    ],
)
def test_google_shopping_unreliable_right_page_is_evaluator_error(
    overrides: dict[str, object],
) -> None:
    """验证正确 Shopping 页面但证据不完整或被阻塞时 fail-closed。

    输入参数：
        overrides：制造不可靠 observation 的字段覆盖。
    输出返回值：
        无；评价器拒绝把基础设施/证据缺陷记成 Agent 零分。
    """

    with pytest.raises(OSWorldStateEvaluationError):
        evaluate_google_shopping_active_tab_observations(
            (_shopping_observation(**overrides),)
        )


def test_any_complete_passes_one_whole_vm_without_cross_vm_field_splicing() -> None:
    """验证多 VM 聚合只接受单台完整通过，不跨 VM 拼接 URL 与筛选项。

    输入参数：
        无；一台 query 正确但缺筛选，另一台筛选正确但 query 错误。
    输出返回值：
        无；两台均未完整通过，聚合结果必须失败。
    """

    result = evaluate_google_shopping_active_tab_observations(
        (
            _shopping_observation(selected_filter_labels=("Black", "$25 - $60")),
            _shopping_observation(
                url=("https://www.google.com/search?tbm=shop&q=espresso+machine")
            ),
        )
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.evaluated_vm_count == 2


def test_any_complete_allows_one_complete_pass_even_if_another_vm_errors() -> None:
    """验证最终修复版 any-complete 保留单 VM 完整通过语义。

    输入参数：
        无；第一台 VM 证据不完整，第二台 VM 在自身快照中完整通过。
    输出返回值：
        无；整体通过，但只暴露 evaluator-error VM 数量。
    """

    result = evaluate_google_shopping_active_tab_observations(
        (
            _shopping_observation(selection_enumeration_complete=False),
            _shopping_observation(),
        )
    )

    assert result.passed is True
    assert result.evaluator_error_vm_count == 1
    assert result.evaluated_vm_count == 2


def test_empty_vm_observation_set_is_evaluator_error() -> None:
    """验证没有任何参与 VM 证据时不能产生零分或通过结果。

    输入参数：
        无；传入空 observation 闭包。
    输出返回值：
        无；公开入口抛 evaluator error。
    """

    with pytest.raises(OSWorldStateEvaluationError, match="VM"):
        evaluate_google_shopping_active_tab_observations(())
