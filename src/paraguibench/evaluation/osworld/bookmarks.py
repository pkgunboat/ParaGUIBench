"""WebNavigate/Settings Chrome 书签任务的纯评价协议。

规则忠实对齐 2026-07-28 审计后的
``webnavigate_bookmark_evaluator.py``：主机、路径、查询和片段分离
校验，每个 URL 最多支持一个语义目标，Settings-003 还要求
精确文件夹层级。本模块不读 VM、不保存 URL，也不信任 Agent 文本。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from types import MappingProxyType
from urllib.parse import parse_qs, unquote, urlsplit

from paraguibench.integrations.osworld.bookmark_contracts import (
    CHROME_BOOKMARKS_PROTOCOL_ID,
    OSWORLD_BOOKMARK_TASK_IDS,
    ChromeBookmarkRecord,
    ChromeBookmarksObservation,
)


_MAX_BOOKMARK_RECORDS = 4096
_MAX_URL_BYTES = 8192
_MAX_FOLDER_DEPTH = 32
_MAX_FOLDER_COMPONENT_BYTES = 1024


class OSWorldBookmarkEvaluationError(RuntimeError):
    """表示书签证据、规则身份或多 VM 闭包无法可靠评价。"""


@dataclass(frozen=True, slots=True)
class BookmarkURLRule:
    """保存一条结构化 URL allowlist 规则。

    输入参数：
        hosts：允许的完整小写主机名。
        path_patterns：对解码后 path 做 ``fullmatch`` 的固定正则。
        fragment_patterns：可选的 fragment 完整匹配规则。
        query_equals：查询键与可接受值闭集。
    输出返回值：
        不可变规则，仅由代码内固定目录构造。
    """

    hosts: tuple[str, ...]
    path_patterns: tuple[str, ...]
    fragment_patterns: tuple[str, ...] | None = None
    query_equals: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class BookmarkTargetGroup:
    """保存一个独立语义目标及其 URL 变体规则。

    输入参数：
        rules：任一命中即可满足该组的固定规则。
    输出返回值：
        不可变目标；不在评价结果中暴露名称或 URL。
    """

    rules: tuple[BookmarkURLRule, ...]


@dataclass(frozen=True, slots=True)
class BookmarkTaskRule:
    """保存单个 canonical task 的书签闭集规则。

    输入参数：
        task_id：与 benchmark task 精确相等的稳定身份。
        groups：必须一对一匹配的全部语义目标。
        required_folder_path：可选的 Chrome root 起始精确路径。
    输出返回值：
        不可变 task 规则。
    """

    task_id: str
    groups: tuple[BookmarkTargetGroup, ...]
    required_folder_path: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class OSWorldBookmarkEvaluation:
    """保存不含 URL、书签名或文件夹名的评价结果。

    输入参数：
        protocol_id/task_rule_id：实际执行的协议与固定规则身份。
        passed/score：完整通过状态与旧协议的目标覆盖分数。
        reason_codes：仅含固定、可公开的原因码。
        evaluated_vm_count/evaluator_error_vm_count：参与聚合的 VM 计数。
        expected_target_count/matched_target_count：完整 VM 记录并集的目标计数。
    输出返回值：
        可安全投影到 RunStore details 的不可变结果。
    """

    protocol_id: str
    task_rule_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    evaluated_vm_count: int
    evaluator_error_vm_count: int
    expected_target_count: int
    matched_target_count: int


@dataclass(frozen=True, slots=True)
class _BookmarkMatchEvaluation:
    """保存完整 VM bookmark record 并集的内部评价计数。"""

    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    expected_target_count: int
    matched_target_count: int


def _url_rule(
    hosts: Sequence[str],
    *path_patterns: str,
    fragment_patterns: Sequence[str] | None = None,
    query_equals: Mapping[str, Sequence[str]] | None = None,
) -> BookmarkURLRule:
    """构造一条内部固定 URL 规则。

    输入参数：
        hosts/path_patterns/fragment_patterns/query_equals：与
            ``BookmarkURLRule`` 同名字段对应的不可变输入。
    输出返回值：
        已折叠为 tuple 的固定规则。
    """

    return BookmarkURLRule(
        hosts=tuple(hosts),
        path_patterns=tuple(path_patterns),
        fragment_patterns=(
            tuple(fragment_patterns) if fragment_patterns is not None else None
        ),
        query_equals=tuple(
            (key, tuple(values)) for key, values in (query_equals or {}).items()
        ),
    )


def _group(*rules: BookmarkURLRule) -> BookmarkTargetGroup:
    """构造一个至少包含一条规则的语义目标。

    输入参数：
        rules：该目标允许的 URL 变体。
    输出返回值：
        不可变目标组。
    """

    if not rules:
        raise ValueError("bookmark target group 不能为空")
    return BookmarkTargetGroup(rules=tuple(rules))


_ACCUWEATHER_HOSTS = ("accuweather.com", "www.accuweather.com")
_AMAZON_HOSTS = ("amazon.com", "www.amazon.com")
_TESLA_HOSTS = ("tesla.com", "www.tesla.com")
_LIBREOFFICE_HOSTS = ("libreoffice.org", "www.libreoffice.org")
_UNITREE_HOSTS = ("unitree.com", "www.unitree.com")
_APPLE_SUPPORT_HOSTS = ("support.apple.com",)
_FDA_HOSTS = ("fda.gov", "www.fda.gov")


_OSWORLD_BOOKMARK_TASK_RULES: dict[str, BookmarkTaskRule] = {
    "Operation-WebOperate-Settings-002": BookmarkTaskRule(
        task_id="Operation-WebOperate-Settings-002",
        groups=(
            _group(_url_rule(("mit.edu", "www.mit.edu"), r"/")),
            _group(_url_rule(("cam.ac.uk", "www.cam.ac.uk"), r"/")),
            _group(_url_rule(("ox.ac.uk", "www.ox.ac.uk"), r"/")),
            _group(_url_rule(("harvard.edu", "www.harvard.edu"), r"/")),
            _group(_url_rule(("stanford.edu", "www.stanford.edu"), r"/")),
            _group(_url_rule(("imperial.ac.uk", "www.imperial.ac.uk"), r"/")),
            _group(
                _url_rule(
                    ("ethz.ch", "www.ethz.ch"),
                    r"/",
                    r"/(?:en|de)(?:\.html)?/?",
                )
            ),
            _group(_url_rule(("nus.edu.sg", "www.nus.edu.sg"), r"/")),
            _group(_url_rule(("ucl.ac.uk", "www.ucl.ac.uk"), r"/")),
            _group(_url_rule(("berkeley.edu", "www.berkeley.edu"), r"/")),
        ),
    ),
    "Operation-WebOperate-Settings-003": BookmarkTaskRule(
        task_id="Operation-WebOperate-Settings-003",
        required_folder_path=("bookmark_bar", "My Favorite Authors"),
        groups=(
            _group(
                _url_rule(("jimfan.me", "www.jimfan.me"), r"/"),
                _url_rule(("research.nvidia.com",), r"/person/linxi-jim-fan/?"),
                _url_rule(("linkedin.com", "www.linkedin.com"), r"/in/drjimfan/?"),
            ),
            _group(
                _url_rule(("research.nvidia.com",), r"/person/de-an-huang/?"),
                _url_rule(("ai.stanford.edu",), r"/~dahuang/?"),
                _url_rule(
                    ("linkedin.com", "www.linkedin.com"), r"/in/de-an-huang-38242a69/?"
                ),
            ),
            _group(
                _url_rule(("yukezhu.me", "www.yukezhu.me"), r"/"),
                _url_rule(
                    ("cs.utexas.edu", "www.cs.utexas.edu"),
                    r"/people/faculty-researchers/yuke-zhu/?",
                ),
                _url_rule(("experts.utexas.edu",), r"/yuke_zhu/?"),
                _url_rule(("research.nvidia.com",), r"/person/yuke-zhu/?"),
                _url_rule(("linkedin.com", "www.linkedin.com"), r"/in/yukez/?"),
            ),
            _group(
                _url_rule(
                    ("tensorlab.cms.caltech.edu",), r"/users/anima(?:/index\.html|/)?"
                ),
                _url_rule(
                    ("eas.caltech.edu", "www.eas.caltech.edu"), r"/people/anima/?"
                ),
                _url_rule(("en.wikipedia.org",), r"/wiki/Anima_Anandkumar/?"),
                _url_rule(
                    ("linkedin.com", "www.linkedin.com"), r"/in/anima-anandkumar/?"
                ),
            ),
        ),
    ),
    "Operation-WebOperate-WebNavigate-001": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-001",
        groups=(
            _group(
                _url_rule(
                    _ACCUWEATHER_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?gb/manchester/(?:m15-6/)?(?:monthly-weather-forecast|(?:january|february|march|april|may|june|july|august|september|october|november|december)-weather)/329260/?",
                )
            ),
            _group(
                _url_rule(
                    _ACCUWEATHER_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?gb/manchester/(?:m15-6/)?air-quality-index/329260/?",
                )
            ),
        ),
    ),
    "Operation-WebOperate-WebNavigate-002": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-002",
        groups=(
            _group(_url_rule(("shipping.amazon.com",), r"/help/?")),
            _group(
                _url_rule(
                    _AMAZON_HOSTS,
                    r"/gp/help/customer/display\.html/?",
                    query_equals={"nodeId": ("GKM69DUUYKQWKWX7",)},
                )
            ),
        ),
    ),
    "Operation-WebOperate-WebNavigate-003": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-003",
        groups=(
            _group(_url_rule(_TESLA_HOSTS, r"/(?:[a-z]{2}_[a-z]{2}/)?modely/?")),
            _group(_url_rule(_TESLA_HOSTS, r"/(?:[a-z]{2}_[a-z]{2}/)?model3/?")),
            _group(_url_rule(_TESLA_HOSTS, r"/(?:[a-z]{2}_[a-z]{2}/)?models/?")),
        ),
    ),
    "Operation-WebOperate-WebNavigate-004": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-004",
        groups=(
            _group(
                _url_rule(
                    _LIBREOFFICE_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?get-help/install-howto/macos/?",
                ),
                _url_rule(
                    _LIBREOFFICE_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?installation-instructions/?",
                    fragment_patterns=(r"macos",),
                ),
            ),
            _group(
                _url_rule(
                    _LIBREOFFICE_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?get-help/install-howto/windows/?",
                ),
                _url_rule(
                    _LIBREOFFICE_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?installation-instructions/?",
                    fragment_patterns=(r"windows",),
                ),
            ),
        ),
    ),
    "Operation-WebOperate-WebNavigate-005": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-005",
        groups=(
            _group(
                _url_rule(("helpdoc.deerapi.com",), r"/about-price/?"),
                _url_rule(("api.deerapi.com",), r"/pricing/?"),
            ),
            _group(
                _url_rule(("siliconflow.com", "www.siliconflow.com"), r"/pricing/?")
            ),
        ),
    ),
    "Operation-WebOperate-WebNavigate-007": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-007",
        groups=(
            _group(_url_rule(_UNITREE_HOSTS, r"/(?:[a-z]{2}/)?about/?")),
            _group(_url_rule(_UNITREE_HOSTS, r"/(?:[a-z]{2}/)?(?:g1|unitree-g1)/?")),
        ),
    ),
    "Operation-WebOperate-WebNavigate-008": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-008",
        groups=(
            _group(
                _url_rule(("store.steampowered.com",), r"/app/1238810(?:/[^/]*)?/?")
            ),
        ),
    ),
    "Operation-WebOperate-WebNavigate-010": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-010",
        groups=(
            _group(
                _url_rule(
                    _APPLE_SUPPORT_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?111828/?"
                )
            ),
            _group(
                _url_rule(
                    _APPLE_SUPPORT_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?111846/?"
                )
            ),
            _group(
                _url_rule(
                    _APPLE_SUPPORT_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?111870/?"
                )
            ),
        ),
    ),
    "Operation-WebOperate-WebNavigate-011": BookmarkTaskRule(
        task_id="Operation-WebOperate-WebNavigate-011",
        groups=(
            _group(
                _url_rule(
                    _FDA_HOSTS,
                    r"/drugs/postmarket-drug-safety-information-patients-and-providers/tamiflu-consumer-questions-and-answers/?",
                ),
                _url_rule(
                    _FDA_HOSTS,
                    r"/drugs/postmarket-drug-safety-information-patients-and-providers/tamiflu-pediatric-adverse-events-questions-and-answers/?",
                ),
            ),
        ),
    ),
}
if frozenset(_OSWORLD_BOOKMARK_TASK_RULES) != OSWORLD_BOOKMARK_TASK_IDS:
    raise RuntimeError("OSWorld bookmark task 目录与证据协议不一致")
OSWORLD_BOOKMARK_TASK_RULES: Mapping[str, BookmarkTaskRule] = MappingProxyType(
    _OSWORLD_BOOKMARK_TASK_RULES
)


def evaluate_chrome_bookmark_observations(
    task_id: str,
    observations: Sequence[ChromeBookmarksObservation],
) -> OSWorldBookmarkEvaluation:
    """合并全部完整 VM 的原子书签记录后执行评价。

    输入参数：
        task_id：必须命中固定 11 任务目录的 canonical ID。
        observations：每台参与 VM 各自的书签快照。
    输出返回值：
        对所有完整 VM 的 ``(URL, folder_path)`` 并集返回旧协议
        目标覆盖分数；不完整 VM 记为 evidence error 且不贡献数据。
    异常：
        OSWorldBookmarkEvaluationError：任务未注册、无 VM，或没有任何
            完整可用的 VM 证据。
    """

    if not isinstance(task_id, str) or task_id not in OSWORLD_BOOKMARK_TASK_RULES:
        raise OSWorldBookmarkEvaluationError("bookmark task 规则未注册")
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise OSWorldBookmarkEvaluationError("bookmark observations 必须是序列")
    if not observations:
        raise OSWorldBookmarkEvaluationError("没有收到任何 VM bookmark observation")

    rule = OSWORLD_BOOKMARK_TASK_RULES[task_id]
    merged_records: list[ChromeBookmarkRecord] = []
    seen_record_keys: set[tuple[str, tuple[str, ...]]] = set()
    complete_vm_count = 0
    evaluator_error_count = 0
    for observation in observations:
        try:
            records = _validate_observation(observation)
        except OSWorldBookmarkEvaluationError:
            evaluator_error_count += 1
            continue
        complete_vm_count += 1
        for record in records:
            key = (record.url, record.folder_path)
            if key in seen_record_keys:
                continue
            seen_record_keys.add(key)
            merged_records.append(record)
    if complete_vm_count == 0:
        raise OSWorldBookmarkEvaluationError("没有 VM 提供完整可用的书签证据")
    result = _evaluate_merged_records(
        rule,
        tuple(merged_records),
    )
    return _public_result(
        rule,
        result,
        evaluated_vm_count=len(observations),
        evaluator_error_vm_count=evaluator_error_count,
    )


def _evaluate_merged_records(
    rule: BookmarkTaskRule,
    records: tuple[ChromeBookmarkRecord, ...],
) -> _BookmarkMatchEvaluation:
    """评价全部完整 VM 去重后的 bookmark record 并集。

    输入参数：
        rule：根据 canonical task ID 取得的固定规则。
        records：已逐 VM 验证并按 ``(URL, folder_path)`` 去重的记录。
    输出返回值：
        仅含布尔值、分数、原因码和计数的内部结果。
    """

    folder_required = rule.required_folder_path is not None
    if folder_required:
        scoring_records = tuple(
            record
            for record in records
            if record.folder_path == rule.required_folder_path
        )
    else:
        scoring_records = records

    unique_urls = tuple(dict.fromkeys(record.url.strip() for record in scoring_records))
    matched_count = _maximum_group_matches(rule.groups, unique_urls)
    expected_count = len(rule.groups)
    score = matched_count / expected_count
    folder_evidence_present = not folder_required or bool(scoring_records)
    passed = folder_evidence_present and matched_count == expected_count
    reason_codes: list[str] = []
    if not folder_evidence_present:
        reason_codes.append("BOOKMARK_FOLDER_MISMATCH")
    if matched_count != expected_count:
        reason_codes.append("BOOKMARK_TARGET_MISSING")
    return _BookmarkMatchEvaluation(
        passed=passed,
        score=score,
        reason_codes=tuple(reason_codes),
        expected_target_count=expected_count,
        matched_target_count=matched_count,
    )


def _validate_observation(
    observation: ChromeBookmarksObservation,
) -> tuple[ChromeBookmarkRecord, ...]:
    """验证 observation 及其全部记录的类型和资源边界。

    输入参数：
        observation：未信任证据 source 返回的对象。
    输出返回值：
        经验证的不可变书签记录 tuple。
    异常：
        OSWorldBookmarkEvaluationError：任一完整性、类型、UTF-8
            长度或文件夹深度约束失效。
    """

    if not isinstance(observation, ChromeBookmarksObservation):
        raise OSWorldBookmarkEvaluationError("bookmark observation 类型无效")
    if type(observation.complete) is not bool or not observation.complete:
        raise OSWorldBookmarkEvaluationError("bookmark observation 不完整")
    if (
        not isinstance(observation.records, tuple)
        or len(observation.records) > _MAX_BOOKMARK_RECORDS
    ):
        raise OSWorldBookmarkEvaluationError("bookmark records 资源上限无效")
    for record in observation.records:
        if not isinstance(record, ChromeBookmarkRecord):
            raise OSWorldBookmarkEvaluationError("bookmark record 类型无效")
        try:
            url_size = len(record.url.encode("utf-8", "strict"))
        except (AttributeError, UnicodeError):
            raise OSWorldBookmarkEvaluationError("bookmark URL contract 无效") from None
        if (
            not isinstance(record.url, str)
            or not record.url.strip()
            or record.url != record.url.strip()
            or url_size > _MAX_URL_BYTES
            or any(not character.isprintable() for character in record.url)
        ):
            raise OSWorldBookmarkEvaluationError("bookmark URL contract 无效")
        if not isinstance(record.folder_path, tuple) or not (
            1 <= len(record.folder_path) <= _MAX_FOLDER_DEPTH
        ):
            raise OSWorldBookmarkEvaluationError("bookmark folder path 无效")
        for component in record.folder_path:
            try:
                component_size = len(component.encode("utf-8", "strict"))
            except (AttributeError, UnicodeError):
                raise OSWorldBookmarkEvaluationError(
                    "bookmark folder path 无效"
                ) from None
            if (
                not isinstance(component, str)
                or not component
                or component != component.strip()
                or component_size > _MAX_FOLDER_COMPONENT_BYTES
                or any(not character.isprintable() for character in component)
            ):
                raise OSWorldBookmarkEvaluationError("bookmark folder path 无效")
    return observation.records


def _maximum_group_matches(
    groups: Sequence[BookmarkTargetGroup],
    urls: Sequence[str],
) -> int:
    """用增广路径计算语义目标与 URL 的最大一对一匹配。

    输入参数：
        groups：当前 task 的固定目标组。
        urls：单台 VM 经文件夹筛选和去重后的 URL。
    输出返回值：
        每个 URL 最多支持一个目标时的最大命中数。
    """

    candidates = [
        [
            index
            for index, url in enumerate(urls)
            if any(_url_matches_rule(url, rule) for rule in group.rules)
        ]
        for group in groups
    ]
    url_to_group: dict[int, int] = {}

    def assign(group_index: int, visited_urls: set[int]) -> bool:
        """为单个目标寻找一条可重排旧匹配的增广路径。

        输入参数：
            group_index：当前目标下标。
            visited_urls：本次 DFS 已访问的 URL 下标。
        输出返回值：
            成功建立或重排匹配时返回 ``True``。
        """

        for url_index in candidates[group_index]:
            if url_index in visited_urls:
                continue
            visited_urls.add(url_index)
            previous_group = url_to_group.get(url_index)
            if previous_group is None or assign(previous_group, visited_urls):
                url_to_group[url_index] = group_index
                return True
        return False

    return sum(assign(group_index, set()) for group_index in range(len(groups)))


def _url_matches_rule(raw_url: str, rule: BookmarkURLRule) -> bool:
    """对一条书签 URL 执行 scheme/host/path/query/fragment 分离校验。

    输入参数：
        raw_url：来自 Bookmarks 快照的完整 URL。
        rule：代码内固定的 allowlist 规则。
    输出返回值：
        全部声明条件都满足时返回 ``True``；任何非法
        URL 或不匹配条件返回 ``False``。
    """

    if not isinstance(raw_url, str) or not raw_url or "\\" in raw_url:
        return False
    try:
        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if scheme not in {"http", "https"} or not host:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if port is not None and port != (443 if scheme == "https" else 80):
        return False
    if host not in {item.lower().rstrip(".") for item in rule.hosts}:
        return False

    path = unquote(parsed.path or "/")
    if not any(
        re.fullmatch(pattern, path, flags=re.IGNORECASE)
        for pattern in rule.path_patterns
    ):
        return False
    fragment = unquote(parsed.fragment or "")
    if rule.fragment_patterns is not None and not any(
        re.fullmatch(pattern, fragment, flags=re.IGNORECASE)
        for pattern in rule.fragment_patterns
    ):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, accepted_values in rule.query_equals:
        actual_values = query.get(key, [])
        accepted = {value.casefold() for value in accepted_values}
        if not actual_values or not any(
            value.casefold() in accepted for value in actual_values
        ):
            return False
    return True


def _public_result(
    rule: BookmarkTaskRule,
    result: _BookmarkMatchEvaluation,
    *,
    evaluated_vm_count: int,
    evaluator_error_vm_count: int,
) -> OSWorldBookmarkEvaluation:
    """将完整 VM 记录并集计数投影为不泄露的公开结果。

    输入参数：
        rule/result：当前固定任务与全局记录并集评价。
        evaluated_vm_count/evaluator_error_vm_count：多 VM 聚合计数。
    输出返回值：
        可持久化的固定协议、原因码和数值结果。
    """

    return OSWorldBookmarkEvaluation(
        protocol_id=CHROME_BOOKMARKS_PROTOCOL_ID,
        task_rule_id=rule.task_id,
        passed=result.passed,
        score=result.score,
        reason_codes=result.reason_codes,
        evaluated_vm_count=evaluated_vm_count,
        evaluator_error_vm_count=evaluator_error_vm_count,
        expected_target_count=result.expected_target_count,
        matched_target_count=result.matched_target_count,
    )


__all__ = [
    "CHROME_BOOKMARKS_PROTOCOL_ID",
    "OSWORLD_BOOKMARK_TASK_RULES",
    "BookmarkTaskRule",
    "OSWorldBookmarkEvaluation",
    "OSWorldBookmarkEvaluationError",
    "evaluate_chrome_bookmark_observations",
]
