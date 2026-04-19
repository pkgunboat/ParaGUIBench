# 基于 OnlyOffice 共享文档的 Benchmark 任务设计

本文档说明共享文档的存放位置、链接生成方式，以及如何实现「任务初始化 → 给 Agent 共享链接 → 评价函数读取文档」的完整流程。

---

## 1. 共享文档存放位置（代码中的定义）

| 用途         | 路径（相对 onlyoffice 目录） | 说明 |
|--------------|------------------------------|------|
| **文档文件** | `shared_documents/`          | 所有上传/放置的文档，文件名格式 `{doc_id}.{ext}`，如 `a1b2c3.docx` |
| **共享链接** | `shared_links.json`          | `share_key -> { document_id, created_at }`，用于 `/share/<share_key>` 解析 |
| **协作 key** | `document_keys.json`         | `document_id -> { key, created_at }`，OnlyOffice 多人在线编辑用 |

**绝对路径示例**（仅当 onlyoffice 在项目内时）：

- 文档目录：`<repo>/ubuntu_env/extra_docker_env/onlyoffice/shared_documents/`
- 链接文件：`<repo>/ubuntu_env/extra_docker_env/onlyoffice/shared_links.json`

在代码中（`document_sharing_server.py` / `manage_documents.py`）：

```python
BASE_DIR = Path(__file__).parent   # onlyoffice 目录
DOCUMENTS_DIR = BASE_DIR / "shared_documents"
SHARED_LINKS_FILE = BASE_DIR / "shared_links.json"
DOCUMENT_KEYS_FILE = BASE_DIR / "document_keys.json"
```

**文档 ID 规则**：

- 标准文件名 `{hash}.{ext}`（ext 在允许列表内）时，**doc_id = hash**（无扩展名）。
- 其他情况 doc_id 可能为完整文件名；API 查找时先精确匹配 `shared_documents/<document_id>`，再尝试 `shared_documents/<document_id>.<ext>`。

---

## 2. 任务初始化：把文件放到共享文档目录

两种方式任选其一。

### 方式 A：直接写文件到 `shared_documents/`（推荐用于固定模板）

- 在任务初始化脚本里，把准备好的文档**复制或写入**到 `DOCUMENTS_DIR`。
- 文件名必须为 `{doc_id}.{ext}`，且 `ext` 在 OnlyOffice 允许列表中（如 `docx`, `xlsx`, `pptx`, `txt`, `pdf` 等，见 `ALLOWED_EXTENSIONS`）。
- 为便于后续生成链接和评测，**doc_id 建议与 task_id 或任务唯一标识一致**（例如 `task_uid` 或自定义 `doc_id`）。

示例（在 benchmark 代码里）：

```python
from pathlib import Path

ONLYOFFICE_DIR = Path(__file__).resolve().parent  # 或从配置读取
DOCUMENTS_DIR = ONLYOFFICE_DIR / "shared_documents"

def init_task_document(task_uid: str, template_path: str, ext: str = "docx") -> str:
    """将模板文件复制到 shared_documents，命名为 {task_uid}.{ext}，返回 doc_id。"""
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOCUMENTS_DIR / f"{task_uid}.{ext}"
    import shutil
    shutil.copy2(template_path, dest)
    return task_uid  # doc_id
```

### 方式 B：通过 Flask 上传 API

- `POST /api/upload`，form-data 字段 `file`。
- 返回 `{ "success": true, "document_id": "<file_id>", "filename": "..." }`。
- 服务器会按「文件名 + 时间」生成 MD5 作为 `document_id`，文件名形如 `{file_id}.{ext}`。若需与任务绑定，可在上传后以返回的 `document_id` 作为该任务的 doc_id 使用。

---

## 3. 给每个 Agent 生成共享链接

共享链接形式：`http://<HOST_IP>:<PORT>/share/<share_key>`。Agent 在浏览器中打开该链接即可进入 OnlyOffice 编辑页。

### 方式 A：调用 Flask API（适合 benchmark 脚本与 Flask 同机或可访问）

1. **创建共享链接**：`POST /api/document/<document_id>/share`，无需 body。
2. 返回：`{ "success": true, "share_key": "<share_key>" }`。
3. 拼接：`share_url = f"http://{host}:{port}/share/{share_key}"`。其中 `host` 需为 **Agent 所在环境能访问的地址**（例如宿主机 IP 或 VM 能解析的 host），`port` 为当前 Flask 服务端口（你当前为 5050）。

示例（Python）：

```python
import requests

def create_share_link(base_url: str, document_id: str) -> str:
    r = requests.post(f"{base_url}/api/document/{document_id}/share")
    r.raise_for_status()
    data = r.json()
    assert data.get("success") and data.get("share_key")
    return f"{base_url}/share/{data['share_key']}"
```

### 方式 B：直接写 `shared_links.json`（与 manage_documents.py 一致）

- 生成随机 `share_key`（如 `secrets.token_urlsafe(16)`）。
- 在 `shared_links.json` 中新增：`links[share_key] = { "document_id": document_id, "created_at": "<iso8601>" }`。
- 保存文件。共享链接同样为 `http://<HOST_IP>:<PORT>/share/<share_key>`。

若多个 Agent 共享**同一文档**，只需生成**一个** share_key 对应该 doc_id，把同一链接发给各 Agent 即可（OnlyOffice 侧协作 key 由服务端按 doc_id 管理）。

---

## 4. 评价函数如何读取共享文档

两种方式二选一即可。

### 方式 A：直接读本地文件（评测与 onlyoffice 同机时）

- 文档路径：`DOCUMENTS_DIR / f"{document_id}.{ext}"`，若扩展名不确定，可遍历 `ALLOWED_EXTENSIONS` 尝试 `DOCUMENTS_DIR / f"{document_id}.{ext}"` 是否存在。
- 读内容：
  - **docx**：`python-docx`，`Document(path).paragraphs` 等。
  - **xlsx**：`openpyxl`，`load_workbook(path)`。
  - **txt**：直接读文本。

这样评价函数不依赖 Flask 是否运行，只依赖「评测时文档已在 `shared_documents` 中」。

### 方式 B：通过 HTTP 拉取（评测与 onlyoffice 不同机或希望统一走服务）

- `GET /api/document/<document_id>/file` 返回文档二进制流。
- 将响应内容写入临时文件或内存，再用上述库解析。注意 `document_id` 需与创建链接时一致（无扩展名）。

示例：

```python
def fetch_document_file(base_url: str, document_id: str) -> bytes:
    r = requests.get(f"{base_url}/api/document/{document_id}/file")
    r.raise_for_status()
    return r.content
```

评价逻辑根据任务要求解析文档内容（如某单元格值、某段文字、是否包含某关键词等），与现有 QA/Manipulation 评测方式一致即可。

---

## 5. 配置要点小结（便于写进任务/评测配置）

| 项           | 建议 |
|--------------|------|
| **文档根目录** | 使用 `onlyoffice` 目录下的 `shared_documents`，或通过配置注入绝对路径。 |
| **doc_id**   | 任务初始化时固定（如 task_uid），便于生成链接与评测时定位同一文档。 |
| **Flask 基地址** | `http://<HOST_IP>:<PORT>`，PORT 当前为 5050；HOST_IP 为 Agent/评测可访问的 IP。 |
| **共享链接**  | `{base_url}/share/{share_key}`，share_key 由 POST share API 或写 shared_links.json 得到。 |
| **评价读取**  | 本地读 `shared_documents/<doc_id>.<ext>` 或 GET `/api/document/<doc_id>/file`。 |

---

## 6. 与现有 Benchmark 的衔接

- **任务 JSON**：可增加字段，例如 `onlyoffice_doc_id`、`onlyoffice_share_url`（若在初始化阶段就生成好），或仅存 `onlyoffice_doc_id`，在分发任务时用工具模块按 doc_id 生成 share_url。
- **初始化阶段**：在现有 `stage1_initialize_parallel` 或等价逻辑中，调用「把模板写入 shared_documents + 生成 share_key」的脚本/函数，并把得到的 `share_url` 写入任务指令或 agent 可见的上下文。
- **评价阶段**：在现有 evaluator（如 `evaluator_path` 指向的脚本）中，根据 `task_config` 中的 `onlyoffice_doc_id`（或任务 ID）读取 `shared_documents` 或调用 `/api/document/<id>/file`，解析文档并判断通过条件。

这样即可完成：**1）任务初始化时把文件放到共享文档目录；2）给每个 Agent 生成共享链接；3）评价函数读取共享文档进行评价** 的闭环。

---

## 7. 工具模块使用示例（onlyoffice_benchmark_utils.py）

同目录下的 `onlyoffice_benchmark_utils.py` 提供可直接在 benchmark 中调用的函数：

```python
from onlyoffice_benchmark_utils import (
    get_documents_dir,
    init_task_document,
    create_share_link_via_api,
    create_share_link_local,
    get_document_path_for_eval,
    fetch_document_file_via_api,
    load_document_for_eval,
    get_document_server_base_url,
)

# 1. 任务初始化：把模板放到 shared_documents
doc_id = init_task_document("task_uid_001", "/path/to/template.docx")  # 或 ext="docx"

# 2. 给 Agent 生成共享链接（二选一）
base_url = get_document_server_base_url()  # 或传入 host/port
share_url = create_share_link_via_api(base_url, doc_id)   # 需 Flask 已启动
# 或
share_url = create_share_link_local(doc_id, base_url)     # 直接写 shared_links.json

# 3. 评价时读取文档
path, content = load_document_for_eval(doc_id, base_url=base_url)
if path:
    # 本地路径，可用 python-docx / openpyxl 等解析
    from docx import Document
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
elif content:
    # 二进制内容，可写临时文件后解析
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(content)
    # 再用 Document(f.name) 等解析
```

文档目录与链接文件路径：`get_documents_dir()`、`get_shared_links_path()`。
