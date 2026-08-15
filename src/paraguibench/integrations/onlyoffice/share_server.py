"""OnlyOffice 文档共享服务。

本模块必须保持可独立复制进容器：除 Flask / requests 外不依赖 ParaGUIBench
其他包。import 本模块不得创建运行状态；只有 create_app() 才会在注入的
data_root 下建立目录。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote, unquote

import requests
from flask import Flask, current_app, jsonify, request, send_file

ALLOWED_EXTENSIONS = frozenset(
    {
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "odt",
        "ods",
        "odp",
        "txt",
        "rtf",
        "pdf",
    }
)
DOCUMENT_TYPES = {
    "docx": "word",
    "doc": "word",
    "odt": "word",
    "txt": "word",
    "rtf": "word",
    "pdf": "word",
    "xlsx": "cell",
    "xls": "cell",
    "ods": "cell",
    "pptx": "presentation",
    "ppt": "presentation",
    "odp": "presentation",
}
_DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,220}$")
_COLLAB_KEY_ILLEGAL_RE = re.compile(r"[^0-9A-Za-z.=_-]")
_APP_CONFIG_KEY = "ONLYOFFICE_SHARE_SERVICE"


def _env_flag(
    name: str, default: bool = False, environ: dict[str, str] | None = None
) -> bool:
    """读取布尔环境变量。

    输入参数：
        name：环境变量名。
        default：变量缺失时的默认值。
        environ：可注入的环境映射；默认使用 ``os.environ``。
    输出返回值：
        ``1/true/yes/on`` 为 True，其余为 False。
    """

    source = os.environ if environ is None else environ
    raw = source.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ShareServerConfig:
    """share service 的可注入运行配置。"""

    data_root: Path
    host_ip: str = "127.0.0.1"
    doc_server_port: int = 8080
    flask_port: int = 5050
    doc_fetch_host: str = "paraguibench-onlyoffice-share"
    doc_fetch_port: int = 5050
    jwt_enabled: bool = False

    @classmethod
    def from_environ(
        cls,
        environ: dict[str, str] | None = None,
        data_root: Path | str | None = None,
    ) -> "ShareServerConfig":
        """从环境变量构造配置，且必须显式给出数据根。

        功能：
            拒绝回退到源码目录，避免 import 或误启动把运行状态写进 Git checkout。
        输入参数：
            environ：可注入环境映射。
            data_root：显式数据根；优先于 ``ONLYOFFICE_SHARE_DATA_DIR``。
        输出返回值：
            冻结的 ``ShareServerConfig``。
        异常语义：
            数据根缺失时抛出 ``RuntimeError``。
        """

        source = os.environ if environ is None else environ
        raw_root = (
            data_root
            if data_root is not None
            else source.get("ONLYOFFICE_SHARE_DATA_DIR")
        )
        if not raw_root:
            raise RuntimeError(
                "ONLYOFFICE_SHARE_DATA_DIR 未设置；拒绝在源码目录创建运行状态"
            )
        return cls(
            data_root=Path(raw_root).expanduser().resolve(),
            host_ip=source.get("HOST_IP")
            or source.get("PARAGUIBENCH_ONLYOFFICE_HOST_IP")
            or "127.0.0.1",
            doc_server_port=int(source.get("DOC_SERVER_PORT") or "8080"),
            flask_port=int(source.get("FLASK_PORT") or "5050"),
            doc_fetch_host=source.get("DOC_FETCH_HOST")
            or "paraguibench-onlyoffice-share",
            doc_fetch_port=int(
                source.get("DOC_FETCH_PORT") or source.get("FLASK_PORT") or "5050"
            ),
            jwt_enabled=_env_flag("ONLYOFFICE_JWT_ENABLED", False, dict(source)),
        )

    @property
    def browser_onlyoffice_server(self) -> str:
        """返回默认的浏览器侧 DocumentServer 地址。"""

        if self.doc_server_port in {80, 443}:
            return f"http://{self.host_ip}"
        return f"http://{self.host_ip}:{self.doc_server_port}"


class ShareService:
    """单实例文档、共享链接与协作 key 的磁盘状态机。"""

    def __init__(self, config: ShareServerConfig) -> None:
        """按配置准备数据目录，但不读取已有 JSON。

        输入参数：
            config：已解析的运行配置。
        输出返回值：
            无。
        """

        self.config = config
        self.documents_dir = config.data_root / "shared_documents"
        self.links_path = config.data_root / "shared_links.json"
        self.keys_path = config.data_root / "document_keys.json"
        self._lock = threading.RLock()
        self.documents_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write_json(self, path: Path, payload: object) -> None:
        """原子写入 JSON 状态文件。

        输入参数：
            path：目标路径。
            payload：可 JSON 序列化的对象。
        输出返回值：
            无。
        """

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)

    def _load_json(self, path: Path) -> dict[str, Any]:
        """读取 JSON 对象；文件不存在时返回空字典。

        输入参数：
            path：状态文件路径。
        输出返回值：
            JSON object。
        异常语义：
            文件存在但不是 object 时抛出 ``ValueError``。
        """

        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"状态文件不是 JSON object: {path}")
        return payload

    def resolve_document_path(self, document_id: str) -> Path | None:
        """按 document_id 解析已落盘文件。

        输入参数：
            document_id：已通过安全检查的文档 ID。
        输出返回值：
            存在时返回 Path，否则 None。
        """

        exact = self.documents_dir / document_id
        if exact.is_file():
            return exact
        for extension in ALLOWED_EXTENSIONS:
            candidate = self.documents_dir / f"{document_id}.{extension}"
            if candidate.is_file():
                return candidate
        return None

    def save_document(self, document_id: str, extension: str, content: bytes) -> Path:
        """以确定性 ID 覆盖保存文档。

        输入参数：
            document_id：Attempt 级文档 ID。
            extension：已校验扩展名。
            content：文件字节。
        输出返回值：
            保存后的路径。
        """

        path = self.documents_dir / f"{document_id}.{extension}"
        path.write_bytes(content)
        return path

    def make_collab_key(self, document_id: str) -> str:
        """生成符合 OnlyOffice 字符集的新协作 key。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            最长 128 的合法 key。
        """

        raw = f"{document_id}:{time.time_ns()}:{secrets.token_hex(8)}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return _COLLAB_KEY_ILLEGAL_RE.sub("_", digest)[:128]

    def get_or_create_collab_key(self, document_id: str) -> str:
        """获取已有协作 key，不存在或非法时创建。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            稳定协作 key；同一文档的并发调用必须收敛到同一个 key。
        """

        with self._lock:
            keys = self._load_json(self.keys_path)
            existing = keys.get(document_id, {}).get("key")
            if (
                isinstance(existing, str)
                and existing
                and not _COLLAB_KEY_ILLEGAL_RE.search(existing)
            ):
                return existing
            keys[document_id] = {
                "key": self.make_collab_key(document_id),
                "created_at": datetime.now().isoformat(),
            }
            self._atomic_write_json(self.keys_path, keys)
            return keys[document_id]["key"]

    def refresh_collab_key(self, document_id: str) -> str:
        """在覆盖上传或成功 callback 后作废旧编辑会话。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            新的协作 key。
        """

        with self._lock:
            keys = self._load_json(self.keys_path)
            keys[document_id] = {
                "key": self.make_collab_key(document_id),
                "created_at": datetime.now().isoformat(),
            }
            self._atomic_write_json(self.keys_path, keys)
            return keys[document_id]["key"]

    def get_or_create_share_key(self, document_id: str) -> str:
        """为文档返回已有共享 key，没有则创建。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            可拼进 ``/share/<key>`` 的共享密钥。
        """

        with self._lock:
            links = self._load_json(self.links_path)
            for share_key, record in links.items():
                if (
                    isinstance(record, dict)
                    and record.get("document_id") == document_id
                ):
                    return share_key
            share_key = secrets.token_urlsafe(16)
            links[share_key] = {
                "document_id": document_id,
                "created_at": datetime.now().isoformat(),
            }
            self._atomic_write_json(self.links_path, links)
            return share_key

    def lookup_share(self, share_key: str) -> str | None:
        """按共享 key 查找 document_id。

        输入参数：
            share_key：URL 中的共享密钥。
        输出返回值：
            对应 document_id；不存在时为 None。
        """

        with self._lock:
            record = self._load_json(self.links_path).get(share_key)
        if isinstance(record, dict):
            document_id = record.get("document_id")
            if isinstance(document_id, str):
                return document_id
        return None

    def delete_document(self, document_id: str) -> Path | None:
        """删除当前文档及其共享链接和协作 key，不影响其他 Attempt。

        输入参数：
            document_id：要删除的文档 ID。
        输出返回值：
            被删除文件路径；文档不存在时为 None。
        """

        path = self.resolve_document_path(document_id)
        if path is None:
            return None
        path.unlink()
        with self._lock:
            links = self._load_json(self.links_path)
            remaining = {
                key: value
                for key, value in links.items()
                if not (
                    isinstance(value, dict) and value.get("document_id") == document_id
                )
            }
            if remaining != links:
                self._atomic_write_json(self.links_path, remaining)
            keys = self._load_json(self.keys_path)
            if document_id in keys:
                del keys[document_id]
                self._atomic_write_json(self.keys_path, keys)
        return path

    def list_documents(self) -> list[dict[str, Any]]:
        """列出数据根中的普通文档文件。

        输入参数：
            无。
        输出返回值：
            按修改时间倒序的文档摘要列表。
        """

        documents: list[dict[str, Any]] = []
        for path in self.documents_dir.iterdir():
            if not path.is_file() or path.name.startswith("._"):
                continue
            documents.append(
                {
                    "id": _document_id_from_filename(path.name),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "uploaded_at": datetime.fromtimestamp(
                        path.stat().st_mtime
                    ).isoformat(),
                }
            )
        documents.sort(key=lambda item: item["uploaded_at"], reverse=True)
        return documents


def _document_id_from_filename(filename: str) -> str:
    """从落盘文件名还原 document_id。

    输入参数：
        filename：``{document_id}.{ext}`` 或完整文件名。
    输出返回值：
        去掉合法扩展名后的 ID；无法拆分时返回原文件名。
    """

    if "." not in filename:
        return filename
    stem, extension = filename.rsplit(".", 1)
    if extension.lower() in ALLOWED_EXTENSIONS:
        return stem
    return filename


def _normalize_document_id(document_id: str) -> str | None:
    """解码并校验路径中的 document_id。

    输入参数：
        document_id：URL 路径参数。
    输出返回值：
        合法 ID；非法时为 None。
    """

    cleaned = unquote(document_id).strip()
    if not _DOCUMENT_ID_RE.fullmatch(cleaned):
        return None
    return cleaned


def _infer_extension(original_name: str) -> str | None:
    """从原始文件名推断允许的扩展名。

    输入参数：
        original_name：上传时的文件名。
    输出返回值：
        小写扩展名；不支持时为 None。
    """

    if "." not in original_name:
        return None
    extension = original_name.rsplit(".", 1)[1].lower()
    if extension in ALLOWED_EXTENSIONS:
        return extension
    return None


def _browser_onlyoffice_server(config: ShareServerConfig) -> str:
    """按当前请求 Host 生成浏览器可达的 DocumentServer 地址。

    输入参数：
        config：运行配置，用作无 Host 时的回退。
    输出返回值：
        ``http://<host>:<doc_port>``。
    """

    try:
        host = (request.host or "").split(":")[0]
    except RuntimeError:
        host = ""
    if not host:
        return config.browser_onlyoffice_server
    if config.doc_server_port in {80, 443}:
        return f"http://{host}"
    return f"http://{host}:{config.doc_server_port}"


def _current_service() -> ShareService:
    """从 Flask 应用配置取出本进程唯一的 ShareService。

    输入参数：
        无。
    输出返回值：
        当前应用绑定的 ``ShareService``。
    异常语义：
        应用未经过 ``create_app`` 装配时抛出 ``RuntimeError``。
    """

    service = current_app.config.get(_APP_CONFIG_KEY)
    if not isinstance(service, ShareService):
        raise RuntimeError("share service 尚未通过 create_app 装配")
    return service


def _render_share_page(service: ShareService, document_id: str, filename: str) -> str:
    """渲染可供浏览器打开的协作编辑页。

    输入参数：
        service：当前服务。
        document_id：文档 ID。
        filename：落盘文件名，仅用于标题和类型推断。
    输出返回值：
        HTML 字符串。
    """

    config = service.config
    escaped_name = html.escape(filename, quote=True)
    doc_id_q = quote(document_id, safe="")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "docx"
    document_type = DOCUMENT_TYPES.get(extension, "word")
    onlyoffice_server = html.escape(_browser_onlyoffice_server(config), quote=True)
    fetch_host = html.escape(config.doc_fetch_host, quote=True)
    jwt_enabled = "true" if config.jwt_enabled else "false"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>共享文档: {escaped_name}</title>
  <script src="{onlyoffice_server}/web-apps/apps/api/documents/api.js"></script>
</head>
<body>
  <h1>{escaped_name}</h1>
  <div id="editor" style="width:100%;height:700px;"></div>
  <script>
    const docServerUrl = "http://{fetch_host}:{config.doc_fetch_port}";
    const baseUrl = window.location.origin;
    const docUrl = docServerUrl + "/api/document/{doc_id_q}/file";
    const callbackUrl = docServerUrl + "/api/document/{doc_id_q}/callback";
    fetch(baseUrl + "/api/document/{doc_id_q}/collab-key")
      .then((response) => response.json())
      .then((keyData) => {{
        const config = {{
          document: {{
            fileType: {json.dumps(extension)},
            key: keyData.key,
            title: {json.dumps(filename)},
            url: docUrl
          }},
          documentType: {json.dumps(document_type)},
          editorConfig: {{
            mode: "edit",
            callbackUrl: callbackUrl
          }},
          width: "100%",
          height: "700px"
        }};
        if (!{jwt_enabled}) {{
          new DocsAPI.DocEditor("editor", config);
          return;
        }}
        return fetch(baseUrl + "/api/document/{doc_id_q}/token", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify(config)
        }}).then((response) => response.json()).then((data) => {{
          if (!data.token) {{
            throw new Error(data.error || "JWT token 生成失败");
          }}
          config.token = data.token;
          new DocsAPI.DocEditor("editor", config);
        }});
      }})
      .catch((error) => {{
        document.getElementById("editor").textContent = "加载失败: " + error;
      }});
  </script>
</body>
</html>
"""


def _resolve_config(
    config: ShareServerConfig | Path | str | None,
) -> ShareServerConfig:
    """把工厂参数规范成 ShareServerConfig。

    输入参数：
        config：配置对象、数据根路径或 None。
    输出返回值：
        ``ShareServerConfig``。
    异常语义：
        None 且环境变量缺失时抛出 ``RuntimeError``。
    """

    if isinstance(config, ShareServerConfig):
        return config
    if isinstance(config, (str, Path)):
        return ShareServerConfig.from_environ(data_root=config)
    return ShareServerConfig.from_environ()


def create_app(config: ShareServerConfig | Path | str | None = None) -> Flask:
    """创建不在 import 时落盘的 Flask 应用。

    功能：
        供 Gunicorn ``--factory`` 与单元测试共同使用。未注入 data_root
        时拒绝启动。
    输入参数：
        config：``ShareServerConfig``、数据根路径，或 None（读环境变量）。
    输出返回值：
        已注册路由的 Flask 应用。
    异常语义：
        数据根缺失时抛出 ``RuntimeError``。
    """

    resolved = _resolve_config(config)
    resolved.data_root.mkdir(parents=True, exist_ok=True)
    service = ShareService(resolved)
    app = Flask(__name__)
    app.config[_APP_CONFIG_KEY] = service
    _register_routes(app)
    return app


def _register_routes(app: Flask) -> None:
    """把 share service API 注册到给定应用。

    输入参数：
        app：由 ``create_app`` 创建的 Flask 应用。
    输出返回值：
        无。
    """

    @app.get("/healthz")
    def healthz() -> Any:
        """返回单实例健康信息。

        输入参数：
            无。
        输出返回值：
            ``ok``、数据目录和 JWT 开关。
        """

        service = _current_service()
        return jsonify(
            {
                "ok": True,
                "documents_dir": str(service.documents_dir),
                "onlyoffice_server": service.config.browser_onlyoffice_server,
                "jwt_enabled": service.config.jwt_enabled,
                "instance": "single",
            }
        )

    @app.get("/api/documents")
    def list_documents() -> Any:
        """列出当前数据根中的文档。

        输入参数：
            无。
        输出返回值：
            ``{"documents": [...]}``。
        """

        return jsonify({"documents": _current_service().list_documents()})

    @app.post("/api/upload")
    def upload_document() -> Any:
        """上传或覆盖恢复模板文档。

        输入参数：
            表单字段 ``file`` 为文件；可选 ``document_id`` 为 Attempt 级 ID。
        输出返回值：
            成功时返回 ``document_id``；自定义 ID 覆盖会刷新协作 key。
        异常语义：
            缺文件、非法 ID 或不支持的扩展名返回 400。
        """

        service = _current_service()
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return jsonify({"success": False, "error": "没有文件"}), 400
        extension = _infer_extension(uploaded.filename)
        if extension is None:
            return jsonify({"success": False, "error": "不支持的文件类型"}), 400
        custom_id = (request.form.get("document_id") or "").strip()
        if custom_id:
            document_id = _normalize_document_id(custom_id)
            if document_id is None:
                return jsonify({"success": False, "error": "非法 document_id"}), 400
        else:
            digest = hashlib.sha256(
                f"{uploaded.filename}:{time.time_ns()}".encode("utf-8")
            ).hexdigest()
            document_id = digest[:32]
        service.save_document(document_id, extension, uploaded.read())
        if custom_id:
            service.refresh_collab_key(document_id)
        return jsonify(
            {
                "success": True,
                "document_id": document_id,
                "filename": uploaded.filename,
            }
        )

    @app.get("/api/document/<path:document_id>/collab-key")
    def get_collab_key(document_id: str) -> Any:
        """获取或创建文档协作 key。

        输入参数：
            document_id：URL 路径中的文档 ID。
        输出返回值：
            ``{"key": "..."}``。
        """

        normalized = _normalize_document_id(document_id)
        if normalized is None:
            return jsonify({"error": "非法 document_id"}), 400
        return jsonify({"key": _current_service().get_or_create_collab_key(normalized)})

    @app.get("/api/document/<path:document_id>/file")
    def get_document_file(document_id: str) -> Any:
        """下载当前文档字节。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            文件响应；不存在时 404。
        """

        normalized = _normalize_document_id(document_id)
        if normalized is None:
            return jsonify({"error": "非法 document_id"}), 400
        path = _current_service().resolve_document_path(normalized)
        if path is None:
            return jsonify({"error": f"文档不存在: {normalized}"}), 404
        return send_file(path, as_attachment=False)

    @app.post("/api/document/<path:document_id>/share")
    def create_share_link(document_id: str) -> Any:
        """创建或复用共享链接。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            ``{"success": true, "share_key": "..."}``；同一文档重复调用返回同一 key。
        """

        normalized = _normalize_document_id(document_id)
        if normalized is None:
            return jsonify({"success": False, "error": "非法 document_id"}), 400
        service = _current_service()
        if service.resolve_document_path(normalized) is None:
            return jsonify({"success": False, "error": "文档不存在"}), 404
        return jsonify(
            {
                "success": True,
                "share_key": service.get_or_create_share_key(normalized),
            }
        )

    @app.post("/api/document/<path:document_id>/callback")
    def document_callback(document_id: str) -> Any:
        """处理 DocumentServer 保存回调。

        功能：
            status 2/6 时下载最终文档并写回；下载失败必须返回 ``error=1``，
            不得伪装成保存成功。
        输入参数：
            document_id：文档 ID。
            JSON body：OnlyOffice callback 载荷，需包含 ``status`` 与可选 ``url``。
        输出返回值：
            OnlyOffice 约定的 ``{"error": 0|1}``。
        """

        normalized = _normalize_document_id(document_id)
        if normalized is None:
            return jsonify({"error": 1})
        service = _current_service()
        payload = request.get_json(silent=True) or {}
        status = payload.get("status")
        if status not in {2, 6}:
            return jsonify({"error": 0})
        download_url = payload.get("url")
        if not download_url:
            return jsonify({"error": 1})
        try:
            response = requests.get(str(download_url), timeout=30)
        except requests.RequestException:
            return jsonify({"error": 1})
        if response.status_code != 200:
            return jsonify({"error": 1})
        path = service.resolve_document_path(normalized)
        if path is None:
            return jsonify({"error": 1})
        path.write_bytes(response.content)
        service.refresh_collab_key(normalized)
        return jsonify({"error": 0})

    @app.delete("/api/document/<path:document_id>")
    def delete_document(document_id: str) -> Any:
        """只删除当前 Attempt 文档及其链接和协作 key。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            成功 ``{"success": true}``；不存在时 404。
        """

        normalized = _normalize_document_id(document_id)
        if normalized is None:
            return jsonify({"success": False, "error": "非法 document_id"}), 400
        deleted = _current_service().delete_document(normalized)
        if deleted is None:
            return jsonify({"success": False, "error": "文档不存在"}), 404
        return jsonify({"success": True})

    @app.get("/share/<share_key>")
    def share_document_view(share_key: str) -> Any:
        """打开共享编辑页。

        输入参数：
            share_key：共享密钥。
        输出返回值：
            HTML 编辑页；链接或文件不存在时 404。
        """

        service = _current_service()
        document_id = service.lookup_share(share_key)
        if document_id is None:
            return "<h1>共享链接无效或已过期</h1>", 404
        path = service.resolve_document_path(document_id)
        if path is None:
            return f"<h1>文档不存在: {html.escape(document_id)}</h1>", 404
        return _render_share_page(service, document_id, path.name)

    @app.post("/api/document/<path:document_id>/token")
    def get_document_token(document_id: str) -> Any:
        """JWT 未启用时拒绝签发 token。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            第一版单实例默认关闭 JWT，固定返回 400。
        """

        return jsonify({"error": "DocumentServer 未启用 JWT，无需生成 token"}), 400
