import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端，避免多线程问题

# from ui_tars.action_parser import parse_action_to_structure_output, parsing_response_to_pyautogui_code

# response = "Thought: Click the button\nAction: click(point='<point>200 300</point>')"
# original_image_width, original_image_height = 1920, 1080
# parsed_dict = parse_action_to_structure_output(
#     response,
#     factor=1000,
#     origin_resized_height=original_image_height,
#     origin_resized_width=original_image_width,
#     model_type="doubao"
# )
# print(parsed_dict)
# parsed_pyautogui_code = parsing_response_to_pyautogui_code(
#     responses=parsed_dict,
#     image_height=original_image_height,
#     image_width=original_image_width
# )
# print(parsed_pyautogui_code)
from io import BytesIO
import base64
import re
import os
import matplotlib.pyplot as plt
from PIL import Image
import numpy as np

def pil_to_base64(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")  # 你可以改成 "JPEG" 等格式
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def visualize_coordinates(screenshot_bytes, action_code, round_num, screenshot_dir="screenshots"):
    """
    可视化坐标并保存截图
    
    Args:
        screenshot_bytes: 截图的字节数据
        action_code: 动作代码字符串
        round_num: 轮次编号
        screenshot_dir: 截图保存目录
    """
    try:
        # 创建截图目录
        os.makedirs(screenshot_dir, exist_ok=True)
        
        # 解析动作代码中的坐标
        coordinates = extract_coordinates_from_action(action_code)
        if not coordinates:
            return
        
        # 将字节数据转换为PIL图像
        img = Image.open(BytesIO(screenshot_bytes))
        width, height = img.size
        
        # 创建matplotlib图像
        plt.figure(figsize=(12, 8))
        plt.imshow(img)
        
        # 标记所有坐标点
        colors = ['red', 'blue', 'green', 'yellow', 'purple']
        for i, (x, y) in enumerate(coordinates):
            color = colors[i % len(colors)]
            plt.scatter([x], [y], c=color, s=100, marker='o', 
                       label=f'Point {i+1}: ({x}, {y})', alpha=0.8)
            # 添加坐标文本
            plt.annotate(f'({x},{y})', (x, y), xytext=(5, 5), 
                        textcoords='offset points', fontsize=10, 
                        bbox=dict(boxstyle='round,pad=0.3', facecolor=color, alpha=0.7))
        
        plt.title(f'Round {round_num} - Coordinate Visualization', fontsize=14)
        plt.legend()
        plt.axis('off')
        
        # 保存图像
        output_path = os.path.join(screenshot_dir, f'round_{round_num}_coordinates.png')
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"Coordinate visualization saved to: {output_path}")
        
    except Exception as e:
        print(f"Error in coordinate visualization: {e}")

def extract_coordinates_from_action(action_code):
    """
    从动作代码中提取坐标
    
    Args:
        action_code: 动作代码字符串，如 "Action: click(point='<point>197 525</point>')"
    
    Returns:
        list: 坐标列表 [(x1, y1), (x2, y2), ...]
    """
    coordinates = []

    point_pattern = re.compile(
        r"(?:Action:\s*)?(click|left_double|right_single)\(point=['\"]<point>\s*(\d+)\s+(\d+)\s*</point>['\"]\)",
        re.IGNORECASE
    )

    for _, x_str, y_str in point_pattern.findall(action_code):
        x, y = int(x_str), int(y_str)
        coordinates.append((x, y))

    return coordinates

def save_screenshot(screenshot_bytes, round_num, screenshot_dir="screenshots"):
    """
    保存原始截图
    
    Args:
        screenshot_bytes: 截图的字节数据
        round_num: 轮次编号
        screenshot_dir: 截图保存目录
    """
    try:
        os.makedirs(screenshot_dir, exist_ok=True)
        
        # 保存原始截图
        output_path = os.path.join(screenshot_dir, f'round_{round_num}_screenshot.png')
        with open(output_path, 'wb') as f:
            f.write(screenshot_bytes)
        
        print(f"Screenshot saved to: {output_path}")
        
    except Exception as e:
        print(f"Error saving screenshot: {e}")
