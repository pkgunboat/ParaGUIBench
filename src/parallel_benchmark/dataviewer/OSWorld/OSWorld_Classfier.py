"""
OSWorld 任务分类器
==================
使用大模型对 OSWorld 任务进行自动分类。

任务类型：
    1. 信息搜索类 (information_search): 从网上搜索信息
    2. 设置类 (settings): 修改软件或系统中的设置
    3. 处理类 (file_processing): 对文件内容进行修改
    4. 其它类 (other): 其他未归类的任务

运行方式：
    python dataviewer/OSWorld/OSWorld_Classfier.py
"""

import os
import json
import base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import threading
import time

from openai import OpenAI

# ========================== API 配置 ==========================
API_KEY = "${OPENAI_API_KEY}"
BASE_URL = 'https://api.deerapi.com/v1/'
MODEL_NAME = "gpt-5.1"  # 使用支持视觉的模型

# ========================== 路径配置 ==========================
SCRIPT_DIR = Path(__file__).parent
EXAMPLES_ZH_DIR = SCRIPT_DIR / "examples_zh"
SCREENSHOT_DIR = SCRIPT_DIR / "OSworld_screenshot"
OUTPUT_FILE = SCRIPT_DIR / "classification_results.json"

# ========================== 分类定义 ==========================
TASK_CATEGORIES = {
    "information_search": "信息搜索类：从网上搜索、查询、浏览信息",
    "settings": "设置类：修改软件、浏览器或系统中的设置、配置、偏好",
    "file_processing": "处理类：对文件（文档、图片、表格等）的内容进行编辑、修改、创建",
    "other": "其它类：不属于以上三类的任务"
}

# ========================== 分类 Prompt ==========================
CLASSIFICATION_PROMPT = """你是一个任务分类专家。请根据以下任务描述和截图，将任务分类为以下四类之一：

## 分类类别：
1. **information_search（信息搜索类）**：从网上搜索、查询、浏览信息。例如：查找航班、搜索商品、查看天气、浏览网页内容等。

2. **settings（设置类）**：修改软件、浏览器或系统中的设置、配置、偏好。例如：修改Chrome设置、调整系统语言、配置软件选项等。

3. **file_processing（处理类）**：对文件（文档、图片、表格等）的内容进行编辑、修改、创建。例如：编辑Word文档、修改Excel表格、处理图片等。

4. **other（其它类）**：不属于以上三类的任务。

## 任务信息：
- **任务ID**: {task_id}
- **应用**: {app_name}
- **中文指令**: {instruction_zh}
- **英文指令**: {instruction_en}
- **相关应用**: {related_apps}

## 输出要求：
请只输出一个JSON对象，格式如下：
```json
{{
    "category": "分类名称（information_search/settings/file_processing/other）",
    "confidence": 0.0-1.0之间的置信度,
    "reason": "简短的分类理由（一句话）"
}}
```

注意：只输出JSON，不要有其他内容。
"""


@dataclass
class ClassificationResult:
    """
    分类结果数据类。
    
    Attributes:
        task_id (str): 任务ID
        app_name (str): 应用名称
        instruction_zh (str): 中文指令
        instruction_en (str): 英文指令
        category (str): 分类类别
        confidence (float): 置信度
        reason (str): 分类理由
        error (Optional[str]): 错误信息（如果分类失败）
        timestamp (str): 分类时间戳
    """
    task_id: str
    app_name: str
    instruction_zh: str
    instruction_en: str
    category: str = "unknown"
    confidence: float = 0.0
    reason: str = ""
    error: Optional[str] = None
    timestamp: str = ""


class OSWorldClassifier:
    """
    OSWorld 任务分类器类。
    
    使用 OpenAI API（支持视觉）对任务进行自动分类。
    支持多线程并行处理以提高效率。
    """
    
    def __init__(
        self, 
        api_key: str = API_KEY, 
        base_url: str = BASE_URL,
        model_name: str = MODEL_NAME,
        max_workers: int = 5
    ):
        """
        初始化分类器。
        
        Args:
            api_key (str): OpenAI API 密钥
            base_url (str): API 基础URL
            model_name (str): 模型名称
            max_workers (int): 最大并行线程数
        """
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model_name
        self.max_workers = max_workers
        self.results: List[ClassificationResult] = []
        self.lock = threading.Lock()
        
        # 统计信息
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "by_category": {cat: 0 for cat in TASK_CATEGORIES.keys()}
        }
    
    def load_task(self, json_path: Path) -> Optional[Dict]:
        """
        加载单个任务的JSON数据。
        
        Args:
            json_path (Path): JSON文件路径
            
        Returns:
            Optional[Dict]: 任务数据字典，加载失败返回None
        """
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ 加载任务失败: {json_path}, 错误: {e}")
            return None
    
    def load_screenshot(self, app_name: str, task_id: str) -> Optional[str]:
        """
        加载任务截图并转换为base64。
        
        Args:
            app_name (str): 应用名称
            task_id (str): 任务ID
            
        Returns:
            Optional[str]: base64编码的图片，不存在返回None
        """
        screenshot_path = SCREENSHOT_DIR / app_name / f"{task_id}.png"
        if not screenshot_path.exists():
            return None
        
        try:
            with open(screenshot_path, 'rb') as f:
                image_data = f.read()
            return base64.b64encode(image_data).decode('utf-8')
        except Exception as e:
            print(f"⚠️ 加载截图失败: {screenshot_path}, 错误: {e}")
            return None
    
    def classify_task(self, task: Dict, app_name: str) -> ClassificationResult:
        """
        对单个任务进行分类。
        
        Args:
            task (Dict): 任务数据
            app_name (str): 应用名称
            
        Returns:
            ClassificationResult: 分类结果
        """
        task_id = task.get("id", "unknown")
        instruction_zh = task.get("instruction_zh", task.get("instruction", ""))
        instruction_en = task.get("instruction", "")
        related_apps = ", ".join(task.get("related_apps", []))
        
        # 创建结果对象
        result = ClassificationResult(
            task_id=task_id,
            app_name=app_name,
            instruction_zh=instruction_zh,
            instruction_en=instruction_en,
            timestamp=datetime.now().isoformat()
        )
        
        try:
            # 构建消息
            prompt = CLASSIFICATION_PROMPT.format(
                task_id=task_id,
                app_name=app_name,
                instruction_zh=instruction_zh,
                instruction_en=instruction_en,
                related_apps=related_apps
            )
            
            messages = [{"role": "user", "content": []}]
            
            # 添加文本内容
            messages[0]["content"].append({
                "type": "text",
                "text": prompt
            })
            
            # 尝试添加截图
            screenshot_base64 = self.load_screenshot(app_name, task_id)
            if screenshot_base64:
                messages[0]["content"].append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{screenshot_base64}",
                        "detail": "low"  # 使用低分辨率以节省token
                    }
                })
            
            # 调用API
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=200,
                temperature=0.1
            )
            
            # 解析响应
            response_text = response.choices[0].message.content.strip()
            
            # 提取JSON
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()
            else:
                json_str = response_text
            
            classification = json.loads(json_str)
            
            result.category = classification.get("category", "other")
            result.confidence = float(classification.get("confidence", 0.0))
            result.reason = classification.get("reason", "")
            
            # 验证分类
            if result.category not in TASK_CATEGORIES:
                result.category = "other"
                result.reason += f" (原分类: {classification.get('category')})"
            
        except json.JSONDecodeError as e:
            result.error = f"JSON解析错误: {e}"
            result.category = "other"
        except Exception as e:
            result.error = f"分类错误: {e}"
            result.category = "other"
        
        return result
    
    def process_task(self, json_path: Path, app_name: str) -> Optional[ClassificationResult]:
        """
        处理单个任务文件（线程安全）。
        
        Args:
            json_path (Path): JSON文件路径
            app_name (str): 应用名称
            
        Returns:
            Optional[ClassificationResult]: 分类结果
        """
        task = self.load_task(json_path)
        if not task:
            return None
        
        result = self.classify_task(task, app_name)
        
        # 线程安全地更新统计
        with self.lock:
            self.results.append(result)
            self.stats["total"] += 1
            
            if result.error:
                self.stats["failed"] += 1
            else:
                self.stats["success"] += 1
                self.stats["by_category"][result.category] += 1
        
        return result
    
    def collect_all_tasks(self) -> List[Tuple[Path, str]]:
        """
        收集所有任务文件。
        
        Returns:
            List[Tuple[Path, str]]: (JSON路径, 应用名称) 列表
        """
        tasks = []
        
        if not EXAMPLES_ZH_DIR.exists():
            print(f"❌ 目录不存在: {EXAMPLES_ZH_DIR}")
            return tasks
        
        for app_dir in EXAMPLES_ZH_DIR.iterdir():
            if app_dir.is_dir() and not app_dir.name.startswith('.'):
                app_name = app_dir.name
                for json_file in app_dir.glob("*.json"):
                    tasks.append((json_file, app_name))
        
        return tasks
    
    def run(self, limit: Optional[int] = None) -> Dict:
        """
        运行分类器，处理所有任务。
        
        Args:
            limit (Optional[int]): 限制处理的任务数量（用于测试）
            
        Returns:
            Dict: 分类统计结果
        """
        print("=" * 60)
        print("🚀 OSWorld 任务分类器")
        print("=" * 60)
        
        # 收集任务
        all_tasks = self.collect_all_tasks()
        if limit:
            all_tasks = all_tasks[:limit]
        
        total = len(all_tasks)
        print(f"📦 共发现 {total} 个任务")
        print(f"🔧 使用 {self.max_workers} 个并行线程")
        print(f"🤖 模型: {self.model_name}")
        print("-" * 60)
        
        start_time = time.time()
        
        # 多线程处理
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.process_task, json_path, app_name): (json_path, app_name)
                for json_path, app_name in all_tasks
            }
            
            completed = 0
            for future in as_completed(futures):
                completed += 1
                json_path, app_name = futures[future]
                
                try:
                    result = future.result()
                    if result:
                        status = "✅" if not result.error else "⚠️"
                        category_display = TASK_CATEGORIES.get(result.category, result.category)[:10]
                        print(f"[{completed}/{total}] {status} {app_name}/{result.task_id[:8]}... → {result.category}")
                except Exception as e:
                    print(f"[{completed}/{total}] ❌ {app_name}/{json_path.stem[:8]}... → 错误: {e}")
        
        elapsed_time = time.time() - start_time
        
        # 打印统计
        print("-" * 60)
        print("📊 分类统计:")
        print(f"  总计: {self.stats['total']}")
        print(f"  成功: {self.stats['success']}")
        print(f"  失败: {self.stats['failed']}")
        print()
        print("📈 分类分布:")
        for category, count in self.stats["by_category"].items():
            percentage = (count / self.stats['success'] * 100) if self.stats['success'] > 0 else 0
            print(f"  {category}: {count} ({percentage:.1f}%)")
        print()
        print(f"⏱️ 总耗时: {elapsed_time:.2f} 秒")
        print(f"📄 平均每个任务: {elapsed_time/total:.2f} 秒" if total > 0 else "")
        print("=" * 60)
        
        return self.stats
    
    def save_results(self, output_path: Optional[Path] = None):
        """
        保存分类结果到JSON文件。
        
        Args:
            output_path (Optional[Path]): 输出文件路径
        """
        output_path = output_path or OUTPUT_FILE
        
        output_data = {
            "metadata": {
                "total_tasks": self.stats["total"],
                "success_count": self.stats["success"],
                "failed_count": self.stats["failed"],
                "model": self.model_name,
                "timestamp": datetime.now().isoformat()
            },
            "statistics": self.stats["by_category"],
            "results": [asdict(r) for r in self.results]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"💾 结果已保存到: {output_path}")
    
    def get_results_by_category(self) -> Dict[str, List[ClassificationResult]]:
        """
        按分类获取结果。
        
        Returns:
            Dict[str, List[ClassificationResult]]: 按类别分组的结果
        """
        by_category = {cat: [] for cat in TASK_CATEGORIES.keys()}
        for result in self.results:
            if result.category in by_category:
                by_category[result.category].append(result)
        return by_category


def main():
    """
    主函数：运行分类器。
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="OSWorld 任务分类器")
    parser.add_argument("--limit", type=int, default=None, help="限制处理的任务数量（用于测试）")
    parser.add_argument("--workers", type=int, default=10, help="并行线程数")
    parser.add_argument("--model", type=str, default=MODEL_NAME, help="使用的模型名称")
    parser.add_argument("--output", type=str, default=None, help="输出文件路径")
    
    args = parser.parse_args()
    
    # 创建分类器
    classifier = OSWorldClassifier(
        max_workers=args.workers,
        model_name=args.model
    )
    
    # 运行分类
    classifier.run(limit=args.limit)
    
    # 保存结果
    output_path = Path(args.output) if args.output else None
    classifier.save_results(output_path)


if __name__ == "__main__":
    main()
