"""
WebMall: Chrome/Chromium 收藏夹（Bookmarks）读写工具。

设计目标：
1) 评测侧不再依赖 Agent 手抄 URL，改为让 Agent 把答案页加入收藏夹。
2) 任务开始前清空收藏夹里的“网页条目”（type=url），保留原有文件夹结构（type=folder）。
3) 任务结束后读取收藏夹中的 URL 列表用于评测。

说明：
- 本模块通过 `desktop_env.controllers.python.PythonController` 与 VM 通信。
- `PythonController` 当前仅支持 `get_file()` + `execute_python_command()`，不提供直接写文件接口；
  因此“清空 Bookmarks 文件”通过在 VM 内执行 Python 代码完成（原子写回）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _parse_last_json_line(output: str) -> Dict[str, Any]:
    """
    从 controller.execute_python_command() 的 stdout 中解析最后一行 JSON。

    输入:
        output: VM 端 Python 运行输出（可能包含多行日志）
    输出:
        dict: 解析到的 JSON 对象；失败返回空 dict
    """
    if not output:
        return {}
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if not lines:
        return {}
    last = lines[-1].strip()
    try:
        return json.loads(last)
    except Exception:
        return {}


def _make_raw_python_controller(controller):
    """
    创建一个“无 pyautogui 前缀”的 PythonController。

    说明：
    - `PythonController` 默认 pkgs_prefix 会强制 import pyautogui，用于 GUI 操作。
    - 书签清空/探测属于纯文件操作，不依赖 GUI；使用纯 `{command}` 更稳、更快。

    输入:
        controller: 现有 PythonController（用于复用 vm_ip/server_port）
    输出:
        PythonController: 新 controller（或原 controller 作为兜底）
    """
    from desktop_env.controllers.python import PythonController  # type: ignore

    vm_ip = getattr(controller, "vm_ip", "")
    http_server = getattr(controller, "http_server", "")
    try:
        server_port = int(str(http_server).rsplit(":", 1)[-1])
    except Exception:
        server_port = None
    if not vm_ip or server_port is None:
        return controller
    return PythonController(vm_ip=vm_ip, server_port=server_port, pkgs_prefix="{command}")


def find_bookmarks_path(controller) -> str:
    """
    在 VM 内探测 Chrome/Chromium 的 Bookmarks 文件路径。

    输入:
        controller: PythonController，用于在 VM 内执行 Python 命令
    输出:
        str: Bookmarks 文件路径；未找到返回空字符串
    """
    probe_code = r"""
import json
import os

candidates = [
    os.path.join(os.path.expanduser("~"), ".config", "google-chrome", "Default", "Bookmarks"),
    os.path.join(os.path.expanduser("~"), ".config", "chromium", "Default", "Bookmarks"),
    os.path.join(os.path.expanduser("~"), "snap", "chromium", "common", "chromium", "Default", "Bookmarks"),
]

found = ""
for p in candidates:
    if os.path.exists(p):
        found = p
        break

print(json.dumps({"found": found, "candidates": candidates}, ensure_ascii=False))
"""
    raw_controller = _make_raw_python_controller(controller)
    result = raw_controller.execute_python_command(probe_code) or {}
    payload = _parse_last_json_line((result.get("output") or "").strip())
    return (payload.get("found") or "").strip()


def extract_urls_from_bookmarks_json(bookmarks: Dict[str, Any]) -> List[str]:
    """
    从 Bookmarks JSON 递归提取全部 URL。

    输入:
        bookmarks: Bookmarks 文件解析后的 dict
    输出:
        List[str]: 书签中的 URL（去重，保留原顺序）
    """
    urls: List[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "url":
                url = (node.get("url") or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(bookmarks.get("roots", bookmarks))
    return urls


def read_bookmark_urls(controller, bookmarks_path: Optional[str] = None) -> List[str]:
    """
    从 VM 读取 Bookmarks 文件并解析出 URL 列表。

    输入:
        controller: PythonController
        bookmarks_path: 可选，指定 Bookmarks 文件路径；为 None 时自动探测
    输出:
        List[str]: 收藏夹 URL 列表
    """
    path = (bookmarks_path or "").strip() or find_bookmarks_path(controller)
    if not path:
        raise FileNotFoundError("未找到 Chrome/Chromium Bookmarks 文件路径")

    raw = controller.get_file(path)
    if not raw:
        raise RuntimeError(f"读取 Bookmarks 文件失败: {path}")

    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        data = json.loads(raw.decode("utf-8", errors="ignore"))

    return extract_urls_from_bookmarks_json(data)


def close_chrome_and_clear_bookmarks(controller) -> Dict[str, Any]:
    """
    关闭浏览器并清空 Bookmarks 中所有网页条目（type=url），保留文件夹结构。

    为什么需要先关闭浏览器？
    - Chrome 可能在退出时把内存里的旧书签写回磁盘，导致“清空后又恢复”。先 kill 进程能显著降低该风险。
    - 同时清空 `Bookmarks.bak`（如果存在）进一步降低被恢复的概率。

    输入:
        controller: PythonController
    输出:
        dict: VM 端执行的结果摘要（ok/paths/removed_count 等），用于调试与日志记录
    """
    clear_code = r"""
import json, os, subprocess, time

cands = [
    os.path.join(os.path.expanduser("~"), ".config", "google-chrome", "Default", "Bookmarks"),
    os.path.join(os.path.expanduser("~"), ".config", "chromium", "Default", "Bookmarks"),
    os.path.join(os.path.expanduser("~"), "snap", "chromium", "common", "chromium", "Default", "Bookmarks"),
]

def run(cmd):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

for cmd in [
    ["pkill", "-x", "google-chrome"],
    ["pkill", "-x", "chromium"],
    ["pkill", "-x", "chrome"],
    ["killall", "-q", "google-chrome"],
    ["killall", "-q", "chromium"],
]:
    run(cmd)
time.sleep(0.2)

path = next((p for p in cands if os.path.exists(p)), "")
out = {"ok": False, "bookmarks_path": path, "candidates": cands, "cleared": [], "removed_url_nodes": 0, "error": ""}

def clear_file(fp):
    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    removed = 0
    def walk(n):
        nonlocal removed
        if isinstance(n, dict):
            if n.get("type") == "url":
                removed += 1
                return None
            ch = n.get("children")
            if isinstance(ch, list):
                new = []
                for it in ch:
                    r = walk(it)
                    if r is not None:
                        new.append(r)
                n["children"] = new
            return n
        if isinstance(n, list):
            new = []
            for it in n:
                r = walk(it)
                if r is not None:
                    new.append(r)
            return new
        return n
    if isinstance(data, dict) and "roots" in data:
        data["roots"] = walk(data["roots"])
    else:
        data = walk(data)
    if isinstance(data, dict):
        data.pop("checksum", None)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, fp)
    return removed

if not path:
    out["error"] = "Bookmarks_not_found"
    print(json.dumps(out, ensure_ascii=False))
else:
    total = 0
    errs = []
    for fp in [path, path + ".bak"]:
        if os.path.exists(fp):
            try:
                total += int(clear_file(fp))
                out["cleared"].append(fp)
            except Exception as e:
                errs.append(f"{fp}: {repr(e)}")
    out["ok"] = True
    out["removed_url_nodes"] = total
    out["clear_errors"] = errs
    print(json.dumps(out, ensure_ascii=False))
"""

    # 注意：部分 VM 的 /execute 端点对 python -c 的命令长度/执行时间较敏感。
    # 为了减少被 SIGTERM(-15) 杀掉的概率，这里使用一个更短的“压缩版”清理脚本。
    clear_code_compact = r"""
import os, json, subprocess, time

def run(cmd):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

for cmd in [
    ["pkill", "-x", "google-chrome"],
    ["pkill", "-x", "chromium"],
    ["pkill", "-x", "chrome"],
    ["killall", "-q", "google-chrome"],
    ["killall", "-q", "chromium"],
]:
    run(cmd)
time.sleep(0.2)

cands = [
    os.path.join(os.path.expanduser("~"), ".config", "google-chrome", "Default", "Bookmarks"),
    os.path.join(os.path.expanduser("~"), ".config", "chromium", "Default", "Bookmarks"),
    os.path.join(os.path.expanduser("~"), "snap", "chromium", "common", "chromium", "Default", "Bookmarks"),
]
p = ""
for x in cands:
    if os.path.exists(x):
        p = x
        break

out = {"ok": False, "bookmarks_path": p, "candidates": cands, "cleared": [], "removed_url_nodes": 0, "error": ""}

def clean(n):
    if isinstance(n, dict):
        if n.get("type") == "url":
            return None, 1
        removed = 0
        ch = n.get("children")
        if isinstance(ch, list):
            new = []
            for it in ch:
                c, r = clean(it)
                removed += r
                if c is not None:
                    new.append(c)
            n["children"] = new
        else:
            # 例如 roots 是一个“字典映射”，不走 children；此时需要递归清理各 value
            for k in list(n.keys()):
                v = n.get(k)
                if isinstance(v, (dict, list)):
                    c, r = clean(v)
                    removed += r
                    if c is None:
                        try:
                            del n[k]
                        except Exception:
                            n[k] = {} if isinstance(v, dict) else []
                    else:
                        n[k] = c
        return n, removed
    if isinstance(n, list):
        removed = 0
        new = []
        for it in n:
            c, r = clean(it)
            removed += r
            if c is not None:
                new.append(c)
        return new, removed
    return n, 0

def clear_file(fp):
    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    removed = 0
    if isinstance(data, dict) and "roots" in data:
        data["roots"], removed = clean(data["roots"])
    else:
        data, removed = clean(data)
    if isinstance(data, dict):
        data.pop("checksum", None)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, fp)
    return removed

if not p:
    out["error"] = "Bookmarks_not_found"
else:
    total = 0
    errs = []
    for fp in [p, p + ".bak"]:
        if os.path.exists(fp):
            try:
                total += int(clear_file(fp))
                out["cleared"].append(fp)
            except Exception as e:
                errs.append(f"{fp}: {repr(e)}")
    out["ok"] = True
    out["removed_url_nodes"] = total
    out["clear_errors"] = errs

print(json.dumps(out, ensure_ascii=False))
"""

    raw_controller = _make_raw_python_controller(controller)
    result = raw_controller.execute_python_command(clear_code_compact) or {}
    output = (result.get("output") or "").strip()
    payload = _parse_last_json_line(output)
    if payload:
        return payload

    # 回传原始信息便于排查（避免打印过长）
    raw_error = (result.get("error") or "").strip()
    raw_tail = output[-2000:] if output else ""
    return {
        "ok": False,
        "error": "no_json_output",
        "returncode": result.get("returncode"),
        "raw_error": raw_error[-2000:] if raw_error else "",
        "raw_output_tail": raw_tail,
        "result_keys": list(result.keys()),
    }

