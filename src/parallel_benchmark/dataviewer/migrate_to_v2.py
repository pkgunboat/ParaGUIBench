#!/usr/bin/env python3
"""
数据迁移脚本：从 v1.0 (agent-centric) 迁移到 v2.0 (device-centric)
"""
import json
from typing import Dict, Any, List

def migrate_v1_to_v2(v1_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 v1.0 格式的数据迁移到 v2.0 格式。
    
    Args:
        v1_data: v1.0 格式的数据字典
        
    Returns:
        v2.0 格式的数据字典
    """
    # 1. 提取基本信息
    v2_data = {
        "version": "2.0.0",
        "task_id": v1_data.get("task_id", ""),
        "instruction": v1_data.get("instruction", ""),
        "metadata": v1_data.get("metadata", {})
    }
    
    # 2. 转换 plan_agent 为 coordinator
    v1_plan_agent = v1_data.get("agents", {}).get("plan_agent", {})
    v2_data["coordinator"] = {
        "agent_id": "coordinator",
        "type": "planner",
        "model_name": "gpt-5.1",  # 默认模型
        "rounds": v1_plan_agent.get("rounds", []),
        "summary": v1_plan_agent.get("summary", {})
    }
    
    # 3. 按设备组织 agents
    device_map: Dict[str, List[Dict[str, Any]]] = {}
    
    # 处理 GUI agents
    for gui_agent in v1_data.get("agents", {}).get("gui_agents", []):
        device_id = gui_agent.get("device_id", ["Unknown Device"])[0]
        
        agent_data = {
            "agent_id": gui_agent.get("agent_id", ""),
            "type": "gui",
            "task": gui_agent.get("task", ""),
            "model_name": "gpt-5.1",
            "parent_agent": gui_agent.get("parent_agent"),
            "parent_round": gui_agent.get("parent_round"),
            "recording": gui_agent.get("recording"),
            "rounds": gui_agent.get("rounds", []),
            "summary": gui_agent.get("summary")
        }
        
        if device_id not in device_map:
            device_map[device_id] = []
        device_map[device_id].append(agent_data)
    
    # 处理 Code agents
    for code_agent in v1_data.get("agents", {}).get("code_agents", []):
        device_id = code_agent.get("device_id", ["Unknown Device"])[0]
        
        agent_data = {
            "agent_id": code_agent.get("agent_id", ""),
            "type": "code",
            "task": code_agent.get("task", ""),
            "model_name": "gpt-5.1",
            "code_type": code_agent.get("code_type"),
            "parent_agent": code_agent.get("parent_agent"),
            "parent_round": code_agent.get("parent_round"),
            "recording": code_agent.get("recording"),
            "rounds": code_agent.get("rounds", []),
            "summary": code_agent.get("summary")
        }
        
        if device_id not in device_map:
            device_map[device_id] = []
        device_map[device_id].append(agent_data)
    
    # 4. 构建 devices 列表
    devices = []
    for device_id, agents in device_map.items():
        device_type = "desktop" if "Desktop" in device_id else ("server" if "Server" in device_id else "unknown")
        devices.append({
            "device_id": device_id,
            "type": device_type,
            "metadata": {},
            "agents": agents
        })
    
    v2_data["devices"] = sorted(devices, key=lambda d: d["device_id"])
    
    # 5. 更新 summary
    v1_summary = v1_data.get("summary", {})
    v2_data["summary"] = {
        "total_duration": v1_summary.get("total_duration", 0.0),
        "total_model_time": v1_summary.get("total_model_time", 0.0),
        "total_action_time": v1_summary.get("total_action_time", 0.0),
        "coordinator_rounds": v1_summary.get("plan_agent_rounds", 0),
        "devices_count": len(devices),
        "total_agents_count": v1_summary.get("gui_agents_count", 0) + v1_summary.get("code_agents_count", 0),
        "total_rounds": v1_summary.get("total_gui_rounds", 0) + v1_summary.get("total_code_rounds", 0),
        "success": v1_summary.get("success", False)
    }
    
    return v2_data

if __name__ == "__main__":
    # 读取 v1 数据
    with open("dataviewer/record_templetae.json", "r", encoding="utf-8") as f:
        v1_data = json.load(f)
    
    # 迁移到 v2
    v2_data = migrate_v1_to_v2(v1_data)
    
    # 保存 v2 数据
    with open("dataviewer/record_templetae.json", "w", encoding="utf-8") as f:
        json.dump(v2_data, f, ensure_ascii=False, indent=2)
    
    print("✅ 数据迁移完成！")
    print(f"   版本: {v2_data['version']}")
    print(f"   设备数量: {v2_data['summary']['devices_count']}")
    print(f"   智能体总数: {v2_data['summary']['total_agents_count']}")














