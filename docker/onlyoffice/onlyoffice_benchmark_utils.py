#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OnlyOffice 共享文档 Benchmark 工具模块

用于基于共享文档的任务：任务初始化放文件、生成 Agent 共享链接、评价时读取文档。
路径与 document_sharing_server.py / manage_documents.py 保持一致。
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional, Tuple

# 与 document_sharing_server 保持一致。
# 当本文件位于 src/stages/ 时，实际共享目录仍应落在 repo 根的 docker/onlyoffice/ 下。
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ONLYOFFICE_DIR = Path(__file__).resolve().parents[2] / "docker" / "onlyoffice"
_SCRIPT_DIR = _THIS_DIR if (_THIS_DIR / "document_sharing_server.py").exists() else _REPO_ONLYOFFICE_DIR
DOCUMENTS_DIR = _SCRIPT_DIR / "shared_documents"
SHARED_LINKS_FILE = _SCRIPT_DIR / "shared_links.json"
ALLOWED_EXTENSIONS = {
    "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "odt", "ods", "odp", "txt", "rtf", "pdf", "csv",
}

DEFAULT_SHARING_PORTS = (
    tuple(range(5050, 5061))
    + tuple(range(5000, 5011))
    + tuple(range(8000, 8011))
)


def get_documents_dir() -> Path:
    """返回共享文档目录路径（供任务初始化与评测读取）。"""
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    return DOCUMENTS_DIR


def get_shared_links_path() -> Path:
    """返回 shared_links.json 的路径。"""
    return SHARED_LINKS_FILE


def is_document_sharing_service(base_url: str, timeout: float = 3.0) -> bool:
    """
    判断 base_url 是否为 Flask 文档共享服务，而不是 OnlyOffice DocumentServer。

    文档共享服务提供 /api/documents；DocumentServer 的 80/443 端口不提供该接口。
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("需要安装 requests: pip install requests")

    try:
        resp = requests.get(f"{base_url.rstrip('/')}/api/documents", timeout=timeout)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return isinstance(data, dict) and "documents" in data
    except Exception:
        return False


def detect_document_sharing_url(
    host: str,
    candidate_ports: Tuple[int, ...] = DEFAULT_SHARING_PORTS,
    timeout: float = 3.0,
) -> Optional[str]:
    """
    动态检测 OnlyOffice Flask 文档共享服务地址。

    注意这里检测的是 benchmark 使用的 Flask 文档共享服务，不是
    OnlyOffice DocumentServer 的 80/443 端口。
    """
    host = (host or "localhost").strip()
    for port in candidate_ports:
        url = f"http://{host}:{port}"
        if is_document_sharing_service(url, timeout=timeout):
            return url
    return None


def resolve_document_sharing_url(
    base_url: str,
    host: str,
    timeout: float = 3.0,
    log=None,
) -> str:
    """
    解析文档共享服务 URL。

    - base_url 可用时直接使用。
    - 显式提供 base_url 但不可用时，保留该配置并尽快失败，避免误连到其他项目的 Flask 服务。
    - base_url 为空时，按常见 Flask 端口自动检测。
    - 无法检测时返回 http://host:5050，让后续健康检查给出明确错误。
    """
    base_url = (base_url or "").strip().rstrip("/")
    if base_url and is_document_sharing_service(base_url, timeout=timeout):
        return base_url
    if base_url:
        if log:
            log.warning("OnlyOffice 文档共享服务 URL %s 不可用；保留显式配置，不再自动切换到其他端口", base_url)
        return base_url

    detected = detect_document_sharing_url(host, timeout=timeout)
    if detected:
        if base_url and log:
            log.warning("OnlyOffice 文档共享服务 URL %s 不可用，自动切换到 %s", base_url, detected)
        elif log:
            log.info("自动检测到 OnlyOffice 文档共享服务: %s", detected)
        return detected

    fallback = f"http://{(host or 'localhost').strip()}:5050"
    if log:
        log.warning("无法自动检测 OnlyOffice 文档共享服务，继续使用 %s", fallback)
    return fallback


# ---------------------------------------------------------------------------
# 1. 任务初始化：把文件放到 shared_documents
# ---------------------------------------------------------------------------


def init_task_document(
    doc_id: str,
    source_path: str | Path,
    ext: Optional[str] = None,
) -> str:
    """
    将本地文件复制到 shared_documents，作为该任务的共享文档。

    参数:
        doc_id: 文档 ID，对应 OnlyOffice 的 document_id，建议与 task_uid 或任务唯一标识一致。
        source_path: 源文件路径（模板或初始内容）。
        ext: 扩展名，如 docx/xlsx/txt。若不传则从 source_path 取；若无则默认 docx。

    返回:
        doc_id（与传入一致，便于后续生成链接和评测）。
    """
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"模板文件不存在: {source_path}")

    if ext is None:
        if source_path.suffix:
            ext = source_path.suffix.lstrip(".").lower()
        else:
            ext = "docx"
    if ext not in ALLOWED_EXTENSIONS:
        ext = "docx"

    dest_dir = get_documents_dir()
    dest = dest_dir / f"{doc_id}.{ext}"
    import shutil
    if dest.exists():
        dest.unlink()
    shutil.copy2(source_path, dest)
    return doc_id


# ---------------------------------------------------------------------------
# 2. 给每个 Agent 生成共享链接
# ---------------------------------------------------------------------------


def create_share_link_via_api(base_url: str, document_id: str) -> str:
    """
    通过 Flask API 创建共享链接。

    参数:
        base_url: 文档共享服务根地址，如 http://<HOST_IP>:5050（不要末尾斜杠）。
        document_id: 文档 ID（与 init_task_document 的 doc_id 一致）。

    返回:
        共享链接 URL，Agent 在浏览器中打开即可编辑。
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("需要安装 requests: pip install requests")

    base_url = base_url.rstrip("/")
    r = requests.post(f"{base_url}/api/document/{document_id}/share", timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data.get("success") or not data.get("share_key"):
        raise RuntimeError(f"创建共享链接失败: {data}")
    return f"{base_url}/share/{data['share_key']}"


def create_share_link_local(
    document_id: str,
    base_url: str,
) -> str:
    """
    不调用 HTTP，直接写 shared_links.json 生成共享链接（与 manage_documents.py 逻辑一致）。
    适用于 benchmark 与 onlyoffice 同机、且希望不依赖 Flask 是否在运行。

    参数:
        document_id: 文档 ID。
        base_url: 文档共享服务根地址，如 http://<HOST_IP>:5050。

    返回:
        共享链接 URL。
    """
    from datetime import datetime

    if not resolve_document_path(document_id):
        raise FileNotFoundError(f"文档不存在，无法创建链接: {document_id}")

    links = {}
    if SHARED_LINKS_FILE.exists():
        with open(SHARED_LINKS_FILE, "r", encoding="utf-8") as f:
            links = json.load(f)

    share_key = secrets.token_urlsafe(16)
    links[share_key] = {
        "document_id": document_id,
        "created_at": datetime.now().isoformat(),
    }
    with open(SHARED_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)

    base_url = base_url.rstrip("/")
    return f"{base_url}/share/{share_key}"


def resolve_document_path(doc_id: str) -> Optional[Path]:
    """
    根据 doc_id 解析 shared_documents 中的真实文件路径。

    参数:
        doc_id: 文档 ID（无扩展名或带扩展名的文件名）。

    返回:
        若存在则返回 Path，否则 None。
    """
    # 精确匹配
    p = DOCUMENTS_DIR / doc_id
    if p.exists() and p.is_file():
        return p
    # 扩展名匹配
    for ext in ALLOWED_EXTENSIONS:
        cand = DOCUMENTS_DIR / f"{doc_id}.{ext}"
        if cand.exists() and cand.is_file() and not cand.name.startswith("._"):
            return cand
    return None


# ---------------------------------------------------------------------------
# 3. 评价函数读取共享文档
# ---------------------------------------------------------------------------


def get_document_path_for_eval(doc_id: str) -> Optional[Path]:
    """
    评测时获取文档本地路径（与 onlyoffice 同机时使用）。

    参数:
        doc_id: 文档 ID。

    返回:
        若存在则返回 Path，否则 None。
    """
    return resolve_document_path(doc_id)


def fetch_document_file_via_api(base_url: str, document_id: str) -> bytes:
    """
    通过 GET /api/document/<id>/file 拉取文档内容（评测与 onlyoffice 不同机时使用）。

    参数:
        base_url: 文档共享服务根地址。
        document_id: 文档 ID。

    返回:
        文档二进制内容。
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("需要安装 requests: pip install requests")

    base_url = base_url.rstrip("/")
    r = requests.get(f"{base_url}/api/document/{document_id}/file", timeout=30)
    r.raise_for_status()
    return r.content


def load_document_for_eval(
    doc_id: str,
    base_url: Optional[str] = None,
) -> Tuple[Optional[Path], Optional[bytes]]:
    """
    评测时统一入口：优先读本地文件，若无则通过 base_url 拉取。

    参数:
        doc_id: 文档 ID。
        base_url: 若提供且本地无文件，则用 HTTP 拉取。

    返回:
        (local_path, content_bytes)。本地存在时 local_path 非 None，content_bytes 可为 None；
        若通过 HTTP 拉取则 local_path 为 None，content_bytes 为内容。两者都不可用时为 (None, None)。
    """
    path = get_document_path_for_eval(doc_id)
    if path is not None:
        return (path, path.read_bytes())
    if base_url:
        try:
            content = fetch_document_file_via_api(base_url, doc_id)
            return (None, content)
        except Exception:
            pass
    return (None, None)


# ---------------------------------------------------------------------------
# 便捷：从环境或配置获取 base_url（供任务/评测脚本使用）
# ---------------------------------------------------------------------------


def get_document_server_base_url(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> str:
    """
    拼出文档共享服务 base_url。用于任务里给 Agent 的链接和评测时拉取文件。

    参数:
        host: 若不传则用环境变量 ONLYOFFICE_DOC_HOST 或 HOST_IP，再否则 localhost。
        port: 若不传则用环境变量 ONLYOFFICE_DOC_PORT 或 SERVER_PORT，再否则 5050。

    返回:
        "http://<host>:<port>"
    """
    h = host or os.environ.get("ONLYOFFICE_DOC_HOST") or os.environ.get("HOST_IP") or "localhost"
    p = port
    if p is None:
        env_port = os.environ.get("ONLYOFFICE_DOC_PORT") or os.environ.get("SERVER_PORT")
        p = int(env_port) if env_port else 5050
    return f"http://{h}:{p}"
