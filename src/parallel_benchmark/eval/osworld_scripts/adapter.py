"""
OSWorld 评测脚本路径适配器

将 OSWorld 原生路径映射到并行评测环境的共享目录路径
"""

import os
from typing import Optional


# OSWorld 原生路径前缀 -> 并行评测环境路径前缀
PATH_MAPPING = {
    "/home/user/Desktop/": "/home/user/shared/",
    "/home/user/Documents/": "/home/user/shared/",
    "/home/user/Downloads/": "/home/user/shared/",
    "/home/user/Pictures/": "/home/user/shared/",
    "/home/user/Videos/": "/home/user/shared/",
    "/home/user/Music/": "/home/user/shared/",
}


def adapt_result_path(osworld_path: str) -> str:
    """
    将 OSWorld 原生路径映射到共享目录路径
    
    输入:
        osworld_path: OSWorld 原生文件路径，如 "/home/user/Desktop/students work/case study.docx"
    输出:
        映射后的路径，如 "/home/user/shared/students work/case study.docx"
    """
    if not osworld_path:
        return osworld_path
    
    for old_prefix, new_prefix in PATH_MAPPING.items():
        if osworld_path.startswith(old_prefix):
            return osworld_path.replace(old_prefix, new_prefix, 1)
    
    # 如果没有匹配的前缀，但路径以 /home/user/ 开头，尝试默认映射到 shared
    if osworld_path.startswith("/home/user/"):
        # 提取相对路径部分
        relative_path = osworld_path.replace("/home/user/", "", 1)
        return f"/home/user/shared/{relative_path}"
    
    return osworld_path


def adapt_result_path_safe(osworld_path: str, default_subdir: str = "") -> str:
    """
    安全版本的路径映射，如果路径为空则使用默认值
    
    输入:
        osworld_path: OSWorld 原生文件路径
        default_subdir: 默认子目录，如 "output"
    输出:
        映射后的路径
    """
    if not osworld_path and default_subdir:
        return f"/home/user/shared/{default_subdir}"
    return adapt_result_path(osworld_path)


def get_file_extension(file_path: str) -> str:
    """
    从文件路径提取扩展名
    
    输入:
        file_path: 文件路径
    输出:
        扩展名（含点号），如 ".xlsx"
    """
    return os.path.splitext(file_path)[1]


def generate_result_filename(task_uid: str, original_path: str) -> str:
    """
    为结果文件生成唯一文件名
    
    输入:
        task_uid: 任务唯一标识
        original_path: 原始文件路径
    输出:
        生成的文件名，如 "result_{task_uid}.xlsx"
    """
    ext = get_file_extension(original_path)
    return f"result_{task_uid}{ext}"
