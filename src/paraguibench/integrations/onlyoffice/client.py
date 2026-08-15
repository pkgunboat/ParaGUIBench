"""OnlyOffice share service 的宿主侧 HTTP 客户端。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class OnlyOfficeShareClientError(RuntimeError):
    """share service 调用失败。"""


@dataclass(frozen=True)
class OnlyOfficeShareClient:
    """面向单实例 share service 的最小 HTTP 客户端。"""

    base_url: str
    timeout_seconds: float = 30.0

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """发送一次有界 HTTP 请求。

        输入参数：
            method：HTTP 方法。
            path：以 ``/`` 开头的路径。
            data：可选请求体。
            headers：可选请求头。
        输出返回值：
            ``(status_code, body)``。
        异常语义：
            网络失败时抛出 ``OnlyOfficeShareClientError``。
        """

        url = self.base_url.rstrip("/") + path
        request = Request(url, data=data, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()
        except URLError as exc:
            raise OnlyOfficeShareClientError(f"无法连接 share service: {url}") from exc

    def healthz(self) -> dict[str, Any]:
        """检查 share service 是否健康。

        输入参数：
            无。
        输出返回值：
            healthz JSON object。
        异常语义：
            非 200 或 JSON 非法时抛出 ``OnlyOfficeShareClientError``。
        """

        status, body = self._request("GET", "/healthz")
        if status != 200:
            raise OnlyOfficeShareClientError(f"healthz 失败: HTTP {status}")
        payload = json.loads(body.decode("utf-8"))
        if not payload.get("ok"):
            raise OnlyOfficeShareClientError("healthz 返回 ok=false")
        return payload

    def upload(self, document_id: str, source_path: Path) -> str:
        """按确定性 document_id 覆盖上传模板。

        输入参数：
            document_id：Attempt 级文档 ID。
            source_path：本地模板文件。
        输出返回值：
            服务端确认的 document_id。
        异常语义：
            文件不存在、上传失败或 ID 不一致时抛出异常。
        """

        if not source_path.is_file():
            raise FileNotFoundError(f"模板文件不存在: {source_path}")
        boundary = "----ParaGUIBenchOnlyOfficeBoundary"
        filename = source_path.name
        file_bytes = source_path.read_bytes()
        chunks = [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="document_id"\r\n\r\n'
                f"{document_id}\r\n"
            ).encode("utf-8"),
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8"),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        status, body = self._request(
            "POST",
            "/api/upload",
            data=b"".join(chunks),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if status != 200:
            raise OnlyOfficeShareClientError(f"上传失败: HTTP {status}: {body!r}")
        payload = json.loads(body.decode("utf-8"))
        if not payload.get("success") or payload.get("document_id") != document_id:
            raise OnlyOfficeShareClientError(f"上传未按自定义 ID 保存: {payload}")
        return document_id

    def collab_key(self, document_id: str) -> str:
        """读取或创建协作 key。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            协作 key。
        """

        encoded = quote(document_id, safe="")
        status, body = self._request("GET", f"/api/document/{encoded}/collab-key")
        if status != 200:
            raise OnlyOfficeShareClientError(f"读取协作 key 失败: HTTP {status}")
        key = json.loads(body.decode("utf-8")).get("key")
        if not key:
            raise OnlyOfficeShareClientError(f"协作 key 为空: {document_id}")
        return str(key)

    def share_url(self, document_id: str) -> str:
        """创建或复用共享 URL。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            ``{base_url}/share/{share_key}``。
        """

        encoded = quote(document_id, safe="")
        status, body = self._request("POST", f"/api/document/{encoded}/share")
        if status != 200:
            raise OnlyOfficeShareClientError(f"创建共享链接失败: HTTP {status}")
        payload = json.loads(body.decode("utf-8"))
        share_key = payload.get("share_key")
        if not payload.get("success") or not share_key:
            raise OnlyOfficeShareClientError(f"创建共享链接失败: {payload}")
        return f"{self.base_url.rstrip('/')}/share/{share_key}"

    def download(self, document_id: str) -> bytes:
        """下载当前文档字节。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            文件字节。
        """

        encoded = quote(document_id, safe="")
        status, body = self._request("GET", f"/api/document/{encoded}/file")
        if status != 200:
            raise OnlyOfficeShareClientError(f"下载文档失败: HTTP {status}")
        return body

    def delete(self, document_id: str) -> None:
        """删除当前 Attempt 文档。

        输入参数：
            document_id：文档 ID。
        输出返回值：
            无。
        异常语义：
            非 200 时抛出 ``OnlyOfficeShareClientError``。
        """

        encoded = quote(document_id, safe="")
        status, body = self._request("DELETE", f"/api/document/{encoded}")
        if status != 200:
            raise OnlyOfficeShareClientError(f"删除文档失败: HTTP {status}: {body!r}")
