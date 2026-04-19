#!/usr/bin/env python3
"""
统一消融实验入口脚本（V2）。

改进:
    - 直接 import Pipeline 类，不再走 subprocess
    - 支持 --mode full/ablation 切换
    - 所有 pipeline 结果统一输出到同一目录
    - 统一参数接口

使用方法:
    # 消融实验（子集）
    python run_ablation.py --conditions baseline plan_kimi --mode ablation

    # 正式全集实验
    python run_ablation.py --conditions baseline --mode full

    # 指定 pipeline
    python run_ablation.py --conditions baseline --pipelines webmall qa --mode full

    # 按子类别测试（指定 task list 文件）
    python run_ablation.py --conditions baseline --pipelines operation \
        --task-list-file tasks/subsets/by_subtype/operation_fileoperate_batchoperationword.txt

    # 直接指定任务 ID
    python run_ablation.py --conditions baseline --task-ids "Operation-FileOperate-BatchOperationWord-001,Operation-FileOperate-BatchOperationWord-002"

    # Dry run（仅打印配置）
    python run_ablation.py --conditions baseline --dry-run

输出:
    logs/ablation_<timestamp>/
    ├── baseline/
    │   ├── qa_results.json
    │   ├── webmall_results.json
    │   ├── agent_results/          # 过程文件（自动创建）
    │   │   ├── qa/
    │   │   ├── webmall/
    │   │   └── ...
    │   └── ablation_config.json
    ├── plan_kimi/
    └── ablation_summary.json
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

# 路径设置：开源版新布局
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(SRC_DIR)
EXAMPLES_DIR = SRC_DIR
UBUNTU_ENV_DIR = REPO_ROOT
LOGS_DIR = os.path.join(REPO_ROOT, "logs")
for _p in [SCRIPT_DIR, SRC_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from qa_pipeline import QAPipeline
from webmall_pipeline import WebMallPipeline
from webnavigate_pipeline import WebNavigatePipeline
from operation_pipeline import OperationPipeline
from searchwrite_pipeline import SearchWritePipeline


# ============================================================
# Pipeline 注册表
# ============================================================

PIPELINE_CLASSES = {
    "qa": QAPipeline,
    "webmall": WebMallPipeline,
    "webnavigate": WebNavigatePipeline,
    "operation": OperationPipeline,
    "searchwrite": SearchWritePipeline,
}

# 机器分组（QA 在机器C，其余在机器B）
MACHINE_GROUPS = {
    "machine_c": ["qa"],
    "machine_b": ["webmall", "webnavigate", "operation", "searchwrite"],
}

# ============================================================
# 预定义消融条件（同旧版 run_ablation.py）
# ============================================================

ABLATION_CONDITIONS = {
    "baseline": {
        "description": "主实验：Plan(gpt-5.4) + GUI(seed-1.8), n=5",
        "env": {
            "ABLATION_PLAN_MODEL": "gpt-5.4",
            "ABLATION_GUI_AGENT": "seed18",
        },
        "vms_per_task": 5,
        "agent_mode": None,
    },
    "gui_only_seed18": {
        "description": "主实验：GUI-only(seed-1.8), n=1",
        "env": {
            "ABLATION_GUI_AGENT": "seed18",
            "ABLATION_AGENT_MODE": "gui_only",
        },
        "vms_per_task": 1,
        "agent_mode": "gui_only",
    },
    "plan_seed18": {
        "description": "Plan Agent 消融：seed-1.8",
        "env": {
            "ABLATION_PLAN_MODEL": "doubao-seed-1-8-251228",
            "ABLATION_GUI_AGENT": "seed18",
        },
        "vms_per_task": 5,
        "agent_mode": None,
    },
    "plan_kimi": {
        "description": "Plan Agent 消融：Kimi K2.5",
        "env": {
            "ABLATION_PLAN_MODEL": "kimi-k2.5",
            "ABLATION_GUI_AGENT": "seed18",
        },
        "vms_per_task": 5,
        "agent_mode": None,
    },
    "plan_claude_opus46": {
        "description": "Plan Agent 消融：Claude Opus 4.6",
        "env": {
            "ABLATION_PLAN_MODEL": "claude-opus-4.6",
            "ABLATION_GUI_AGENT": "seed18",
        },
        "vms_per_task": 5,
        "agent_mode": None,
    },
    "gui_kimi": {
        "description": "GUI Agent 消融：Kimi（Plan=gpt-5.4）",
        "env": {
            "ABLATION_PLAN_MODEL": "gpt-5.4",
            "ABLATION_GUI_AGENT": "kimi",
        },
        "vms_per_task": 5,
        "agent_mode": None,
    },
    "gui_only_kimi": {
        "description": "GUI-Only 消融：Kimi",
        "env": {
            "ABLATION_GUI_AGENT": "kimi",
            "ABLATION_AGENT_MODE": "gui_only",
        },
        "vms_per_task": 1,
        "agent_mode": "gui_only",
    },
    "gui_only_gpt54": {
        "description": "GUI-Only 消融：GPT-5.4",
        "env": {
            "ABLATION_GUI_AGENT": "gpt54",
            "ABLATION_AGENT_MODE": "gui_only",
        },
        "vms_per_task": 1,
        "agent_mode": "gui_only",
    },
    "gui_gpt54": {
        "description": "GUI Agent 消融：GPT-5.4（Plan=gpt-5.4）",
        "env": {
            "ABLATION_PLAN_MODEL": "gpt-5.4",
            "ABLATION_GUI_AGENT": "gpt54",
        },
        "vms_per_task": 5,
        "agent_mode": None,
    },
    "parallel_n1": {
        "description": "并行度消融：n=1（Plan+GUI）",
        "env": {
            "ABLATION_PLAN_MODEL": "gpt-5.4",
            "ABLATION_GUI_AGENT": "seed18",
        },
        "vms_per_task": 1,
        "agent_mode": None,
    },
    "parallel_n3": {
        "description": "并行度消融：n=3（Plan+GUI）",
        "env": {
            "ABLATION_PLAN_MODEL": "gpt-5.4",
            "ABLATION_GUI_AGENT": "seed18",
        },
        "vms_per_task": 3,
        "agent_mode": None,
    },
    "oracle_plan": {
        "description": "Oracle Plan 消融：prompt 注入",
        "env": {
            "ABLATION_PLAN_MODEL": "gpt-5.4",
            "ABLATION_GUI_AGENT": "seed18",
            "ABLATION_ORACLE_PLAN_DIR": os.path.join(
                UBUNTU_ENV_DIR, "logs", "final_results", "oracle_plans"),
            "ABLATION_ORACLE_PLAN_INJECTED": "1",
        },
        "vms_per_task": 5,
        "agent_mode": None,
    },
}


# ============================================================
# 核心执行函数
# ============================================================

def run_one_condition(
    condition_name: str,
    condition_config: Dict,
    pipelines: List[str],
    mode: str,
    output_dir: str,
    common_args: Dict,
    log: logging.Logger,
    dry_run: bool = False,
    progress_state=None,
) -> Dict[str, Any]:
    """
    执行单个消融条件下的所有 pipeline。

    输入:
        condition_name: 条件名
        condition_config: 条件配置（env, vms_per_task, agent_mode）
        pipelines: 要执行的 pipeline 列表
        mode: full / ablation
        output_dir: 该条件的输出目录
        common_args: 公共参数字典
        log: logger
        dry_run: 仅打印不执行

    输出:
        条件报告字典
    """
    os.makedirs(output_dir, exist_ok=True)

    # 保存消融配置
    config_path = os.path.join(output_dir, "ablation_config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({
            "condition": condition_name,
            "description": condition_config["description"],
            "env": condition_config.get("env", {}),
            "vms_per_task": condition_config.get("vms_per_task", 5),
            "agent_mode": condition_config.get("agent_mode"),
            "mode": mode,
            "pipelines": pipelines,
        }, f, ensure_ascii=False, indent=2)

    # 设置消融环境变量
    env_vars = condition_config.get("env", {})
    for k, v in env_vars.items():
        os.environ[k] = v

    results = {}
    for pi, pipeline_name in enumerate(pipelines, 1):
        if progress_state:
            progress_state.set_pipeline(pipeline_name, pi, len(pipelines))

        cls = PIPELINE_CLASSES[pipeline_name]

        # 构建 pipeline 参数
        pipeline_instance = cls()
        # 注入 ProgressState 到 pipeline 实例
        if progress_state:
            pipeline_instance._progress_state = progress_state
        parser = pipeline_instance.build_parser()

        # 组装 CLI 参数列表
        cli_args = [
            "--mode", mode,
            "-n", str(condition_config.get("vms_per_task", 5)),
            "-p", str(common_args.get("max_parallel_tasks", 1)),
        ]
        agent_mode = condition_config.get("agent_mode") or "plan"
        cli_args.extend(["--agent-mode", agent_mode])

        if common_args.get("gui_agent"):
            cli_args.extend(["--gui-agent", common_args["gui_agent"]])
        if common_args.get("skip_completed_dir"):
            cli_args.extend(["--skip-completed-dir", common_args["skip_completed_dir"]])
        # 过程文件存储：优先使用用户指定的目录，否则自动存到汇总结果同级目录
        save_dir = common_args.get("save_result_dir")
        if save_dir:
            pipeline_save_dir = os.path.join(save_dir, pipeline_name)
        else:
            pipeline_save_dir = os.path.join(output_dir, "agent_results", pipeline_name)
        os.makedirs(pipeline_save_dir, exist_ok=True)
        cli_args.extend(["--save-result-dir", pipeline_save_dir])
        if common_args.get("final"):
            cli_args.extend(["--final", common_args["final"]])
        if common_args.get("task_list_file"):
            cli_args.extend(["--task-list-file", common_args["task_list_file"]])
        if common_args.get("task_ids"):
            cli_args.extend(["--task-ids", common_args["task_ids"]])
        if common_args.get("test"):
            cli_args.append("--test")
        if common_args.get("no_dashboard"):
            cli_args.append("--no-dashboard")

        pipeline_args = parser.parse_args(cli_args)
        pipeline_instance.args = pipeline_args

        if dry_run:
            log.info("[DRY RUN] %s / %s: %s",
                     condition_name, pipeline_name,
                     condition_config["description"])
            log.info("  args: %s", cli_args)
            log.info("  env: %s", env_vars)
            continue

        log.info("=" * 60)
        log.info("[%s / %s] 开始执行", condition_name, pipeline_name)
        log.info("  %s", condition_config["description"])
        log.info("=" * 60)

        # 设置过程文件输出环境变量，将执行记录和截图路由到 agent_results/ 下
        os.environ["ABLATION_RECORD_DIR"] = pipeline_save_dir
        os.environ["GPT54_SCREENSHOT_DIR"] = os.path.join(
            pipeline_save_dir, "screenshots")

        pipeline_instance.output_dir_override = output_dir
        start_time = time.time()
        try:
            pipeline_instance.main()
            elapsed = time.time() - start_time
            results[pipeline_name] = {
                "status": "success",
                "elapsed_seconds": round(elapsed, 1),
            }
            log.info("[%s / %s] 完成 (%.1fs)", condition_name, pipeline_name, elapsed)
        except Exception as exc:
            elapsed = time.time() - start_time
            log.error("[%s / %s] 失败 (%.1fs): %s",
                      condition_name, pipeline_name, elapsed, exc)
            results[pipeline_name] = {
                "status": "error",
                "error": str(exc),
                "elapsed_seconds": round(elapsed, 1),
            }
        finally:
            # 清理过程文件环境变量，避免影响下一个 pipeline
            os.environ.pop("ABLATION_RECORD_DIR", None)
            os.environ.pop("GPT54_SCREENSHOT_DIR", None)

        # ── Master Table 记录（需 --record-to-master）──
        if common_args.get("record_to_master"):
            try:
                import master_table
                # 去除 `ablation_` 前缀，与 master_report.import_run 和 spec §4.2 示例（20260413_153000）保持一致
                run_timestamp = os.path.basename(
                    os.path.dirname(output_dir)).replace("ablation_", "", 1)
                rel_run_dir = os.path.relpath(output_dir,
                                               os.path.join(UBUNTU_ENV_DIR, "logs"))
                context = {
                    "mode": mode,
                    "condition": condition_name,
                    "run_timestamp": run_timestamp,
                    "run_dir": rel_run_dir,
                    "plan_model": env_vars.get("ABLATION_PLAN_MODEL", ""),
                    "gui_agent": env_vars.get("ABLATION_GUI_AGENT",
                                              common_args.get("gui_agent", "")),
                    "agent_mode": (condition_config.get("agent_mode") or "plan"),
                    "vms_per_task": condition_config.get("vms_per_task", 5),
                    "oracle_plan_injected":
                        env_vars.get("ABLATION_ORACLE_PLAN_INJECTED", "") == "1",
                }
                master_table.upsert_results(
                    results=getattr(pipeline_instance, "last_results", {}) or {},
                    expected_task_ids=getattr(
                        pipeline_instance, "last_expected_task_ids", []) or [],
                    pipeline=pipeline_name,
                    context=context,
                )
                log.info("[%s / %s] 已写入 master.csv",
                         condition_name, pipeline_name)
            except Exception as mexc:
                log.warning("[%s / %s] master.csv 写入失败: %s",
                            condition_name, pipeline_name, mexc)

    # 清理消融环境变量
    for k in env_vars:
        os.environ.pop(k, None)

    return {
        "condition": condition_name,
        "description": condition_config["description"],
        "pipeline_results": results,
    }


# ============================================================
# 入口
# ============================================================

def main():
    """
    消融实验主入口。

    流程:
        1. 解析参数
        2. 按条件循环执行 pipeline
        3. 输出汇总报告
    """
    parser = argparse.ArgumentParser(
        description="Pipeline V2 统一消融实验入口",
    )
    parser.add_argument(
        "--conditions", nargs="+", default=["baseline"],
        choices=list(ABLATION_CONDITIONS.keys()),
        help="消融条件名称列表",
    )
    parser.add_argument(
        "--pipelines", nargs="+", default=list(PIPELINE_CLASSES.keys()),
        choices=list(PIPELINE_CLASSES.keys()),
        help="要执行的 pipeline 列表",
    )
    parser.add_argument(
        "--mode", type=str, default="ablation", choices=["full", "ablation"],
        help="full=全集, ablation=子集",
    )
    parser.add_argument("-p", "--max-parallel-tasks", type=int, default=1)
    parser.add_argument("--gui-agent", type=str, default="")
    parser.add_argument("--skip-completed-dir", type=str, default="")
    parser.add_argument("--save-result-dir", type=str, default="")
    parser.add_argument("--final", type=str, default="",
                        help="Final 模式：指定固定输出目录，各 pipeline 共用同一目录")
    parser.add_argument("--task-list-file", type=str, default="",
                        help="指定任务列表文件路径（每行一个 task_id），"
                             "可用于按子类别测试，如 tasks/subsets/by_subtype/xxx.txt")
    parser.add_argument("--task-ids", type=str, default="",
                        help="直接指定任务 ID（逗号分隔）")
    parser.add_argument("--record-to-master", dest="record_to_master",
                        action="store_true",
                        help="结束后把结果 upsert 到 master.csv（见 master_table.py）")
    parser.add_argument("--test", action="store_true",
                        help="测试模式：每 pipeline 仅跑 1 个任务，gui_max_rounds=2（转发给子 pipeline）")
    parser.add_argument("--no-dashboard", dest="no_dashboard", action="store_true",
                        help="禁用 Rich 仪表板，保留 stdout 日志（方便 debug init 阶段）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印配置不执行")
    args = parser.parse_args()

    # 日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("ablation_v2")

    # 输出根目录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root_output_dir = os.path.join(UBUNTU_ENV_DIR, "logs", f"ablation_{timestamp}")
    os.makedirs(root_output_dir, exist_ok=True)
    log.info("输出目录: %s", root_output_dir)

    common_args = {
        "max_parallel_tasks": args.max_parallel_tasks,
        "gui_agent": args.gui_agent,
        "skip_completed_dir": args.skip_completed_dir,
        "save_result_dir": args.save_result_dir,
        "final": args.final,
        "task_list_file": args.task_list_file,
        "task_ids": args.task_ids,
        "record_to_master": args.record_to_master,
        "test": args.test,
        "no_dashboard": args.no_dashboard,
    }

    from progress_display import ProgressState
    shared_progress = ProgressState()

    # 按条件顺序执行
    all_reports = []
    for cond_idx, cond_name in enumerate(args.conditions, 1):
        shared_progress.set_condition(cond_name, cond_idx, len(args.conditions))
        cond_config = ABLATION_CONDITIONS[cond_name]
        cond_output_dir = os.path.join(root_output_dir, cond_name)

        report = run_one_condition(
            condition_name=cond_name,
            condition_config=cond_config,
            pipelines=args.pipelines,
            mode=args.mode,
            output_dir=cond_output_dir,
            common_args=common_args,
            log=log,
            dry_run=args.dry_run,
            progress_state=shared_progress,
        )
        all_reports.append(report)

    # 汇总报告
    summary_path = os.path.join(root_output_dir, "ablation_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, ensure_ascii=False, indent=2)
    log.info("汇总报告已保存: %s", summary_path)

    # 问题检测汇总
    try:
        from parallel_benchmark.logs.issue_detector import generate_issue_summary
        issue_summary = generate_issue_summary()
        if issue_summary.get("total", 0) > 0:
            issue_summary_path = os.path.join(root_output_dir, "issue_summary.json")
            with open(issue_summary_path, "w", encoding="utf-8") as f:
                json.dump(issue_summary, f, ensure_ascii=False, indent=2)
            log.info("问题检测汇总: %s (共 %d 条问题)", issue_summary_path, issue_summary["total"])
        else:
            log.info("本次实验未检测到问题")
    except Exception as exc:
        log.debug("问题汇总生成跳过: %s", exc)

    # 合并各条件下的结果，生成统一统计报告
    from report_generator import generate_report
    all_results = {}
    for cond_name in args.conditions:
        cond_output_dir = os.path.join(root_output_dir, cond_name)
        if not os.path.isdir(cond_output_dir):
            continue
        for fname in os.listdir(cond_output_dir):
            if fname.endswith("_results.json"):
                fpath = os.path.join(cond_output_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        all_results.update(json.load(f))
                except Exception:
                    pass
    if all_results:
        report_dir = generate_report(all_results, root_output_dir, log=log)
        log.info("统计报告: %s", report_dir)


if __name__ == "__main__":
    main()
