"""OSWorld WebNavigate/Settings Chrome 书签纯评价协议测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from paraguibench.evaluation.osworld.bookmarks import (
    CHROME_BOOKMARKS_PROTOCOL_ID,
    OSWORLD_BOOKMARK_TASK_RULES,
    OSWorldBookmarkEvaluationError,
    evaluate_chrome_bookmark_observations,
)
from paraguibench.integrations.osworld.bookmark_contracts import (
    OSWORLD_BOOKMARK_TASK_IDS,
    ChromeBookmarkRecord,
    ChromeBookmarksObservation,
)


_PRIMARY_URLS: dict[str, tuple[str, ...]] = {
    "Operation-WebOperate-Settings-002": (
        "https://www.mit.edu/",
        "https://www.cam.ac.uk/",
        "https://www.ox.ac.uk/",
        "https://www.harvard.edu/",
        "https://www.stanford.edu/",
        "https://www.imperial.ac.uk/",
        "https://ethz.ch/",
        "https://www.nus.edu.sg/",
        "https://www.ucl.ac.uk/",
        "https://www.berkeley.edu/",
    ),
    "Operation-WebOperate-Settings-003": (
        "https://jimfan.me/",
        "https://research.nvidia.com/person/de-an-huang/",
        "https://yukezhu.me/",
        "https://tensorlab.cms.caltech.edu/users/anima/",
    ),
    "Operation-WebOperate-WebNavigate-001": (
        "https://www.accuweather.com/en/gb/manchester/m15-6/monthly-weather-forecast/329260",
        "https://www.accuweather.com/en/gb/manchester/m15-6/air-quality-index/329260",
    ),
    "Operation-WebOperate-WebNavigate-002": (
        "https://shipping.amazon.com/help",
        "https://www.amazon.com/gp/help/customer/display.html?nodeId=GKM69DUUYKQWKWX7",
    ),
    "Operation-WebOperate-WebNavigate-003": (
        "https://www.tesla.com/modely",
        "https://www.tesla.com/model3",
        "https://www.tesla.com/models",
    ),
    "Operation-WebOperate-WebNavigate-004": (
        "https://www.libreoffice.org/installation-instructions/#macos",
        "https://www.libreoffice.org/installation-instructions/#windows",
    ),
    "Operation-WebOperate-WebNavigate-005": (
        "https://helpdoc.deerapi.com/about-price",
        "https://www.siliconflow.com/pricing",
    ),
    "Operation-WebOperate-WebNavigate-007": (
        "https://www.unitree.com/cn/about",
        "https://www.unitree.com/cn/g1",
    ),
    "Operation-WebOperate-WebNavigate-008": (
        "https://store.steampowered.com/app/1238810/_5/",
    ),
    "Operation-WebOperate-WebNavigate-010": (
        "https://support.apple.com/en-us/111828",
        "https://support.apple.com/en-us/111846",
        "https://support.apple.com/en-us/111870",
    ),
    "Operation-WebOperate-WebNavigate-011": (
        "https://www.fda.gov/drugs/postmarket-drug-safety-information-patients-and-providers/tamiflu-pediatric-adverse-events-questions-and-answers",
    ),
}


def _observation(
    urls: tuple[str, ...],
    *,
    folder_path: tuple[str, ...] = ("bookmark_bar",),
) -> ChromeBookmarksObservation:
    """构造一份不泄露标题的完整书签快照。

    输入参数：
        urls：待放入快照的 URL tuple。
        folder_path：每条 URL 共用的 Chrome 根起始文件夹路径。
    输出返回值：
        可供纯 evaluator 消费的不可变 observation。
    """

    return ChromeBookmarksObservation(
        records=tuple(
            ChromeBookmarkRecord(url=url, folder_path=folder_path) for url in urls
        )
    )


@pytest.mark.parametrize("task_id", sorted(_PRIMARY_URLS))
def test_all_eleven_final_rules_accept_their_primary_semantic_targets(
    task_id: str,
) -> None:
    """验证 11 个旧最终任务的主目标语义均可满分。

    输入参数：
        task_id：pytest 注入的 canonical 任务 ID。
    输出返回值：
        无；任一任务的目标数或规则漂移时断言失败。
    """

    folder = (
        ("bookmark_bar", "My Favorite Authors")
        if task_id == "Operation-WebOperate-Settings-003"
        else ("bookmark_bar",)
    )
    result = evaluate_chrome_bookmark_observations(
        task_id,
        [_observation(_PRIMARY_URLS[task_id], folder_path=folder)],
    )

    assert result.protocol_id == CHROME_BOOKMARKS_PROTOCOL_ID
    assert result.passed is True
    assert result.score == 1.0
    assert result.expected_target_count == len(_PRIMARY_URLS[task_id])
    assert result.matched_target_count == len(_PRIMARY_URLS[task_id])
    assert result.reason_codes == ()


def test_rule_catalog_is_exact_and_runtime_immutable() -> None:
    """验证评价目录不会漏任务或被运行时篡改。

    输入参数：
        无。
    输出返回值：
        无；目录必须精确等于固定 11 任务且拒绝赋值。
    """

    assert frozenset(OSWORLD_BOOKMARK_TASK_RULES) == OSWORLD_BOOKMARK_TASK_IDS
    with pytest.raises(TypeError):
        OSWORLD_BOOKMARK_TASK_RULES["untrusted-task"] = next(  # type: ignore[index]
            iter(OSWORLD_BOOKMARK_TASK_RULES.values())
        )


def test_settings_folder_path_is_exact_and_not_cross_folder() -> None:
    """验证 Settings-003 只接受书签栏下精确文件夹层级。

    输入参数：
        无；同一批 URL 被放在错误文件夹。
    输出返回值：
        无；应返回零分且同时标记层级和目标缺失。
    """

    result = evaluate_chrome_bookmark_observations(
        "Operation-WebOperate-Settings-003",
        [
            _observation(
                _PRIMARY_URLS["Operation-WebOperate-Settings-003"],
                folder_path=("other", "My Favorite Authors"),
            )
        ],
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.reason_codes == (
        "BOOKMARK_FOLDER_MISMATCH",
        "BOOKMARK_TARGET_MISSING",
    )


def test_complete_vm_record_sets_are_unioned_for_parallel_workers() -> None:
    """验证多 VM 会合并原子 bookmark records，允许 worker 分工。

    输入参数：
        无；两台 VM 分别只含 Tesla 任务的一部分目标。
    输出返回值：
        无；忠实复现旧 parallel pipeline 的集合聚合，整体满分。
    """

    urls = _PRIMARY_URLS["Operation-WebOperate-WebNavigate-003"]
    result = evaluate_chrome_bookmark_observations(
        "Operation-WebOperate-WebNavigate-003",
        [_observation(urls[:2]), _observation(urls[2:])],
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.matched_target_count == 3
    assert result.evaluated_vm_count == 2


def test_valid_complete_vm_can_win_over_an_incomplete_vm() -> None:
    """验证 any-complete 允许一台完整通过 VM 覆盖另一台证据错误。

    输入参数：
        无；一份 incomplete 和一份满分 Steam 快照。
    输出返回值：
        无；公开结果通过并记录一台 evidence-error VM。
    """

    valid = _observation(_PRIMARY_URLS["Operation-WebOperate-WebNavigate-008"])
    result = evaluate_chrome_bookmark_observations(
        "Operation-WebOperate-WebNavigate-008",
        [replace(valid, complete=False), valid],
    )

    assert result.passed is True
    assert result.evaluator_error_vm_count == 1


def test_incomplete_vm_records_are_never_admitted_to_the_union() -> None:
    """验证不完整 VM 的部分 URL 不会被加入全局集合。

    输入参数：
        无；不完整 VM 含唯一 Steam 目标，完整 VM 为空。
    输出返回值：
        无；结果为 Agent FAIL，同时记录一台 evidence-error VM。
    """

    target = _observation(_PRIMARY_URLS["Operation-WebOperate-WebNavigate-008"])
    result = evaluate_chrome_bookmark_observations(
        "Operation-WebOperate-WebNavigate-008",
        [replace(target, complete=False), _observation(())],
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.evaluator_error_vm_count == 1


@pytest.mark.parametrize(
    "malicious_url",
    [
        "https://evil.example/?next=https://www.tesla.com/modely",
        "https://www.tesla.com.evil.example/modely",
        "https://www.tesla.com:444/modely",
        "https://user@www.tesla.com/modely",
        "javascript:https://www.tesla.com/modely",
    ],
)
def test_url_identity_rejects_substring_and_authority_confusion(
    malicious_url: str,
) -> None:
    """验证第三方查询、伪子域、端口和 userinfo 不能凑目标。

    输入参数：
        malicious_url：pytest 注入的混淆 URL。
    输出返回值：
        无；Tesla 任务的命中数必须为零。
    """

    result = evaluate_chrome_bookmark_observations(
        "Operation-WebOperate-WebNavigate-003",
        [_observation((malicious_url,))],
    )

    assert result.matched_target_count == 0
    assert result.passed is False


def test_malformed_unicode_is_a_fixed_evidence_error_without_secret_repr() -> None:
    """验证非法 Unicode 不泄漏 URL，且统一归为 evidence error。

    输入参数：
        无；记录含无法严格 UTF-8 编码的孤立代理项。
    输出返回值：
        无；只抛固定错误，且 contract repr 不含 URL/文件夹。
    """

    record = ChromeBookmarkRecord(
        url="https://private.example/\ud800",
        folder_path=("bookmark_bar", "private-folder"),
    )
    observation = ChromeBookmarksObservation(records=(record,))

    assert "private" not in repr(record)
    assert "private" not in repr(observation)
    with pytest.raises(
        OSWorldBookmarkEvaluationError,
        match="完整可用的书签证据",
    ):
        evaluate_chrome_bookmark_observations(
            "Operation-WebOperate-WebNavigate-008",
            [observation],
        )


def test_no_complete_vm_fails_closed_instead_of_returning_agent_zero() -> None:
    """验证全部证据不完整时不伪装成 Agent 零分。

    输入参数：
        无；仅传入 complete=False 的快照。
    输出返回值：
        无；评价必须抛固定协议错误。
    """

    with pytest.raises(OSWorldBookmarkEvaluationError, match="没有 VM"):
        evaluate_chrome_bookmark_observations(
            "Operation-WebOperate-WebNavigate-008",
            [ChromeBookmarksObservation(records=(), complete=False)],
        )
