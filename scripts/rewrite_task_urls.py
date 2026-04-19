#!/usr/bin/env python3
"""
批量替换任务 JSON 中的 host URL。

场景:
    任务 JSON（src/parallel_benchmark/tasks/ + docker/webmall/tasks/）的 answer
    字段里保留了 benchmark maintainer 环境下的原始 host，例如
    ``http://10.1.110.114:9082/...``。部署到自己的 WebMall 实例后，需要把
    这些 host 改成新环境的地址。

用法:
    # 默认从 configs/deploy.yaml 读新 host（services.webmall.host_ip）
    python scripts/rewrite_task_urls.py

    # 手动指定
    python scripts/rewrite_task_urls.py \
        --from http://10.1.110.114 --to http://127.0.0.1

    # 演练（不写入）
    python scripts/rewrite_task_urls.py --dry-run

    # 多组替换
    python scripts/rewrite_task_urls.py \
        --replace http://10.1.110.114=http://mall.local \
        --replace http://10.1.110.143=http://mall.local

安全:
    - 默认就地覆盖；加 --backup 会把原文件存成 *.bak。
    - 只改 .json 文件；不会破坏 .py / .md 等代码文档。
    - 会自动跳过 __pycache__、.git、node_modules。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

DEFAULT_TARGET_DIRS = [
    REPO_ROOT / "src" / "parallel_benchmark" / "tasks",
    REPO_ROOT / "docker" / "webmall" / "tasks",
]

DEFAULT_ORIGIN_HOSTS = [
    "http://10.1.110.114",
    "http://10.1.110.143",
]


def _parse_replace_pairs(items: List[str]) -> List[Tuple[str, str]]:
    """解析 `--replace FROM=TO` 列表成 (from, to) 元组列表。"""
    pairs = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--replace 需要 FROM=TO 格式，收到: {item!r}")
        src, _, dst = item.partition("=")
        if not src or not dst:
            raise SystemExit(f"--replace 不能为空: {item!r}")
        pairs.append((src.rstrip("/"), dst.rstrip("/")))
    return pairs


def _default_to_host() -> str:
    """从 deploy.yaml 读 webmall host + 第一个端口拼成 http://host；失败返回空。"""
    try:
        from config_loader import DeployConfig
        d = DeployConfig()
        host = d.webmall_host or d.vm_host
        return f"http://{host}"
    except Exception:
        return ""


def _iter_json_files(dirs: List[Path]):
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*.json"):
            if any(part.startswith(".") for part in p.parts):
                continue
            yield p


def _apply_replacements(text: str, pairs: List[Tuple[str, str]]) -> Tuple[str, int]:
    """返回 (新文本, 发生替换次数)。"""
    count = 0
    for src, dst in pairs:
        new_text = text.replace(src, dst)
        if new_text != text:
            count += text.count(src)
            text = new_text
    return text, count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="from_host", default=None,
                    help="被替换的旧 host 前缀（例如 http://10.1.110.114）。可与 --to 搭配。")
    ap.add_argument("--to", dest="to_host", default=None,
                    help="替换成的新 host 前缀。未指定时从 configs/deploy.yaml 读 webmall_host。")
    ap.add_argument("--replace", action="append", default=[], metavar="FROM=TO",
                    help="多组替换，可重复。优先级高于 --from/--to。")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要改动的文件，不写入")
    ap.add_argument("--backup", action="store_true", help="写入前把原文件保存为 *.bak")
    ap.add_argument("--dir", action="append", default=[],
                    help="指定扫描目录（可重复）；默认扫 src/parallel_benchmark/tasks + docker/webmall/tasks")
    args = ap.parse_args()

    # 组装替换对
    pairs: List[Tuple[str, str]] = []
    if args.replace:
        pairs.extend(_parse_replace_pairs(args.replace))
    elif args.from_host or args.to_host:
        if not args.from_host or not args.to_host:
            raise SystemExit("--from 和 --to 必须同时提供；或改用 --replace FROM=TO")
        pairs.append((args.from_host.rstrip("/"), args.to_host.rstrip("/")))
    else:
        # 默认：从 deploy.yaml 读新 host，把两个已知旧 host 都替换过去
        to_host = _default_to_host()
        if not to_host:
            raise SystemExit(
                "未指定 --from/--to 或 --replace，且 configs/deploy.yaml 未提供 services.webmall.host_ip。\n"
                "请显式传参，例如：--from http://10.1.110.114 --to http://127.0.0.1"
            )
        pairs = [(src, to_host) for src in DEFAULT_ORIGIN_HOSTS]

    dirs = [Path(d).expanduser().resolve() for d in args.dir] if args.dir else DEFAULT_TARGET_DIRS

    print("替换规则:")
    for src, dst in pairs:
        print(f"  {src}  →  {dst}")
    print("扫描目录:")
    for d in dirs:
        print(f"  {d}")
    if args.dry_run:
        print("模式: DRY-RUN（不写入）")
    print()

    total_files = 0
    changed_files = 0
    total_subs = 0

    for path in _iter_json_files(dirs):
        total_files += 1
        try:
            orig = path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"  skip (read error): {path} — {exc}")
            continue

        new_text, subs = _apply_replacements(orig, pairs)
        if new_text == orig:
            continue

        # 校验 JSON 仍然合法
        try:
            json.loads(new_text)
        except json.JSONDecodeError as exc:
            print(f"  ✗ 跳过 {path}（替换后不是合法 JSON：{exc}）")
            continue

        changed_files += 1
        total_subs += subs
        rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
        print(f"  ✎ {rel}  ({subs} 处)")

        if not args.dry_run:
            if args.backup:
                path.with_suffix(path.suffix + ".bak").write_text(orig, encoding="utf-8")
            path.write_text(new_text, encoding="utf-8")

    print()
    print(f"扫描 {total_files} 个 JSON；改动 {changed_files} 个文件，共替换 {total_subs} 处。")
    if args.dry_run and changed_files:
        print("DRY-RUN：未写入任何文件；去掉 --dry-run 正式执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
