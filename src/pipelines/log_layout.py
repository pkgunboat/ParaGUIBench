# src/pipelines/log_layout.py
"""
新日志结构（任务中心）的路径布局工具。

集中所有"新结构路径"知识，避免散落在各处。新结构形如：
    logs/by_task/<condition>/<task_prefix>/<task_id>/
        latest -> runs/<host>__<timestamp>/
        runs/<host>__<timestamp>/{task.log, rounds.json, execution_record.json, meta.json}
旧格式软链接视图（仅迁移历史数据时重建）：
    logs/<host>/ablation_<timestamp>/<condition>/<task_id> -> by_task/.../runs/<host>__<timestamp>/
"""
import os


def task_prefix(task_id: str) -> str:
    """
    取 task_id 的二级前缀（前两段）。

    输入: task_id，如 "Operation-FileOperate-BatchOperationWord-006"
    输出: 前缀字符串，如 "Operation-FileOperate"；段数 < 2 时原样返回。
    """
    parts = task_id.split("-")
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return task_id


def run_name(host: str, timestamp: str) -> str:
    """
    拼装单次 run 的目录名。

    输入: host（host_tag），timestamp（YYYYMMDD_HHMMSS）
    输出: "<host>__<timestamp>"
    """
    return f"{host}__{timestamp}"


def by_task_dir(logs_dir: str, condition: str, task_id: str) -> str:
    """
    返回某任务在某 condition 下的根目录。

    输入: logs_dir（logs 绝对路径），condition，task_id
    输出: logs/by_task/<condition>/<prefix>/<task_id> 绝对路径
    """
    return os.path.join(logs_dir, "by_task", condition,
                        task_prefix(task_id), task_id)


def run_dir(logs_dir: str, condition: str, task_id: str,
            host: str, timestamp: str) -> str:
    """
    返回某任务某次 run 的目录。

    输入: logs_dir, condition, task_id, host, timestamp
    输出: <by_task_dir>/runs/<host>__<timestamp> 绝对路径
    """
    return os.path.join(by_task_dir(logs_dir, condition, task_id),
                        "runs", run_name(host, timestamp))


def update_latest_symlink(task_dir: str, rname: str) -> str:
    """
    原子化更新 task_dir/latest 软链接，指向 runs/<rname>。

    输入: task_dir（by_task 下的任务根目录），rname（run_name 结果）
    输出: latest 软链接绝对路径
    副作用: 先写临时软链接再 os.replace 覆盖 latest，保证并发安全。
    """
    latest = os.path.join(task_dir, "latest")
    tmp = os.path.join(task_dir, ".latest.tmp")
    target = os.path.join("runs", rname)
    if os.path.islink(tmp) or os.path.exists(tmp):
        os.unlink(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, latest)
    return latest


def legacy_view_path(logs_dir: str, host: str, timestamp: str,
                     condition: str, task_id: str) -> str:
    """
    返回旧格式视图中某任务的路径（迁移脚本用）。

    输入: logs_dir, host, timestamp, condition, task_id
    输出: logs/<host>/ablation_<timestamp>/<condition>/<task_id> 绝对路径
    """
    return os.path.join(logs_dir, host, f"ablation_{timestamp}",
                        condition, task_id)


def create_legacy_symlink(logs_dir: str, host: str, timestamp: str,
                          condition: str, task_id: str) -> str:
    """
    在旧格式原位创建指向新 run_dir 的目录级软链接（相对路径）。

    输入: logs_dir, host, timestamp, condition, task_id
    输出: 创建的软链接绝对路径
    副作用: 自动建父目录；若链接已存在先删除再建（幂等）。
    """
    link = legacy_view_path(logs_dir, host, timestamp, condition, task_id)
    os.makedirs(os.path.dirname(link), exist_ok=True)
    target_abs = run_dir(logs_dir, condition, task_id, host, timestamp)
    rel = os.path.relpath(target_abs, os.path.dirname(link))
    if os.path.islink(link) or os.path.exists(link):
        if os.path.isdir(link) and not os.path.islink(link):
            raise RuntimeError(f"旧视图位置是实体目录，拒绝覆盖: {link}")
        os.unlink(link)
    os.symlink(rel, link)
    return link
