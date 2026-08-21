#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档管理命令行工具

功能：
- 列出所有文档
- 删除指定文档
- 查看文档详情
- 获取/生成文档在线链接
- 清理孤立的共享链接和协作key

使用方法：
    python manage_documents.py list              # 列出所有文档
    python manage_documents.py delete <doc_id>   # 删除指定文档
    python manage_documents.py info <doc_id>     # 查看文档详情
    python manage_documents.py link <doc_id>     # 获取/生成文档在线链接
    python manage_documents.py cleanup           # 清理孤立数据
"""

import json
import sys
import os
import secrets
import socket
from pathlib import Path
from datetime import datetime

# 配置路径
SCRIPT_DIR = Path(__file__).parent
DOCUMENTS_DIR = SCRIPT_DIR / 'shared_documents'
SHARED_LINKS_FILE = SCRIPT_DIR / 'shared_links.json'
DOCUMENT_KEYS_FILE = SCRIPT_DIR / 'document_keys.json'

# 服务器配置
SERVER_PORT = int(os.environ.get('SERVER_PORT', '5001'))


def get_host_ip():
    """
    获取主机 IP 地址
    优先级：环境变量 > 自动检测
    """
    host_ip = os.environ.get('HOST_IP')
    if host_ip:
        return host_ip
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return 'localhost'


def list_documents():
    """
    列出 shared_documents 目录下的所有文档
    
    返回：
        list[dict]: 文档信息列表，每个元素包含 name/path/size/mtime
    """
    if not DOCUMENTS_DIR.exists():
        return []

    docs = []
    for p in DOCUMENTS_DIR.glob('*'):
        # 跳过 macOS 元数据文件
        if not p.is_file() or p.name.startswith('._'):
            continue

        st = p.stat()
        docs.append({
            'name': p.name,
            'path': str(p),
            'size': st.st_size,
            'mtime': datetime.fromtimestamp(st.st_mtime).isoformat()
        })

    # 最近修改的排在前面
    docs.sort(key=lambda x: x['mtime'], reverse=True)
    return docs


def resolve_document_file(doc_id: str) -> Path | None:
    """
    根据 doc_id 定位真实文件路径
    
    兼容策略（与 Flask 服务保持一致）：
    1) 先尝试精确匹配 shared_documents/<doc_id>
    2) 再尝试 shared_documents/<doc_id>.*（任意扩展名）
    
    参数：
        doc_id: 文档ID（可能是hash，也可能是含点的完整文件名）
    
    返回：
        Path | None: 找到则返回文件路径，否则 None
    """
    # 精确匹配
    p = DOCUMENTS_DIR / doc_id
    if p.exists() and p.is_file():
        return p

    # 扩展名匹配
    for candidate in DOCUMENTS_DIR.glob(f"{doc_id}.*"):
        if candidate.is_file() and not candidate.name.startswith('._'):
            return candidate

    return None


def print_documents_table(docs: list[dict]):
    """
    以更友好的格式输出文档列表
    
    参数：
        docs: list_documents() 的返回结果
    """
    if not docs:
        print('暂无文档')
        return

    print(f"共 {len(docs)} 个文档：")
    print('-' * 80)
    for d in docs:
        size_kb = d['size'] / 1024
        print(f"- {d['name']}\n  size={size_kb:.2f}KB\n  mtime={d['mtime']}\n  path={d['path']}")
    print('-' * 80)


def cmd_list():
    """命令：list"""
    docs = list_documents()
    print_documents_table(docs)


def cmd_info(doc_id: str):
    """
    命令：info
    
    参数：
        doc_id: 文档ID
    """
    fp = resolve_document_file(doc_id)
    if not fp:
        print(f"未找到文档：{doc_id}")
        sys.exit(1)

    st = fp.stat()
    print('文档详情：')
    print(f"- name: {fp.name}")
    print(f"- path: {fp}")
    print(f"- size: {st.st_size} bytes ({st.st_size/1024:.2f} KB)")
    print(f"- mtime: {datetime.fromtimestamp(st.st_mtime).isoformat()}")

    # 共享链接
    links = load_json_file(SHARED_LINKS_FILE)
    related_links = [k for k, v in links.items() if v.get('document_id') == doc_id]
    print(f"- shared_links: {len(related_links)}")
    for k in related_links:
        print(f"  - {k}")

    # 协作key
    keys = load_json_file(DOCUMENT_KEYS_FILE)
    if doc_id in keys:
        print(f"- collab_key: {keys[doc_id].get('key')}")
        print(f"- collab_created_at: {keys[doc_id].get('created_at')}")
    else:
        print("- collab_key: (none)")


def cmd_delete(doc_id: str):
    """
    命令：delete
    
    功能：删除文档文件，并清理相关 shared_links / document_keys
    
    参数：
        doc_id: 文档ID
    """
    fp = resolve_document_file(doc_id)
    if not fp:
        print(f"未找到文档：{doc_id}")
        sys.exit(1)

    # 删除文件
    fp.unlink()
    print(f"已删除文件：{fp}")

    # 清理共享链接
    links = load_json_file(SHARED_LINKS_FILE)
    keys_to_remove = [k for k, v in links.items() if v.get('document_id') == doc_id]
    for k in keys_to_remove:
        del links[k]
    if keys_to_remove:
        save_json_file(SHARED_LINKS_FILE, links)
        print(f"已清理共享链接：{len(keys_to_remove)}")

    # 清理协作key
    doc_keys = load_json_file(DOCUMENT_KEYS_FILE)
    if doc_id in doc_keys:
        del doc_keys[doc_id]
        save_json_file(DOCUMENT_KEYS_FILE, doc_keys)
        print("已清理协作key")


def cmd_link(doc_id: str, create_new: bool = False):
    """
    命令：link
    
    功能：获取或生成文档的在线共享链接
    
    参数：
        doc_id: 文档ID
        create_new: 是否强制创建新链接（即使已有链接）
    """
    fp = resolve_document_file(doc_id)
    if not fp:
        print(f"未找到文档：{doc_id}")
        sys.exit(1)
    
    host_ip = get_host_ip()
    base_url = f"http://{host_ip}:{SERVER_PORT}"
    
    # 查找现有的共享链接
    links = load_json_file(SHARED_LINKS_FILE)
    existing_keys = [k for k, v in links.items() if v.get('document_id') == doc_id]
    
    share_key = None
    
    if existing_keys and not create_new:
        # 使用最新创建的链接
        latest_key = max(existing_keys, key=lambda k: links[k].get('created_at', ''))
        share_key = latest_key
        print(f"文档已有共享链接（共 {len(existing_keys)} 个）")
    else:
        # 创建新的共享链接
        share_key = secrets.token_urlsafe(16)
        links[share_key] = {
            'document_id': doc_id,
            'created_at': datetime.now().isoformat()
        }
        save_json_file(SHARED_LINKS_FILE, links)
        print("已创建新的共享链接")
    
    # 输出链接信息
    share_url = f"{base_url}/share/{share_key}"
    edit_url = f"{base_url}/#edit/{doc_id}"
    
    print()
    print("=" * 60)
    print(f"文档: {fp.name}")
    print(f"文档ID: {doc_id}")
    print("=" * 60)
    print()
    print("【共享链接】（可分享给他人协作编辑）")
    print(f"  {share_url}")
    print()
    print("【直接编辑链接】（需要在主页面打开）")
    print(f"  {base_url}")
    print()
    print("=" * 60)
    
    # 返回共享链接（方便脚本调用）
    return share_url


def cmd_cleanup():
    """
    命令：cleanup
    
    功能：清理孤立的 shared_links 和 document_keys
    - shared_links 中指向不存在文档的项会被删除
    - document_keys 中指向不存在文档的项会被删除
    """
    existing_files = {p.name for p in DOCUMENTS_DIR.glob('*') if p.is_file() and not p.name.startswith('._')}
    existing_ids = set()
    for name in existing_files:
        # 与服务端 list_documents 的策略近似：如果是 hash.ext（ext不为空），则 id=hash；否则 id=完整文件名
        if '.' in name:
            head, tail = name.rsplit('.', 1)
            if tail:
                existing_ids.add(head)
            else:
                existing_ids.add(name)
        else:
            existing_ids.add(name)

    # 清理共享链接
    links = load_json_file(SHARED_LINKS_FILE)
    before_links = len(links)
    links = {k: v for k, v in links.items() if v.get('document_id') in existing_ids}
    after_links = len(links)
    if after_links != before_links:
        save_json_file(SHARED_LINKS_FILE, links)
    print(f"shared_links: {before_links} -> {after_links}")

    # 清理协作key
    doc_keys = load_json_file(DOCUMENT_KEYS_FILE)
    before_keys = len(doc_keys)
    doc_keys = {k: v for k, v in doc_keys.items() if k in existing_ids}
    after_keys = len(doc_keys)
    if after_keys != before_keys:
        save_json_file(DOCUMENT_KEYS_FILE, doc_keys)
    print(f"document_keys: {before_keys} -> {after_keys}")


def print_usage():
    """输出帮助信息"""
    print(__doc__.strip())
    print('\n示例：')
    print('  python manage_documents.py list')
    print('  python manage_documents.py info <doc_id>')
    print('  python manage_documents.py link <doc_id>        # 获取/生成在线链接')
    print('  python manage_documents.py link <doc_id> --new  # 强制生成新链接')
    print('  python manage_documents.py delete <doc_id>')
    print('  python manage_documents.py cleanup')
    print('\n环境变量：')
    print('  HOST_IP=<ip>        # 指定服务器IP（默认自动检测）')
    print('  SERVER_PORT=<port>  # 指定服务器端口（默认5001）')


def main(argv: list[str]):
    """
    命令行入口
    
    参数：
        argv: sys.argv
    """
    if len(argv) < 2:
        print_usage()
        sys.exit(1)

    cmd = argv[1].lower()
    if cmd == 'list':
        cmd_list()
        return

    if cmd == 'info':
        if len(argv) < 3:
            print('缺少参数：doc_id')
            sys.exit(1)
        cmd_info(argv[2])
        return

    if cmd == 'delete':
        if len(argv) < 3:
            print('缺少参数：doc_id')
            sys.exit(1)
        cmd_delete(argv[2])
        return

    if cmd == 'link':
        if len(argv) < 3:
            print('缺少参数：doc_id')
            sys.exit(1)
        create_new = '--new' in argv or '-n' in argv
        cmd_link(argv[2], create_new)
        return

    if cmd == 'cleanup':
        cmd_cleanup()
        return

    print(f"未知命令：{cmd}")
    print_usage()
    sys.exit(1)

def load_json_file(file_path):
    """
    加载JSON文件
    
    参数：
        file_path: JSON文件路径
    
    返回：
        dict: JSON数据，如果文件不存在则返回空字典
    """
    if file_path.exists():
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_json_file(file_path, data):
    """
    保存JSON文件
    
    参数：
        file_path: JSON文件路径
        data: 要保存的数据
    """
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    main(sys.argv)
