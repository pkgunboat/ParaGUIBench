from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from typing import Iterable, List, Optional
from urllib.parse import unquote

import requests

current_dir = os.path.dirname(os.path.abspath(__file__))
ubuntu_env_dir = os.path.dirname(current_dir)
parallel_benchmark_dir = os.path.join(ubuntu_env_dir, "parallel_benchmark")

if parallel_benchmark_dir not in sys.path:
    sys.path.insert(0, parallel_benchmark_dir)
if ubuntu_env_dir not in sys.path:
    sys.path.insert(0, ubuntu_env_dir)

try:  # noqa: E402
    from stages.run_QA_pipeline import (
        _list_hf_files,
        parse_prepare_script_path,
    )
except ImportError:  # pragma: no cover
    from run_QA_pipeline import (
        _list_hf_files,
        parse_prepare_script_path,
    )

HF_DATA_DIR = os.path.join(parallel_benchmark_dir, "hf_data")


def get_task_cache_dir(task_uid: str) -> str:
    return os.path.join(HF_DATA_DIR, "benchmark_dataset", task_uid)


def _cache_marker_path(task_uid: str) -> str:
    return os.path.join(get_task_cache_dir(task_uid), ".complete")


def list_cached_task_files(task_uid: str) -> List[str]:
    cache_dir = get_task_cache_dir(task_uid)
    if not os.path.isdir(cache_dir):
        return []
    results: List[str] = []
    for root, _, files in os.walk(cache_dir):
        for name in files:
            if name == ".complete":
                continue
            results.append(os.path.join(root, name))
    results.sort()
    return results


def _log(log: Optional[logging.Logger], level: str, message: str, *args) -> None:
    if log is None:
        text = message % args if args else message
        print(text)
        return
    getattr(log, level)(message, *args)


def _download_with_retry(url: str, dest_path: str, timeout: int, attempts: int) -> None:
    last_exc: Exception | None = None
    tmp_path = f"{dest_path}.tmp"
    for attempt in range(1, attempts + 1):
        try:
            with requests.get(url, timeout=timeout, stream=True) as resp:
                resp.raise_for_status()
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with open(tmp_path, "wb") as file_obj:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            file_obj.write(chunk)
            os.replace(tmp_path, dest_path)
            return
        except Exception as exc:
            last_exc = exc
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if attempt < attempts:
                time.sleep(min(2 * attempt, 5))
    assert last_exc is not None
    raise last_exc


def _list_hf_files_with_retry(
    repo_id: str,
    revision: str,
    subdir: str,
    attempts: int,
) -> Iterable[str]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _list_hf_files(repo_id, revision, subdir)
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(min(2 * attempt, 5))
    assert last_exc is not None
    raise last_exc


def ensure_task_data_cached(
    task_uid: str,
    prepare_script_path: str,
    log: Optional[logging.Logger] = None,
    timeout: int = 120,
    attempts: int = 3,
    force: bool = False,
) -> bool:
    """
    将任务依赖文件缓存到本地 hf_data/benchmark_dataset/<task_uid>/ 目录。

    tree URL 会缓存为扁平化文件名，这与运行时 shared 根目录的最终形态一致。
    """
    cache_dir = get_task_cache_dir(task_uid)
    marker_path = _cache_marker_path(task_uid)
    existing_files = list_cached_task_files(task_uid)
    if existing_files and os.path.isfile(marker_path) and not force:
        _log(log, "info", "任务 %s 已命中本地缓存，共 %d 个文件", task_uid, len(existing_files))
        return True

    if not prepare_script_path:
        return False

    if os.path.isdir(cache_dir) and (force or not os.path.isfile(marker_path)):
        shutil.rmtree(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)

    urls = [item.strip() for item in prepare_script_path.split(",") if item.strip()]
    downloaded = 0

    for url in urls:
        try:
            repo_id, revision, subdir = parse_prepare_script_path(url)
            file_paths = _list_hf_files_with_retry(repo_id, revision, subdir, attempts)
            for rel_path in file_paths:
                filename = os.path.basename(rel_path)
                if not filename:
                    continue
                dest_path = os.path.join(cache_dir, filename)
                if os.path.isfile(dest_path) and not force:
                    downloaded += 1
                    continue
                dl_url = f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{rel_path}"
                _log(log, "info", "缓存 %s ← HF tree", filename)
                _download_with_retry(dl_url, dest_path, timeout=timeout, attempts=attempts)
                downloaded += 1
            continue
        except ValueError:
            pass
        except Exception as exc:
            _log(log, "warning", "HF tree 缓存失败，停止回退 HTML 下载: %s", exc)
            return False

        filename = unquote(url.rstrip("/").split("/")[-1])
        if not filename:
            continue
        dest_path = os.path.join(cache_dir, filename)
        if os.path.isfile(dest_path) and not force:
            downloaded += 1
            continue
        try:
            _log(log, "info", "缓存 %s ← 直接 URL", filename)
            _download_with_retry(url, dest_path, timeout=timeout, attempts=attempts)
            downloaded += 1
        except Exception as exc:
            _log(log, "warning", "直接 URL 缓存失败 %s: %s", filename, exc)
            return False

    if downloaded > 0 or bool(list_cached_task_files(task_uid)):
        with open(marker_path, "w", encoding="utf-8") as file_obj:
            file_obj.write("ok\n")
        return True
    return False


def copy_cached_task_data(
    task_uid: str,
    host_shared_dir: str,
    clear_dest: bool = False,
    log: Optional[logging.Logger] = None,
) -> bool:
    cache_files = list_cached_task_files(task_uid)
    if not cache_files:
        _log(log, "warning", "任务 %s 本地缓存为空，无法复制到 shared", task_uid)
        return False

    os.makedirs(host_shared_dir, exist_ok=True)
    if clear_dest:
        for name in os.listdir(host_shared_dir):
            path = os.path.join(host_shared_dir, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

    copied = 0
    for src_path in cache_files:
        dest_path = os.path.join(host_shared_dir, os.path.basename(src_path))
        shutil.copy2(src_path, dest_path)
        copied += 1

    _log(log, "info", "已复制 %d 个缓存文件到 shared: %s", copied, host_shared_dir)
    return copied > 0
