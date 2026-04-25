#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OnlyOffice 文档共享服务器
功能：提供文档上传、管理、共享和协作编辑功能
"""

import os
import json
import hashlib
import secrets
import base64
import hmac
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

from flask import Flask, request, jsonify, send_file, render_template_string
from werkzeug.utils import secure_filename

app = Flask(__name__)

# 配置
BASE_DIR = Path(__file__).parent
DOCUMENTS_DIR = BASE_DIR / "shared_documents"
SHARED_LINKS_FILE = BASE_DIR / "shared_links.json"
DOCUMENT_KEYS_FILE = BASE_DIR / "document_keys.json"  # 存储文档的协作 key
ALLOWED_EXTENSIONS = {'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp', 'txt', 'rtf', 'pdf'}


def env_flag(name, default=False):
    """读取布尔环境变量。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# 获取主机 IP 地址（支持本地和远程访问）
def get_host_ip():
    """
    获取主机 IP 地址
    优先级：环境变量 > 自动检测
    """
    import os
    # 优先使用环境变量
    host_ip = os.environ.get('HOST_IP')
    if host_ip:
        return host_ip
    
    # 默认使用局域网 IP（您需要根据实际情况修改）
    # 或者可以通过以下方式自动获取：
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return 'localhost'

HOST_IP = get_host_ip()
DOC_SERVER_PORT = int(os.environ.get("DOC_SERVER_PORT", "8080"))
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5050"))

# OnlyOffice 服务器地址（浏览器访问）
# 本地访问使用 localhost，远程访问需要使用主机 IP
if DOC_SERVER_PORT in (80, 443):
    ONLYOFFICE_SERVER = f"http://{HOST_IP}"
else:
    ONLYOFFICE_SERVER = f"http://{HOST_IP}:{DOC_SERVER_PORT}"

# 浏览器打开共享页时使用宿主机地址；DocumentServer 后台拉文件时应走 Docker
# 网络内地址，避免触发 OnlyOffice 的 private-IP 过滤。
DOC_FETCH_HOST = os.environ.get("DOC_FETCH_HOST", "bench-onlyoffice-share")
DOC_FETCH_PORT = int(os.environ.get("DOC_FETCH_PORT", str(FLASK_PORT)))

# JWT 配置。
# 当 DocumentServer 未启用 browser JWT 时，不应向 DocsAPI 传递 config.token。
ONLYOFFICE_JWT_ENABLED = env_flag("ONLYOFFICE_JWT_ENABLED", False)
JWT_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "")

# 确保目录存在
DOCUMENTS_DIR.mkdir(exist_ok=True)

# 文档类型映射
DOCUMENT_TYPES = {
    'docx': 'word', 'doc': 'word', 'odt': 'word', 'txt': 'word', 'rtf': 'word',
    'xlsx': 'cell', 'xls': 'cell', 'ods': 'cell', 'csv': 'cell',
    'pptx': 'presentation', 'ppt': 'presentation', 'odp': 'presentation',
    'pdf': 'word'  # PDF 在 OnlyOffice 中按文档类型处理
}


def load_shared_links():
    """加载共享链接数据"""
    if SHARED_LINKS_FILE.exists():
        with open(SHARED_LINKS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_shared_links(data):
    """保存共享链接数据"""
    with open(SHARED_LINKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_document_keys():
    """
    加载文档协作 key 数据
    协作 key 用于 OnlyOffice 识别同一个编辑会话
    所有打开同一文档的用户必须使用相同的 key 才能实时协作
    """
    if DOCUMENT_KEYS_FILE.exists():
        with open(DOCUMENT_KEYS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_document_keys(data):
    """保存文档协作 key 数据"""
    with open(DOCUMENT_KEYS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_document_collab_key(document_id):
    """
    获取文档的协作 key
    - 如果 key 不存在，创建新的
    - 同一文档的所有用户使用相同的 key 才能实时协作
    
    参数：
        document_id: 文档ID
    返回：
        协作 key 字符串
    """
    keys = load_document_keys()
    
    if document_id not in keys:
        # 创建新的协作 key
        keys[document_id] = {
            'key': f"{document_id}_{int(time.time())}",
            'created_at': datetime.now().isoformat()
        }
        save_document_keys(keys)
    
    return keys[document_id]['key']


def refresh_document_collab_key(document_id):
    """
    刷新文档的协作 key（文档保存后调用）
    当文档内容更新后，需要生成新的 key，否则 OnlyOffice 会使用缓存
    
    参数：
        document_id: 文档ID
    返回：
        新的协作 key 字符串
    """
    keys = load_document_keys()
    keys[document_id] = {
        'key': f"{document_id}_{int(time.time())}",
        'created_at': datetime.now().isoformat()
    }
    save_document_keys(keys)
    return keys[document_id]['key']


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_document_type(filename):
    """获取文档类型"""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return DOCUMENT_TYPES.get(ext, 'word')


def generate_share_key():
    """生成共享密钥"""
    return secrets.token_urlsafe(16)


def generate_jwt_token(payload):
    """
    生成 JWT token（使用 HS256 算法）
    OnlyOffice 需要的 JWT token 格式
    
    注意：OnlyOffice 的 JWT token 只需要对整个配置对象签名，不是标准的 JWT payload
    """
    try:
        if not JWT_SECRET:
            raise RuntimeError("ONLYOFFICE_JWT_SECRET 为空，无法生成 JWT token")
        # 尝试使用 PyJWT 库（如果已安装）
        import jwt
        # OnlyOffice 使用的 JWT 格式：对整个 payload 签名
        token = jwt.encode(payload, JWT_SECRET, algorithm='HS256')
        # PyJWT 3.0+ 返回字符串，旧版本返回 bytes
        if isinstance(token, bytes):
            return token.decode('utf-8')
        return token
    except ImportError:
        # 如果没有 PyJWT，使用简单的手动实现（仅用于 HS256）
        # JWT 格式: header.payload.signature
        header = {
            "alg": "HS256",
            "typ": "JWT"
        }
        
        # Base64URL 编码 header 和 payload
        def base64url_encode(data):
            encoded = base64.urlsafe_b64encode(
                json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
            ).decode('utf-8')
            # 移除填充
            return encoded.rstrip('=')
        
        encoded_header = base64url_encode(header)
        encoded_payload = base64url_encode(payload)
        
        # 生成签名
        message = f"{encoded_header}.{encoded_payload}"
        signature = hmac.new(
            JWT_SECRET.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).decode('utf-8').rstrip('=')
        
        return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


@app.route('/')
def index():
    """主页 - 文档管理界面"""
    html = '''
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>文档共享管理系统</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                padding: 30px;
            }
            h1 {
                color: #333;
                margin-bottom: 30px;
                font-size: 28px;
            }
            .upload-section {
                background: #f8f9fa;
                padding: 25px;
                border-radius: 8px;
                margin-bottom: 30px;
                border: 2px dashed #dee2e6;
                text-align: center;
            }
            .upload-section:hover {
                border-color: #667eea;
                background: #f0f4ff;
            }
            input[type="file"] {
                margin: 10px 0;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                width: 100%;
                max-width: 400px;
            }
            button {
                background: #667eea;
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 16px;
                margin: 5px;
                transition: background 0.3s;
            }
            button:hover {
                background: #5568d3;
            }
            .documents-list {
                margin-top: 30px;
            }
            .document-item {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 15px;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                margin-bottom: 10px;
                background: #fafafa;
                transition: transform 0.2s;
            }
            .document-item:hover {
                transform: translateX(5px);
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }
            .document-info {
                flex: 1;
            }
            .document-name {
                font-weight: 600;
                color: #333;
                margin-bottom: 5px;
            }
            .document-meta {
                font-size: 12px;
                color: #666;
            }
            .document-actions {
                display: flex;
                gap: 10px;
            }
            .btn-sm {
                padding: 8px 16px;
                font-size: 14px;
            }
            .btn-share {
                background: #28a745;
            }
            .btn-share:hover {
                background: #218838;
            }
            .btn-edit {
                background: #007bff;
            }
            .btn-edit:hover {
                background: #0056b3;
            }
            .btn-delete {
                background: #dc3545;
            }
            .btn-delete:hover {
                background: #c82333;
            }
            .share-link {
                margin-top: 10px;
                padding: 10px;
                background: #e7f3ff;
                border-radius: 4px;
                word-break: break-all;
                font-family: monospace;
                font-size: 12px;
            }
            #editor-container {
                margin-top: 30px;
                border: 1px solid #ddd;
                border-radius: 8px;
                min-height: 600px;
                display: none;
            }
        </style>
        <script src="''' + ONLYOFFICE_SERVER + '''/web-apps/apps/api/documents/api.js"></script>
    </head>
    <body>
        <div class="container">
            <h1>📄 文档共享管理系统</h1>
            
            <div class="upload-section">
                <h3>上传文档</h3>
                <input type="file" id="fileInput" accept=".doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp,.txt,.rtf,.pdf">
                <br>
                <button onclick="uploadDocument()">上传文档</button>
            </div>

            <div class="documents-list">
                <h2>我的文档</h2>
                <div id="documentsList">
                    <p>加载中...</p>
                </div>
            </div>

            <div id="editor-container"></div>
        </div>

        <script>
            let currentDocEditor = null;

            // HTML转义函数，防止XSS攻击
            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }

            // JavaScript字符串转义，用于onclick属性
            function escapeJs(text) {
                if (text === null || text === undefined) return '';
                try {
                    return JSON.stringify(String(text)).slice(1, -1); // 去掉首尾的引号
                } catch (e) {
                    return String(text).replace(/'/g, "\\'").replace(/"/g, '\\"');
                }
            }

            // 加载文档列表
            function loadDocuments() {
                console.log('开始加载文档列表...');
                const list = document.getElementById('documentsList');
                if (!list) {
                    console.error('找不到documentsList元素');
                    return;
                }
                
                fetch('/api/documents')
                    .then(r => {
                        console.log('API响应状态:', r.status);
                        if (!r.ok) {
                            throw new Error('HTTP错误: ' + r.status);
                        }
                        return r.json();
                    })
                    .then(data => {
                        console.log('收到数据:', data);
                        
                        try {
                            if (!data || !data.documents || data.documents.length === 0) {
                                list.innerHTML = '<p style="color: #999; padding: 20px; text-align: center;">暂无文档，请上传文档开始使用</p>';
                                console.log('没有文档');
                                return;
                            }
                            
                            // 清空列表
                            list.innerHTML = '';
                            
                            console.log('开始渲染', data.documents.length, '个文档');
                            
                            // 为每个文档创建DOM元素（避免字符串转义问题）
                            data.documents.forEach((doc, index) => {
                                try {
                                    console.log('处理文档', index, ':', doc.name);
                                    const docId = String(doc.id || '');
                                    const docName = String(doc.name || '无名称');
                                    const docSize = (doc.size || 0) / 1024;
                                    const docDate = doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleString('zh-CN') : '未知';
                                    
                                    // 创建文档项容器
                                    const item = document.createElement('div');
                                    item.className = 'document-item';
                                    
                                    // 创建文档信息部分
                                    const infoDiv = document.createElement('div');
                                    infoDiv.className = 'document-info';
                                    
                                    const nameDiv = document.createElement('div');
                                    nameDiv.className = 'document-name';
                                    nameDiv.textContent = docName;
                                    
                                    const metaDiv = document.createElement('div');
                                    metaDiv.className = 'document-meta';
                                    metaDiv.textContent = '大小: ' + docSize.toFixed(2) + ' KB | 上传时间: ' + docDate;
                                    
                                    infoDiv.appendChild(nameDiv);
                                    infoDiv.appendChild(metaDiv);
                                    
                                    // 创建操作按钮部分
                                    const actionsDiv = document.createElement('div');
                                    actionsDiv.className = 'document-actions';
                                    
                                    const editBtn = document.createElement('button');
                                    editBtn.className = 'btn-sm btn-edit';
                                    editBtn.textContent = '编辑';
                                    editBtn.dataset.docId = docId;
                                    editBtn.dataset.docName = docName;
                                    editBtn.addEventListener('click', function() {
                                        openDocument(this.dataset.docId, this.dataset.docName);
                                    });
                                    
                                    const shareBtn = document.createElement('button');
                                    shareBtn.className = 'btn-sm btn-share';
                                    shareBtn.textContent = '共享';
                                    shareBtn.dataset.docId = docId;
                                    shareBtn.dataset.docName = docName;
                                    shareBtn.addEventListener('click', function() {
                                        shareDocument(this.dataset.docId, this.dataset.docName);
                                    });
                                    
                                    const deleteBtn = document.createElement('button');
                                    deleteBtn.className = 'btn-sm btn-delete';
                                    deleteBtn.textContent = '删除';
                                    deleteBtn.dataset.docId = docId;
                                    deleteBtn.dataset.docName = docName;
                                    deleteBtn.addEventListener('click', function() {
                                        deleteDocument(this.dataset.docId, this.dataset.docName);
                                    });
                                    
                                    actionsDiv.appendChild(editBtn);
                                    actionsDiv.appendChild(shareBtn);
                                    actionsDiv.appendChild(deleteBtn);
                                    
                                    // 组装完整项
                                    item.appendChild(infoDiv);
                                    item.appendChild(actionsDiv);
                                    
                                    list.appendChild(item);
                                } catch (itemErr) {
                                    console.error('处理文档项失败:', itemErr, doc);
                                }
                            });
                            
                            console.log('文档列表渲染完成，共', data.documents.length, '个文档');
                        } catch (renderErr) {
                            console.error('渲染文档列表失败:', renderErr);
                            list.innerHTML = '<p style="color: red;">渲染文档列表失败，请查看控制台</p>';
                        }
                    })
                    .catch(err => {
                        console.error('加载文档失败:', err);
                        console.error('错误详情:', err.stack);
                        const list = document.getElementById('documentsList');
                        if (list) {
                            list.innerHTML = '<p style="color: red;">加载文档列表失败: ' + escapeHtml(err.message || '未知错误') + '</p>';
                        }
                    });
            }
            
            // 页面加载完成后执行
            console.log('页面脚本加载完成');

            // 上传文档
            function uploadDocument() {
                const input = document.getElementById('fileInput');
                const file = input.files[0];
                if (!file) {
                    alert('请选择文件');
                    return;
                }

                const formData = new FormData();
                formData.append('file', file);

                fetch('/api/upload', {
                    method: 'POST',
                    body: formData
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        alert('上传成功！');
                        input.value = '';
                        loadDocuments();
                    } else {
                        alert('上传失败: ' + (data.error || '未知错误'));
                    }
                })
                .catch(err => {
                    console.error('上传失败:', err);
                    alert('上传失败，请检查文件格式');
                });
            }

            // 打开文档编辑
            function openDocument(docId, docName) {
                const editorContainer = document.getElementById('editor-container');
                editorContainer.style.display = 'block';
                editorContainer.innerHTML = '<div id="editor" style="height: 600px;"><p style="padding: 20px; text-align: center;">正在加载编辑器...</p></div>';

                if (currentDocEditor) {
                    currentDocEditor.destroyEditor();
                }

                // 浏览器打开 share 页用公开地址，但 DocumentServer 后台拉文件要走 Docker 内网地址。
                const docServerUrl = "http://''' + DOC_FETCH_HOST + ":" + str(DOC_FETCH_PORT) + '''";
                const baseUrl = window.location.origin;
                
                // URL 编码文档ID（处理包含特殊字符的情况）
                const encodedDocId = encodeURIComponent(docId);
                // 文档 URL / callback URL 由 DocumentServer 后台访问。
                const docUrl = docServerUrl + '/api/document/' + encodedDocId + '/file';
                const callbackUrl = docServerUrl + '/api/document/' + encodedDocId + '/callback';

                const fileType = docName.split('.').pop().toLowerCase();
                const documentType = getDocumentType(fileType);

                // 首先获取协作 key（所有用户必须使用相同的 key 才能实时协作）
                fetch(baseUrl + '/api/document/' + encodedDocId + '/collab-key')
                    .then(r => r.json())
                    .then(keyData => {
                        const collabKey = keyData.key;
                        console.log('获取到协作 key:', collabKey);
                        
                        // 使用服务器返回的协作 key 构建配置对象
                        const config = {
                            document: {
                                fileType: fileType,
                                key: collabKey,
                                title: docName,
                                url: docUrl
                            },
                            documentType: documentType,
                            editorConfig: {
                                mode: 'edit',
                                callbackUrl: callbackUrl
                            },
                            width: '100%',
                            height: '600px'
                        };

                        const openEditor = () => {
                            editorContainer.innerHTML = '<div id="editor" style="height: 600px;"></div>';
                            currentDocEditor = new DocsAPI.DocEditor('editor', config);
                            editorContainer.scrollIntoView({ behavior: 'smooth' });
                        };

                        if (!''' + ('true' if ONLYOFFICE_JWT_ENABLED else 'false') + ''') {
                            openEditor();
                            return;
                        }

                        // 获取 JWT token（发送配置对象让服务器签名）
                        return fetch(baseUrl + '/api/document/' + encodedDocId + '/token', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify(config)
                        })
                        .then(r => r.json())
                        .then(data => {
                            if (!data.token) {
                                throw new Error(data.error || 'JWT token 生成失败');
                            }
                            config.token = data.token;
                            openEditor();
                        });
                    })
                    .catch(err => {
                        console.error('加载编辑器失败:', err);
                        editorContainer.innerHTML = '<div style="padding: 20px; color: red;">加载失败，请刷新页面重试</div>';
                    });
            }

            // 共享文档
            function shareDocument(docId, docName) {
                const encodedDocId = encodeURIComponent(docId);
                fetch('/api/document/' + encodedDocId + '/share', {
                    method: 'POST'
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        const shareUrl = window.location.origin + '/share/' + data.share_key;
                        
                        // 找到对应的文档项并显示共享链接
                        const items = document.querySelectorAll('.document-item');
                        items.forEach(item => {
                            // 使用 data-doc-id 属性查找对应的按钮
                            const shareBtn = item.querySelector(`button[data-doc-id="${docId}"].btn-share`);
                            if (shareBtn) {
                                // 移除已有的共享链接
                                const existingLink = item.querySelector('.share-link');
                                if (existingLink) {
                                    existingLink.remove();
                                }
                                // 创建新的共享链接元素
                                const linkDiv = document.createElement('div');
                                linkDiv.className = 'share-link';
                                linkDiv.innerHTML = '<strong>共享链接已生成！</strong><br>' +
                                    '<a href="' + shareUrl + '" target="_blank">' + shareUrl + '</a><br>' +
                                    '<small>复制此链接即可与他人共享文档，支持多人协作编辑</small>';
                                item.appendChild(linkDiv);
                            }
                        });
                        
                        // 也弹出提示框，方便复制
                        alert('共享链接已生成！\\n' + shareUrl);
                    } else {
                        alert('生成共享链接失败: ' + (data.error || '未知错误'));
                    }
                })
                .catch(err => {
                    console.error('共享失败:', err);
                    alert('生成共享链接失败: ' + err.message);
                });
            }

            // 删除文档
            function deleteDocument(docId, docName) {
                if (!confirm('确定要删除文档 "' + docName + '" 吗？\\n此操作无法撤销！')) {
                    return;
                }
                
                const encodedDocId = encodeURIComponent(docId);
                fetch('/api/document/' + encodedDocId, {
                    method: 'DELETE'
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        alert('文档已删除');
                        loadDocuments();  // 重新加载文档列表
                    } else {
                        alert('删除失败: ' + (data.error || '未知错误'));
                    }
                })
                .catch(err => {
                    console.error('删除失败:', err);
                    alert('删除失败: ' + err.message);
                });
            }

            function getDocumentType(fileType) {
                const wordTypes = ['docx', 'doc', 'odt', 'txt', 'rtf', 'pdf'];
                const cellTypes = ['xlsx', 'xls', 'ods', 'csv'];
                if (wordTypes.includes(fileType)) return 'word';
                if (cellTypes.includes(fileType)) return 'cell';
                return 'presentation';
            }

            // 页面加载时获取文档列表
            // 使用多种方式确保执行
            console.log('脚本开始执行，readyState:', document.readyState);
            
            function initPage() {
                console.log('初始化页面，准备加载文档列表');
                try {
                    loadDocuments();
                } catch (err) {
                    console.error('初始化失败:', err);
                    const list = document.getElementById('documentsList');
                    if (list) {
                        list.innerHTML = '<p style="color: red;">页面初始化失败: ' + err.message + '</p>';
                    }
                }
            }
            
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', function() {
                    console.log('DOMContentLoaded事件触发');
                    initPage();
                });
            } else {
                // DOM已经加载完成
                console.log('DOM已就绪，立即执行');
                // 使用setTimeout确保所有脚本都已加载
                setTimeout(initPage, 100);
            }
        </script>
    </body>
    </html>
    '''
    return html


@app.route('/api/documents', methods=['GET'])
def list_documents():
    """列出所有文档"""
    documents = []
    for file_path in DOCUMENTS_DIR.glob('*'):
        if file_path.is_file() and not file_path.name.startswith('._'):  # 跳过 macOS 元数据文件
            stat = file_path.stat()
            file_name = file_path.name
            
            # 文档ID策略：
            # 1. 标准格式 hash.ext（ext在允许列表中），ID是 hash
            # 2. 其他格式（包括 hash.），ID是完整文件名（用于路由匹配）
            if '.' in file_name:
                parts = file_name.rsplit('.', 1)
                if len(parts) == 2 and parts[1] in ALLOWED_EXTENSIONS:
                    # 标准格式：hash.ext，ID是hash（去掉扩展名）
                    doc_id = parts[0]
                else:
                    # 非标准格式（如 hash. 或 hash.xxx），ID是完整文件名
                    doc_id = file_name
            else:
                # 无扩展名，ID是完整文件名
                doc_id = file_name
            
            documents.append({
                'id': doc_id,
                'name': file_name,
                'size': stat.st_size,
                'uploaded_at': datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
    
    # 按上传时间排序（最新的在前）
    documents.sort(key=lambda x: x['uploaded_at'], reverse=True)
    
    return jsonify({'documents': documents})


@app.route('/api/upload', methods=['POST'])
def upload_document():
    """上传文档"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有文件'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': '文件名为空'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': '不支持的文件类型'}), 400
    
    # 生成安全的文件名（使用哈希作为ID）
    filename = secure_filename(file.filename)
    if not filename or filename == '':
        filename = 'document'
    
    file_hash = hashlib.md5((filename + str(datetime.now())).encode()).hexdigest()
    file_id = file_hash
    
    # 提取扩展名
    if '.' in filename:
        file_ext = filename.rsplit('.', 1)[1].lower()
        # 确保扩展名有效
        if not file_ext or file_ext not in ALLOWED_EXTENSIONS:
            # 尝试从原始文件名获取
            original_ext = request.files['file'].filename.rsplit('.', 1)[1].lower() if '.' in request.files['file'].filename else ''
            if original_ext in ALLOWED_EXTENSIONS:
                file_ext = original_ext
            else:
                file_ext = 'docx'  # 默认扩展名
    else:
        file_ext = 'docx'  # 默认扩展名
    
    # 保存文件
    file_path = DOCUMENTS_DIR / f"{file_id}.{file_ext}"
    file.save(file_path)
    
    return jsonify({
        'success': True,
        'document_id': file_id,
        'filename': filename
    })


@app.route('/api/document/<path:document_id>/collab-key', methods=['GET'])
def get_collab_key(document_id):
    """
    获取文档的协作 key
    所有用户必须使用相同的 key 才能实时协作编辑
    
    返回：
        JSON: {"key": "协作key"}
    """
    from urllib.parse import unquote
    document_id = unquote(document_id)
    
    # 获取或创建协作 key
    collab_key = get_document_collab_key(document_id)
    
    return jsonify({'key': collab_key})


@app.route('/api/document/<path:document_id>/file', methods=['GET'])
def get_document_file(document_id):
    """获取文档文件（使用 path 转换器以支持包含点的文档ID）"""
    from urllib.parse import unquote
    document_id = unquote(document_id)
    
    # 先尝试精确匹配（包含点的情况）
    potential_file = DOCUMENTS_DIR / document_id
    if potential_file.exists() and potential_file.is_file():
        return send_file(potential_file, as_attachment=False)
    
    # 查找文件（可能不同的扩展名）
    for ext in ALLOWED_EXTENSIONS:
        file_path = DOCUMENTS_DIR / f"{document_id}.{ext}"
        if file_path.exists():
            return send_file(file_path, as_attachment=False)
    
    return jsonify({'error': f'文档不存在: {document_id}'}), 404


@app.route('/api/document/<path:document_id>/token', methods=['POST'])
def get_document_token(document_id):
    """生成文档的 JWT token
    
    OnlyOffice 需要标准的 JWT token，对整个配置对象进行签名
    使用 <path:document_id> 以支持包含点的文档ID
    """
    # Flask 的 path 转换器会自动解码 URL，但为了安全，我们显式处理一下
    from urllib.parse import unquote
    document_id = unquote(document_id)
    
    data = request.json
    if not data:
        return jsonify({'error': '需要提供配置对象'}), 400
    if not ONLYOFFICE_JWT_ENABLED:
        return jsonify({'error': 'DocumentServer 未启用 JWT，无需生成 token'}), 400
    
    # 验证文档是否存在（支持以点结尾的文档ID）
    file_exists = False
    # 先尝试精确匹配（包含点的情况）
    potential_file = DOCUMENTS_DIR / document_id
    if potential_file.exists() and potential_file.is_file():
        file_exists = True
    else:
        # 尝试添加扩展名
        for ext in ALLOWED_EXTENSIONS:
            file_path = DOCUMENTS_DIR / f"{document_id}.{ext}"
            if file_path.exists():
                file_exists = True
                break
    
    if not file_exists:
        return jsonify({'error': f'文档不存在: {document_id}'}), 404
    
    # 生成 JWT token，使用配置对象作为 payload
    payload = data.copy()  # 复制配置对象作为 payload
    
    # 使用标准的 JWT 生成函数
    token = generate_jwt_token(payload)
    
    return jsonify({'token': token})


@app.route('/healthz')
def healthz():
    """健康检查接口。"""
    return jsonify({
        'ok': True,
        'documents_dir': str(DOCUMENTS_DIR),
        'onlyoffice_server': ONLYOFFICE_SERVER,
        'jwt_enabled': ONLYOFFICE_JWT_ENABLED,
    })


@app.route('/api/document/<path:document_id>/callback', methods=['POST'])
def document_callback(document_id):
    """
    处理文档保存回调
    OnlyOffice 回调状态码：
    - 0: 没有更改
    - 1: 正在编辑
    - 2: 准备保存（编辑器关闭后）
    - 3: 保存出错
    - 4: 关闭但未保存
    - 6: 正在编辑，但当前状态已保存
    - 7: 强制保存时出错
    """
    from urllib.parse import unquote
    document_id = unquote(document_id)
    
    data = request.json
    status = data.get('status')
    
    print(f"[Callback] 文档: {document_id}, 状态: {status}, 数据: {data}")
    
    # 状态 2 或 6 表示文档已保存，需要下载更新
    if status in [2, 6]:
        download_url = data.get('url')
        print(f"[Callback] 需要保存，下载URL: {download_url}")
        
        if download_url:
            import requests
            try:
                response = requests.get(download_url)
                print(f"[Callback] 下载状态: {response.status_code}, 内容大小: {len(response.content)} bytes")
                
                if response.status_code == 200:
                    # 找到对应的文件并更新
                    file_saved = False
                    # 先尝试精确匹配
                    potential_file = DOCUMENTS_DIR / document_id
                    if potential_file.exists() and potential_file.is_file():
                        with open(potential_file, 'wb') as f:
                            f.write(response.content)
                        print(f"[Callback] 文件已保存（精确匹配）: {potential_file}")
                        file_saved = True
                    else:
                        # 尝试添加扩展名
                        for ext in ALLOWED_EXTENSIONS:
                            file_path = DOCUMENTS_DIR / f"{document_id}.{ext}"
                            if file_path.exists():
                                with open(file_path, 'wb') as f:
                                    f.write(response.content)
                                print(f"[Callback] 文件已保存: {file_path}")
                                file_saved = True
                                break
                    
                    if not file_saved:
                        print(f"[Callback] 警告: 未找到对应文件 {document_id}")
                    
                    return jsonify({'error': 0})
                else:
                    print(f"[Callback] 下载失败: HTTP {response.status_code}")
            except Exception as e:
                print(f"[Callback] 下载出错: {e}")
                return jsonify({'error': 0})  # 仍返回成功，避免重试
    
    return jsonify({'error': 0})


@app.route('/api/document/<path:document_id>', methods=['DELETE'])
def delete_document(document_id):
    """
    删除文档
    
    功能：删除指定的文档文件，同时清理相关的共享链接和协作 key
    
    参数：
        document_id: 文档ID（URL路径参数）
    
    返回：
        JSON: {"success": true} 或 {"success": false, "error": "错误信息"}
    """
    from urllib.parse import unquote
    document_id = unquote(document_id)
    
    # 查找并删除文档文件
    file_deleted = False
    deleted_file_path = None
    
    # 先尝试精确匹配
    potential_file = DOCUMENTS_DIR / document_id
    if potential_file.exists() and potential_file.is_file():
        potential_file.unlink()
        file_deleted = True
        deleted_file_path = potential_file
    else:
        # 尝试添加扩展名
        for ext in ALLOWED_EXTENSIONS:
            file_path = DOCUMENTS_DIR / f"{document_id}.{ext}"
            if file_path.exists():
                file_path.unlink()
                file_deleted = True
                deleted_file_path = file_path
                break
    
    if not file_deleted:
        return jsonify({'success': False, 'error': '文档不存在'}), 404
    
    # 清理相关的共享链接
    links = load_shared_links()
    links_to_remove = [key for key, value in links.items() if value.get('document_id') == document_id]
    for key in links_to_remove:
        del links[key]
    if links_to_remove:
        save_shared_links(links)
        print(f"[Delete] 已清理 {len(links_to_remove)} 个共享链接")
    
    # 清理协作 key
    keys = load_document_keys()
    if document_id in keys:
        del keys[document_id]
        save_document_keys(keys)
        print(f"[Delete] 已清理协作 key")
    
    print(f"[Delete] 文档已删除: {deleted_file_path}")
    
    return jsonify({'success': True})


@app.route('/api/document/<path:document_id>/share', methods=['POST'])
def create_share_link(document_id):
    """创建共享链接"""
    from urllib.parse import unquote
    document_id = unquote(document_id)
    
    # 验证文档是否存在（支持以点结尾的文档ID）
    potential_file = DOCUMENTS_DIR / document_id
    file_exists = (potential_file.exists() and potential_file.is_file()) or \
                  any((DOCUMENTS_DIR / f"{document_id}.{ext}").exists() for ext in ALLOWED_EXTENSIONS)
    if not file_exists:
        return jsonify({'success': False, 'error': '文档不存在'}), 404
    
    # 生成共享密钥
    share_key = generate_share_key()
    
    # 保存共享链接
    links = load_shared_links()
    links[share_key] = {
        'document_id': document_id,
        'created_at': datetime.now().isoformat()
    }
    save_shared_links(links)
    
    return jsonify({
        'success': True,
        'share_key': share_key
    })


@app.route('/share/<share_key>')
def share_document_view(share_key):
    """共享文档视图页面"""
    links = load_shared_links()
    if share_key not in links:
        return '<h1>共享链接无效或已过期</h1>', 404
    
    document_id = links[share_key]['document_id']
    
    # 查找文件名（支持以点结尾的文档ID）
    filename = None
    # 先尝试精确匹配
    potential_file = DOCUMENTS_DIR / document_id
    if potential_file.exists() and potential_file.is_file():
        filename = potential_file.name
    else:
        # 尝试添加扩展名
        for ext in ALLOWED_EXTENSIONS:
            file_path = DOCUMENTS_DIR / f"{document_id}.{ext}"
            if file_path.exists():
                filename = file_path.name
                break
    
    if not filename:
        return f'<h1>文档不存在: {document_id}</h1>', 404
    
    html = f'''
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>共享文档: {filename}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f5f5f5;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
                background: white;
                border-radius: 8px;
                padding: 20px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            h1 {{
                color: #333;
                margin-bottom: 20px;
            }}
            #editor {{
                width: 100%;
                height: 700px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }}
        </style>
        <script src="{ONLYOFFICE_SERVER}/web-apps/apps/api/documents/api.js"></script>
    </head>
    <body>
        <div class="container">
            <h1>📄 {filename}</h1>
            <div id="editor"></div>
        </div>
        <script>
            // 浏览器打开 share 页用公开地址，但 DocumentServer 后台拉文件要走 Docker 内网地址。
            const docServerUrl = "http://{DOC_FETCH_HOST}:{DOC_FETCH_PORT}";
            const baseUrl = window.location.origin;
            
            const docUrl = docServerUrl + '/api/document/{document_id}/file';
            const callbackUrl = docServerUrl + '/api/document/{document_id}/callback';
            
            const fileType = '{filename.split(".").pop()}';
            const documentType = fileType.match(/^(docx?|odt|txt|rtf|pdf)$/i) ? 'word' :
                               fileType.match(/^(xlsx?|ods|csv)$/i) ? 'cell' : 'presentation';
            
            // 首先获取协作 key（所有用户必须使用相同的 key 才能实时协作）
            fetch(baseUrl + '/api/document/{document_id}/collab-key')
                .then(r => r.json())
                .then(keyData => {{
                    const collabKey = keyData.key;
                    console.log('获取到协作 key:', collabKey);
                    
                    // 使用服务器返回的协作 key 构建配置对象
                    const config = {{
                        document: {{
                            fileType: fileType.toLowerCase(),
                            key: collabKey,
                            title: '{filename}',
                            url: docUrl
                        }},
                        documentType: documentType,
                        editorConfig: {{
                            mode: 'edit',
                            callbackUrl: callbackUrl
                        }},
                        width: '100%',
                        height: '700px'
                    }};

                    const openEditor = () => {{
                        new DocsAPI.DocEditor('editor', config);
                    }};

                    if (!{str(ONLYOFFICE_JWT_ENABLED).lower()}) {{
                        openEditor();
                        return;
                    }}

                    // 获取 JWT token（发送配置对象让服务器签名）
                    return fetch(baseUrl + '/api/document/{document_id}/token', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }},
                        body: JSON.stringify(config)
                    }})
                    .then(r => r.json())
                    .then(data => {{
                        if (!data.token) {{
                            throw new Error(data.error || 'JWT token 生成失败');
                        }}
                        config.token = data.token;
                        openEditor();
                    }});
                }})
                .catch(err => {{
                    console.error('加载编辑器失败:', err);
                    document.getElementById('editor').innerHTML = '<div style="padding: 20px; color: red;">加载失败，请刷新页面重试</div>';
                }});
        </script>
    </body>
    </html>
    '''
    return html


if __name__ == '__main__':
    print("=" * 60)
    print("OnlyOffice 文档共享服务器")
    print("=" * 60)
    print(f"文档存储目录: {DOCUMENTS_DIR}")
    print(f"OnlyOffice 服务器: {ONLYOFFICE_SERVER}")
    print(f"Flask 端口: {FLASK_PORT}")
    print(f"JWT Browser Token: {'enabled' if ONLYOFFICE_JWT_ENABLED else 'disabled'}")
    
    # 检查端口是否被占用
    # 支持环境变量 PORT 指定端口，或自动搜索可用端口
    import socket
    port = None
    
    # 优先使用环境变量指定的端口
    env_port = os.environ.get('PORT')
    if env_port:
        try:
            port = int(env_port)
            print(f"使用环境变量指定的端口: {port}")
        except ValueError:
            print(f"⚠️  环境变量 PORT 值无效: {env_port}，将自动选择端口")
    elif FLASK_PORT:
        port = FLASK_PORT
        print(f"使用 FLASK_PORT 指定的端口: {port}")
    
    # 如果没有指定端口或指定的端口无效，自动搜索可用端口
    if port is None:
        # 依次尝试端口列表
        port_candidates = list(range(5000, 5011)) + list(range(5050, 5061)) + list(range(8000, 8011))
        for try_port in port_candidates:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', try_port))
                sock.close()
                port = try_port
                if try_port != 5000:
                    print(f"⚠️  端口 5000 被占用，自动切换到端口 {port}")
                break
            except OSError:
                sock.close()
                continue
    
    if port is None:
        print("❌ 所有备选端口都被占用，无法启动服务")
        print("   请设置环境变量 PORT 指定一个可用端口，例如: PORT=5100 python3 document_sharing_server.py")
        exit(1)
    
    print("\n访问地址:")
    print(f"  - 文档管理: http://localhost:{port}")
    print(f"  - OnlyOffice 服务: {ONLYOFFICE_SERVER}")
    print(f"  - 浏览器访问地址: http://{HOST_IP}:{port}")
    print(f"  - DocumentServer 拉取地址: http://{DOC_FETCH_HOST}:{DOC_FETCH_PORT}")
    print("\n⚠️  重要：确保 OnlyOffice 容器可以访问上述文档服务器 URL")
    print("   如果无法访问，请检查防火墙设置或 Docker 网络配置")
    print("\n按 Ctrl+C 停止服务器")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=True)
