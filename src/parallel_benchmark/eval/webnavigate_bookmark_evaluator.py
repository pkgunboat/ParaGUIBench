"""
Webnavigate 任务评估器：基于 Chrome 收藏夹（Bookmarks）评估。

评估逻辑：
1. 从 VM 读取 Chrome Bookmarks 文件，提取所有书签 URL
2. 根据任务 ID 获取对应的正则匹配规则
3. 使用正则表达式匹配书签 URL
4. 评分 = 匹配到的 URL 数 / 期望的 URL 数

依赖：
- bookmark_utils.py（递归提取书签 URL、探测 Bookmarks 路径）
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# 路径设置：确保可以导入 bookmark_utils 和 desktop_env
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARALLEL_BENCHMARK_DIR = os.path.dirname(_THIS_DIR)
_UBUNTU_ENV_DIR = os.path.dirname(_PARALLEL_BENCHMARK_DIR)
_WEBMALL_ASSETS_DIR = os.path.join(_UBUNTU_ENV_DIR, "examples", "webmall_eval_assets")

for _p in [_UBUNTU_ENV_DIR, _WEBMALL_ASSETS_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# 正则匹配规则注册表
# ---------------------------------------------------------------------------

REGEX_PATTERNS = {
    "Operation-WebOperate-Settings-002": {
        "pattern_groups": [
            {
                "name": "MIT",
                "patterns": [r'https?://(www\.)?mit\.edu/?(?:[?#].*)?$'],
            },
            {
                "name": "University of Cambridge",
                "patterns": [r'https?://(www\.)?cam\.ac\.uk/?(?:[?#].*)?$'],
            },
            {
                "name": "University of Oxford",
                "patterns": [r'https?://(www\.)?ox\.ac\.uk/?(?:[?#].*)?$'],
            },
            {
                "name": "Harvard University",
                "patterns": [r'https?://(www\.)?harvard\.edu/?(?:[?#].*)?$'],
            },
            {
                "name": "Stanford University",
                "patterns": [r'https?://(www\.)?stanford\.edu/?(?:[?#].*)?$'],
            },
            {
                "name": "Imperial College London",
                "patterns": [r'https?://(www\.)?imperial\.ac\.uk/?(?:[?#].*)?$'],
            },
            {
                "name": "ETH Zurich",
                "patterns": [
                    r'https?://(www\.)?ethz\.ch/?(?:[?#].*)?$',
                    r'https?://(www\.)?ethz\.ch/(?:en|de)(?:\.html)?/?(?:[?#].*)?$',
                ],
            },
            {
                "name": "National University of Singapore",
                "patterns": [r'https?://(www\.)?nus\.edu\.sg/?(?:[?#].*)?$'],
            },
            {
                "name": "UCL",
                "patterns": [r'https?://(www\.)?ucl\.ac\.uk/?(?:[?#].*)?$'],
            },
            {
                "name": "UC Berkeley",
                "patterns": [r'https?://(www\.)?berkeley\.edu/?(?:[?#].*)?$'],
            },
        ],
        "description": "2024 QS 前十学校官网书签"
    },
    "Operation-WebOperate-Settings-003": {
        "pattern_groups": [
            {
                "name": "Jim Fan",
                "patterns": [
                    r'https?://(www\.)?jimfan\.me/?(?:[?#].*)?$',
                    r'https?://research\.nvidia\.com/person/linxi-jim-fan/?(?:[?#].*)?$',
                    r'https?://(www\.)?linkedin\.com/in/drjimfan/?(?:[?#].*)?$',
                ],
            },
            {
                "name": "De-An Huang",
                "patterns": [
                    r'https?://research\.nvidia\.com/person/de-an-huang/?(?:[?#].*)?$',
                    r'https?://ai\.stanford\.edu/~dahuang/?(?:[?#].*)?$',
                    r'https?://(www\.)?linkedin\.com/in/de-an-huang-38242a69/?(?:[?#].*)?$',
                ],
            },
            {
                "name": "Yuke Zhu",
                "patterns": [
                    r'https?://(www\.)?yukezhu\.me/?(?:[?#].*)?$',
                    r'https?://(www\.)?cs\.utexas\.edu/people/faculty-researchers/yuke-zhu/?(?:[?#].*)?$',
                    r'https?://experts\.utexas\.edu/yuke_zhu/?(?:[?#].*)?$',
                    r'https?://research\.nvidia\.com/person/yuke-zhu/?(?:[?#].*)?$',
                    r'https?://(www\.)?linkedin\.com/in/yukez/?(?:[?#].*)?$',
                ],
            },
            {
                "name": "Anima Anandkumar",
                "patterns": [
                    r'https?://tensorlab\.cms\.caltech\.edu/users/anima(?:/index\.html|/)?(?:[?#].*)?$',
                    r'https?://(www\.)?eas\.caltech\.edu/people/anima/?(?:[?#].*)?$',
                    r'https?://en\.wikipedia\.org/wiki/Anima_Anandkumar(?:[?#].*)?$',
                    r'https?://(www\.)?linkedin\.com/in/anima-anandkumar/?(?:[?#].*)?$',
                ],
            },
        ],
        "description": "论文首位作者和最后三位作者个人网页书签"
    },
    "Operation-WebOperate-WebNavigate-001": {
        "patterns": [
            r'accuweather.*manchester',
            r'accuweather.*manchester.*air-quality-index'
        ],
        "expected_count": 2,
        "description": "accuweather manchester 天气和空气质量"
    },
    "Operation-WebOperate-WebNavigate-002": {
        "patterns": [
            r'amazon.*help|shipping\.amazon',
            r'GKM69DUUYKQWKWX7'
        ],
        "expected_count": 2,
        "description": "Amazon 运输和退换货政策"
    },
    "Operation-WebOperate-WebNavigate-003": {
        "patterns": [
            r'tesla\.com/model[3ys]'
        ],
        "expected_count": 3,
        "description": "Tesla Model Y/3/S 车型页面"
    },
    "Operation-WebOperate-WebNavigate-004": {
        "patterns": [
            r'libreoffice.*get-help.*install-howto.*(macos|os-x)',
            r'libreoffice.*download.*download-libreoffice'
        ],
        "expected_count": 2,
        "description": "LibreOffice Mac 安装指南和下载页面"
    },
    "Operation-WebOperate-WebNavigate-005": {
        "patterns": [
            r'deerapi.*about-price',
            r'api\.deerapi\.com/pricing',
            r'siliconflow.*pricing'
        ],
        "expected_count": 2,
        "description": "deerAPI 和 Silicon Flow API 价格页面"
    },
    "Operation-WebOperate-WebNavigate-007": {
        "patterns": [
            r'unitree.*about',
            r'unitree.*g1|unitree-g1'
        ],
        "expected_count": 2,
        "description": "Unitree 关于我们和 G1 机器人页面"
    },
    "Operation-WebOperate-WebNavigate-008": {
        "patterns": [
            r'steampowered.*1238810'
        ],
        "expected_count": 1,
        "description": "Steam Battlefield 5 购买页面"
    },
    "Operation-WebOperate-WebNavigate-010": {
        "patterns": [
            r'support\.apple.*(111828|111846|111870)'
        ],
        "expected_count": 3,
        "description": "Apple iPhone 15/14/13 Pro Max 技术规格页面"
    },
    "Operation-WebOperate-WebNavigate-011": {
        "patterns": [
            r'fda.*drugs.*tamiflu.*consumer.*questions.*and.*answers'
        ],
        "expected_count": 1,
        "description": "FDA Tamiflu 副作用和历史信息页面"
    },
}


UNSUPPORTED_TASKS = {
    "Operation-WebOperate-Settings-001": (
        "unsupported: 任务目标是修改书签网页中的用户名，当前 WebNavigate "
        "书签 URL 评价器无法验证网页内状态；任务 JSON 也未声明可评测目标。"
    ),
    "Operation-WebOperate-WebNavigate-009": (
        "unsupported: 任务目标是生成 Google Shopping 商品列表，不是收藏目标网页；"
        "当前书签 URL 评价器无法验证列表内容。"
    ),
}


def has_rule(task_id: str) -> bool:
    """返回指定任务是否有可执行的书签 URL 匹配规则。"""
    return task_id in REGEX_PATTERNS


def has_task_entry(task_id: str) -> bool:
    """返回 evaluator 是否知道该任务，包括明确标注为不可验证的任务。"""
    return task_id in REGEX_PATTERNS or task_id in UNSUPPORTED_TASKS


def _load_task(task: Union[Dict, str]) -> Dict:
    """
    加载任务数据。

    输入:
        task: 任务字典或任务 JSON 文件路径
    输出:
        任务字典
    """
    if isinstance(task, dict):
        return task
    if isinstance(task, str):
        with open(task, "r", encoding="utf-8") as f:
            return json.load(f)
    raise TypeError(f"不支持的任务类型: {type(task)}")


def _match_urls_with_regex(
    patterns: List[str],
    bookmark_urls: List[str],
    expected_count: int,
) -> Dict[str, Any]:
    """
    使用正则表达式匹配书签 URL。

    输入:
        patterns: 正则表达式列表
        bookmark_urls: 书签中实际的 URL 列表
        expected_count: 期望匹配到的 URL 数量
    输出:
        匹配详情字典，包含 matched 列表和分数
    """
    matched = []
    matched_urls = set()  # 避免重复计数

    for url in bookmark_urls:
        for pattern in patterns:
            if re.search(pattern, url, re.IGNORECASE):
                if url not in matched_urls:
                    matched.append({
                        "url": url,
                        "pattern": pattern
                    })
                    matched_urls.add(url)
                break  # 一个 URL 只匹配一次

    matched_count = len(matched)
    score = min(matched_count / expected_count, 1.0) if expected_count > 0 else 0.0

    return {
        "expected_count": expected_count,
        "matched_count": matched_count,
        "score": score,
        "matched": matched,
    }


def _match_urls_with_pattern_groups(
    pattern_groups: List[Dict[str, Any]],
    bookmark_urls: List[str],
) -> Dict[str, Any]:
    """
    按目标分组匹配书签 URL。

    每个分组代表一个必须完成的语义目标（例如一所学校或一位作者）。
    一个分组命中任意候选 URL 即计 1 分，避免多个同类 URL 重复凑满数量。
    """
    group_results = []
    matched = []

    for group in pattern_groups:
        group_name = group.get("name", "")
        patterns = group.get("patterns", [])
        group_matches = []

        for url in bookmark_urls:
            for pattern in patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    group_matches.append({
                        "url": url,
                        "pattern": pattern,
                    })
                    break

        group_passed = bool(group_matches)
        group_result = {
            "name": group_name,
            "passed": group_passed,
            "matched_count": len(group_matches),
            "matched": group_matches,
            "patterns": patterns,
        }
        group_results.append(group_result)

        if group_passed:
            first_match = group_matches[0]
            matched.append({
                "group": group_name,
                "url": first_match["url"],
                "pattern": first_match["pattern"],
            })

    expected_count = len(pattern_groups)
    matched_count = sum(1 for item in group_results if item["passed"])
    score = min(matched_count / expected_count, 1.0) if expected_count > 0 else 0.0

    return {
        "expected_count": expected_count,
        "matched_count": matched_count,
        "score": score,
        "matched": matched,
        "groups": group_results,
    }


def evaluate(
    task: Union[Dict, str],
    agent_answer: Optional[str] = None,
    *,
    vm_ip: Optional[str] = None,
    vm_port: Optional[int] = None,
    controller: Optional[Any] = None,
    bookmark_urls: Optional[List[str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    评估 Webnavigate 任务：使用正则表达式匹配 Chrome 书签 URL。

    支持多种调用方式（按优先级）：
    1. 直接传入 bookmark_urls（已预读的书签 URL 列表）
    2. 传入 controller（PythonController 实例，用于从 VM 读取）
    3. 传入 vm_ip + vm_port（自动创建 controller）

    输入:
        task: 任务字典或任务 JSON 文件路径
        agent_answer: agent 输出（本 evaluator 不使用，保留接口兼容性）
        vm_ip: VM IP 地址
        vm_port: VM Python Server 端口
        controller: PythonController 实例
        bookmark_urls: 已预读的书签 URL 列表（跳过 VM 读取）
    输出:
        评估结果字典:
            - pass: True/False/None
            - score: 0.0 ~ 1.0
            - status: "ok"/"error"/"skip"
            - reason: 解释信息
            - match_detail: 匹配详情
    """
    task_data = _load_task(task)
    task_id = task_data.get("task_id", "unknown")

    # 1. 获取正则匹配规则
    if task_id in UNSUPPORTED_TASKS:
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": UNSUPPORTED_TASKS[task_id],
            "task_id": task_id,
        }

    if task_id not in REGEX_PATTERNS:
        # 任务在 evaluator 端未配置匹配规则；统一上报 evaluator_error，
        # 让上层与 operation_evaluator 的状态语义保持一致
        # （score=-1.0 哨兵表示评价器无法给出有意义的得分）
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": f"任务 {task_id} 未在 webnavigate_bookmark_evaluator 中配置匹配规则。",
            "task_id": task_id,
        }

    regex_config = REGEX_PATTERNS[task_id]
    description = regex_config.get("description", "")

    # 2. 获取书签 URL
    actual_urls = bookmark_urls  # 优先使用直接传入的

    if actual_urls is None and controller is not None:
        try:
            from bookmark_utils import read_bookmark_urls
            actual_urls = read_bookmark_urls(controller)
        except Exception as exc:
            # 评价器无法获取必需输入（书签数据），归类为评价器故障
            return {
                "pass": False,
                "score": -1.0,
                "status": "evaluator_error",
                "reason": f"通过 controller 读取书签失败: {exc}",
                "task_id": task_id,
            }

    if actual_urls is None and vm_ip and vm_port is not None:
        try:
            from desktop_env.controllers.python import PythonController
            from bookmark_utils import read_bookmark_urls
            ctrl = PythonController(vm_ip=vm_ip, server_port=vm_port)
            actual_urls = read_bookmark_urls(ctrl)
        except Exception as exc:
            return {
                "pass": False,
                "score": -1.0,
                "status": "evaluator_error",
                "reason": f"通过 vm_ip={vm_ip}:{vm_port} 读取书签失败: {exc}",
                "task_id": task_id,
            }

    if actual_urls is None:
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": "未提供书签数据来源（需 bookmark_urls / controller / vm_ip+vm_port 之一）。",
            "task_id": task_id,
        }

    # 3. 执行正则匹配
    if "pattern_groups" in regex_config:
        match_detail = _match_urls_with_pattern_groups(
            regex_config["pattern_groups"],
            actual_urls,
        )
    else:
        patterns = regex_config["patterns"]
        expected_count = regex_config["expected_count"]
        match_detail = _match_urls_with_regex(patterns, actual_urls, expected_count)

    expected_count = match_detail["expected_count"]
    score = match_detail["score"]
    passed = score == 1.0

    # 4. 构造结果
    if match_detail["matched_count"] == 0:
        reason = (
            f"{description}: 未匹配到任何 URL（期望 {expected_count} 个），"
            f"书签中有 {len(actual_urls)} 个 URL。"
        )
    elif not passed:
        missing_groups = [
            item.get("name", "")
            for item in match_detail.get("groups", [])
            if not item.get("passed")
        ]
        missing_suffix = (
            f" 缺少: {', '.join(missing_groups)}。"
            if missing_groups else ""
        )
        reason = f"{description}: 部分匹配 {match_detail['matched_count']}/{expected_count}。{missing_suffix}"
    else:
        reason = f"{description}: 全部匹配成功（{match_detail['matched_count']}/{expected_count}）。"

    return {
        "pass": passed,
        "score": score,
        # 严格通过：score==1.0 才 pass（_match_urls_with_regex 已使用 min(...,1.0) 钳位）
        "status": "ok",
        "reason": reason,
        "task_id": task_id,
        "match_detail": match_detail,
        "bookmark_urls": actual_urls,
    }
