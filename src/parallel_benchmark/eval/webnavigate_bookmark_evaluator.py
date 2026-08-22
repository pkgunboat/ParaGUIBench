"""WebNavigate/Settings 书签任务评价器。

评价器使用 Chrome Bookmarks 作为可验证状态，按任务的独立语义目标评分。
URL 规则通过 ``urlsplit`` 分离校验主机、路径和查询参数，不会将出现在
Google 查询或恶意路径里的目标 URL 子串当作真实内容页。
Settings-003 额外要求作者书签位于 ``bookmark_bar/My Favorite Authors``。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Union

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARALLEL_BENCHMARK_DIR = os.path.dirname(_THIS_DIR)
_SRC_DIR = os.path.dirname(_PARALLEL_BENCHMARK_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from parallel_benchmark.eval.webnavigate_url_rules import match_semantic_groups


def _rule(
    hosts: Sequence[str],
    *path_patterns: str,
    fragment_patterns: Optional[Sequence[str]] = None,
    query_equals: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    """构造一条结构化 URL 规则。

    功能：统一生成规则字典，避免大量任务配置重复声明可选字段。
    输入参数：hosts 为允许的完整主机名；path_patterns 为完整路径正则；
    fragment_patterns 为可选锚点规则；query_equals 为必须等于指定值的查询键。
    输出返回值：可供 URL 规则匹配器消费的字典。
    """
    rule: Dict[str, Any] = {
        "hosts": list(hosts),
        "path_patterns": list(path_patterns),
    }
    if fragment_patterns is not None:
        rule["fragment_patterns"] = list(fragment_patterns)
    if query_equals is not None:
        rule["query_equals"] = {
            key: list(values) for key, values in query_equals.items()
        }
    return rule


ACCuweather_HOSTS = ["accuweather.com", "www.accuweather.com"]
AMAZON_HOSTS = ["amazon.com", "www.amazon.com"]
TESLA_HOSTS = ["tesla.com", "www.tesla.com"]
LIBREOFFICE_HOSTS = ["libreoffice.org", "www.libreoffice.org"]
UNITREE_HOSTS = ["unitree.com", "www.unitree.com"]
APPLE_SUPPORT_HOSTS = ["support.apple.com"]
FDA_HOSTS = ["fda.gov", "www.fda.gov"]


URL_RULES: Dict[str, Dict[str, Any]] = {
    "Operation-WebOperate-Settings-002": {
        "pattern_groups": [
            {"name": "MIT", "url_rules": [_rule(["mit.edu", "www.mit.edu"], r"/")]},
            {"name": "University of Cambridge", "url_rules": [_rule(["cam.ac.uk", "www.cam.ac.uk"], r"/")]},
            {"name": "University of Oxford", "url_rules": [_rule(["ox.ac.uk", "www.ox.ac.uk"], r"/")]},
            {"name": "Harvard University", "url_rules": [_rule(["harvard.edu", "www.harvard.edu"], r"/")]},
            {"name": "Stanford University", "url_rules": [_rule(["stanford.edu", "www.stanford.edu"], r"/")]},
            {"name": "Imperial College London", "url_rules": [_rule(["imperial.ac.uk", "www.imperial.ac.uk"], r"/")]},
            {
                "name": "ETH Zurich",
                "url_rules": [_rule(["ethz.ch", "www.ethz.ch"], r"/", r"/(?:en|de)(?:\.html)?/?")],
            },
            {"name": "National University of Singapore", "url_rules": [_rule(["nus.edu.sg", "www.nus.edu.sg"], r"/")]},
            {"name": "UCL", "url_rules": [_rule(["ucl.ac.uk", "www.ucl.ac.uk"], r"/")]},
            {"name": "UC Berkeley", "url_rules": [_rule(["berkeley.edu", "www.berkeley.edu"], r"/")]},
        ],
        "description": "2024 QS 前十学校官网书签",
    },
    "Operation-WebOperate-Settings-003": {
        "required_folder_path": ["bookmark_bar", "My Favorite Authors"],
        "pattern_groups": [
            {
                "name": "Jim Fan",
                "url_rules": [
                    _rule(["jimfan.me", "www.jimfan.me"], r"/"),
                    _rule(["research.nvidia.com"], r"/person/linxi-jim-fan/?"),
                    _rule(["linkedin.com", "www.linkedin.com"], r"/in/drjimfan/?"),
                ],
            },
            {
                "name": "De-An Huang",
                "url_rules": [
                    _rule(["research.nvidia.com"], r"/person/de-an-huang/?"),
                    _rule(["ai.stanford.edu"], r"/~dahuang/?"),
                    _rule(["linkedin.com", "www.linkedin.com"], r"/in/de-an-huang-38242a69/?"),
                ],
            },
            {
                "name": "Yuke Zhu",
                "url_rules": [
                    _rule(["yukezhu.me", "www.yukezhu.me"], r"/"),
                    _rule(["cs.utexas.edu", "www.cs.utexas.edu"], r"/people/faculty-researchers/yuke-zhu/?"),
                    _rule(["experts.utexas.edu"], r"/yuke_zhu/?"),
                    _rule(["research.nvidia.com"], r"/person/yuke-zhu/?"),
                    _rule(["linkedin.com", "www.linkedin.com"], r"/in/yukez/?"),
                ],
            },
            {
                "name": "Anima Anandkumar",
                "url_rules": [
                    _rule(["tensorlab.cms.caltech.edu"], r"/users/anima(?:/index\.html|/)?"),
                    _rule(["eas.caltech.edu", "www.eas.caltech.edu"], r"/people/anima/?"),
                    _rule(["en.wikipedia.org"], r"/wiki/Anima_Anandkumar/?"),
                    _rule(["linkedin.com", "www.linkedin.com"], r"/in/anima-anandkumar/?"),
                ],
            },
        ],
        "description": "指定论文四位作者的个人网页书签及文件夹层级",
    },
    "Operation-WebOperate-WebNavigate-001": {
        "pattern_groups": [
            {
                "name": "Manchester monthly forecast",
                "url_rules": [_rule(
                    ACCuweather_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?gb/manchester/(?:m15-6/)?(?:monthly-weather-forecast|(?:january|february|march|april|may|june|july|august|september|october|november|december)-weather)/329260/?",
                )],
            },
            {
                "name": "Manchester air quality index",
                "url_rules": [_rule(
                    ACCuweather_HOSTS,
                    r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?gb/manchester/(?:m15-6/)?air-quality-index/329260/?",
                )],
            },
        ],
        "description": "AccuWeather Manchester 月度天气和空气质量",
    },
    "Operation-WebOperate-WebNavigate-002": {
        "pattern_groups": [
            {
                "name": "Amazon Shipping help",
                "url_rules": [_rule(["shipping.amazon.com"], r"/help/?")],
            },
            {
                "name": "Amazon returns policy",
                "url_rules": [_rule(
                    AMAZON_HOSTS,
                    r"/gp/help/customer/display\.html/?",
                    query_equals={"nodeId": ["GKM69DUUYKQWKWX7"]},
                )],
            },
        ],
        "description": "Amazon 运输常见问题和退款退货政策",
    },
    "Operation-WebOperate-WebNavigate-003": {
        "pattern_groups": [
            {"name": "Tesla Model Y", "url_rules": [_rule(TESLA_HOSTS, r"/(?:[a-z]{2}_[a-z]{2}/)?modely/?")]},
            {"name": "Tesla Model 3", "url_rules": [_rule(TESLA_HOSTS, r"/(?:[a-z]{2}_[a-z]{2}/)?model3/?")]},
            {"name": "Tesla Model S", "url_rules": [_rule(TESLA_HOSTS, r"/(?:[a-z]{2}_[a-z]{2}/)?models/?")]},
        ],
        "description": "Tesla Model Y、Model 3 和 Model S 官方车型页",
    },
    "Operation-WebOperate-WebNavigate-004": {
        "pattern_groups": [
            {
                "name": "LibreOffice macOS installation",
                "url_rules": [
                    _rule(LIBREOFFICE_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?get-help/install-howto/macos/?"),
                    _rule(
                        LIBREOFFICE_HOSTS,
                        r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?installation-instructions/?",
                        fragment_patterns=[r"macos"],
                    ),
                ],
            },
            {
                "name": "LibreOffice Windows installation",
                "url_rules": [
                    _rule(LIBREOFFICE_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?get-help/install-howto/windows/?"),
                    _rule(
                        LIBREOFFICE_HOSTS,
                        r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?installation-instructions/?",
                        fragment_patterns=[r"windows"],
                    ),
                ],
            },
        ],
        "description": "LibreOffice macOS 和 Windows 安装指南",
    },
    "Operation-WebOperate-WebNavigate-005": {
        "pattern_groups": [
            {
                "name": "deerAPI pricing",
                "url_rules": [
                    _rule(["helpdoc.deerapi.com"], r"/about-price/?"),
                    _rule(["api.deerapi.com"], r"/pricing/?"),
                ],
            },
            {
                "name": "SiliconFlow pricing",
                "url_rules": [_rule(["siliconflow.com", "www.siliconflow.com"], r"/pricing/?")],
            },
        ],
        "description": "deerAPI 和 SiliconFlow API 价格页",
    },
    "Operation-WebOperate-WebNavigate-007": {
        "pattern_groups": [
            {
                "name": "Unitree About",
                "url_rules": [_rule(UNITREE_HOSTS, r"/(?:[a-z]{2}/)?about/?")],
            },
            {
                "name": "Unitree G1",
                "url_rules": [_rule(UNITREE_HOSTS, r"/(?:[a-z]{2}/)?(?:g1|unitree-g1)/?")],
            },
        ],
        "description": "Unitree 关于我们和 G1 人形机器人页",
    },
    "Operation-WebOperate-WebNavigate-008": {
        "pattern_groups": [
            {
                "name": "Steam Battlefield V",
                "url_rules": [_rule(
                    ["store.steampowered.com"],
                    r"/app/1238810(?:/[^/]*)?/?",
                )],
            },
        ],
        "description": "Steam Battlefield V 商店页",
    },
    "Operation-WebOperate-WebNavigate-010": {
        "pattern_groups": [
            {"name": "iPhone 15 Pro Max", "url_rules": [_rule(APPLE_SUPPORT_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?111828/?")]},
            {"name": "iPhone 14 Pro Max", "url_rules": [_rule(APPLE_SUPPORT_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?111846/?")]},
            {"name": "iPhone 13 Pro Max", "url_rules": [_rule(APPLE_SUPPORT_HOSTS, r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?111870/?")]},
        ],
        "description": "Apple iPhone 15/14/13 Pro Max 技术规格页",
    },
    "Operation-WebOperate-WebNavigate-011": {
        "pattern_groups": [
            {
                "name": "FDA Tamiflu safety/history",
                "url_rules": [
                    _rule(
                        FDA_HOSTS,
                        r"/drugs/postmarket-drug-safety-information-patients-and-providers/tamiflu-consumer-questions-and-answers/?",
                    ),
                    _rule(
                        FDA_HOSTS,
                        r"/drugs/postmarket-drug-safety-information-patients-and-providers/tamiflu-pediatric-adverse-events-questions-and-answers/?",
                    ),
                ],
            },
        ],
        "description": "FDA Tamiflu 副作用及历史信息页",
    },
}

# 保留旧导出名，避免外部调试脚本因变量更名失效。
REGEX_PATTERNS = URL_RULES

UNSUPPORTED_TASKS = {
    "Operation-WebOperate-WebNavigate-009": (
        "unsupported in bookmark mode: 原 OSWorld gold 检查活动标签页查询词、"
        "已选筛选及无额外筛选；该任务应由 osworld_active_tab 路由评价。"
    ),
}


def has_rule(task_id: str) -> bool:
    """返回任务是否有可执行 URL 规则。

    输入参数：task_id 为 canonical 任务 ID。
    输出返回值：规则表存在该任务时返回 True。
    """
    return task_id in URL_RULES


def has_task_entry(task_id: str) -> bool:
    """返回评价器是否识别该任务。

    输入参数：task_id 为 canonical 任务 ID。
    输出返回值：有可执行规则或有明确不可评声明时返回 True。
    """
    return task_id in URL_RULES or task_id in UNSUPPORTED_TASKS


def _load_task(task: Union[Dict[str, Any], str]) -> Dict[str, Any]:
    """加载任务配置。

    输入参数：task 为已解析字典或 JSON 文件路径。
    输出返回值：解析后的任务字典。
    """
    if isinstance(task, dict):
        return task
    if isinstance(task, str):
        with open(task, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    raise TypeError(f"不支持的任务类型: {type(task)}")


def _records_in_required_folder(
    records: Sequence[Dict[str, Any]],
    required_path: Sequence[str],
) -> List[Dict[str, Any]]:
    """筛选位于指定 Chrome 文件夹层级的 URL 记录。

    输入参数：records 为保留 folder_path 的书签记录；required_path 为
    从 Chrome roots 开始的完整期望路径。
    输出返回值：类型为 url、URL 非空且路径与期望完全相等的记录列表。
    """
    expected = list(required_path)
    selected: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        url = str(record.get("url") or "").strip()
        folder_path = record.get("folder_path")
        record_type = record.get("type", "url")
        if record_type == "url" and url and folder_path == expected:
            selected.append(record)
    return selected


def _read_records_from_controller(controller: Any) -> List[Dict[str, Any]]:
    """从 VM controller 读取结构化书签记录。

    输入参数：controller 为 PythonController 实例。
    输出返回值：包含 URL 和 folder_path 的记录列表；读取失败时向上抛异常。
    """
    from stages.webmall_eval_assets.bookmark_utils import read_bookmark_records

    return read_bookmark_records(controller)


def evaluate(
    task: Union[Dict[str, Any], str],
    agent_answer: Optional[str] = None,
    *,
    vm_ip: Optional[str] = None,
    vm_port: Optional[int] = None,
    controller: Optional[Any] = None,
    bookmark_urls: Optional[List[str]] = None,
    bookmark_records: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """根据 Chrome 书签状态评估 WebNavigate/Settings 任务。

    功能：获取扁平 URL 及可选层级记录，按独立语义目标一对一匹配；
    对 Settings-003 先强制校验书签栏和指定文件夹。
    输入参数：task 为任务配置；agent_answer 仅保留接口兼容；vm_ip/vm_port
    或 controller 可用于现场读取；bookmark_urls/bookmark_records 为已预读证据。
    输出返回值：包含 pass、score、status、reason、task_id 和 match_detail 的评价字典。
    """
    del agent_answer, kwargs
    task_data = _load_task(task)
    task_id = str(task_data.get("task_id") or "unknown")

    if task_data.get("skip_eval"):
        return {
            "pass": None,
            "score": None,
            "status": "skip",
            "reason": task_data.get("skip_eval_reason") or "任务已标记 skip_eval=true。",
            "task_id": task_id,
        }
    if task_id in UNSUPPORTED_TASKS:
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": UNSUPPORTED_TASKS[task_id],
            "task_id": task_id,
        }
    if task_id not in URL_RULES:
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": f"任务 {task_id} 未配置可执行的结构化 URL 规则。",
            "task_id": task_id,
        }

    actual_records = bookmark_records
    actual_urls = bookmark_urls
    if actual_records is None and actual_urls is None and controller is not None:
        try:
            actual_records = _read_records_from_controller(controller)
        except Exception as exc:
            return {
                "pass": False,
                "score": -1.0,
                "status": "evaluator_error",
                "reason": f"通过 controller 读取书签失败: {exc}",
                "task_id": task_id,
            }
    if actual_records is None and actual_urls is None and vm_ip and vm_port is not None:
        try:
            from desktop_env.controllers.python import PythonController

            actual_records = _read_records_from_controller(
                PythonController(vm_ip=vm_ip, server_port=vm_port)
            )
        except Exception as exc:
            return {
                "pass": False,
                "score": -1.0,
                "status": "evaluator_error",
                "reason": f"通过 vm_ip={vm_ip}:{vm_port} 读取书签失败: {exc}",
                "task_id": task_id,
            }

    if actual_records is not None and actual_urls is None:
        actual_urls = [
            str(record.get("url") or "").strip()
            for record in actual_records
            if isinstance(record, dict) and str(record.get("url") or "").strip()
        ]
    if actual_urls is None:
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": "未提供书签数据来源。",
            "task_id": task_id,
        }

    config = URL_RULES[task_id]
    scoring_urls = list(actual_urls)
    required_path = config.get("required_folder_path")
    hierarchy_ok = True
    if required_path is not None:
        if actual_records is None:
            hierarchy_ok = False
            scoring_urls = []
        else:
            selected_records = _records_in_required_folder(actual_records, required_path)
            scoring_urls = [str(record["url"]).strip() for record in selected_records]
            hierarchy_ok = bool(selected_records)

    match_detail = match_semantic_groups(config["pattern_groups"], scoring_urls)
    if required_path is not None:
        match_detail["required_folder_path"] = list(required_path)
        match_detail["hierarchy_evidence_present"] = hierarchy_ok

    score = float(match_detail["score"])
    passed = hierarchy_ok and score == 1.0
    missing_groups = [
        str(item.get("name") or "")
        for item in match_detail.get("groups", [])
        if not item.get("passed")
    ]
    if required_path is not None and not hierarchy_ok:
        reason = (
            f"{config['description']}: 缺少层级证据，要求路径 "
            f"{'/'.join(required_path)}。"
        )
    elif missing_groups:
        reason = (
            f"{config['description']}: 匹配 {match_detail['matched_count']}/"
            f"{match_detail['expected_count']}，缺少: {', '.join(missing_groups)}。"
        )
    else:
        reason = (
            f"{config['description']}: 全部匹配成功（{match_detail['matched_count']}/"
            f"{match_detail['expected_count']}）。"
        )

    return {
        "pass": passed,
        "score": score,
        "status": "ok",
        "reason": reason,
        "task_id": task_id,
        "match_detail": match_detail,
        "bookmark_urls": list(actual_urls),
        "bookmark_records": actual_records,
    }
