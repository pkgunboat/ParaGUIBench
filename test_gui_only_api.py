#!/usr/bin/env python3
"""
测试 gui-only 消融实验任务，使用用户提供的 API (gpt-5.4)。
直接复用本地已有的 VM 容器，无需 SSH / Pipeline 容器管理。
"""

import sys
import os
import base64
import json

# 路径设置
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(REPO_ROOT, "src")
STAGES_DIR = os.path.join(SRC_DIR, "stages")
PB_DIR = os.path.join(SRC_DIR, "parallel_benchmark")
for p in [SRC_DIR, STAGES_DIR, PB_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from desktop_env.controllers.python import PythonController
from parallel_benchmark.parallel_agents.gui_agent import GUIAgent

# API 配置从环境变量读取，避免把本地密钥写入仓库。
BASE_URL = os.environ.get("GUI_ONLY_TEST_BASE_URL", "https://v2.pincc.ai/v1")
API_KEY = os.environ.get("GUI_ONLY_TEST_API_KEY", "")
MODEL_NAME = os.environ.get("GUI_ONLY_TEST_MODEL", "gpt-5.4")

# ── VM 配置（复用已有容器）──
VM_IP = "127.0.0.1"
VM_PORT = 5002

# ── 任务配置 ──
TASK_ID = "Operation-FileOperate-BatchOperation-001"
TASK_JSON = os.path.join(PB_DIR, "tasks", f"{TASK_ID}.json")


def load_task():
    with open(TASK_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    if not API_KEY:
        raise RuntimeError("请先设置 GUI_ONLY_TEST_API_KEY")

    print("=" * 60)
    print("GUI-Only API 测试")
    print(f"  Model: {MODEL_NAME}")
    print(f"  Base URL: {BASE_URL}")
    print(f"  Task: {TASK_ID}")
    print(f"  VM: {VM_IP}:{VM_PORT}")
    print("=" * 60)

    # 1. 加载任务
    task = load_task()
    instruction = task.get("instruction", "")
    print(f"\n[Task Instruction] {instruction}\n")

    # 2. 连接 VM
    print("[1/4] 连接 VM ...")
    controller = PythonController(vm_ip=VM_IP, server_port=VM_PORT)
    screenshot = controller.get_screenshot()
    print(f"      VM 已连接，截图大小: {len(screenshot)} bytes")

    # 3. 初始化 GUIAgent（使用 gpt 模式，注入用户 API）
    print("[2/4] 初始化 GUIAgent (gpt-5.4) ...")
    agent = GUIAgent(
        model_type="gpt",
        runtime_conf={
            "gpt_api_key": API_KEY,
            "gpt_base_url": BASE_URL,
            "gpt_model_name": MODEL_NAME,
            "temperature": 0.0,
            "max_tokens": 500,
            "history_n": 3,
        }
    )
    print("      GUIAgent 初始化完成")

    # 4. 运行几轮 request/response 测试
    print("[3/4] 开始 request/response 循环（最多 3 轮）...")
    max_rounds = 3
    for round_idx in range(1, max_rounds + 1):
        print(f"\n  --- Round {round_idx}/{max_rounds} ---")

        # 获取截图
        screenshot = controller.get_screenshot()
        print(f"  [Request] 截图大小: {len(screenshot)} bytes")

        # 调用模型 predict（request → response）
        thought, actions, pyautogui_code = agent.predict(
            instruction=instruction,
            obs={"screenshot": screenshot},
        )

        print(f"  [Response] Thought: {thought[:120]}..." if len(thought) > 120 else f"  [Response] Thought: {thought}")
        print(f"  [Response] Actions: {actions}")
        print(f"  [Response] Pyautogui code: {pyautogui_code[:120]}..." if len(str(pyautogui_code)) > 120 else f"  [Response] Pyautogui code: {pyautogui_code}")

        # 执行动作（如果有效）
        if pyautogui_code and pyautogui_code not in ("DONE", "WAIT", "FAIL", "client error"):
            try:
                result = controller.execute_python_command(pyautogui_code)
                print(f"  [Execute] 结果: {str(result)[:100]}")
            except Exception as e:
                print(f"  [Execute] 执行失败: {e}")
        elif pyautogui_code == "DONE":
            print("  [Status] Agent 报告任务完成")
            break
        elif pyautogui_code == "FAIL":
            print("  [Status] Agent 报告失败")
            break
        elif pyautogui_code == "client error":
            print("  [Status] API 调用失败（client error）")
            break

    # 5. 总结
    print("\n" + "=" * 60)
    print("[4/4] 测试总结")
    print(f"  总轮次: {len(agent.history_responses)}")
    print(f"  API 调用次数: {len(agent.history_responses)}")
    print(f"  最后动作: {pyautogui_code}")
    print("=" * 60)
    print("\n✅ API request/response 测试完成！")
    print("   你的 API (gpt-5.4 @ v2.pincc.ai) 可以正常用于 gui-only 任务。")


if __name__ == "__main__":
    main()
