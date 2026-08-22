"""WebNavigate 官方网页 URL 规则匹配工具。

本模块仅依赖 Python 标准库，将 URL 来源校验与任务评分解耦。
规则对 scheme、hostname 和 path 分别校验，避免目标 URL 只是出现在
Google 查询参数或第三方路径时被子串正则误判。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlsplit


ParsedUrl = Dict[str, Any]
UrlRule = Dict[str, Any]


def parse_trusted_http_url(raw_url: str) -> Optional[ParsedUrl]:
    """解析可用于评分的 HTTP(S) URL。

    功能：拒绝非 HTTP(S)、嵌入用户信息、非默认端口及无主机 URL，
    并返回标准化后的主机、路径、查询和片段。
    输入参数：raw_url，书签中的原始 URL 字符串。
    输出返回值：解析成功时返回字典；URL 不可信或格式错误时返回 None。
    """
    if not isinstance(raw_url, str):
        return None
    candidate = raw_url.strip()
    if not candidate or "\\" in candidate:
        return None

    try:
        parts = urlsplit(candidate)
        scheme = parts.scheme.lower()
        hostname = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except (TypeError, ValueError):
        return None

    if scheme not in {"http", "https"} or not hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if port is not None and port != (443 if scheme == "https" else 80):
        return None

    path = unquote(parts.path or "/")
    return {
        "raw": candidate,
        "scheme": scheme,
        "host": hostname,
        "path": path,
        "query": parse_qs(parts.query, keep_blank_values=True),
        "fragment": unquote(parts.fragment or ""),
    }


def url_matches_rule(raw_url: str, rule: UrlRule) -> bool:
    """判断 URL 是否完整满足一条结构化规则。

    功能：精确校验主机，对 path/fragment 执行完整匹配，并可按键
    校验 query 值；规则中未声明的 query 可作为跟踪参数存在。
    输入参数：raw_url 为原始 URL；rule 为包含 hosts、path_patterns 等的规则字典。
    输出返回值：全部声明条件均满足时返回 True，否则返回 False。
    """
    parsed = parse_trusted_http_url(raw_url)
    if parsed is None:
        return False

    hosts = {str(item).lower().rstrip(".") for item in rule.get("hosts", [])}
    if not hosts or parsed["host"] not in hosts:
        return False

    path_patterns = rule.get("path_patterns") or [r"/.*"]
    if not any(
        re.fullmatch(str(pattern), parsed["path"], flags=re.IGNORECASE)
        for pattern in path_patterns
    ):
        return False

    fragment_patterns = rule.get("fragment_patterns")
    if fragment_patterns is not None and not any(
        re.fullmatch(str(pattern), parsed["fragment"], flags=re.IGNORECASE)
        for pattern in fragment_patterns
    ):
        return False

    for key, accepted_values in (rule.get("query_equals") or {}).items():
        actual_values = parsed["query"].get(str(key), [])
        accepted = {str(value).casefold() for value in accepted_values}
        if not actual_values or not any(value.casefold() in accepted for value in actual_values):
            return False

    return True


def match_semantic_groups(
    pattern_groups: Sequence[Dict[str, Any]],
    bookmark_urls: Sequence[str],
) -> Dict[str, Any]:
    """将书签 URL 与语义目标分组做一对一最大匹配。

    功能：每个分组代表一个独立任务目标，每个 URL 最多支持一个目标；
    通过增广路径获得最大覆盖，避免 locale 变体或宽规则重复凑分。
    输入参数：pattern_groups 为目标分组及其 url_rules；bookmark_urls 为书签 URL 序列。
    输出返回值：包含 expected_count、matched_count、score、matched 和 groups 的详情字典。
    """
    unique_urls = list(dict.fromkeys(str(url).strip() for url in bookmark_urls if str(url).strip()))
    candidates: List[List[Tuple[int, int]]] = []
    for group in pattern_groups:
        group_candidates: List[Tuple[int, int]] = []
        for url_index, url in enumerate(unique_urls):
            for rule_index, rule in enumerate(group.get("url_rules") or []):
                if url_matches_rule(url, rule):
                    group_candidates.append((url_index, rule_index))
                    break
        candidates.append(group_candidates)

    url_to_group: Dict[int, int] = {}
    group_to_match: Dict[int, Tuple[int, int]] = {}

    def assign(group_index: int, visited_urls: set[int]) -> bool:
        """为单个目标寻找增广路径。

        输入参数：group_index 为当前目标下标；visited_urls 为本轮已访问 URL 下标。
        输出返回值：成功建立或重排匹配时返回 True。
        """
        for url_index, rule_index in candidates[group_index]:
            if url_index in visited_urls:
                continue
            visited_urls.add(url_index)
            previous_group = url_to_group.get(url_index)
            if previous_group is None or assign(previous_group, visited_urls):
                url_to_group[url_index] = group_index
                group_to_match[group_index] = (url_index, rule_index)
                return True
        return False

    for group_index in range(len(pattern_groups)):
        assign(group_index, set())

    groups: List[Dict[str, Any]] = []
    matched: List[Dict[str, Any]] = []
    for group_index, group in enumerate(pattern_groups):
        selected = group_to_match.get(group_index)
        candidate_urls = [unique_urls[index] for index, _ in candidates[group_index]]
        item: Dict[str, Any] = {
            "name": group.get("name", ""),
            "passed": selected is not None,
            "candidate_urls": candidate_urls,
        }
        if selected is not None:
            url_index, rule_index = selected
            item["url"] = unique_urls[url_index]
            item["rule_index"] = rule_index
            matched.append({"group": item["name"], "url": item["url"]})
        groups.append(item)

    expected_count = len(pattern_groups)
    matched_count = len(group_to_match)
    score = matched_count / expected_count if expected_count else 0.0
    return {
        "expected_count": expected_count,
        "matched_count": matched_count,
        "score": score,
        "matched": matched,
        "groups": groups,
    }
