"""不依赖桌面图像栈的 JSON 对象比较 metric。"""

from __future__ import annotations

import ast
import json
import logging
from typing import Any, Dict


logger = logging.getLogger("parallel_benchmark.metric.json_object")


def check_direct_json_object(
    result: Any,
    rules: Dict[str, Any],
) -> float:
    """解析并比较 agent 或 active-tab provider 输出的 JSON 对象。

    功能：保持 OSWorld ``check_direct_json_object`` 的字典、标准 JSON
    字符串与 Python repr 兼容语义，同时不导入 ``desktop_env.metrics``
    包，避免一个轻量字典比较被 skimage/easyocr 等图像依赖阻塞。
    输入参数：
        result: 字典、标准 JSON 字符串或旧脚本的 Python 字典文本。
        rules: 含非空 ``expected`` 字典及可选匹配选项的规则。
    输出返回值：
        内容满足规则返回 ``1.0``，可解析但不匹配返回 ``0.0``。
    异常：
        rules 或 expected 配置无效时抛出 ``ValueError``。
    """

    if isinstance(result, str):
        raw_result = result.strip()
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError:
            try:
                result = ast.literal_eval(raw_result)
            except (ValueError, SyntaxError):
                return 0.0
    if not isinstance(result, dict):
        return 0.0
    if not isinstance(rules, dict):
        raise ValueError("check_direct_json_object rules 必须是字典")
    expected = rules.get("expected")
    if not isinstance(expected, dict) or not expected:
        raise ValueError(
            "check_direct_json_object expected 必须是非空字典"
        )

    if not rules.get("expect_in_result", False):
        return float(
            all(result.get(key) == value for key, value in expected.items())
        )

    for key, expected_value in expected.items():
        result_value = result.get(key)
        if isinstance(expected_value, list):
            if isinstance(result_value, list):
                matched = any(
                    candidate in result_value
                    for candidate in expected_value
                )
            elif (
                rules.get("result_not_list", False)
                and isinstance(result_value, str)
            ):
                matched = result_value in expected_value
            else:
                matched = False
            if not matched:
                return 0.0
        elif isinstance(expected_value, str):
            if (
                not isinstance(result_value, (str, list))
                or expected_value not in result_value
            ):
                return 0.0
        else:
            logger.debug(
                "check_direct_json_object: expected value type not supported"
            )
            return 0.0
    return 1.0
