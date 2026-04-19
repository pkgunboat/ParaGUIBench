#!/usr/bin/env python3
"""
资源下载脚本：从 HuggingFace dataset 拉取 Ubuntu.qcow2 + 任务素材 +
OnlyOffice 模板 + WebMall 素材，落到 configs/deploy.yaml 指定的 resources.root。

支持三种来源：
    huggingface   —— 自动从 HF Hub 下载（需要 huggingface_hub；首次运行
                     会下载几十 GB，建议后台执行）
    usb           —— 从 U 盘解压（需先用 scripts/usb_transfer.sh pack 打包）
    local         —— 资源已经在本地，只校验目录结构是否完整

用法:
    python scripts/download_resources.py                      # 读 deploy.yaml
    python scripts/download_resources.py --source local       # 强制本地模式
    python scripts/download_resources.py --source usb --usb-dir /media/yuzedong/u盘1/ParaGUIBench-resources
    python scripts/download_resources.py --root /mnt/usb/bench  # 覆盖 root
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from config_loader import DeployConfig, load_deploy_config  # noqa: E402


REQUIRED_FILES: List[Dict[str, str]] = [
    # (logical_name, hf_rel_path, local_rel_path)
    {"name": "VM 磁盘镜像",     "hf": "Ubuntu.qcow2.zst",           "local": "Ubuntu.qcow2"},
    {"name": "Operation GT 缓存", "hf": "operation_gt_cache.tar.gz", "local": "operation_gt_cache"},
    {"name": "SearchWrite 模板", "hf": "searchwrite_templates.tar.gz","local": "searchwrite_templates"},
    {"name": "WebMall 资产",    "hf": "webmall_assets.tar.gz",      "local": "webmall_assets"},
]


def _hf_login_hint() -> str:
    return (
        "如果 HuggingFace 需要登录（私有 dataset / rate-limit），请先运行：\n"
        "    huggingface-cli login\n"
        "或导出 HF_TOKEN 环境变量。"
    )


def download_from_huggingface(hf_repo: str, root: Path, files: List[Dict[str, str]]) -> None:
    """从 HF Hub 下载并解压到 root。"""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise SystemExit(
            "缺少 huggingface_hub 包。\n"
            "    pip install huggingface_hub\n"
            f"{_hf_login_hint()}"
        )

    root.mkdir(parents=True, exist_ok=True)
    for item in files:
        hf_rel = item["hf"]
        print(f"\n下载 {item['name']} ({hf_rel}) ...")
        try:
            local_path = hf_hub_download(
                repo_id=hf_repo,
                repo_type="dataset",
                filename=hf_rel,
                cache_dir=str(root / ".hf_cache"),
            )
        except Exception as exc:
            raise SystemExit(f"下载失败 {hf_rel}: {exc}\n{_hf_login_hint()}")
        print(f"  缓存位置: {local_path}")
        _extract_or_move(Path(local_path), root, item)


def _extract_or_move(src: Path, root: Path, item: Dict[str, str]) -> None:
    """按文件后缀决定解压或直接拷贝/链接。"""
    target = root / item["local"]
    if item["hf"].endswith(".tar.gz"):
        import tarfile
        print(f"  解压到 {target} ...")
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(src, "r:gz") as tf:
            tf.extractall(target)
    elif item["hf"].endswith(".zst"):
        try:
            import zstandard as zstd
        except ImportError:
            raise SystemExit("缺少 zstandard 包；请 pip install zstandard")
        print(f"  解压到 {target} ...")
        with src.open("rb") as s, target.open("wb") as d:
            dctx = zstd.ZstdDecompressor()
            dctx.copy_stream(s, d)
    else:
        import shutil
        print(f"  拷贝到 {target} ...")
        shutil.copy2(src, target)


def verify_local(root: Path, files: List[Dict[str, str]]) -> bool:
    """检查本地目录是否包含所有必需文件/目录。"""
    ok = True
    print(f"\n检查本地资源目录: {root}")
    for item in files:
        target = root / item["local"]
        if target.exists():
            size = _du(target)
            print(f"  ✓ {item['name']}: {target} ({size})")
        else:
            print(f"  ✗ 缺少 {item['name']}: {target}")
            ok = False
    return ok


def _du(path: Path) -> str:
    """返回人类可读的磁盘占用。"""
    total = 0
    if path.is_file():
        total = path.stat().st_size
    else:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if total < 1024:
            return f"{total:.1f}{unit}"
        total /= 1024
    return f"{total:.1f}PB"


def _unpack_from_usb(usb_dir: Path, root: Path) -> None:
    """从 U 盘目录解压资源到 root。"""
    import shutil
    import tarfile

    root.mkdir(parents=True, exist_ok=True)
    print(f"U 盘目录:   {usb_dir}")

    for item in REQUIRED_FILES:
        hf_rel = item["hf"]
        local_rel = item["local"]
        target = root / local_rel

        if target.exists():
            print(f"\n  ✓ {item['name']} 已存在: {target}，跳过")
            continue

        src = usb_dir / hf_rel
        if not src.exists():
            print(f"\n  ✗ U 盘缺少 {item['name']}: {src}")
            continue

        print(f"\n解压 {item['name']} ({hf_rel}) ...")
        if hf_rel.endswith(".tar.gz"):
            target.mkdir(parents=True, exist_ok=True)
            with tarfile.open(src, "r:gz") as tf:
                tf.extractall(target)
        elif hf_rel.endswith(".zst"):
            try:
                import zstandard as zstd
            except ImportError:
                raise SystemExit("缺少 zstandard 包；请 pip install zstandard")
            with src.open("rb") as s, target.open("wb") as d:
                dctx = zstd.ZstdDecompressor()
                dctx.copy_stream(s, d)
        else:
            shutil.copy2(src, target)

    # sha256 校验（如果 U 盘上有）
    sumfile = usb_dir / "sha256sum.txt"
    if sumfile.exists():
        print("\n校验 sha256sum ...")
        import subprocess
        result = subprocess.run(["sha256sum", "-c", str(sumfile)],
                                cwd=str(usb_dir), capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ⚠ 部分文件校验失败:\n{result.stdout}{result.stderr}")
        else:
            print("  ✓ 校验通过")


def main() -> int:
    ap = argparse.ArgumentParser(description="下载/校验 benchmark 资源")
    ap.add_argument("--source", choices=["huggingface", "usb", "local"], default=None,
                    help="覆盖 deploy.yaml 的 resources.source")
    ap.add_argument("--root", type=str, default=None,
                    help="覆盖 deploy.yaml 的 resources.root")
    ap.add_argument("--hf-repo", type=str, default=None,
                    help="覆盖 deploy.yaml 的 resources.hf_repo")
    ap.add_argument("--usb-dir", type=str, default=None,
                    help="U 盘资源目录路径（source=usb 时使用）")
    ap.add_argument("--config", type=str, default=None, help="指定 deploy.yaml 路径")
    args = ap.parse_args()

    deploy = load_deploy_config(args.config)
    resources = deploy.get("resources", {}) if deploy else {}

    source = args.source or resources.get("source", "huggingface")
    root = Path(args.root or resources.get("root") or (REPO_ROOT / "resources"))
    root = root.expanduser().resolve()
    hf_repo = args.hf_repo or resources.get("hf_repo", "")

    print(f"资源根目录: {root}")
    print(f"来源:       {source}")

    if source == "usb":
        usb_dir = Path(args.usb_dir or resources.get("usb_dir", ""))
        if not usb_dir or not usb_dir.is_absolute():
            raise SystemExit(
                "USB 模式需要指定 --usb-dir 或在 deploy.yaml 中配置 resources.usb_dir"
            )
        if not usb_dir.exists():
            raise SystemExit(f"U 盘目录不存在: {usb_dir}")
        _unpack_from_usb(usb_dir, root)

    elif source == "huggingface":
        if not hf_repo or hf_repo.startswith("your-org/"):
            raise SystemExit(
                "未配置 resources.hf_repo。请在 configs/deploy.yaml 中填入真实 HF dataset repo。\n"
                "或使用 --source usb 从 U 盘安装：bash scripts/usb_transfer.sh unpack <USB_DIR>"
            )
        print(f"HF repo:    {hf_repo}")
        download_from_huggingface(hf_repo, root, REQUIRED_FILES)

    print("")
    ok = verify_local(root, REQUIRED_FILES)
    if not ok:
        print("\n✗ 资源目录不完整。")
        return 1

    print("\n✓ 资源就绪。接下来可以：")
    print("  bash scripts/start_services.sh             # 启动 OnlyOffice / WebMall")
    print("  python -m src.pipelines.run_ablation --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
