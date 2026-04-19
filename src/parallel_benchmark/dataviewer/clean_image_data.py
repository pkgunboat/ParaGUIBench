"""
清理JSON文件中的base64图片编码数据，并提取图片到文件

功能：
    1. 递归遍历JSON数据，将所有base64编码的图片数据提取为文件
    2. 将JSON中的base64编码替换为图片路径
    3. 支持批量处理目录下的所有JSON文件

输入：
    原始JSON文件路径 或 目录路径

输出：
    - 清理后的JSON文件（原地更新，备份原文件）
    - 提取的图片文件（保存到 extracted_images 目录）
"""

import json
import base64
import os
import re
import sys
from pathlib import Path
from typing import Any, List, Tuple, Dict, Optional
from PIL import Image, ImageDraw, ImageFont
import io


def find_image_urls(data: Any, path: str = "", images: List[Tuple[str, str]] = None) -> List[Tuple[str, str]]:
    """
    递归查找JSON中所有的base64图片编码
    
    Args:
        data: JSON数据（可以是dict, list, 或其他类型）
        path: 当前路径（用于标识图片位置）
        images: 已找到的图片列表 [(path, base64_string), ...]
    
    Returns:
        图片列表，每个元素是 (path, base64_string) 元组
    """
    if images is None:
        images = []
    
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            
            # 检查是否是image_url字段
            if key == "image_url" and isinstance(value, dict):
                url = value.get("url", "")
                if url and isinstance(url, str) and url.startswith("data:image/"):
                    images.append((current_path, url))
            elif key == "url" and isinstance(value, str) and value.startswith("data:image/"):
                images.append((current_path, value))
            else:
                # 递归查找
                find_image_urls(value, current_path, images)
    
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            current_path = f"{path}[{idx}]"
            find_image_urls(item, current_path, images)
    
    return images


def set_value_by_path(data: Any, path: str, value: Any) -> None:
    """
    根据路径字符串设置JSON中的值
    
    Args:
        data: JSON数据
        path: 路径字符串，如 "devices[0].agents[0].rounds[0].model_prediction.messages[1].content[0].image_url.url"
        value: 要设置的值
    """
    # 处理路径，支持 [index] 和 .key 格式
    parts = []
    current = ""
    i = 0
    while i < len(path):
        if path[i] == '[':
            if current:
                parts.append(current)
                current = ""
            # 读取索引
            i += 1
            index_str = ""
            while i < len(path) and path[i] != ']':
                index_str += path[i]
                i += 1
            if i < len(path) and path[i] == ']':
                i += 1
            parts.append(int(index_str))
        elif path[i] == '.':
            if current:
                parts.append(current)
                current = ""
            i += 1
        else:
            current += path[i]
            i += 1
    if current:
        parts.append(current)
    
    # 导航到目标位置
    current = data
    for i, part in enumerate(parts[:-1]):
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                raise IndexError(f"List index out of range at path: {'.'.join(str(p) for p in parts[:i+1])}")
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                raise KeyError(f"Key not found: {part} at path: {'.'.join(str(p) for p in parts[:i+1])}")
            current = current[part]
    
    # 设置最后一个值
    last_part = parts[-1]
    if isinstance(last_part, int):
        if not isinstance(current, list) or last_part >= len(current):
            raise IndexError(f"List index out of range: {last_part}")
        current[last_part] = value
    else:
        if not isinstance(current, dict):
            raise TypeError(f"Cannot set key on non-dict at path: {'.'.join(str(p) for p in parts[:-1])}")
        current[last_part] = value


def extract_base64_image(data_url: str) -> Tuple[str, bytes]:
    """
    从data URL中提取图片格式和二进制数据
    
    Args:
        data_url: data URL字符串，格式如 "data:image/png;base64,..."
    
    Returns:
        (image_format, image_data) 元组，如 ("png", b"...")
    """
    match = re.match(r'data:image/(\w+);base64,(.+)', data_url)
    if not match:
        raise ValueError(f"Invalid data URL format: {data_url[:50]}...")
    
    image_format = match.group(1)
    base64_data = match.group(2)
    
    try:
        image_data = base64.b64decode(base64_data)
    except Exception as e:
        raise ValueError(f"Failed to decode base64: {e}")
    
    return image_format, image_data


def extract_action_from_path(data: Any, path: str) -> Optional[Dict]:
    """
    从JSON路径中提取该截图对应的操作信息
    
    Args:
        data: JSON根数据
        path: 图片路径，如 "devices[0].agents[0].rounds[0].model_prediction.messages[2].content[0].image_url"
    
    Returns:
        操作信息字典 {action, coordinate, text} 或 None
    """
    # 解析路径，找到对应的round
    # 路径格式: devices[X].agents[Y].rounds[Z].model_prediction.messages[...]
    match = re.match(r'devices\[(\d+)\]\.agents\[(\d+)\]\.rounds\[(\d+)\]', path)
    if not match:
        return None
    
    device_idx = int(match.group(1))
    agent_idx = int(match.group(2))
    round_idx = int(match.group(3))
    
    try:
        # 导航到对应的round
        round_data = data['devices'][device_idx]['agents'][agent_idx]['rounds'][round_idx]
        
        # 在messages中查找assistant的tool_calls
        messages = round_data.get('model_prediction', {}).get('messages', [])
        
        # 优先级的操作列表
        priority_actions = ['left_click', 'right_click', 'double_click', 'middle_click', 'type', 'scroll', 'drag']
        found_actions = []
        
        for msg in messages:
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                for tool_call in msg['tool_calls']:
                    func = tool_call.get('function', {})
                    if func.get('name') in ['computer_use', 'computer']:
                        try:
                            args = json.loads(func.get('arguments', '{}'))
                            action_data = {
                                'action': args.get('action'),
                                'coordinate': args.get('coordinate'),
                                'text': args.get('text')
                            }
                            found_actions.append(action_data)
                        except:
                            pass
        
        # 优先返回重要的操作
        for action in found_actions:
            if action.get('action') in priority_actions:
                return action
        
        # 如果没有重要操作，返回第一个找到的操作（如果有）
        if found_actions:
            return found_actions[0]
            
    except (KeyError, IndexError, TypeError):
        pass
    
    return None


def draw_action_marker(image: Image.Image, action_info: Dict) -> Image.Image:
    """
    在图片上绘制操作标记
    
    Args:
        image: PIL Image对象
        action_info: 操作信息 {action, coordinate, text}
    
    Returns:
        绘制后的Image对象
    """
    if not action_info or not action_info.get('coordinate'):
        return image
    
    # 创建可绘制对象
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA')
    draw = ImageDraw.Draw(image)
    
    action = action_info.get('action', '')
    coordinate = action_info.get('coordinate', [])
    text = action_info.get('text', '')
    
    if len(coordinate) < 2:
        return image
    
    x, y = coordinate[0], coordinate[1]
    
    # 根据操作类型选择颜色
    color_map = {
        'left_click': '#ff4444',
        'right_click': '#44ff44',
        'double_click': '#ff44ff',
        'middle_click': '#ffaa44',
        'type': '#44ff44',
        'key': '#44ff44',
        'scroll': '#4488ff'
    }
    
    color = color_map.get(action, '#ff4444')
    
    # 将hex颜色转换为RGB
    color_rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    
    try:
        # 尝试加载字体
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except:
        # 如果加载失败，使用默认字体
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    if action in ['left_click', 'right_click', 'double_click', 'middle_click']:
        # 绘制圆圈
        radius = 20
        draw.ellipse(
            [(x - radius, y - radius), (x + radius, y + radius)],
            outline=color_rgb,
            width=3
        )
        
        # 绘制十字准线
        crosshair_len = 12
        draw.line([(x, y - crosshair_len), (x, y + crosshair_len)], fill=color_rgb, width=2)
        draw.line([(x - crosshair_len, y), (x + crosshair_len, y)], fill=color_rgb, width=2)
        
        # 绘制标签
        label_map = {
            'left_click': '左键',
            'right_click': '右键',
            'double_click': '双击',
            'middle_click': '中键'
        }
        label = label_map.get(action, action)
        
        # 计算文本位置（在圆圈下方）
        bbox = draw.textbbox((0, 0), label, font=font_small)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        text_x = x - text_width // 2
        text_y = y + radius + 5
        
        # 绘制文本背景
        padding = 3
        draw.rectangle(
            [(text_x - padding, text_y - padding), 
             (text_x + text_width + padding, text_y + text_height + padding)],
            fill=(0, 0, 0, 180)
        )
        
        # 绘制文本
        draw.text((text_x, text_y), label, fill=color_rgb, font=font_small)
        
    elif action == 'type' and text:
        # 输入操作：显示输入的文本
        display_text = text[:30] + '...' if len(text) > 30 else text
        label = f"⌨️ {display_text}"
        
        # 计算文本位置
        bbox = draw.textbbox((0, 0), label, font=font_small)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # 确保不超出图片边界
        text_x = max(5, min(x - text_width // 2, image.width - text_width - 5))
        text_y = max(5, y - text_height - 10)
        
        # 绘制文本背景（绿色半透明）
        padding = 5
        draw.rectangle(
            [(text_x - padding, text_y - padding), 
             (text_x + text_width + padding, text_y + text_height + padding)],
            fill=(68, 255, 68, 200)
        )
        
        # 绘制文本（黑色）
        draw.text((text_x, text_y), label, fill=(0, 0, 0), font=font_small)
        
    elif action == 'scroll':
        # 滚动操作：绘制箭头
        # 在图片中央绘制向下的箭头
        center_x = image.width // 2
        center_y = image.height // 2
        arrow_size = 30
        
        # 绘制向下箭头
        arrow_points = [
            (center_x, center_y - arrow_size),  # 顶部
            (center_x, center_y + arrow_size),  # 底部
        ]
        draw.line(arrow_points, fill=color_rgb, width=4)
        
        # 绘制箭头尖端
        draw.polygon([
            (center_x, center_y + arrow_size),
            (center_x - 15, center_y + arrow_size - 15),
            (center_x + 15, center_y + arrow_size - 15)
        ], fill=color_rgb)
        
        # 绘制标签
        label = "滚动"
        bbox = draw.textbbox((0, 0), label, font=font_small)
        text_width = bbox[2] - bbox[0]
        text_x = center_x - text_width // 2
        text_y = center_y - arrow_size - 20
        
        # 文本背景
        padding = 3
        draw.rectangle(
            [(text_x - padding, text_y - padding), 
             (text_x + text_width + padding, text_y + bbox[3] - bbox[1] + padding)],
            fill=(0, 0, 0, 180)
        )
        draw.text((text_x, text_y), label, fill=color_rgb, font=font_small)
    
    return image


def process_json_file(json_file: str, output_dir: str = None) -> Dict:
    """
    处理单个JSON文件：提取图片并替换base64编码
    
    Args:
        json_file: JSON文件路径
        output_dir: 图片输出目录，如果为None则使用JSON文件同级的extracted_images目录
    
    Returns:
        统计信息字典
    """
    json_path = Path(json_file)
    print(f"\n处理文件: {json_file}")
    
    # 读取JSON文件
    with open(json_file, 'r', encoding='utf-8') as f:
        original_content = f.read()
        original_size = len(original_content)
        data = json.loads(original_content)
    
    # 查找所有图片
    images = find_image_urls(data)
    print(f"  找到 {len(images)} 张图片")
    
    if not images:
        return {
            "file": str(json_file),
            "original_size": original_size,
            "cleaned_size": original_size,
            "images_extracted": 0
        }
    
    # 确定输出目录（与JSON文件同级的extracted_images目录）
    if output_dir is None:
        output_dir = json_path.parent / "extracted_images"
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 计算相对路径（用于JSON中的路径引用）
    relative_path = os.path.relpath(output_dir, json_path.parent)
    
    # 备份原文件
    backup_file = str(json_file) + ".backup"
    if not os.path.exists(backup_file):
        with open(backup_file, 'w', encoding='utf-8') as f:
            f.write(original_content)
        print(f"  备份已创建: {backup_file}")
    
    # 提取并保存图片
    extracted_count = 0
    for idx, (path, data_url) in enumerate(images, 1):
        try:
            # 提取图片格式和数据
            image_format, image_data = extract_base64_image(data_url)
            
            # 加载图片
            image = Image.open(io.BytesIO(image_data))
            
            # 提取该图片对应的操作信息
            action_info = extract_action_from_path(data, path)
            
            # 如果有操作信息，在图片上绘制标记
            if action_info:
                image = draw_action_marker(image, action_info)
                print(f"  绘制标记: {action_info.get('action')} at {action_info.get('coordinate')}")
            
            # 生成文件名（包含JSON文件名前缀以区分来源）
            json_prefix = json_path.stem  # 不含扩展名的文件名
            safe_path = re.sub(r'[^\w\-_\.\[\]]', '_', path)
            filename = f"{json_prefix}_img{idx:04d}_{safe_path}.{image_format}"
            output_path = output_dir / filename
            
            # 保存图片（转换格式以确保兼容性）
            if image_format.lower() == 'jpeg' or image_format.lower() == 'jpg':
                # JPEG不支持透明度，转换为RGB
                if image.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', image.size, (255, 255, 255))
                    if image.mode == 'P':
                        image = image.convert('RGBA')
                    background.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
                    image = background
                image.save(output_path, 'JPEG', quality=95)
            else:
                # PNG等格式
                image.save(output_path, image_format.upper())
            
            # 替换JSON中的base64编码
            image_relative_path = os.path.join(relative_path, filename).replace('\\', '/')
            
            # 判断路径类型
            if path.endswith('.image_url'):
                target_path = path + '.url'
            elif path.endswith('.url'):
                target_path = path
            else:
                target_path = path
            
            set_value_by_path(data, target_path, image_relative_path)
            extracted_count += 1
            
        except Exception as e:
            print(f"  警告: 处理图片失败 {path}: {e}")
    
    # 保存修改后的JSON
    cleaned_content = json.dumps(data, ensure_ascii=False, indent=2)
    cleaned_size = len(cleaned_content)
    
    with open(json_file, 'w', encoding='utf-8') as f:
        f.write(cleaned_content)
    
    print(f"  ✅ 提取 {extracted_count} 张图片到 {output_dir}")
    print(f"  ✅ 文件大小: {original_size:,} → {cleaned_size:,} 字节 (减少 {(1 - cleaned_size/original_size)*100:.1f}%)")
    
    return {
        "file": str(json_file),
        "original_size": original_size,
        "cleaned_size": cleaned_size,
        "images_extracted": extracted_count
    }


def process_directory(directory: str, output_dir: str = None) -> List[Dict]:
    """
    批量处理目录下的所有JSON文件
    
    Args:
        directory: 目录路径
        output_dir: 图片输出目录，如果为None则使用每个JSON文件同级的extracted_images目录
    
    Returns:
        所有文件的统计信息列表
    """
    dir_path = Path(directory)
    json_files = list(dir_path.glob("*.json"))
    
    # 排除备份文件
    json_files = [f for f in json_files if not str(f).endswith('.backup')]
    
    print(f"在 {directory} 中找到 {len(json_files)} 个JSON文件")
    
    results = []
    for json_file in json_files:
        result = process_json_file(str(json_file), output_dir)
        results.append(result)
    
    return results


def main():
    if len(sys.argv) < 2:
        print("用法: python clean_image_data.py <json_file_or_directory> [output_dir]")
        print("\n参数:")
        print("  json_file_or_directory - 输入的JSON文件路径或目录路径")
        print("  output_dir             - 图片输出目录（可选，默认为JSON文件同级的extracted_images目录）")
        print("\n示例:")
        print("  python clean_image_data.py execution_record.json")
        print("  python clean_image_data.py test_data/")
        print("  python clean_image_data.py test_data/ ./all_images")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.exists(input_path):
        print(f"错误: 路径不存在: {input_path}")
        sys.exit(1)
    
    if os.path.isdir(input_path):
        results = process_directory(input_path, output_dir)
    else:
        results = [process_json_file(input_path, output_dir)]
    
    # 打印汇总
    print("\n" + "=" * 60)
    print("处理完成！汇总：")
    print("=" * 60)
    
    total_original = sum(r["original_size"] for r in results)
    total_cleaned = sum(r["cleaned_size"] for r in results)
    total_images = sum(r["images_extracted"] for r in results)
    
    print(f"处理文件数:     {len(results)}")
    print(f"提取图片总数:   {total_images}")
    print(f"原始总大小:     {total_original:,} 字节")
    print(f"清理后总大小:   {total_cleaned:,} 字节")
    if total_original > 0:
        print(f"总压缩比例:     {(1 - total_cleaned/total_original)*100:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
