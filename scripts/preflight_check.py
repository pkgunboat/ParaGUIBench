#!/usr/bin/env python3
"""
Preflight check — 验证所有 232 个任务的准备条件是否充分。

检查维度：
  A. 数据缓存   — prepare_script_path 对应的 HF 缓存是否有 .complete 标记
  B. OSWorld 配置 — evaluator_path 指向的 JSON 是否存在
  C. 服务状态   — WebMall / OnlyOffice / WebNavigate 服务是否可达
  D. VM 基础设施 — QEMU 镜像、Docker daemon、Docker image
  E. Pipeline 分类 — 每个任务能被唯一分到一个 pipeline 且字段齐全
  F. 文件可部署性 — shared_base_dir 可写

用法:
  python scripts/preflight_check.py
  python scripts/preflight_check.py --no-network
  python scripts/preflight_check.py --verbose --pipeline webmall
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, SRC_DIR)

from config_loader import DeployConfig, get_ssh_password  # noqa: E402
from pipelines.task_scanner import _match_pipeline, VALID_PIPELINES  # noqa: E402
from pipelines.service_health import required_service_checks_for_pipeline  # noqa: E402

TASKS_DIR = os.path.join(REPO_ROOT, "src", "parallel_benchmark", "tasks")
HF_DATA_DIR = os.path.join(REPO_ROOT, "src", "parallel_benchmark", "hf_data", "benchmark_dataset")
EVAL_DIR = os.path.join(REPO_ROOT, "src", "parallel_benchmark", "eval")

PASS = "PASS"
FAIL = "FAIL"
NA = "N/A"
SKIP = "SKIP"


@dataclass
class CheckResult:
    task_id: str
    category: str
    status: str  # PASS / FAIL / N/A / SKIP
    detail: str = ""


def load_all_tasks() -> List[Tuple[str, Dict[str, Any]]]:
    """扫描所有任务 JSON，返回 [(task_id, config), ...]。"""
    tasks = []
    for name in sorted(os.listdir(TASKS_DIR)):
        if not name.endswith(".json") or name == "id_mapping.json":
            continue
        path = os.path.join(TASKS_DIR, name)
        try:
            cfg = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        tid = cfg.get("task_id", "")
        if not tid:
            continue
        tasks.append((tid, cfg))
    return tasks


# Pipeline 分类优先级：更具体的规则优先
_PIPELINE_PRIORITY = ["webmall", "operation", "searchwrite", "webnavigate", "qa"]


def classify_pipeline(task_id: str, cfg: Dict[str, Any]) -> Optional[str]:
    """返回任务所属的 pipeline 名称，或 None。按固定优先级匹配。"""
    for p in _PIPELINE_PRIORITY:
        if _match_pipeline(task_id, cfg, p):
            return p
    return None


def _detect_local_ip() -> str:
    """Detect local IP address (same logic as config_loader)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0] or "127.0.0.1"
    except OSError:
        return "127.0.0.1"


# ── 检查 A: 数据缓存 ──────────────────────────────────────────────

def check_task_data_cache(tasks: List[Tuple[str, Dict]], verbose: bool) -> List[CheckResult]:
    results = []
    for tid, cfg in tasks:
        if cfg.get("skip_eval"):
            results.append(CheckResult(tid, "A.data_cache", SKIP, "skip_eval"))
            continue
        prep = cfg.get("prepare_script_path", "")
        if not prep:
            results.append(CheckResult(tid, "A.data_cache", NA, "no prepare_script_path"))
            continue
        uid = cfg.get("task_uid", "")
        cache_dir = os.path.join(HF_DATA_DIR, uid)
        marker = os.path.join(cache_dir, ".complete")
        if os.path.isfile(marker):
            file_count = sum(
                1 for f in os.listdir(cache_dir)
                if os.path.isfile(os.path.join(cache_dir, f)) and f != ".complete"
            )
            results.append(CheckResult(tid, "A.data_cache", PASS, f"{file_count} files cached"))
        else:
            results.append(CheckResult(tid, "A.data_cache", FAIL, "missing .complete marker"))
    return results


# ── 检查 B: OSWorld 配置文件存在 ──────────────────────────────────

def check_osworld_configs(tasks: List[Tuple[str, Dict]], verbose: bool) -> List[CheckResult]:
    results = []
    seen_jsons = {}
    for tid, cfg in tasks:
        epath = cfg.get("evaluator_path", "")
        if not epath or "osworld_scripts" not in epath:
            continue
        full = os.path.join(REPO_ROOT, "src", "parallel_benchmark", epath)
        if full in seen_jsons:
            results.append(CheckResult(tid, "B.osworld_config", seen_jsons[full][0], f"shared with {seen_jsons[full][1]}"))
            continue
        if os.path.isfile(full):
            seen_jsons[full] = (PASS, tid)
            results.append(CheckResult(tid, "B.osworld_config", PASS, f"evaluator JSON exists"))
        else:
            seen_jsons[full] = (FAIL, tid)
            results.append(CheckResult(tid, "B.osworld_config", FAIL, f"missing: {epath}"))
    return results


# ── 检查 C: 服务状态 ─────────────────────────────────────────────

def _http_get(url: str, timeout: int = 5) -> Tuple[bool, str]:
    try:
        import requests
        r = requests.get(url, timeout=timeout, allow_redirects=True)
        return r.status_code == 200, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:80]


def check_services(
    tasks: List[Tuple[str, Dict]],
    deploy: DeployConfig,
    no_network: bool,
    verbose: bool,
) -> List[CheckResult]:
    if no_network:
        return [CheckResult("*", "C.services", SKIP, "--no-network")]

    results = []

    # 确定哪些服务是需要的
    pipelines_needed = set()
    for tid, cfg in tasks:
        p = classify_pipeline(tid, cfg)
        if p:
            pipelines_needed.add(p)

    needs_webmall = "webmall" in pipelines_needed
    needs_onlyoffice = any(
        classify_pipeline(tid, cfg) == "searchwrite"
        and not (
            cfg.get("task_type") == "OSWorld脚本"
            or cfg.get("evaluator_path", "").endswith(".json")
        )
        for tid, cfg in tasks
    )

    if needs_webmall:
        for item in required_service_checks_for_pipeline("webmall", deploy):
            tag = PASS if item.ok else FAIL
            results.append(CheckResult(item.target, "C.services", tag, f"{item.service}: {item.detail}"))
    else:
        results.append(CheckResult("WebMall", "C.services", NA, "no webmall tasks"))

    if needs_onlyoffice:
        for item in required_service_checks_for_pipeline("searchwrite", deploy):
            tag = PASS if item.ok else FAIL
            results.append(CheckResult(item.target, "C.services", tag, f"{item.service}: {item.detail}"))
    else:
        results.append(CheckResult("OnlyOffice", "C.services", NA, "no OnlyOffice-backed searchwrite tasks"))

    return results


# ── 检查 D: VM 基础设施 ──────────────────────────────────────────

def check_vm_infrastructure(
    deploy: DeployConfig,
    no_network: bool,
    verbose: bool,
) -> List[CheckResult]:
    results = []

    # D1: QEMU 镜像
    qcow2 = deploy.qcow2_path
    if os.path.isfile(qcow2) and os.path.getsize(qcow2) > 0:
        size_gb = os.path.getsize(qcow2) / (1024 ** 3)
        results.append(CheckResult("QEMU image", "D.infrastructure", PASS, f"{size_gb:.1f} GB"))
    else:
        results.append(CheckResult("QEMU image", "D.infrastructure", FAIL, f"missing or empty: {qcow2}"))

    # D2: Docker daemon 可达（本机用 unix socket，远程用 TCP）
    if no_network:
        results.append(CheckResult("Docker daemon", "D.infrastructure", SKIP, "--no-network"))
    else:
        vm_host = deploy.vm_host
        docker_port = deploy.docker_daemon_port
        import subprocess as _sp
        # 如果 vm_host 是本机，通过 docker info 检查
        local_ip = _detect_local_ip()
        if vm_host in (local_ip, "127.0.0.1", "localhost"):
            try:
                r = _sp.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                            capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    results.append(CheckResult("Docker daemon", "D.infrastructure", PASS, f"local Docker {r.stdout.strip()}"))
                else:
                    results.append(CheckResult("Docker daemon", "D.infrastructure", FAIL, "docker info failed"))
            except Exception as e:
                results.append(CheckResult("Docker daemon", "D.infrastructure", FAIL, f"docker check failed: {e}"))
        else:
            try:
                with socket.create_connection((vm_host, docker_port), timeout=5):
                    results.append(CheckResult("Docker daemon", "D.infrastructure", PASS, f"{vm_host}:{docker_port} reachable"))
            except Exception as e:
                results.append(CheckResult("Docker daemon", "D.infrastructure", FAIL, f"{vm_host}:{docker_port} unreachable: {e}"))

    # D3: Docker image 存在
    if no_network:
        results.append(CheckResult("Docker image", "D.infrastructure", SKIP, "--no-network"))
    else:
        local_ip = _detect_local_ip()
        vm_host = deploy.vm_host
        img = "happysixd/osworld-docker-sshfs"
        try:
            if vm_host in (local_ip, "127.0.0.1", "localhost"):
                r = subprocess.run(
                    ["docker", "image", "inspect", img],
                    capture_output=True, timeout=10)
            else:
                password = get_ssh_password()
                if not password:
                    results.append(CheckResult("Docker image", "D.infrastructure", SKIP, "BENCH_SSH_PASSWORD not set"))
                    return results
                cmd = (
                    f"sshpass -p {password} ssh -o StrictHostKeyChecking=no "
                    f"-p {deploy.docker_daemon_port} {deploy.vm_user}@{vm_host} "
                    f"docker image inspect {img}"
                )
                r = subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
            if r.returncode == 0:
                results.append(CheckResult("Docker image", "D.infrastructure", PASS, f"{img} exists"))
            else:
                results.append(CheckResult("Docker image", "D.infrastructure", FAIL, f"{img} not found"))
        except Exception as e:
            results.append(CheckResult("Docker image", "D.infrastructure", SKIP, f"check failed: {e}"))

    # D4: operation_gt_cache
    gt_cache = os.path.join(REPO_ROOT, "resources", "operation_gt_cache")
    if os.path.isdir(gt_cache):
        subdirs = [d for d in os.listdir(gt_cache) if os.path.isdir(os.path.join(gt_cache, d))]
        results.append(CheckResult("GT cache", "D.infrastructure", PASS, f"{len(subdirs)} subdirs"))
    else:
        results.append(CheckResult("GT cache", "D.infrastructure", FAIL, "resources/operation_gt_cache missing"))

    return results


# ── 检查 E: Pipeline 分类 + 必填字段 ──────────────────────────────

REQUIRED_FIELDS = {
    "all": {"task_id", "task_uid", "instruction"},
    "webmall": {"answer_type"},
}


def check_pipeline_classification(tasks: List[Tuple[str, Dict]], verbose: bool) -> List[CheckResult]:
    results = []
    for tid, cfg in tasks:
        if cfg.get("skip_eval"):
            results.append(CheckResult(tid, "E.pipeline_class", SKIP, "skip_eval"))
            continue

        # E1: 能分到一个 pipeline
        pipeline = classify_pipeline(tid, cfg)
        if pipeline is None:
            results.append(CheckResult(tid, "E.pipeline_class", FAIL, "no matching pipeline"))
            continue

        # E2: 必填字段
        missing = []
        for f in REQUIRED_FIELDS["all"]:
            if not cfg.get(f):
                missing.append(f)
        if pipeline in REQUIRED_FIELDS:
            for f in REQUIRED_FIELDS[pipeline]:
                if not cfg.get(f):
                    missing.append(f)
        if missing:
            results.append(CheckResult(tid, "E.pipeline_class", FAIL, f"missing fields: {', '.join(missing)}"))
        else:
            results.append(CheckResult(tid, "E.pipeline_class", PASS, f"pipeline={pipeline}"))
    return results


# ── 检查 F: 文件可部署性 ──────────────────────────────────────────

def check_deployability(tasks: List[Tuple[str, Dict]], deploy: DeployConfig, verbose: bool) -> List[CheckResult]:
    shared_base = deploy.shared_base_dir
    results = []

    # 检查 shared_base_dir 可写
    parent = os.path.dirname(shared_base) or "/"
    writable = os.access(parent, os.W_OK)

    for tid, cfg in tasks:
        if cfg.get("skip_eval"):
            results.append(CheckResult(tid, "F.deployability", SKIP, "skip_eval"))
            continue
        prep = cfg.get("prepare_script_path", "")
        if not prep:
            results.append(CheckResult(tid, "F.deployability", NA, "no files needed"))
            continue
        if not writable:
            results.append(CheckResult(tid, "F.deployability", FAIL, f"{parent} not writable"))
            continue
        uid = cfg.get("task_uid", "")
        cache_dir = os.path.join(HF_DATA_DIR, uid)
        if not os.path.isdir(cache_dir):
            results.append(CheckResult(tid, "F.deployability", FAIL, "cache dir missing"))
        else:
            results.append(CheckResult(tid, "F.deployability", PASS, "deployable"))
    return results


# ── 输出 ──────────────────────────────────────────────────────────

def print_results(results: List[CheckResult], verbose: bool):
    by_cat: Dict[str, List[CheckResult]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)

    # Verbose: 逐任务输出
    if verbose:
        for cat in sorted(by_cat):
            for r in by_cat[cat]:
                print(f"  [{r.status:4s}] {r.category:20s} | {r.task_id:60s} | {r.detail}")
        print()

    # 汇总表
    cat_order = ["A.data_cache", "B.osworld_config", "C.services", "D.infrastructure", "E.pipeline_class", "F.deployability"]
    print(f"{'Category':24s} | {'Total':>5s} | {'PASS':>5s} | {'FAIL':>5s} | {'N/A':>5s} | {'SKIP':>5s}")
    print("-" * 24 + "-+-" + ("------+-" * 5))

    total_all = 0
    counts_all = {"PASS": 0, "FAIL": 0, "N/A": 0, "SKIP": 0}

    for cat in cat_order:
        items = by_cat.get(cat, [])
        counts = {"PASS": 0, "FAIL": 0, "N/A": 0, "SKIP": 0}
        for r in items:
            counts[r.status] += 1
        total = len(items)
        print(f"{cat:24s} | {total:5d} | {counts[PASS]:5d} | {counts[FAIL]:5d} | {counts[NA]:5d} | {counts[SKIP]:5d}")
        total_all += total
        for k in counts_all:
            counts_all[k] += counts[k]

    print("-" * 24 + "-+-" + ("------+-" * 5))
    print(f"{'TOTAL':24s} | {total_all:5d} | {counts_all[PASS]:5d} | {counts_all[FAIL]:5d} | {counts_all[NA]:5d} | {counts_all[SKIP]:5d}")
    print()

    if counts_all[FAIL] > 0:
        print(f"FAIL: {counts_all[FAIL]} check(s) failed. Review details above with --verbose.")
        return 1

    print("All checks passed or N/A. Ready to run experiments.")
    return 0


# ── Pipeline 分布统计 ─────────────────────────────────────────────

def print_pipeline_distribution(tasks: List[Tuple[str, Dict]]):
    dist: Dict[str, List[str]] = defaultdict(list)
    unclassified = []
    for tid, cfg in tasks:
        p = classify_pipeline(tid, cfg)
        if p:
            dist[p].append(tid)
        else:
            unclassified.append(tid)

    print("Pipeline distribution:")
    for p in sorted(dist):
        print(f"  {p:16s}: {len(dist[p]):3d} tasks")
    if unclassified:
        print(f"  {'unclassified':16s}: {len(unclassified):3d} tasks")
        for t in unclassified:
            print(f"    - {t}")
    print()


# ── Main ──────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Preflight check for ParaGUIBench tasks")
    ap.add_argument("--no-network", action="store_true", help="Skip network-dependent checks")
    ap.add_argument("--verbose", action="store_true", help="Show per-task details")
    ap.add_argument("--pipeline", choices=sorted(VALID_PIPELINES), help="Only check tasks for this pipeline")
    args = ap.parse_args()

    deploy = DeployConfig()
    tasks = load_all_tasks()

    # 按 pipeline 过滤
    if args.pipeline:
        tasks = [(tid, cfg) for tid, cfg in tasks if _match_pipeline(tid, cfg, args.pipeline)]

    print(f"Tasks to check: {len(tasks)}")
    print_pipeline_distribution(tasks)

    all_results: List[CheckResult] = []

    all_results.extend(check_task_data_cache(tasks, args.verbose))
    all_results.extend(check_osworld_configs(tasks, args.verbose))
    all_results.extend(check_services(tasks, deploy, args.no_network, args.verbose))
    all_results.extend(check_vm_infrastructure(deploy, args.no_network, args.verbose))
    all_results.extend(check_pipeline_classification(tasks, args.verbose))
    all_results.extend(check_deployability(tasks, deploy, args.verbose))

    return print_results(all_results, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
