"""OSWorld Chrome profile 与 Google Shopping 活动页的纯评价协议。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re
from typing import TypeVar
import unicodedata
from urllib.parse import parse_qs, urlsplit

from paraguibench.integrations.osworld.state_contracts import (
    ChromeProfileNameObservation,
    GoogleShoppingActiveTabObservation,
)


CHROME_PROFILE_NAME_PROTOCOL_ID = "paraguibench.osworld.chrome-profile-name.v1"
GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID = (
    "paraguibench.osworld.google-shopping-active-tab.v1"
)

_EXPECTED_GOOGLE_SHOPPING_QUERIES = frozenset(
    {
        "drip coffee maker",
        "drip coffee maker sale",
        "black drip coffee maker sale",
        "black drip coffee maker sale between $25 and $60",
    }
)
_EXPECTED_GOOGLE_SHOPPING_FILTERS = frozenset({"Black", "$25 - $60", "On sale"})
_COMPLETE_SELECTION_EVIDENCE = frozenset(
    {
        "semantic_selected_filter_list",
        "semantic_google_filter_state_list",
    }
)
_TRUSTED_GOOGLE_HOSTS = frozenset(
    {
        "google.com",
        "www.google.com",
        "google.com.hk",
        "www.google.com.hk",
        "shopping.google.com",
        "consent.google.com",
    }
)
_TRUSTED_GOOGLE_BLOCK_REASONS = frozenset({"google_captcha", "google_consent"})


class OSWorldStateEvaluationError(RuntimeError):
    """表示状态证据、gold contract 或多 VM 闭包无法可靠评价。"""


@dataclass(frozen=True, slots=True)
class OSWorldStateEvaluation:
    """保存不含 profile、URL 或筛选原值的状态评价结果。

    输入参数：
        protocol_id：实际执行的版本化评价协议。
        passed/score：二值通过状态与 0/1 分数。
        reason_codes：固定、可公开的失败原因码。
        evaluated_vm_count：参与 any-complete 聚合的 VM 数。
        evaluator_error_vm_count：证据不可靠的 VM 数。
        missing_state_count：可评价 VM 中缺失必要状态的总数。
        unexpected_state_count：可评价 VM 中额外状态的总数。
    输出返回值：
        可安全投影到 runtime details 的不可变结果。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    evaluated_vm_count: int
    evaluator_error_vm_count: int
    missing_state_count: int
    unexpected_state_count: int


@dataclass(frozen=True, slots=True)
class _SingleVMStateEvaluation:
    """保存单台 VM 的内部纯评价结果，不携带 observation 原值。"""

    passed: bool
    reason_codes: tuple[str, ...]
    missing_state_count: int = 0
    unexpected_state_count: int = 0


_ObservationT = TypeVar("_ObservationT")


def evaluate_chrome_profile_name_observations(
    observations: Sequence[ChromeProfileNameObservation],
    *,
    expected_name: str,
) -> OSWorldStateEvaluation:
    """按最终 OSWorld exact-match 语义评价一个或多个 profile 快照。

    输入参数：
        observations：每台参与 VM 各自完整读取的 profile observation。
        expected_name：可信 task contract 中的精确 profile 名称。
    输出返回值：
        any-complete 聚合后的二值结果；不同 VM 的字段不会相互拼接。
    异常：
        OSWorldStateEvaluationError：gold 无效、没有 VM 或全部未通过且至少
            一个 observation 不完整。
    """

    if not isinstance(expected_name, str) or not expected_name:
        raise OSWorldStateEvaluationError("profile gold contract 无效")

    def evaluate_one(
        observation: ChromeProfileNameObservation,
    ) -> _SingleVMStateEvaluation:
        """评价单台 VM 的 profile 名称精确状态。

        输入参数：
            observation：一台 VM 的 profile observation。
        输出返回值：
            不含 profile 值的内部通过或失败结果。
        异常：
            OSWorldStateEvaluationError：证据不完整或字段类型无效。
        """

        if not isinstance(observation, ChromeProfileNameObservation):
            raise OSWorldStateEvaluationError("profile observation 类型无效")
        if type(observation.complete) is not bool or not observation.complete:
            raise OSWorldStateEvaluationError("profile observation 不完整")
        if observation.profile_name is not None and not isinstance(
            observation.profile_name, str
        ):
            raise OSWorldStateEvaluationError("profile name 读取结果无效")
        if observation.profile_name == expected_name:
            return _SingleVMStateEvaluation(passed=True, reason_codes=())
        return _SingleVMStateEvaluation(
            passed=False,
            reason_codes=("PROFILE_NAME_MISMATCH",),
            missing_state_count=1,
        )

    return _aggregate_any_complete(
        observations,
        protocol_id=CHROME_PROFILE_NAME_PROTOCOL_ID,
        evaluator=evaluate_one,
    )


def evaluate_google_shopping_active_tab_observations(
    observations: Sequence[GoogleShoppingActiveTabObservation],
) -> OSWorldStateEvaluation:
    """评价 Google Shopping URL 查询与筛选闭集的同快照合取。

    输入参数：
        observations：按参与 VM 分组的完整活动页快照。
    输出返回值：
        一台 VM 必须独立同时满足 URL 与筛选状态；any-complete 仅在至少
        一台完整通过时通过，不会跨 VM 拼接子指标。
    异常：
        OSWorldStateEvaluationError：没有 VM，或在没有完整通过 VM 时出现
            locale、页面阻塞、DOM 完整性等 evaluator 证据错误。
    """

    return _aggregate_any_complete(
        observations,
        protocol_id=GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
        evaluator=_evaluate_google_shopping_active_tab,
    )


def _evaluate_google_shopping_active_tab(
    observation: GoogleShoppingActiveTabObservation,
) -> _SingleVMStateEvaluation:
    """评价单台 VM 的 Google Shopping 活动页快照。

    输入参数：
        observation：同一采样时点的 URL、locale 与筛选闭集证据。
    输出返回值：
        只包含固定原因码与缺失/额外计数的内部结果。
    异常：
        OSWorldStateEvaluationError：正确目标页上的证据不可靠，或字段类型
            违反固定 I/O contract。
    """

    if not isinstance(observation, GoogleShoppingActiveTabObservation):
        raise OSWorldStateEvaluationError("active-tab observation 类型无效")
    if not isinstance(observation.url, str):
        raise OSWorldStateEvaluationError("active-tab URL 类型无效")
    parsed = urlsplit(observation.url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        observation.blocked_reason in _TRUSTED_GOOGLE_BLOCK_REASONS
        and host in _TRUSTED_GOOGLE_HOSTS
    ):
        raise OSWorldStateEvaluationError("Google 活动页被阻塞，无法可靠评价")
    if not _is_google_shopping_page(parsed.scheme, host, parsed.path, parsed.query):
        return _SingleVMStateEvaluation(
            passed=False,
            reason_codes=("WRONG_ACTIVE_PAGE",),
            missing_state_count=1,
        )

    query = parse_qs(parsed.query, keep_blank_values=True)
    query_values = query.get("q", [])
    if (
        len(query_values) != 1
        or query_values[0] not in _EXPECTED_GOOGLE_SHOPPING_QUERIES
    ):
        return _SingleVMStateEvaluation(
            passed=False,
            reason_codes=("SEARCH_QUERY_MISMATCH",),
            missing_state_count=1,
        )

    if not isinstance(observation.locale, str) or not (
        observation.locale.lower().replace("_", "-").startswith("en")
    ):
        raise OSWorldStateEvaluationError("Google Shopping locale 不受支持")
    if type(observation.filter_surface_observed) is not bool:
        raise OSWorldStateEvaluationError("筛选表面 observation 类型无效")
    if not observation.filter_surface_observed:
        raise OSWorldStateEvaluationError("Google Shopping 筛选表面不可观测")
    if type(observation.selection_enumeration_complete) is not bool:
        raise OSWorldStateEvaluationError("筛选闭集 observation 类型无效")
    if not observation.selection_enumeration_complete:
        raise OSWorldStateEvaluationError("Google Shopping 筛选枚举不完整")
    if observation.selection_evidence not in _COMPLETE_SELECTION_EVIDENCE:
        raise OSWorldStateEvaluationError("Google Shopping 完整性证据无效")
    labels = observation.selected_filter_labels
    if not isinstance(labels, tuple) or len(labels) > 128:
        raise OSWorldStateEvaluationError("筛选标签 observation 无效")
    if not all(isinstance(label, str) and len(label) <= 512 for label in labels):
        raise OSWorldStateEvaluationError("筛选标签字段无效")

    selected = {
        normalized for label in labels if (normalized := _normalize_filter_label(label))
    }
    missing = _EXPECTED_GOOGLE_SHOPPING_FILTERS - selected
    unexpected = selected - _EXPECTED_GOOGLE_SHOPPING_FILTERS
    if missing or unexpected:
        return _SingleVMStateEvaluation(
            passed=False,
            reason_codes=("FILTER_STATE_MISMATCH",),
            missing_state_count=len(missing),
            unexpected_state_count=len(unexpected),
        )
    return _SingleVMStateEvaluation(passed=True, reason_codes=())


def _aggregate_any_complete(
    observations: Sequence[_ObservationT],
    *,
    protocol_id: str,
    evaluator: Callable[[_ObservationT], _SingleVMStateEvaluation],
) -> OSWorldStateEvaluation:
    """按最终修复版 any-complete 规则聚合完整的逐 VM 评价。

    输入参数：
        observations：每台参与 VM 的不可变 observation 序列。
        protocol_id：写入安全结果的版本化协议标识。
        evaluator：只评价一台 VM 完整快照的纯函数。
    输出返回值：
        至少一台完整通过则整体通过；全部可评价但均失败则整体零分。
    异常：
        OSWorldStateEvaluationError：没有 VM，或无 VM 通过且至少一台 VM
            证据错误。异常不回显 observation 原值。
    """

    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise OSWorldStateEvaluationError("VM observation 必须是序列")
    if not observations:
        raise OSWorldStateEvaluationError("没有收到任何 VM observation")

    results: list[_SingleVMStateEvaluation] = []
    evaluator_error_count = 0
    for observation in observations:
        try:
            results.append(evaluator(observation))
        except OSWorldStateEvaluationError:
            evaluator_error_count += 1

    passing = [result for result in results if result.passed]
    if passing:
        return OSWorldStateEvaluation(
            protocol_id=protocol_id,
            passed=True,
            score=1.0,
            reason_codes=(),
            evaluated_vm_count=len(observations),
            evaluator_error_vm_count=evaluator_error_count,
            missing_state_count=0,
            unexpected_state_count=0,
        )
    if evaluator_error_count:
        raise OSWorldStateEvaluationError(
            "没有 VM 完整通过，且至少一台 VM 的状态证据无效"
        )

    reason_codes = tuple(
        dict.fromkeys(reason for result in results for reason in result.reason_codes)
    )
    return OSWorldStateEvaluation(
        protocol_id=protocol_id,
        passed=False,
        score=0.0,
        reason_codes=reason_codes,
        evaluated_vm_count=len(observations),
        evaluator_error_vm_count=0,
        missing_state_count=sum(result.missing_state_count for result in results),
        unexpected_state_count=sum(result.unexpected_state_count for result in results),
    )


def _is_google_shopping_page(
    scheme: str,
    host: str,
    path: str,
    query_text: str,
) -> bool:
    """判断 URL 是否为本协议允许的 Google Shopping 页面。

    输入参数：
        scheme/host/path/query_text：``urlsplit`` 得到的 URL 组件。
    输出返回值：
        HTTPS 且主机与 Shopping 页面形态均在固定 allowlist 时返回真。
    """

    if scheme.lower() != "https":
        return False
    if host not in _TRUSTED_GOOGLE_HOSTS - {"consent.google.com"}:
        return False
    if host == "shopping.google.com":
        return True
    query = parse_qs(query_text, keep_blank_values=True)
    return (
        query.get("tbm", [""])[0] == "shop"
        or query.get("udm", [""])[0] == "28"
        or path.rstrip("/").endswith("/shopping")
    )


def _normalize_filter_label(label: str) -> str:
    """仅规范化筛选标签的 Unicode 与布局差异。

    输入参数：
        label：活动页语义控件返回的原始标签。
    输出返回值：
        NFKC、统一 dash 并压缩空白后的标签；不做大小写或模糊匹配。
    """

    normalized = unicodedata.normalize("NFKC", label)
    normalized = re.sub(
        r"[\u2010\u2011\u2012\u2013\u2014\u2212]",
        "-",
        normalized,
    )
    normalized = re.sub(
        (
            r"(?P<left>(?:[$€£¥]\s*)?\d+(?:[.,]\d+)?)"
            r"\s*-\s*"
            r"(?P<right>(?:[$€£¥]\s*)?\d+(?:[.,]\d+)?)"
        ),
        r"\g<left> - \g<right>",
        normalized,
    )
    return " ".join(normalized.split())
