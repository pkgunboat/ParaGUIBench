#!/usr/bin/env python3
"""
WebMall 单任务评价器（整合 String / Cart / Checkout）

功能：
- 按任务 ID 读取 WebMall 任务配置
- 使用 answers_json 中的 agent 返回 URL 列表进行 String 评价
- 使用 AT 评价器进行 Cart/Checkout 评价
- 按 AND 逻辑汇总结果（多个评价器必须同时通过）

使用方法:
    python webmall_task_evaluator.py \
        --task-id MY_TASK_001 \
        --mapping-json mapping.json \
        --answers-json answers.json \
        --vm-ip <HOST_IP> \
        --server-ports 5000 5001
"""

import argparse
import json
import os
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from cart_evaluator_from_at import (
    create_checkpoints_from_urls,
    detect_vm_all_carts,
    evaluate_all_vms,
)
from checkout_evaluator_from_at import (
    ExpectedCheckout,
    extract_checkout_info,
    extract_checkout_info_with_recovery,
    get_at as get_checkout_at,
    verify_checkout,
)

# Browsergym 生成的 task_sets.json 路径。该文件不在本仓库内，需用户自行
# 从上游 WebMall 打包得到；通过环境变量 WEBMALL_TASK_SET_PATH 显式指定。
# 默认尝试项目根 docker/webmall/Browsergym/... 下的相对路径（若存在）。
_repo_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
TASK_SET_PATH = os.environ.get(
    "WEBMALL_TASK_SET_PATH",
    os.path.join(
        _repo_root, "docker", "webmall", "Browsergym",
        "browsergym", "webmall", "src", "browsergym", "webmall",
        "task_sets.json",
    ),
)


def normalize_url(url: str) -> str:
    return url.rstrip("/").lstrip("http://").lstrip("https://")


def load_task_sets(path: str) -> Dict[str, dict]:
    """读取 task_sets.json，返回 task_id -> task_config"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    task_map = {}
    for task_set in data:
        for task in task_set.get("tasks", []):
            task_id = task.get("id")
            if task_id:
                task_map[task_id] = task
    return task_map


def load_json(path: Optional[str]) -> dict:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def map_task_id(external_id: str, mapping: dict) -> str:
    return mapping.get(external_id, external_id)


def get_answers_for_task(
    answers_data: dict,
    external_task_id: str,
    mapped_task_id: str,
) -> List[str]:
    if external_task_id in answers_data:
        return answers_data[external_task_id] or []
    if mapped_task_id in answers_data:
        return answers_data[mapped_task_id] or []
    return []


def evaluate_string(expected_urls: List[str], submitted_urls: List[str]) -> dict:
    expected_norm = {normalize_url(u): u for u in expected_urls}
    submitted_norm = {normalize_url(u): u for u in submitted_urls}

    matched = []
    wrong = []
    for sub_norm, original in submitted_norm.items():
        if sub_norm in expected_norm:
            matched.append(expected_norm[sub_norm])
        else:
            wrong.append(original)

    passed = len(matched) == len(expected_urls) if expected_urls else False
    score = len(matched) / len(expected_urls) if expected_urls else 0.0

    return {
        "type": "string",
        "passed": passed,
        "score": score,
        "matched": matched,
        "wrong": wrong,
        "expected": expected_urls,
        "submitted": submitted_urls,
    }


def evaluate_cart(
    expected_urls: List[str],
    vm_ip: str,
    server_ports: List[int],
    shop_ip: str,
    wait_time: float,
) -> dict:
    checkpoints = create_checkpoints_from_urls(expected_urls)

    all_results = {}
    for port in server_ports:
        results = detect_vm_all_carts(vm_ip, port, shop_ip, wait_time)
        all_results[f"{vm_ip}:{port}"] = results

    eval_results = evaluate_all_vms(all_results, checkpoints)

    matched_count = sum(1 for cp in checkpoints if cp.flag)
    total_expected = len(checkpoints)
    passed = matched_count == total_expected if total_expected else False
    score = matched_count / total_expected if total_expected else 0.0

    return {
        "type": "cart",
        "passed": passed,
        "score": score,
        "matched_count": matched_count,
        "total_expected": total_expected,
        "evaluation_results": {
            vm_key: {
                "score": res.score,
                "total_weight": res.total_weight,
                "matched": [cp.slug for cp in res.matched_checkpoints],
                "unmatched": [cp.slug for cp in res.unmatched_checkpoints],
                "unexpected": res.unexpected_products,
            }
            for vm_key, res in eval_results.items()
        },
    }


def build_expected_checkout(task_config: dict) -> ExpectedCheckout:
    answers = task_config["correct_answer"]["answers"]
    product_url = answers[0] if answers else ""
    product_slug = urlparse(product_url).path.rstrip("/").split("/")[-1]

    return ExpectedCheckout(
        product_slug=product_slug,
        shop_port=urlparse(product_url).port or 0,
        user_details=task_config.get("user_details", {}),
    )


def evaluate_checkout(
    expected: ExpectedCheckout,
    vm_ip: str,
    server_ports: List[int],
) -> dict:
    port_results = []
    passed_any = False
    best_score = 0.0

    for port in server_ports:
        result = extract_checkout_info_with_recovery(vm_ip, port)
        if result.error and not result.is_checkout_page:
            port_results.append(
                {"port": port, "passed": False, "error": result.error}
            )
            continue

        result = verify_checkout(result, expected)

        checks = result.checks or {}
        score = sum(checks.values()) / len(checks) if checks else 0.0
        passed = bool(checks) and all(checks.values())

        port_results.append(
            {
                "port": port,
                "passed": passed,
                "score": score,
                "checks": checks,
                "page_url": result.page_url,
                "order_number": result.order_number,
                "billing_info": result.billing_info,
                "product_name": result.product_name,
                "error": result.error,
                "recovery_used": result.recovery_used,
                "recovery_url": result.recovery_url,
            }
        )

        if passed:
            passed_any = True
        if score > best_score:
            best_score = score

    return {
        "type": "checkout",
        "passed": passed_any,
        "score": best_score,
        "ports": port_results,
    }


def select_evaluators(task_config: dict) -> List[str]:
    answer_type = task_config.get("correct_answer", {}).get("type", "string")
    category = task_config.get("category", "")

    if category == "FindAndOrder":
        return ["string", "checkout"]

    if answer_type == "string":
        return ["string"]
    if answer_type == "cart":
        return ["cart"]
    if answer_type == "checkout":
        return ["checkout"]

    return ["string"]


def main() -> int:
    parser = argparse.ArgumentParser(description="WebMall 单任务评价器")
    parser.add_argument("--task-id", required=True, help="外部任务 ID")
    parser.add_argument("--mapping-json", required=False, help="任务 ID 映射表 JSON")
    parser.add_argument("--answers-json", required=True, help="agent URL 列表 JSON")
    parser.add_argument("--vm-ip", required=True, help="虚拟机 IP")
    parser.add_argument(
        "--server-ports", type=int, nargs="+", required=True, help="虚拟机端口列表"
    )
    parser.add_argument("--shop-ip", required=False, help="商店 IP（默认=vm-ip）")
    parser.add_argument("--wait-time", type=float, default=3.0, help="等待时间")
    parser.add_argument("--output", help="输出 JSON 文件路径")

    args = parser.parse_args()

    mapping = load_json(args.mapping_json)
    answers_data = load_json(args.answers_json)
    task_map = load_task_sets(TASK_SET_PATH)

    mapped_task_id = map_task_id(args.task_id, mapping)
    if mapped_task_id not in task_map:
        raise SystemExit(f"任务未找到: {mapped_task_id}")

    task_config = task_map[mapped_task_id]
    expected_urls = task_config["correct_answer"]["answers"]
    eval_types = select_evaluators(task_config)
    shop_ip = args.shop_ip or args.vm_ip

    submitted_urls = get_answers_for_task(
        answers_data, args.task_id, mapped_task_id
    )

    results = []

    if "string" in eval_types:
        results.append(evaluate_string(expected_urls, submitted_urls))

    if "cart" in eval_types:
        results.append(
            evaluate_cart(
                expected_urls,
                args.vm_ip,
                args.server_ports,
                shop_ip,
                args.wait_time,
            )
        )

    if "checkout" in eval_types:
        expected_checkout = build_expected_checkout(task_config)
        results.append(
            evaluate_checkout(expected_checkout, args.vm_ip, args.server_ports)
        )

    overall_passed = all(r.get("passed") for r in results) if results else False
    output = {
        "task_id": args.task_id,
        "mapped_task_id": mapped_task_id,
        "category": task_config.get("category"),
        "eval_types": eval_types,
        "results": results,
        "passed": overall_passed,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    return 0 if overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
