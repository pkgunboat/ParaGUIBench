"""
Trajectory Exporter - 导出执行日志为可视化格式

将 PlanAgent 的 execution_log 导出为 JSON 格式，用于 trajectory 可视化
"""

import json
import os
from typing import Dict, Any, List
from datetime import datetime


class TrajectoryExporter:
    """导出 trajectory 数据用于可视化"""
    
    def __init__(self, output_dir: str):
        """
        初始化导出器
        
        Args:
            output_dir: 输出目录（与录屏视频同一目录）
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def export_execution_log(
        self,
        execution_log: Dict[str, Any],
        fps: int = 2
    ) -> str:
        """
        导出执行日志为 JSON 格式
        
        Args:
            execution_log: PlanAgent 返回的 execution_log
            fps: 录屏帧率（用于时间同步）
            
        Returns:
            导出的 JSON 文件路径
        """
        if not execution_log:
            print("[WARNING] No execution_log provided")
            return None
        
        # 构建可视化数据结构
        visualization_data = {
            "task": execution_log.get("task", "Unknown Task"),
            "recording": {
                "start_timestamp": execution_log.get("start_timestamp"),
                "end_timestamp": execution_log.get("end_timestamp"),
                "fps": fps
            },
            "rounds": []
        }
        
        # 处理每一轮
        for round_data in execution_log.get("rounds", []):
            round_viz = {
                "round": round_data.get("round"),
                "timestamp": round_data.get("timestamp"),
                "relative_time": round_data.get("relative_time"),
                "thought": round_data.get("thought") or "(No thought provided)",
                "actions": []
            }
            
            # 处理工具调用
            for tool_call in round_data.get("tool_calls", []):
                action = {
                    "function": tool_call.get("function"),
                    "arguments": tool_call.get("arguments", {}),
                    "vm_assigned": tool_call.get("vm_assigned"),
                    "start_timestamp": tool_call.get("start_timestamp"),
                    "end_timestamp": tool_call.get("end_timestamp"),
                    "duration": tool_call.get("duration"),
                    "status": tool_call.get("status"),
                    "task_description": tool_call.get("arguments", {}).get("task_description", ""),
                    "result": tool_call.get("result")  # 添加 result 字段（包含 steps）
                }
                round_viz["actions"].append(action)
            
            visualization_data["rounds"].append(round_viz)
        
        # 保存到文件
        output_path = os.path.join(self.output_dir, "timing_records.json")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(visualization_data, f, indent=2, ensure_ascii=False)
        
        print(f"[INFO] Exported trajectory data to: {output_path}")
        return output_path
    
    def export_separate_vm_logs(
        self,
        execution_log: Dict[str, Any],
        fps: int = 2
    ) -> Dict[str, str]:
        """
        为每个 VM 导出单独的日志文件
        
        Args:
            execution_log: PlanAgent 返回的 execution_log
            fps: 录屏帧率
            
        Returns:
            VM ID -> 文件路径的字典
        """
        if not execution_log:
            return {}
        
        # 按 VM 分组
        vm_logs = {}
        
        for round_data in execution_log.get("rounds", []):
            for tool_call in round_data.get("tool_calls", []):
                vm_assigned = tool_call.get("vm_assigned")
                if not vm_assigned or vm_assigned == "Code Agent (no VM)":
                    continue
                
                if vm_assigned not in vm_logs:
                    vm_logs[vm_assigned] = {
                        "task": execution_log.get("task"),
                        "vm": vm_assigned,
                        "recording": {
                            "start_timestamp": execution_log.get("start_timestamp"),
                            "end_timestamp": execution_log.get("end_timestamp"),
                            "fps": fps
                        },
                        "actions": []
                    }
                
                action = {
                    "round": round_data.get("round"),
                    "timestamp": tool_call.get("start_timestamp"),
                    "relative_time": tool_call.get("start_timestamp") - execution_log.get("start_timestamp"),
                    "thought": round_data.get("thought"),
                    "function": tool_call.get("function"),
                    "task_description": tool_call.get("arguments", {}).get("task_description", ""),
                    "duration": tool_call.get("duration"),
                    "status": tool_call.get("status")
                }
                vm_logs[vm_assigned]["actions"].append(action)
        
        # 保存每个 VM 的日志
        output_paths = {}
        for vm_id, vm_data in vm_logs.items():
            # 转换 VM1 -> vm1
            vm_filename = vm_id.lower().replace(" ", "_")
            output_path = os.path.join(self.output_dir, f"{vm_filename}_timing.json")
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(vm_data, f, indent=2, ensure_ascii=False)
            
            print(f"[INFO] Exported {vm_id} log to: {output_path}")
            output_paths[vm_id] = output_path
        
        return output_paths
