"""OnlyOffice share service 的 Flask 工厂与 API 契约测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import json
from pathlib import Path
import threading
from typing import Any

import pytest

flask = pytest.importorskip("flask")

from paraguibench.integrations.onlyoffice.share_server import (  # noqa: E402
    create_app,
)


class _FakeDownloadResponse:
    """模拟 OnlyOffice DocumentServer 回调下载结果。"""

    def __init__(self, status_code: int, content: bytes) -> None:
        """保存状态码与字节。

        输入参数：
            status_code：HTTP 状态码。
            content：响应体。
        输出返回值：
            无。
        """

        self.status_code = status_code
        self.content = content


def _xlsx_bytes() -> bytes:
    """构造最小可识别的假 xlsx 字节，避免依赖 Office 解析器。

    输入参数：
        无。
    输出返回值：
        带 xlsx 扩展名语义的确定性字节。
    """

    return b"PK\x03\x04fake-xlsx-template-v1"


def _upload(
    client: Any,
    document_id: str,
    content: bytes,
    filename: str = "template.xlsx",
) -> Any:
    """通过测试客户端上传一份自定义 ID 文档。

    输入参数：
        client：Flask test client。
        document_id：Attempt 级文档 ID。
        content：文件字节。
        filename：原始文件名，用于扩展名推断。
    输出返回值：
        Flask 响应对象。
    """

    return client.post(
        "/api/upload",
        data={
            "document_id": document_id,
            "file": (BytesIO(content), filename),
        },
    )


def test_import_does_not_create_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """功能：导入 share_server 不得在源码或当前目录创建运行状态。

    输入参数：
        tmp_path：隔离工作目录。
        monkeypatch：清除数据目录环境变量。
    输出返回值：
        无；子进程导入后工作目录必须仍为空。
    """

    import os
    import subprocess
    import sys

    monkeypatch.delenv("ONLYOFFICE_SHARE_DATA_DIR", raising=False)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import paraguibench.integrations.onlyoffice.share_server as s; print(s.__name__)",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key != "ONLYOFFICE_SHARE_DATA_DIR"
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.iterdir()) == []


def test_create_app_requires_explicit_data_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """功能：工厂在未注入数据根时必须失败，避免写进源码树。

    输入参数：
        monkeypatch：删除 ONLYOFFICE_SHARE_DATA_DIR。
    输出返回值：
        无；未提供 data_root 时抛出 RuntimeError。
    """

    monkeypatch.delenv("ONLYOFFICE_SHARE_DATA_DIR", raising=False)
    with pytest.raises(RuntimeError, match="ONLYOFFICE_SHARE_DATA_DIR"):
        create_app()


def test_healthz_and_upload_overwrite_share_file_delete(tmp_path: Path) -> None:
    """功能：覆盖健康检查、确定性上传、覆盖恢复、共享、文件与删除。

    输入参数：
        tmp_path：本测试私有数据根。
    输出返回值：
        无；API 必须按自定义 document_id 落盘，覆盖后刷新协作 key，
        删除只清理当前文档。
    """

    app = create_app(tmp_path)
    client = app.test_client()
    health = client.get("/healthz")
    assert health.status_code == 200
    payload = health.get_json()
    assert payload["ok"] is True
    assert payload["jwt_enabled"] is False

    document_id = "Operation-FileOperate-SearchAndWrite-002__runA__attempt1__sheet__abcd1234abcd1234"
    first = _upload(client, document_id, _xlsx_bytes())
    assert first.status_code == 200
    assert first.get_json()["document_id"] == document_id

    key_one = client.get(f"/api/document/{document_id}/collab-key").get_json()["key"]
    overwritten = _upload(client, document_id, b"PK\x03\x04restored-template")
    assert overwritten.status_code == 200
    key_two = client.get(f"/api/document/{document_id}/collab-key").get_json()["key"]
    assert key_one != key_two
    downloaded = client.get(f"/api/document/{document_id}/file")
    assert downloaded.status_code == 200
    assert downloaded.data == b"PK\x03\x04restored-template"

    share_one = client.post(f"/api/document/{document_id}/share")
    share_two = client.post(f"/api/document/{document_id}/share")
    assert share_one.status_code == 200
    assert share_one.get_json()["share_key"] == share_two.get_json()["share_key"]
    share_key = share_one.get_json()["share_key"]
    page = client.get(f"/share/{share_key}")
    assert page.status_code == 200
    assert document_id.encode("utf-8") in page.data or b"DocsAPI" in page.data

    other_id = "Operation-FileOperate-SearchAndWrite-004__runB__attempt9__sheet__ffffeeeeddddcccc"
    _upload(client, other_id, b"PK\x03\x04other-attempt")
    deleted = client.delete(f"/api/document/{document_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/document/{document_id}/file").status_code == 404
    remaining = client.get(f"/api/document/{other_id}/file")
    assert remaining.status_code == 200
    assert remaining.data == b"PK\x03\x04other-attempt"


def test_same_attempt_workers_share_url_and_collab_key(tmp_path: Path) -> None:
    """功能：同一 Attempt 文档 ID 的多个 worker 必须得到相同链接与协作 key。

    输入参数：
        tmp_path：隔离数据根。
    输出返回值：
        无；重复 share / collab-key 调用结果必须一致。
    """

    app = create_app(tmp_path)
    client = app.test_client()
    document_id = (
        "Operation-FileOperate-SearchAndWrite-008__runX__att1__a__0123456789abcdef"
    )
    _upload(client, document_id, _xlsx_bytes(), filename="a.xlsx")
    keys = {
        client.get(f"/api/document/{document_id}/collab-key").get_json()["key"]
        for _ in range(3)
    }
    shares = {
        client.post(f"/api/document/{document_id}/share").get_json()["share_key"]
        for _ in range(3)
    }
    assert len(keys) == 1
    assert len(shares) == 1


def test_different_attempts_are_isolated(tmp_path: Path) -> None:
    """功能：不同 Attempt 的文档、协作 key 与共享链接必须隔离。

    输入参数：
        tmp_path：隔离数据根。
    输出返回值：
        无；两个 document_id 的文件字节和 key 都不得串扰。
    """

    app = create_app(tmp_path)
    client = app.test_client()
    first_id = (
        "Operation-FileOperate-SearchAndWrite-006__run1__a1__doc__1111111111111111"
    )
    second_id = (
        "Operation-FileOperate-SearchAndWrite-006__run1__a2__doc__2222222222222222"
    )
    _upload(client, first_id, b"PK\x03\x04attempt-one", filename="note.docx")
    _upload(client, second_id, b"PK\x03\x04attempt-two", filename="note.docx")
    assert client.get(f"/api/document/{first_id}/file").data == b"PK\x03\x04attempt-one"
    assert (
        client.get(f"/api/document/{second_id}/file").data == b"PK\x03\x04attempt-two"
    )
    first_key = client.get(f"/api/document/{first_id}/collab-key").get_json()["key"]
    second_key = client.get(f"/api/document/{second_id}/collab-key").get_json()["key"]
    assert first_key != second_key


def test_callback_status_2_and_6_write_back_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """功能：callback status 2/6 必须成功写回最终字节并刷新协作 key。

    输入参数：
        tmp_path：隔离数据根。
        monkeypatch：注入成功的下载响应。
    输出返回值：
        无；磁盘内容变为下载字节，且 error=0。
    """

    app = create_app(tmp_path)
    client = app.test_client()
    document_id = (
        "Operation-FileOperate-SearchAndWrite-002__runC__a1__sheet__aaaabbbbccccdddd"
    )
    _upload(client, document_id, _xlsx_bytes())
    old_key = client.get(f"/api/document/{document_id}/collab-key").get_json()["key"]

    def _ok_get(url: str, timeout: float = 30) -> _FakeDownloadResponse:
        """返回一次成功下载。

        输入参数：
            url：DocumentServer 给出的下载地址。
            timeout：超时秒数，测试中忽略。
        输出返回值：
            状态 200 且带有新文档字节的假响应。
        """

        assert url == "http://documentserver.test/saved.xlsx"
        return _FakeDownloadResponse(200, b"PK\x03\x04edited-by-callback")

    monkeypatch.setattr(
        "paraguibench.integrations.onlyoffice.share_server.requests.get",
        _ok_get,
    )
    for status in (2, 6):
        _upload(client, document_id, _xlsx_bytes())
        response = client.post(
            f"/api/document/{document_id}/callback",
            json={"status": status, "url": "http://documentserver.test/saved.xlsx"},
        )
        assert response.status_code == 200
        assert response.get_json() == {"error": 0}
        assert (
            client.get(f"/api/document/{document_id}/file").data
            == b"PK\x03\x04edited-by-callback"
        )
    new_key = client.get(f"/api/document/{document_id}/collab-key").get_json()["key"]
    assert new_key != old_key


def test_callback_download_failure_is_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """功能：callback 下载失败不得被记为保存成功。

    输入参数：
        tmp_path：隔离数据根。
        monkeypatch：注入 HTTP 500 下载。
    输出返回值：
        无；返回 error=1，原模板字节保持不变。
    """

    app = create_app(tmp_path)
    client = app.test_client()
    document_id = (
        "Operation-FileOperate-SearchAndWrite-004__runD__a1__sheet__ddddeeeeffff0000"
    )
    original = _xlsx_bytes()
    _upload(client, document_id, original)

    def _fail_get(url: str, timeout: float = 30) -> _FakeDownloadResponse:
        """返回一次失败下载。

        输入参数：
            url：回调中的下载地址。
            timeout：超时秒数，测试中忽略。
        输出返回值：
            状态 500 的假响应。
        """

        return _FakeDownloadResponse(500, b"")

    monkeypatch.setattr(
        "paraguibench.integrations.onlyoffice.share_server.requests.get",
        _fail_get,
    )
    response = client.post(
        f"/api/document/{document_id}/callback",
        json={"status": 2, "url": "http://documentserver.test/missing.xlsx"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"error": 1}
    assert client.get(f"/api/document/{document_id}/file").data == original


def test_concurrent_share_and_collab_key_keep_valid_json(tmp_path: Path) -> None:
    """功能：并发生成 share/collab key 后状态 JSON 仍必须合法且可复用。

    输入参数：
        tmp_path：隔离数据根。
    输出返回值：
        无；并发结束后 JSON 可解析，同一文档只保留一个协作 key。
    """

    app = create_app(tmp_path)
    client = app.test_client()
    document_id = (
        "Operation-FileOperate-SearchAndWrite-008__runE__a1__b__abcdef0123456789"
    )
    _upload(client, document_id, _xlsx_bytes(), filename="b.xlsx")
    barrier = threading.Barrier(8)

    def _worker() -> tuple[str, str]:
        """在同一时刻请求协作 key 与共享链接。

        输入参数：
            无。
        输出返回值：
            ``(collab_key, share_key)``。
        """

        barrier.wait(timeout=5)
        key = client.get(f"/api/document/{document_id}/collab-key").get_json()["key"]
        share = client.post(f"/api/document/{document_id}/share").get_json()[
            "share_key"
        ]
        return key, share

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_worker) for _ in range(8)]
        results = [future.result() for future in as_completed(futures)]
    keys = {item[0] for item in results}
    shares = {item[1] for item in results}
    assert len(keys) == 1
    assert len(shares) == 1
    links = json.loads((tmp_path / "shared_links.json").read_text(encoding="utf-8"))
    doc_keys = json.loads((tmp_path / "document_keys.json").read_text(encoding="utf-8"))
    assert isinstance(links, dict)
    assert isinstance(doc_keys, dict)
    assert doc_keys[document_id]["key"] in keys
