"""
Qwen GUI Agent Prompt
专门为 Qwen VL 模型设计的 GUI Agent prompt
使用 1000x1000 归一化坐标系统
"""

# Qwen 使用的显示分辨率（归一化坐标）
QWEN_DISPLAY_WIDTH = 1000
QWEN_DISPLAY_HEIGHT = 1000


def get_qwen_computer_use_tool(display_width_px: int = 1000, display_height_px: int = 1000):
    """
    获取 Qwen 版本的 computer_use 工具定义
    使用 1000x1000 归一化坐标系统
    
    Args:
        display_width_px: 显示宽度（默认1000，归一化坐标）
        display_height_px: 显示高度（默认1000，归一化坐标）
    
    Returns:
        工具定义字典
    """
    return {
        "type": "function",
        "function": {
            "name": "computer_use",
            "description": f"""Execute computer actions on a Linux desktop environment.
The screen uses RELATIVE coordinates from (0,0) at top-left to (1000,1000) at bottom-right.
All coordinates must be in the range [0, 1000] for both X and Y (relative, not pixels).

Available actions:
- click: Click at relative position (x, y) with optional button (left/right/middle)
- double_click: Double-click at relative position (x, y)
- type: Type text string
- key: Press key(s) like "Return", "ctrl+s", "alt+F4"
- scroll: Scroll at position (x, y) with direction (up/down/left/right)
- drag: Drag from (start_x, start_y) to (end_x, end_y) in relative coordinates
- move: Move mouse to relative position (x, y)
- screenshot: Take a screenshot (no parameters needed)
- wait: Wait for specified seconds
- terminate: End the task with status and optional result text
- answer: Provide an answer or response (use for reporting results)""",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation of why you're performing this action and what you expect to happen (1-2 sentences)"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["click", "double_click", "type", "key", "scroll", "drag", "move", "screenshot", "wait", "terminate", "answer"],
                        "description": "The action to perform"
                    },
                    "x": {
                        "type": "integer",
                        "description": f"X coordinate (0-1000, relative)"
                    },
                    "y": {
                        "type": "integer",
                        "description": f"Y coordinate (0-1000, relative)"
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button for click action"
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type or answer content"
                    },
                    "key": {
                        "type": "string",
                        "description": "Key(s) to press, e.g., 'Return', 'ctrl+s', 'alt+F4'"
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "Scroll direction"
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Scroll amount or wait seconds"
                    },
                    "start_x": {
                        "type": "integer",
                        "description": f"Drag start X coordinate (0-{display_width_px})"
                    },
                    "start_y": {
                        "type": "integer",
                        "description": f"Drag start Y coordinate (0-{display_height_px})"
                    },
                    "end_x": {
                        "type": "integer",
                        "description": f"Drag end X coordinate (0-{display_width_px})"
                    },
                    "end_y": {
                        "type": "integer",
                        "description": f"Drag end Y coordinate (0-{display_height_px})"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["success", "failure"],
                        "description": "Task completion status for terminate action"
                    }
                },
                "required": ["reasoning", "action"]
            }
        }
    }


# Qwen 系统提示词 - 使用 0-1000 相对坐标（官方标准）
QWEN_SYSTEM_PROMPT = """You are a GUI automation agent that can control a Linux (Ubuntu) desktop computer.
You can see the screen through screenshots and perform actions using the computer_use function.
You are being called by a Plan Agent that coordinates multiple tasks - when you complete your task, you will return control back to it.

**OUTPUT FORMAT**: Always respond with valid JSON format using the computer_use tool.

**COORDINATE SYSTEM** (IMPORTANT):
- Use RELATIVE coordinates in the range [0, 1000] for both X and Y axes.
- (0, 0) is the TOP-LEFT corner of the screen.
- (1000, 1000) is the BOTTOM-RIGHT corner of the screen.
- (500, 500) is the CENTER of the screen.
- These are relative coordinates, NOT pixel coordinates.

**ACCURACY AND SELF-CORRECTION**:
- Your visual recognition may not be 100% accurate. This is normal.
- After clicking, you will see the result in the next screenshot.
- If you realize your click missed the target, ADJUST your coordinates and try again.
- Look at WHERE you clicked vs WHERE you intended to click, then correct the offset.
- Example: If you aimed for a file icon but clicked too far right, move your next click LEFT.

**TASK EXECUTION GUIDELINES**:
1. **Observe carefully**: Analyze the screenshot to understand the current screen state
2. **Plan your actions**: Think step-by-step to complete the task
3. **Explain reasoning**: ALWAYS provide a "reasoning" field explaining what you see and why you're taking this action
4. **Use precise coordinates**: Based on element positions in the screenshot (0-1000 range)
5. **Wait for UI response**: After clicking or typing, wait for the UI to update before next action
6. **Self-correct if needed**: If your previous click missed, adjust coordinates and retry
7. **Avoid repetition**: Don't repeat the same action multiple times - move forward in the task

**COMMON GUI OPERATIONS**:
- **Opening files**: Use double_click on the file icon in file manager to open it
- **Launching applications**: Single click on app icons in dock/launcher is usually sufficient
- **Selecting items**: Single click to select, double_click to open/activate
- **Closing windows**: Click the X button in the title bar, or use key action with "alt+F4"
- **Saving files**: Use key action with "ctrl+s" (example: {"action": "key", "key": "ctrl+s"})
- **Keyboard shortcuts**: Always use action="key" with combined keys like "ctrl+s", "alt+F4", etc.

**WHEN TO TERMINATE** (CRITICAL - READ THIS):
You MUST use the terminate action to return control to the Plan Agent when:
- ✓ Task is COMPLETE: Use terminate with status="success" and provide a brief summary
  Example: After opening document, reading content, typing answers, saving (Ctrl+S), and closing the app
- ✗ Task FAILED: Use terminate with status="failure" and explain why it cannot be completed
  Example: File not found, application crashed, or task is impossible

**DO NOT**:
- Continue operating after the task is done
- Repeat the same action endlessly
- Forget to save your work before closing
- Leave applications open after completing the task

**COORDINATE EXAMPLES** (relative 0-1000):
- Left dock panel (icons): approximately x=20-60
- Top panel: approximately y=0-30
- Files icon in dock: around (30, 150-200)
- LibreOffice Writer in dock: around (30, 250-300)
- Chrome icon in dock: around (30, 50-100)
- Center of screen: (500, 500)
- Top-right corner: close to (1000, 0)

**REMEMBER**: You are a tool being called by a Plan Agent. Always terminate when done to return control!
"""


# 用户首次提示词
QWEN_USER_PROMPT_FIRST = """Task: {instruction}

Look at the current screenshot and determine the first action needed to complete this task.
Remember: Use coordinates in the range [0, 1000] for both X and Y (normalized coordinate system).

Analyze the screen and call the computer_use function with:
1. "reasoning": Explain what you see and why you're taking this action
2. "action": The specific action to perform
3. Other required parameters for the action"""


# 用户后续提示词
QWEN_USER_PROMPT_CONTINUE = """Task: {instruction}

Continue the task based on the current screen state.
Remember: 
1. Always provide "reasoning" explaining what you observe and your next step
2. Use coordinates in the range [0, 1000] for both X and Y (normalized coordinate system).

Analyze what has changed since the last action and determine the next step."""


def convert_normalized_to_pixel(x: int, y: int, screen_width: int, screen_height: int) -> tuple:
    """
    将 1000x1000 归一化坐标转换为实际像素坐标
    
    Args:
        x: 归一化 X 坐标 (0-1000)
        y: 归一化 Y 坐标 (0-1000)
        screen_width: 实际屏幕宽度（像素）
        screen_height: 实际屏幕高度（像素）
    
    Returns:
        (pixel_x, pixel_y) 实际像素坐标
    """
    pixel_x = int(x * screen_width / QWEN_DISPLAY_WIDTH)
    pixel_y = int(y * screen_height / QWEN_DISPLAY_HEIGHT)
    return pixel_x, pixel_y


def convert_pixel_to_normalized(pixel_x: int, pixel_y: int, screen_width: int, screen_height: int) -> tuple:
    """
    将实际像素坐标转换为 1000x1000 归一化坐标
    
    Args:
        pixel_x: 实际 X 坐标（像素）
        pixel_y: 实际 Y 坐标（像素）
        screen_width: 实际屏幕宽度（像素）
        screen_height: 实际屏幕高度（像素）
    
    Returns:
        (normalized_x, normalized_y) 归一化坐标
    """
    normalized_x = int(pixel_x * QWEN_DISPLAY_WIDTH / screen_width)
    normalized_y = int(pixel_y * QWEN_DISPLAY_HEIGHT / screen_height)
    return normalized_x, normalized_y
