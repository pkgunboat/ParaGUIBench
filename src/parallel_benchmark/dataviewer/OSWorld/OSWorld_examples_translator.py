# 读取OSWorld/examples目录下的json文件，将其中的instruction部分翻译成中文
# 在原始的json文件的基础上加上中文翻译（instruction_zh字段）
# 按照原目录的结构保存到examples_zh目录下
# 使用并行执行提高翻译效率

import os
import json
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
import time

# API 配置
API_KEY = "${OPENAI_API_KEY}"
BASE_URL = 'https://api.deerapi.com/v1/'
MODEL_NAME = "gemini-2.5-flash"

# 定义输入和输出目录
INPUT_DIR = '/Users/zedongyu/code/parallel_benchmark/OSWorld/evaluation_examples/examples'
OUTPUT_DIR = '/Users/zedongyu/code/parallel_benchmark/dataviewer/OSWorld/examples_zh'

# 并行配置
MAX_CONCURRENT_REQUESTS = 10  # 最大并发请求数
RETRY_TIMES = 3  # 重试次数
RETRY_DELAY = 2  # 重试延迟（秒）


def translate_instruction_sync(client: OpenAI, instruction: str) -> Optional[str]:
    """
    使用 OpenAI API 将英文指令翻译成中文（同步版本）
    
    Args:
        client: OpenAI 客户端实例
        instruction: 需要翻译的英文指令
    
    Returns:
        翻译后的中文指令，如果翻译失败则返回 None
    """
    if not instruction or not instruction.strip():
        return ""
    
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个专业的翻译助手。请将以下英文指令翻译成中文，保持原意，不要添加额外内容。只输出翻译结果，不要有任何解释。"
                },
                {
                    "role": "user",
                    "content": instruction
                }
            ],
            temperature=0.3,
            max_tokens=1024
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"翻译失败: {e}")
        return None


def process_single_file(args: tuple) -> dict:
    """
    处理单个 JSON 文件：读取、翻译、保存
    
    Args:
        args: (input_path, output_path, client) 元组
    
    Returns:
        包含处理结果的字典 {"success": bool, "file": str, "error": str}
    """
    input_path, output_path, client = args
    result = {"success": False, "file": str(input_path), "error": None}
    
    try:
        # 读取原始 JSON 文件
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 检查是否已有翻译
        if 'instruction_zh' in data and data['instruction_zh']:
            # 如果输出文件已存在，跳过
            if output_path.exists():
                result["success"] = True
                result["error"] = "已存在翻译，跳过"
                return result
        
        # 获取需要翻译的 instruction
        instruction = data.get('instruction', '')
        
        # 执行翻译（带重试）
        translated = None
        for attempt in range(RETRY_TIMES):
            translated = translate_instruction_sync(client, instruction)
            if translated is not None:
                break
            if attempt < RETRY_TIMES - 1:
                time.sleep(RETRY_DELAY)
        
        if translated is None:
            result["error"] = "翻译失败，已达最大重试次数"
            return result
        
        # 添加中文翻译字段
        data['instruction_zh'] = translated
        
        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 保存到输出目录
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        result["success"] = True
        return result
        
    except Exception as e:
        result["error"] = str(e)
        return result


def collect_json_files(input_dir: str, output_dir: str) -> list:
    """
    收集所有需要处理的 JSON 文件路径
    
    Args:
        input_dir: 输入目录路径
        output_dir: 输出目录路径
    
    Returns:
        包含 (input_path, output_path) 元组的列表
    """
    files = []
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    
    for json_file in input_path.rglob('*.json'):
        # 计算相对路径，保持目录结构
        relative_path = json_file.relative_to(input_path)
        output_file = output_path / relative_path
        files.append((json_file, output_file))
    
    return files


def run_parallel_translation(max_workers: int = MAX_CONCURRENT_REQUESTS):
    """
    并行执行所有 JSON 文件的翻译任务
    
    Args:
        max_workers: 最大并行工作线程数
    """
    # 创建 OpenAI 客户端
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    
    # 确保输出目录存在
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 收集所有 JSON 文件
    files = collect_json_files(INPUT_DIR, OUTPUT_DIR)
    print(f"找到 {len(files)} 个 JSON 文件需要处理")
    
    # 过滤掉已经处理过的文件
    files_to_process = []
    for input_path, output_path in files:
        if not output_path.exists():
            files_to_process.append((input_path, output_path, client))
        else:
            # 检查是否已有翻译
            try:
                with open(output_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'instruction_zh' not in data or not data['instruction_zh']:
                        files_to_process.append((input_path, output_path, client))
            except:
                files_to_process.append((input_path, output_path, client))
    
    print(f"需要翻译 {len(files_to_process)} 个文件（跳过 {len(files) - len(files_to_process)} 个已翻译文件）")
    
    if not files_to_process:
        print("所有文件已翻译完成！")
        return
    
    # 统计结果
    success_count = 0
    fail_count = 0
    failed_files = []
    
    # 使用线程池并行执行翻译
    start_time = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(process_single_file, files_to_process))
    
    # 统计结果
    for result in results:
        if result["success"]:
            success_count += 1
            print(f"✓ 完成: {Path(result['file']).name}")
        else:
            fail_count += 1
            failed_files.append((result["file"], result["error"]))
            print(f"✗ 失败: {Path(result['file']).name} - {result['error']}")
    
    # 打印总结
    elapsed_time = time.time() - start_time
    print("\n" + "=" * 50)
    print(f"翻译完成！")
    print(f"成功: {success_count} 个文件")
    print(f"失败: {fail_count} 个文件")
    print(f"总耗时: {elapsed_time:.2f} 秒")
    print(f"平均每个文件: {elapsed_time / len(files_to_process):.2f} 秒")
    
    # 保存失败列表
    if failed_files:
        failed_log_path = Path(OUTPUT_DIR) / "failed_translations.json"
        with open(failed_log_path, 'w', encoding='utf-8') as f:
            json.dump(failed_files, f, ensure_ascii=False, indent=2)
        print(f"\n失败文件列表已保存到: {failed_log_path}")


def retry_failed_translations():
    """
    重试之前失败的翻译任务
    """
    failed_log_path = Path(OUTPUT_DIR) / "failed_translations.json"
    if not failed_log_path.exists():
        print("没有找到失败记录文件")
        return
    
    with open(failed_log_path, 'r', encoding='utf-8') as f:
        failed_files = json.load(f)
    
    print(f"找到 {len(failed_files)} 个失败的翻译任务，开始重试...")
    
    # 创建客户端
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    
    # 重新构建任务列表
    files_to_process = []
    for file_path, _ in failed_files:
        input_path = Path(file_path)
        relative_path = input_path.relative_to(INPUT_DIR)
        output_path = Path(OUTPUT_DIR) / relative_path
        files_to_process.append((input_path, output_path, client))
    
    # 并行执行
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
        results = list(executor.map(process_single_file, files_to_process))
    
    # 统计新的失败
    new_failed = [(r["file"], r["error"]) for r in results if not r["success"]]
    
    if new_failed:
        with open(failed_log_path, 'w', encoding='utf-8') as f:
            json.dump(new_failed, f, ensure_ascii=False, indent=2)
        print(f"仍有 {len(new_failed)} 个文件翻译失败")
    else:
        # 删除失败记录文件
        failed_log_path.unlink()
        print("所有失败的翻译已成功重试！")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='并行翻译 OSWorld 任务指令')
    parser.add_argument('--retry', action='store_true', help='重试之前失败的翻译')
    parser.add_argument('--workers', type=int, default=MAX_CONCURRENT_REQUESTS, 
                        help=f'最大并行工作线程数（默认: {MAX_CONCURRENT_REQUESTS}）')
    args = parser.parse_args()
    
    if args.retry:
        retry_failed_translations()
    else:
        run_parallel_translation(max_workers=args.workers)
