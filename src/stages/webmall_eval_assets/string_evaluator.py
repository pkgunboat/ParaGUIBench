#!/usr/bin/env python3
"""
StringEvaluator 模块 - 针对非cart和非end2end任务的评价器

功能:
1. 从 task_sets.json 加载所有 string 类型任务的标准答案
2. 对比Agent提交的URL与标准答案URL（归一化后精确匹配）
3. 提供命令行接口，支持指定任务ID和模拟的Agent返回结果

使用方式:
    # 作为模块导入
    from string_evaluator import StringEvaluator, load_all_string_tasks
    
    # 命令行使用
    python string_evaluator.py --task-id <task_uid> --submitted-urls <url1> <url2> ...
"""

import json
import re
import sys
import hashlib
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ===== 配置 =====
# 默认 VM IP；运行时一般由上游 pipeline 或 CLI --vm-ip 传入具体值。
DEFAULT_VM_IP = "127.0.0.1"

# task_sets.json 的默认路径
_module_dir = Path(__file__).resolve().parent
TASK_SETS_JSON_PATH = _module_dir / "Browsergym" / "browsergym" / "webmall" / "src" / "browsergym" / "webmall" / "task_sets.json"

# tasks 目录的路径（用于建立 task_uid 映射）
TASKS_DIR = _module_dir.parent / "tasks"


@dataclass
class TaskAnswer:
    """
    任务答案数据类
    
    属性:
        webmall_task_id: WebMall 原始任务ID（如 "Webmall_Find_Specific_Product_Task1"）
        task_uid: 任务唯一标识（32位MD5哈希）
        category: 任务类别（如 "Specific_Product"）
        task_tag: 任务标签（如 "SingleProductSearch"）
        instruction: 任务描述
        answer_type: 答案类型（"string", "cart", "checkout"）
        expected_urls: 标准答案URL列表（包含占位符）
    """
    webmall_task_id: str
    task_uid: str
    category: str
    task_tag: str
    instruction: str
    answer_type: str
    expected_urls: List[str]


# ===== 任务类别到task_tag的映射 =====
CATEGORY_TO_TAG = {
    "Specific_Product": "SingleProductSearch",
    "Cheapest_Offer": "CheapestProductSearch",
    "Find_Substitutes": "FindSubstitutes",
    "Find_Compatible_Products": "FindCompatibleProducts",
    "Cheapest_Offer_Vague": "CheapestOfferVagueRequirements",
    "Cheapest_Offer_Specific": "CheapestOfferSpecificRequirements",
    "Products_Satisfying_Vague": "ProductsSatisfyingVagueRequirements",
    "Products_Fulfilling_Specific": "ProductsFulfillingSpecificRequirements",
    "Add_To_Cart": "AddtoCart",
    "Checkout": "Checkout",
    "Find_And_Order": "EndtoEnd",
}


def replace_url_placeholders(url: str, vm_ip: str = DEFAULT_VM_IP) -> str:
    """
    将URL占位符替换为实际地址
    
    参数:
        url: 包含占位符的URL（如 {{URL_1}}/product/xxx）
        vm_ip: 虚拟机IP地址
        
    返回:
        替换后的实际URL
    """
    replacements = {
        "{{URL_1}}": f"http://{vm_ip}:9081",
        "{{URL_2}}": f"http://{vm_ip}:9082",
        "{{URL_3}}": f"http://{vm_ip}:9083",
        "{{URL_4}}": f"http://{vm_ip}:9084",
        "{{URL_5}}": f"http://{vm_ip}:9085",
    }
    for placeholder, actual in replacements.items():
        url = url.replace(placeholder, actual)
    return url


def normalize_url(url: str) -> str:
    """
    归一化URL：去除末尾斜杠，去除协议前缀
    
    参数:
        url: 原始URL
        
    返回:
        归一化后的URL
    """
    return url.rstrip("/").lstrip("http://").lstrip("https://")


def generate_task_uid(webmall_task_id: str) -> str:
    """
    从WebMall任务ID生成唯一标识（MD5哈希）
    
    参数:
        webmall_task_id: WebMall原始任务ID
        
    返回:
        32位MD5哈希字符串
    """
    return hashlib.md5(webmall_task_id.encode()).hexdigest()


def load_all_string_tasks(
    task_sets_path: Optional[str] = None,
) -> Dict[str, TaskAnswer]:
    """
    从 task_sets.json 加载所有 string 类型任务（非cart和非checkout）
    
    参数:
        task_sets_path: task_sets.json 文件路径，默认使用模块内置路径
        
    返回:
        任务答案字典，key为task_uid
    """
    if task_sets_path is None:
        task_sets_path = TASK_SETS_JSON_PATH
    
    task_sets_path = Path(task_sets_path)
    if not task_sets_path.exists():
        raise FileNotFoundError(f"task_sets.json not found: {task_sets_path}")
    
    tasks = {}
    
    with open(task_sets_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    for task_set in data:
        for task in task_set.get("tasks", []):
            correct_answer = task.get("correct_answer", {})
            answer_type = correct_answer.get("type", "")
            
            # 只加载 string 类型的任务（非cart和非checkout）
            if answer_type != "string":
                continue
            
            webmall_task_id = task.get("id", "")
            task_uid = generate_task_uid(webmall_task_id)
            category = task.get("category", "")
            task_tag = CATEGORY_TO_TAG.get(category, category)
            
            # 提取任务描述（去除标签）
            instruction = task.get("task", "")
            instruction = re.sub(r'</?task>', '', instruction).strip()
            
            tasks[task_uid] = TaskAnswer(
                webmall_task_id=webmall_task_id,
                task_uid=task_uid,
                category=category,
                task_tag=task_tag,
                instruction=instruction,
                answer_type=answer_type,
                expected_urls=correct_answer.get("answers", []),
            )
    
    return tasks


def load_task_uid_mapping(mapping_file: Optional[str] = None) -> Dict[str, dict]:
    """
    从 task_uid_mapping.json 加载预计算的任务映射
    
    参数:
        mapping_file: 映射文件路径
        
    返回:
        任务UID到详细信息的映射
    """
    if mapping_file is None:
        mapping_file = _module_dir / "task_uid_mapping.json"
    
    mapping_file = Path(mapping_file)
    if not mapping_file.exists():
        return {}
    
    try:
        with open(mapping_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}


class StringEvaluator:
    """
    StringEvaluator - 用于评价非cart/非end2end任务
    
    评价逻辑：对比Agent提交的URL与标准答案URL（归一化后精确匹配）
    
    支持两种查找方式：
    1. 通过 task_sets.json 中的任务（使用webmall_task_id的MD5哈希）
    2. 通过 tasks/*.json 中的 task_uid（使用预计算的映射文件）
    """
    
    def __init__(self, task_sets_path: Optional[str] = None, mapping_file: Optional[str] = None):
        """
        初始化评价器
        
        参数:
            task_sets_path: task_sets.json 文件路径，默认使用模块内置路径
            mapping_file: task_uid_mapping.json 文件路径，默认使用模块内置路径
        """
        self.tasks = load_all_string_tasks(task_sets_path)
        self.uid_mapping = load_task_uid_mapping(mapping_file)
    
    def get_task(self, task_id: str) -> Optional[TaskAnswer]:
        """
        获取任务信息
        
        参数:
            task_id: 任务ID，支持以下格式：
                - tasks/*.json 中的 task_uid（如 "0d34c4e1863c4ddb832ec4eba10d5ef6"）
                - task_sets.json 中的 webmall_task_id（如 "Webmall_Find_Specific_Product_Task1"）
                - webmall_task_id 的 MD5 哈希
            
        返回:
            TaskAnswer对象，未找到返回None
        """
        # 1. 尝试从预计算映射中查找（tasks/*.json 的 task_uid）
        if task_id in self.uid_mapping:
            mapping_info = self.uid_mapping[task_id]
            if mapping_info.get("answer_type") == "string":
                return TaskAnswer(
                    webmall_task_id=mapping_info.get("webmall_task_id", ""),
                    task_uid=task_id,
                    category=mapping_info.get("category", ""),
                    task_tag=mapping_info.get("task_tag", ""),
                    instruction=mapping_info.get("instruction", ""),
                    answer_type="string",
                    expected_urls=mapping_info.get("expected_urls", []),
                )
        
        # 2. 直接用task_uid（MD5哈希）查找
        if task_id in self.tasks:
            return self.tasks[task_id]
        
        # 3. 尝试用webmall_task_id生成uid查找
        uid = generate_task_uid(task_id)
        if uid in self.tasks:
            return self.tasks[uid]
        
        return None
    
    def evaluate(
        self,
        task_id: str,
        submitted_urls: List[str],
        vm_ip: str = DEFAULT_VM_IP,
    ) -> Dict[str, Any]:
        """
        评价Agent提交的URL
        
        参数:
            task_id: 任务ID（可以是task_uid、webmall_task_id或source_task_id）
            submitted_urls: Agent提交的URL列表（模拟大模型返回的结果）
            vm_ip: 虚拟机IP地址
            
        返回:
            评价结果字典，包含:
            - task_id: 任务ID
            - task_tag: 任务类型
            - score: 得分（匹配的URL数量）
            - max_score: 最高分（期望URL数量）
            - matched_urls: 匹配的URL列表
            - wrong_urls: 错误提交的URL列表
            - missing_urls: 未提交的期望URL列表
            - accuracy: 准确率（matched / expected）
            - precision: 精确率（matched / submitted）
            - recall: 召回率（与accuracy相同）
            - f1: F1分数
            - detail: 详细说明
        """
        task = self.get_task(task_id)
        if task is None:
            return {
                "error": f"未找到任务: {task_id}",
                "score": 0,
                "max_score": 0,
                "accuracy": 0.0,
            }
        
        # 替换占位符
        expected_urls = [replace_url_placeholders(u, vm_ip) for u in task.expected_urls]
        submitted_urls_resolved = [replace_url_placeholders(u, vm_ip) for u in submitted_urls]
        
        # 归一化URL
        norm_expected = {normalize_url(u): u for u in expected_urls}
        norm_submitted = {normalize_url(u): u for u in submitted_urls_resolved}
        
        # 计算匹配
        matched = []
        wrong = []
        
        for norm_sub, orig_sub in norm_submitted.items():
            if norm_sub in norm_expected:
                matched.append(orig_sub)
            else:
                wrong.append(orig_sub)
        
        # 计算未提交的期望URL
        matched_norm = set(normalize_url(u) for u in matched)
        missing = [u for u in expected_urls if normalize_url(u) not in matched_norm]
        
        # 计算分数
        score = len(matched)
        max_score = len(expected_urls)
        
        # 计算指标
        accuracy = score / max_score if max_score > 0 else 0.0
        precision = score / len(submitted_urls_resolved) if submitted_urls_resolved else 0.0
        recall = accuracy  # 召回率 = 匹配数 / 期望数
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        return {
            "task_id": task_id,
            "webmall_task_id": task.webmall_task_id,
            "task_tag": task.task_tag,
            "instruction": task.instruction,
            "score": score,
            "max_score": max_score,
            "matched_urls": matched,
            "wrong_urls": wrong,
            "missing_urls": missing,
            "expected_urls": expected_urls,
            "submitted_urls": submitted_urls_resolved,
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "detail": f"匹配 {score}/{max_score} 个期望URL，错误提交 {len(wrong)} 个",
        }
    
    def list_tasks(self, task_tag: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        列出所有可用任务
        
        参数:
            task_tag: 筛选特定类型的任务
            
        返回:
            任务列表
        """
        result = []
        for uid, task in self.tasks.items():
            if task_tag is None or task.task_tag == task_tag:
                result.append({
                    "task_uid": uid,
                    "webmall_task_id": task.webmall_task_id,
                    "task_tag": task.task_tag,
                    "category": task.category,
                    "instruction": task.instruction[:80] + "..." if len(task.instruction) > 80 else task.instruction,
                    "num_expected_urls": len(task.expected_urls),
                })
        return result


def print_evaluation_result(result: Dict[str, Any], verbose: bool = True):
    """
    打印评价结果
    
    参数:
        result: 评价结果字典
        verbose: 是否打印详细信息
    """
    if "error" in result:
        print(f"❌ 错误: {result['error']}")
        return
    
    print("=" * 70)
    print(f"任务: {result.get('webmall_task_id', result.get('task_id'))}")
    print(f"类型: {result['task_tag']}")
    if verbose:
        instruction = result.get('instruction', '')
        print(f"描述: {instruction[:80]}..." if len(instruction) > 80 else f"描述: {instruction}")
    print("-" * 70)
    
    if verbose:
        print(f"期望URL ({result['max_score']} 个):")
        for i, url in enumerate(result.get('expected_urls', []), 1):
            print(f"  {i}. {url}")
        print(f"\n提交URL ({len(result.get('submitted_urls', []))} 个):")
        for i, url in enumerate(result.get('submitted_urls', []), 1):
            marker = "✅" if url in result["matched_urls"] else "❌"
            print(f"  {marker} {i}. {url}")
        print("-" * 70)
    
    print(f"得分: {result['score']}/{result['max_score']}")
    print(f"准确率/召回率: {result['accuracy']:.1%}")
    print(f"精确率: {result['precision']:.1%}")
    print(f"F1分数: {result['f1']:.2f}")
    
    if verbose and result.get("wrong_urls"):
        print(f"\n❌ 错误提交:")
        for url in result["wrong_urls"]:
            print(f"  - {url}")
    
    if verbose and result.get("missing_urls"):
        print(f"\n⚠️  未提交的期望URL:")
        for url in result["missing_urls"]:
            print(f"  - {url}")
    
    print("=" * 70)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="StringEvaluator - 非cart/非end2end任务评价器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 列出所有可用任务
  python string_evaluator.py --list
  
  # 列出特定类型的任务
  python string_evaluator.py --list --task-tag SingleProductSearch
  
  # 评价单个任务
  python string_evaluator.py --task-id 9203328311c7430aaedb899a4496b6e9 \\
    --submitted-urls "http://<HOST_IP>:9083/product/asus-rog-falchion-ace..."
  
  # 使用占位符格式的URL
  python string_evaluator.py --task-id 0d34c4e1863c4ddb832ec4eba10d5ef6 \\
    --submitted-urls "{{URL_1}}/product/xxx" "{{URL_2}}/product/yyy"
""",
    )
    
    parser.add_argument("--list", action="store_true", help="列出所有可用任务")
    parser.add_argument("--task-id", type=str, help="任务ID（task_uid或webmall_task_id）")
    parser.add_argument("--task-tag", type=str, help="任务类型筛选")
    parser.add_argument("--submitted-urls", nargs="*", help="Agent提交的URL列表")
    parser.add_argument("--vm-ip", type=str, default=DEFAULT_VM_IP, help="虚拟机IP地址")
    parser.add_argument("--task-sets-path", type=str, help="task_sets.json 路径")
    parser.add_argument("-q", "--quiet", action="store_true", help="简洁输出模式")
    
    args = parser.parse_args()
    
    # 初始化评价器
    evaluator = StringEvaluator(args.task_sets_path)
    
    # 列出任务
    if args.list:
        tasks = evaluator.list_tasks(args.task_tag)
        print(f"\n找到 {len(tasks)} 个{'符合条件的' if args.task_tag else ''} string 类型任务:\n")
        
        # 按task_tag分组显示
        by_tag = {}
        for t in tasks:
            tag = t["task_tag"]
            if tag not in by_tag:
                by_tag[tag] = []
            by_tag[tag].append(t)
        
        for tag, tag_tasks in sorted(by_tag.items()):
            print(f"【{tag}】 ({len(tag_tasks)} 个)")
            for t in tag_tasks[:3]:  # 每类只显示前3个
                print(f"  - {t['task_uid'][:8]}... | {t['instruction']}")
            if len(tag_tasks) > 3:
                print(f"  ... 还有 {len(tag_tasks) - 3} 个")
            print()
        return
    
    # 评价任务
    if args.task_id:
        submitted_urls = args.submitted_urls or []
        result = evaluator.evaluate(args.task_id, submitted_urls, args.vm_ip)
        print_evaluation_result(result, verbose=not args.quiet)
        return
    
    # 无参数时显示帮助
    parser.print_help()


if __name__ == "__main__":
    main()
