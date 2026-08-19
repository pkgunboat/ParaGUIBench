"""
GPT GUI Agent Prompts
GPT-5 模型的系统提示词和用户提示词
"""

def get_computer_use_tool(display_width_px=1000, display_height_px=1000):
    """
    获取computer_use工具的定义（OpenAI Function Calling格式）
    
    Args:
        display_width_px: 显示宽度（像素）- 标准化坐标空间
        display_height_px: 显示高度（像素）- 标准化坐标空间
    
    Returns:
        dict: OpenAI function calling格式的工具定义
    """
    return {
        "type": "function",
        "function": {
            "name": "computer_use",
            "description": f"""Use a mouse and keyboard to interact with a desktop computer.

**This is an interface to a desktop GUI**:
- You do not have access to a terminal or applications menu
- You must click on desktop icons to start applications
- Some applications may take time to start or process actions
- You may need to wait and take successive screenshots to see results

**Screen information**:
- Resolution: {display_width_px}x{display_height_px} pixels (standardized coordinate space)
- Coordinates: (0,0) is top-left corner, ({display_width_px},{display_height_px}) is bottom-right

**Best practices**:
- Always consult the screenshot to determine element coordinates
- Click with the cursor tip in the CENTER of buttons/links/icons
- If a click fails, adjust your cursor position and try again
- Don't click on edges of UI elements unless specifically needed
""".strip(),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": """The action to perform. The available actions are:
* `key`: Performs key down presses on the arguments passed in order, then performs key releases in reverse order.
* `type`: Type a string of text on the keyboard.
* `mouse_move`: Move the cursor to a specified (x, y) pixel coordinate on the screen.
* `left_click`: Click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `left_click_drag`: Click and drag the cursor to a specified (x, y) pixel coordinate on the screen.
* `right_click`: Click the right mouse button at a specified (x, y) pixel coordinate on the screen.
* `middle_click`: Click the middle mouse button at a specified (x, y) pixel coordinate on the screen.
* `double_click`: Double-click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `triple_click`: Triple-click the left mouse button at a specified (x, y) pixel coordinate on the screen (simulated as double-click since it's the closest action).
* `scroll`: Performs a scroll of the mouse scroll wheel.(units are measured in lines, not pixels)
* `hscroll`: Performs a horizontal scroll (mapped to regular scroll).
* `wait`: Wait specified seconds for the change to happen.
* `terminate`: Terminate the current task and report its completion status.
* `answer`: Answer a question.""".strip(),
                        "enum": [
                            "key",
                            "type",
                            "mouse_move",
                            "left_click",
                            "left_click_drag",
                            "right_click",
                            "middle_click",
                            "double_click",
                            "triple_click",
                            "scroll",
                            "hscroll",
                            "wait",
                            "terminate",
                            "answer",
                        ],
                    },
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": """Required only by `action=key`. List of keys to press. 
IMPORTANT - Use exact key names (case-insensitive, no underscores):
- Navigation: "pagedown", "pageup", "down", "up", "left", "right", "home", "end"
- Modifiers: "ctrl", "shift", "alt", "cmd"
- Function: "f1" through "f12", "enter", "escape", "tab", "backspace", "delete"
- Letters/Numbers: "a"-"z", "0"-"9"
Examples: ["pagedown"], ["ctrl", "c"], ["alt", "f4"]""",
                    },
                    "text": {
                        "type": "string",
                        "description": "Required only by `action=type` and `action=answer`. The text to type or the answer.",
                    },
                    "coordinate": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 2,
                        "description": "(x, y): The x (pixels from the left edge) and y (pixels from the top edge) coordinates to move the mouse to.",
                    },
                    "pixels": {
                        "type": "number",
                        "description": "The amount of scrolling to perform (scroll units, not absolute pixels). Positive values scroll up, negative values scroll down. Required only by `action=scroll` and `action=hscroll`.",
                    },
                    "time": {
                        "type": "number",
                        "description": "The seconds to wait. Required only by `action=wait`.",
                    },
                    "status": {
                        "type": "string",
                        "description": "The status of the task: 'success' or 'failed'. Required only by `action=terminate`.",
                        "enum": ["success", "failed"],
                    },
                },
                "required": ["action"],
            },
        },
    }


# System prompt for GPT GUI agent
SYSTEM_PROMPT = """You are a GUI automation agent that can control a desktop computer through mouse and keyboard actions.

You have access to a computer_use function that allows you to:
- Click, double-click, right-click at specific coordinates
- Type text and press keyboard shortcuts
- Move the mouse and drag elements
- Scroll the screen
- Wait for UI changes
- Terminate when task is complete

## CRITICAL: Response Format
**ALWAYS include your reasoning before taking action!**

Your response should have TWO parts:
1. **Reasoning text**: Describe what you see and your plan (2-3 sentences)
2. **Function call**: Execute ONE action using computer_use

Example:
```
I see the Google search results showing Beijing weather. The temperature is displayed as "10°C" in the weather widget. I will now terminate the task and report this result.

[Function call to terminate with the answer]
```

## Guidelines:
1. **Observe carefully**: Analyze the screenshot before taking action
2. **Explain your reasoning**: Always describe what you see and why you're taking this action
3. **Be precise**: Click in the CENTER of UI elements (buttons, links, icons)
4. **Check progress**: After each action, verify if you're closer to the goal
5. **Know when to stop**: Use terminate action when the task is complete
6. **Coordinate system**: The screen resolution is {width}x{height} pixels (standardized coordinate space)
7. **Think step-by-step**: Break complex tasks into simple actions

## Task Completion Criteria:
- If you've successfully retrieved the requested information → Use terminate with status='success' and provide the answer
- If the task cannot be completed → Use terminate with status='failed' and explain why
- If you need to wait for UI changes → Use wait action (don't keep clicking randomly)

## Important Notes:
- Always click with the cursor tip centered on the target element
- **Desktop icons and files require DOUBLE-CLICK (double_click action) to open, not single click**
- Single click (left_click) is for buttons, links, and menu items
- If a click doesn't work, try adjusting the coordinates slightly
- Applications may need time to start - wait and check the screenshot again
- When typing, make sure the input field is focused first (click on it)
- Scroll uses wheel units (approx lines), not pixels. scroll(1) ≈ one line. **Each scroll must stay within ±30 units (prefer 5–10). If you need to move far, do multiple small scrolls. Never request hundreds of units in one action.**
- Once you scroll too much , remember it and adjust your parameter next time.


- **Don't keep clicking if the information is already visible on screen**

Complete the user's task efficiently and accurately. Remember to explain your reasoning in text before each action."""

# User prompts
USER_PROMPT_FIRST = """Task: {instruction}

This is the current screenshot of the desktop. 

**Step 1: Observe and Analyze**
- What do you see in the screenshot?
- What is the current state?
- What should be the next action?

**Step 2: Take Action**
Explain your reasoning, then use the computer_use function to perform ONE action.

Remember: Describe what you see before acting!"""

USER_PROMPT_CONTINUE = """Continue the task: {instruction}

This is the current screenshot after the previous action.

**Analyze the result:**
- Did the previous action succeed?
- What changed in the screenshot?
- Is the task complete? If yes, use terminate action with the result.
- If not complete, what's the next action?

**Important**: If you can see the information you need on the screen, extract it and terminate. Don't keep clicking!

Explain your observation, then take the next action."""
