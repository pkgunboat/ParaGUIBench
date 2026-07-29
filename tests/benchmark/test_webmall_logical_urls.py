"""WebMall canonical task 的 logical URL 与公开安全门禁。"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ROOT = REPO_ROOT / "benchmark" / "tasks"
HTTP_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
LOGICAL_ORIGIN_PATTERN = re.compile(
    r"webmall://(?P<store_id>[A-Za-z0-9][A-Za-z0-9-]*)"
)
EXPECTED_STORE_IDS = {"store-1", "store-2", "store-3", "store-4"}


def _iter_strings(value: Any) -> list[str]:
    """递归收集 JSON 兼容结构中的全部字符串标量。

    输入参数：
        value：task JSON 中的任意值。
    输出返回值：
        按原结构遍历顺序收集的字符串列表。
    """

    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for nested in value.values():
            strings.extend(_iter_strings(nested))
        return strings
    if isinstance(value, list):
        strings = []
        for nested in value:
            strings.extend(_iter_strings(nested))
        return strings
    return []


def _is_private_http_url(candidate: str) -> bool:
    """判断 HTTP(S) URL 是否直接使用私有 IP 主机。

    输入参数：
        candidate：从 task 字符串中提取的 URL 候选。
    输出返回值：
        主机是私有 IP 时返回 ``True``；域名或无效候选返回 ``False``。
    """

    hostname = urlsplit(candidate.rstrip(".,;:)]}")).hostname
    if hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False


def test_webmall_tasks_use_only_stable_logical_store_urls() -> None:
    """验证 91 个 WebMall task 不含部署 host 且保留四店身份。

    输入参数：
        无；从 release-v1 canonical task 目录读取正式任务。
    输出返回值：
        无；任何私有 IP、runtime gold URL 或未知 store 都会使测试失败。
    """

    webmall_tasks: list[dict[str, Any]] = []
    private_url_count = 0
    observed_store_ids: set[str] = set()
    logical_origin_count = 0

    for task_path in sorted(TASK_ROOT.glob("*.json")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if task.get("task_source") != "WebMall":
            continue
        webmall_tasks.append(task)
        for text in _iter_strings(task):
            private_url_count += sum(
                _is_private_http_url(match.group(0))
                for match in HTTP_URL_PATTERN.finditer(text)
            )
            for match in LOGICAL_ORIGIN_PATTERN.finditer(text):
                logical_origin_count += 1
                observed_store_ids.add(match.group("store_id"))

        expected_urls = task["expected_urls"]
        assert all(url.startswith("webmall://") for url in expected_urls)
        assert task["answer"].split("###") == expected_urls

    assert len(webmall_tasks) == 91
    assert private_url_count == 0, (
        f"canonical WebMall task 仍有 {private_url_count} 个私有 URL"
    )
    assert logical_origin_count > 0
    assert observed_store_ids == EXPECTED_STORE_IDS
