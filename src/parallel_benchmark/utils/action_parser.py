# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: Apache-2.0
import re
import ast
import math

IMAGE_FACTOR = 28
MIN_PIXELS = 100 * 28 * 28
MAX_PIXELS = 16384 * 28 * 28
MAX_RATIO = 200


def convert_point_to_coordinates(text, is_answer=False):
    # 匹配 <bbox> 后面的四个数字
    pattern = r"<point>(\d+)\s+(\d+)</point>"

    def replace_match(match):
        x1, y1 = map(int, match.groups())
        x = (x1 + x1) // 2  # 使用截断取整
        y = (y1 + y1) // 2  # 使用截断取整
        if is_answer:
            return f"({x},{y})"  # 只返回 (x, y) 格式
        return f"({x},{y})"  # 返回带标签的格式

    # 去掉 [EOS] 并替换 <bbox> 坐标
    text = re.sub(r"\[EOS\]", "", text)
    return re.sub(pattern, replace_match, text).strip()


# 定义一个函数来解析每个 action
def parse_action(action_str):
    try:
        # 解析字符串为 AST 节点
        node = ast.parse(action_str, mode='eval')

        # 确保节点是一个表达式
        if not isinstance(node, ast.Expression):
            raise ValueError("Not an expression")

        # 获取表达式的主体
        call = node.body

        # 确保主体是一个函数调用
        if not isinstance(call, ast.Call):
            raise ValueError("Not a function call")

        # 获取函数名
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            func_name = call.func.attr
        else:
            func_name = None

        # 获取关键字参数
        kwargs = {}
        for kw in call.keywords:
            key = kw.arg
            # 处理不同类型的值，这里假设都是常量
            if isinstance(kw.value, ast.Constant):
                value = kw.value.value
            elif isinstance(kw.value, ast.Str):  # 兼容旧版本 Python
                value = kw.value.s
            else:
                value = None
            kwargs[key] = value

        return {'function': func_name, 'args': kwargs}

    except Exception as e:
        print(f"Failed to parse action '{action_str}': {e}")
        return None


def escape_single_quotes(text):
    # 匹配未转义的单引号（不匹配 \\'）
    pattern = r"(?<!\\)'"
    return re.sub(pattern, r"\\'", text)


def round_by_factor(number: int, factor: int) -> int:
    """Returns the closest integer to 'number' that is divisible by 'factor'."""
    return round(number / factor) * factor


def ceil_by_factor(number: int, factor: int) -> int:
    """Returns the smallest integer greater than or equal to 'number' that is divisible by 'factor'."""
    return math.ceil(number / factor) * factor


def floor_by_factor(number: int, factor: int) -> int:
    """Returns the largest integer less than or equal to 'number' that is divisible by 'factor'."""
    return math.floor(number / factor) * factor


def linear_resize(height: int,
                  width: int,
                  factor: int = IMAGE_FACTOR,
                  min_pixels: int = MIN_PIXELS,
                  max_pixels: int = MAX_PIXELS) -> tuple[int, int]:
    if width * height > max_pixels:
        """
        如果图片超过/低于像素限制，则计算一个缩放因子resize_factor，使图片的像素数缩小到等于或小于max_pixels。这个缩放因子是通过开平方根计算的，确保纵横比保持不变,这样原始的相对坐标可以不经转换直接复用
        """
        resize_factor = math.sqrt(max_pixels / (width * height))
        width, height = int(width * resize_factor), int(height * resize_factor)
    if width * height < min_pixels:
        resize_factor = math.sqrt(min_pixels / (width * height))
        width, height = math.ceil(width * resize_factor), math.ceil(
            height * resize_factor)

    return height, width


def smart_resize(height: int,
                 width: int,
                 factor: int = IMAGE_FACTOR,
                 min_pixels: int = MIN_PIXELS,
                 max_pixels: int = MAX_PIXELS) -> tuple[int, int]:
    """
    Rescales the image so that the following conditions are met:

    1. Both dimensions (height and width) are divisible by 'factor'.

    2. The total number of pixels is within the range ['min_pixels', 'max_pixels'].

    3. The aspect ratio of the image is maintained as closely as possible.
    """
    if max(height, width) / min(height, width) > MAX_RATIO:
        raise ValueError(
            f"absolute aspect ratio must be smaller than {MAX_RATIO}, got {max(height, width) / min(height, width)}"
        )
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(height / beta, factor)
        w_bar = floor_by_factor(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(height * beta, factor)
        w_bar = ceil_by_factor(width * beta, factor)
    return h_bar, w_bar


def parse_action_to_structure_output(text,
                                     factor,
                                     origin_resized_height,
                                     origin_resized_width,
                                     model_type="qwen25vl",
                                     max_pixels=16384 * 28 * 28,
                                     min_pixels=100 * 28 * 28):
    text = text.strip()
    
    # 清理可能残留的 XML 标签（Claude 有时会输出不完整的标签）
    # 移除不完整的 <point> 标签
    text = re.sub(r'<point>\s*\d+(?!\s+\d+\s*</point>)', '', text)
    # 移除不完整的 </point> 标签
    text = re.sub(r'</point>', '', text)


    if "<point>" in text:
        text = convert_point_to_coordinates(text)
    if "start_point=" in text:
        text = text.replace("start_point=", "start_box=")
    if "end_point=" in text:
        text = text.replace("end_point=", "end_box=")
    if "point=" in text:
        text = text.replace("point=", "start_box=")

    if model_type == "qwen25vl":
        smart_resize_height, smart_resize_width = smart_resize(
            origin_resized_height,
            origin_resized_width,
            factor=IMAGE_FACTOR,
            min_pixels=min_pixels,
            max_pixels=max_pixels)

    # 正则表达式匹配 Action 字符串
    if text.startswith("Thought:"):
        thought_pattern = r"Thought: (.+?)(?=\s*Action: |$)"
        thought_hint = "Thought: "
    elif text.startswith("Reflection:"):
        thought_pattern = r"Reflection: (.+?)Action_Summary: (.+?)(?=\s*Action: |$)"
        thought_hint = "Reflection: "
    elif text.startswith("Action_Summary:"):
        thought_pattern = r"Action_Summary: (.+?)(?=\s*Action: |$)"
        thought_hint = "Action_Summary: "
    else:
        thought_pattern = r"Thought: (.+?)(?=\s*Action: |$)"
        thought_hint = "Thought: "
    reflection, thought = None, None
    thought_match = re.search(thought_pattern, text, re.DOTALL)
    if thought_match:
        if len(thought_match.groups()) == 1:
            thought = thought_match.group(1).strip()
        elif len(thought_match.groups()) == 2:
            thought = thought_match.group(2).strip()
            reflection = thought_match.group(1).strip()
    # 如果thought和reflection都为None，且没有Action，则说明模型直接输出的Action，直接将模型输出转换为Action
    if thought == None and reflection == None:
        if "Action:" not in text:
            action_str = text
    # assert "Action:" in text
    action_str = text.split("Action: ")[-1]
    
    # 检测并转换 Anthropic XML 格式的 function_calls
    # 格式: <function_calls><invoke name="computer"><parameter name="action">click</parameter><parameter name="coordinate">[x, y]</parameter></invoke></function_calls>
    if "<function_calls>" in action_str and "<invoke" in action_str:
        try:
            # 提取 action 和 coordinate
            import xml.etree.ElementTree as ET
            # 清理可能的额外字符
            xml_start = action_str.find("<function_calls>")
            xml_end = action_str.find("</function_calls>") + len("</function_calls>")
            if xml_start >= 0 and xml_end > xml_start:
                xml_str = action_str[xml_start:xml_end]
                root = ET.fromstring(xml_str)
                
                # 查找 action 和 coordinate 参数
                action_type = None
                coordinate = None
                for param in root.findall(".//parameter"):
                    if param.get("name") == "action":
                        action_type = param.text
                    elif param.get("name") == "coordinate":
                        coord_text = param.text.strip()
                        # 解析 [x, y] 格式
                        if coord_text.startswith("[") and coord_text.endswith("]"):
                            coord_text = coord_text[1:-1]
                            parts = coord_text.split(",")
                            if len(parts) == 2:
                                coordinate = f"({parts[0].strip()}, {parts[1].strip()})"
                
                # 转换为标准格式
                if action_type and coordinate:
                    action_str = f"{action_type}(start_box='{coordinate}')"
                    print(f"[INFO] Converted Anthropic XML format to: {action_str}")
        except Exception as e:
            print(f"[WARN] Failed to parse Anthropic XML format: {e}")
    
    # 清理 markdown 代码块标记
    action_str = action_str.strip()
    # 移除开头的代码块标记（```python 或 ```）
    if action_str.startswith("```"):
        lines = action_str.split("\n")
        if lines[0].strip() in ["```python", "```"]:
            lines = lines[1:]  # 移除第一行
        action_str = "\n".join(lines)
    # 移除结尾的代码块标记
    if action_str.endswith("```"):
        action_str = action_str[:-3].strip()
    # 移除可能的尾部闭合标记 ```)
    if action_str.endswith("```)"):
        action_str = action_str[:-4].strip()
    
    # 修复：如果 action_str 包含多行 Markdown 文本，提取最后一个有效的函数调用
    # 例如：LLM 输出 "### Summary...\n\nfinished(...)" 时，只取 "finished(...)"
    if "\n" in action_str and any(func in action_str for func in ["finished(", "click(", "type(", "scroll(", "wait(", "hotkey("]):
        # 查找所有可能的函数调用行
        lines = action_str.split("\n")
        valid_actions = []
        for line in lines:
            line_stripped = line.strip()
            # 移除反引号包裹（如 `finished(...)`）
            line_stripped = line_stripped.strip('`')
            # 匹配以函数名开头的行（忽略 Markdown、解释性文字）
            if re.match(r'^(finished|click|type|scroll|wait|hotkey|drag)\s*\(', line_stripped):
                valid_actions.append(line_stripped)
        
        # 如果找到有效的函数调用，用最后一个替换 action_str
        if valid_actions:
            action_str = valid_actions[-1]
            print(f"[INFO] Extracted action from multi-line response: {action_str[:100]}...")

    tmp_all_action = action_str.split(")\n\n")
    all_action = []
    for action_str in tmp_all_action:
        if "type(content" in action_str:
            if not action_str.strip().endswith(")"):
                action_str = action_str.strip() + ")"
            # 正则表达式匹配 content 中的字符串并转义单引号
            def escape_quotes(match):
                content = match.group(1)  # 获取 content 的值
                return content

            # 使用正则表达式进行替换
            pattern = r"type\(content='(.*?)'\)"  # 匹配 type(content='...')
            if re.search(pattern, action_str):  # 检查是否有匹配项
                content = re.sub(pattern, escape_quotes, action_str)
            else:
                raise ValueError("Pattern not found in the input string.")

            # 处理字符串
            action_str = escape_single_quotes(content)
            action_str = "type(content='" + action_str + "')"
        if not action_str.strip().endswith(")"):
            action_str = action_str.strip() + ")"
        all_action.append(action_str)

    parsed_actions = [
        parse_action(action.replace("\n", "\\n").lstrip())
        for action in all_action
    ]
    actions = []
    for action_instance, raw_str in zip(parsed_actions, all_action):
        if action_instance == None:
            print(f"Action can't parse: {raw_str}")
            raise ValueError(f"Action can't parse: {raw_str}")
        action_type = action_instance["function"]
        params = action_instance["args"]

        # import pdb; pdb.set_trace()
        action_inputs = {}
        for param_name, param in params.items():
            if param == "": continue
            param = param.lstrip()  # 去掉引号和多余的空格
            # 处理start_box或者end_box参数格式 '<bbox>x1 y1 x2 y2</bbox>'
            action_inputs[param_name.strip()] = param

            if "start_box" in param_name or "end_box" in param_name:
                ori_box = param
                # 修复坐标格式：将空格分隔改为逗号分隔
                # 例如: '326 193' -> '326, 193'
                ori_box = re.sub(r'(\d+)\s+(\d+)', r'\1, \2', ori_box)
                
                # Remove parentheses and split the string by commas
                numbers = ori_box.replace("(", "").replace(")", "").split(",")

                # Convert to float and scale by 1000
                # Qwen2.5vl output absolute coordinates, qwen2vl output relative coordinates
                if model_type == "qwen25vl":
                    float_numbers = []
                    for num_idx, num in enumerate(numbers):
                        num = float(num)
                        if (num_idx + 1) % 2 == 0:
                            float_numbers.append(
                                float(num / smart_resize_height))
                        else:
                            float_numbers.append(
                                float(num / smart_resize_width))
                else:
                    float_numbers = [float(num) / factor for num in numbers]

                if len(float_numbers) == 2:
                    float_numbers = [
                        float_numbers[0], float_numbers[1], float_numbers[0],
                        float_numbers[1]
                    ]
                action_inputs[param_name.strip()] = str(float_numbers)

        # import pdb; pdb.set_trace()
        actions.append({
            "reflection": reflection,
            "thought": thought,
            "action_type": action_type,
            "action_inputs": action_inputs,
            "text": text
        })
    return actions

def generate_pyautogui_hotkey_code(keys: list) -> str:
    """pyautogui的hotkey实现有问题，改成等价的实现
    To make pressing hotkeys or keyboard shortcuts convenient, the hotkey() can be passed several key strings which will be pressed down in order, and then released in reverse order. This code:

    >>> pyautogui.hotkey('ctrl', 'shift', 'esc')
    …is equivalent to this code:

    >>> pyautogui.keyDown('ctrl')
    >>> pyautogui.keyDown('shift')
    >>> pyautogui.keyDown('esc')
    >>> pyautogui.keyUp('esc')
    >>> pyautogui.keyUp('shift')
    >>> pyautogui.keyUp('ctrl')
    输入：按键列表
    输出：pyautogui代码字符串
    """
    if not keys:
        return ""
    
    code_lines = []
    
    # 按顺序按下所有键
    for key in keys:
        code_lines.append(f"pyautogui.keyDown({repr(key)})")
    
    # 反序释放所有键
    for key in reversed(keys):
        code_lines.append(f"pyautogui.keyUp({repr(key)})")
    
    return "\n".join(code_lines)


def parsing_response_to_pyautogui_code(responses,
                                       image_height: int,
                                       image_width: int,
                                       input_swap: bool = True) -> str:
    '''
    将M模型的输出解析为OSWorld中的action，生成pyautogui代码字符串
    参数:
        response: 包含模型输出的字典，结构类似于：
        {
            "action_type": "hotkey",
            "action_inputs": {
                "hotkey": "v ctrl",
                "start_box": None,
                "end_box": None
            }
        }
    返回:
        生成的pyautogui代码字符串
    '''

    pyautogui_code = f"import pyautogui\nimport time\n"
    if isinstance(responses, dict):
        responses = [responses]
    for response_id, response in enumerate(responses):
        if "observation" in response:
            observation = response["observation"]
        else:
            observation = ""

        if "thought" in response:
            thought = response["thought"]
        else:
            thought = ""

        if response_id == 0:
            pyautogui_code += f"'''\nObservation:\n{observation}\n\nThought:\n{thought}\n'''\n"
        else:
            pyautogui_code += f"\ntime.sleep(1)\n"

        action_dict = response
        action_type = action_dict.get("action_type")
        action_inputs = action_dict.get("action_inputs", {})

        if action_type == "hotkey":
            # Parsing hotkey action
            if "key" in action_inputs:
                hotkey = action_inputs.get("key", "")
            else:
                hotkey = action_inputs.get("hotkey", "")

            if hotkey == "arrowleft":
                hotkey = "left"

            elif hotkey == "arrowright":
                hotkey = "right"

            elif hotkey == "arrowup":
                hotkey = "up"

            elif hotkey == "arrowdown":
                hotkey = "down"

            if hotkey:
                # Handle other hotkeys
                keys = hotkey.split()  # Split the keys by space
                convert_keys = []
                for key in keys:
                    if key == "space":
                        key = ' '
                    convert_keys.append(key)
                # 使用 generate_pyautogui_hotkey_code 替代 pyautogui.hotkey()
                pyautogui_code += f"\n{generate_pyautogui_hotkey_code(convert_keys)}"

        elif action_type in ["press", "keydown"]:
            # Parsing press action
            if "key" in action_inputs:
                key_to_press = action_inputs.get("key", "")
            else:
                key_to_press = action_inputs.get("press", "")

            if key_to_press == "arrowleft":
                key_to_press = "left"

            elif key_to_press == "arrowright":
                key_to_press = "right"

            elif key_to_press == "arrowup":
                key_to_press = "up"

            elif key_to_press == "arrowdown":
                key_to_press = "down"

            elif key_to_press == "space":
                key_to_press = " "

            if key_to_press:
                # Simulate pressing a single key
                pyautogui_code += f"\npyautogui.keyDown({repr(key_to_press)})"

        elif action_type in ["release", "keyup"]:
            # Parsing press action
            if "key" in action_inputs:
                key_to_press = action_inputs.get("key", "")
            else:
                key_to_press = action_inputs.get("press", "")

            if key_to_press == "arrowleft":
                key_to_press = "left"

            elif key_to_press == "arrowright":
                key_to_press = "right"

            elif key_to_press == "arrowup":
                key_to_press = "up"

            elif key_to_press == "arrowdown":
                key_to_press = "down"

            elif key_to_press == "space":
                key_to_press = " "

            if key_to_press:
                # Simulate pressing a single key
                pyautogui_code += f"\npyautogui.keyUp({repr(key_to_press)})"

        elif action_type == "type":
            # Parsing typing action - 直接使用 pyautogui.write() 避免 clipboard 问题
            # 兼容 Qwen 的 'text' 字段和 OSWorld 的 'content' 字段
            content = action_inputs.get("content", "") or action_inputs.get("text", "")
            content = escape_single_quotes(content)
            stripped_content = content
            if content.endswith("\n") or content.endswith("\\n"):
                stripped_content = stripped_content.rstrip("\\n").rstrip("\n")
            if content:
                # 始终使用 pyautogui.write() 而不是 pyperclip（VM 环境没有 clipboard 支持）
                # interval=0.08: 增大字符间隔，缓解 VNC 环境下键盘事件丢失（防御性措施）
                # time.sleep(1.0): 等待输入完成后 UI 响应
                pyautogui_code += f"\npyautogui.write('{stripped_content}', interval=0.08)"
                pyautogui_code += f"\ntime.sleep(1.0)\n"
                if content.endswith("\n") or content.endswith("\\n"):
                    pyautogui_code += f"\npyautogui.press('enter')"

        elif action_type in ["drag", "select"]:
            # Parsing drag or select action based on start and end_boxes
            start_box = action_inputs.get("start_box")
            end_box = action_inputs.get("end_box")
            if start_box and end_box:
                # 修复坐标格式
                start_box = str(start_box)
                start_box = re.sub(r'\((\d+)\s+(\d+)\)', r'(\1, \2)', start_box)
                start_box = re.sub(r'\((\d+)\s+(\d+)\s+(\d+)\s+(\d+)\)', r'(\1, \2, \3, \4)', start_box)
                end_box = str(end_box)
                end_box = re.sub(r'\((\d+)\s+(\d+)\)', r'(\1, \2)', end_box)
                end_box = re.sub(r'\((\d+)\s+(\d+)\s+(\d+)\s+(\d+)\)', r'(\1, \2, \3, \4)', end_box)
                
                x1, y1, x2, y2 = eval(
                    start_box)  # Assuming box is in [x1, y1, x2, y2]
                sx = round(float((x1 + x2) / 2) * image_width, 3)
                sy = round(float((y1 + y2) / 2) * image_height, 3)
                x1, y1, x2, y2 = eval(
                    end_box)  # Assuming box is in [x1, y1, x2, y2]
                ex = round(float((x1 + x2) / 2) * image_width, 3)
                ey = round(float((y1 + y2) / 2) * image_height, 3)
                pyautogui_code += (
                    f"\npyautogui.moveTo({sx}, {sy})\n"
                    f"\npyautogui.dragTo({ex}, {ey}, duration=1.0)\n")

        elif action_type == "scroll":
            # Parsing scroll action
            start_box = action_inputs.get("start_box")
            if start_box:
                # 修复坐标格式
                start_box = str(start_box)
                start_box = re.sub(r'\((\d+)\s+(\d+)\)', r'(\1, \2)', start_box)
                start_box = re.sub(r'\((\d+)\s+(\d+)\s+(\d+)\s+(\d+)\)', r'(\1, \2, \3, \4)', start_box)
                
                x1, y1, x2, y2 = eval(
                    start_box)  # Assuming box is in [x1, y1, x2, y2]
                x = round(float((x1 + x2) / 2) * image_width, 3)
                y = round(float((y1 + y2) / 2) * image_height, 3)

                # # 先点对应区域，再滚动
                # pyautogui_code += f"\npyautogui.click({x}, {y}, button='left')"
            else:
                x = None
                y = None
            direction = action_inputs.get("direction", "")

            if x == None:
                if "up" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(5)"
                elif "down" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(-5)"
            else:
                if "up" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(5, x={x}, y={y})"
                elif "down" in direction.lower():
                    pyautogui_code += f"\npyautogui.scroll(-5, x={x}, y={y})"

        elif action_type == "key":
            # Parsing key action for keyboard shortcuts like 'ctrl+s'
            key_combo = action_inputs.get("key", "")
            if key_combo:
                if "+" in key_combo:
                    # 组合键如 'ctrl+s'
                    keys = key_combo.split("+")
                    keys = [k.strip() for k in keys]
                    pyautogui_code += f"\n{generate_pyautogui_hotkey_code(keys)}"
                else:
                    # 单个按键
                    pyautogui_code += f"\npyautogui.press('{key_combo}')"

        elif action_type in [
                "click", "left_click", "left_single", "left_double", "double_click", "right_click", "right_single", "hover"
        ]:
            # Parsing mouse click actions
            start_box = action_inputs.get("start_box")
            start_box = str(start_box)
            if start_box:
                # 修复 Claude 输出的坐标格式：(272 225) -> (272, 225)
                # 匹配括号内用空格分隔的数字，添加逗号
                start_box = re.sub(r'\((\d+)\s+(\d+)\)', r'(\1, \2)', start_box)
                # 也处理四个数字的情况：(x1 y1 x2 y2) -> (x1, y1, x2, y2)
                start_box = re.sub(r'\((\d+)\s+(\d+)\s+(\d+)\s+(\d+)\)', r'(\1, \2, \3, \4)', start_box)
                
                start_box = eval(start_box)
                if len(start_box) == 4:
                    x1, y1, x2, y2 = start_box  # Assuming box is in [x1, y1, x2, y2]
                elif len(start_box) == 2:
                    x1, y1 = start_box
                    x2 = x1
                    y2 = y1
                x = round(float((x1 + x2) / 2) * image_width, 3)
                y = round(float((y1 + y2) / 2) * image_height, 3)
                if action_type == "left_single" or action_type == "click" or action_type == "left_click":
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='left')"
                elif action_type == "left_double" or action_type == "double_click":
                    # 使用两次快速单击代替 doubleClick,在 VNC 环境中更可靠
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='left')"
                    pyautogui_code += f"\ntime.sleep(0.1)"
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='left')"
                    pyautogui_code += f"\ntime.sleep(1.0)  # Wait for file to open"
                elif action_type == "right_single" or action_type == "right_click":
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='right')"
                elif action_type == "hover":
                    pyautogui_code += f"\npyautogui.moveTo({x}, {y})"

        elif action_type in ["finished"]:
            pyautogui_code = f"DONE"

        else:
            pyautogui_code += f"\n# Unrecognized action type: {action_type}"

    return pyautogui_code


def add_box_token(input_string):
    # Step 1: Split the string into individual actions
    if "Action: " in input_string and "start_box=" in input_string:
        suffix = input_string.split("Action: ")[0] + "Action: "
        actions = input_string.split("Action: ")[1:]
        processed_actions = []
        for action in actions:
            action = action.strip()
            # Step 2: Extract coordinates (start_box or end_box) using regex
            coordinates = re.findall(
                r"(start_box|end_box)='\((\d+),\s*(\d+)\)'", action)

            updated_action = action  # Start with the original action
            for coord_type, x, y in coordinates:
                # Convert x and y to integers
                updated_action = updated_action.replace(
                    f"{coord_type}='({x},{y})'",
                    f"{coord_type}='<|box_start|>({x},{y})<|box_end|>'")
            processed_actions.append(updated_action)

        # Step 5: Reconstruct the final string
        final_string = suffix + "\n\n".join(processed_actions)
    else:
        final_string = input_string
    return final_string