"""
JSON Logger for Plan Agent Thought-Action
将 execution_log 转换为符合 record_template.json 格式的结构化日志
"""

import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional


class JSONLogger:
    """
    JSON 日志生成器
    将 PlanAgentThoughtAction 的 execution_log 转换为标准的 JSON 格式
    """
    
    def __init__(self, output_path: str):
        """
        Args:
            output_path: JSON 日志文件的输出路径
        """
        self.output_path = output_path
    
    @staticmethod
    def _timestamp_to_iso(timestamp: float) -> str:
        """将 Unix 时间戳转换为 ISO 8601 格式"""
        return datetime.fromtimestamp(timestamp).isoformat()
    
    def generate_json_log(
        self,
        execution_log: Dict[str, Any],
        task_id: str,
        instruction: str,
        recording_paths: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        生成符合模板格式的 JSON 日志
        
        Args:
            execution_log: PlanAgentThoughtAction.execution_log
            task_id: 任务唯一标识
            instruction: 用户原始指令
            recording_paths: 录屏文件路径字典 (可选)，格式: {"vm1": "path/to/recording.mp4"}
            
        Returns:
            格式化的 JSON 日志字典
        """
        start_timestamp = execution_log.get("start_timestamp", time.time())
        end_timestamp = execution_log.get("end_timestamp", time.time())
        total_duration = execution_log.get("elapsed_time", end_timestamp - start_timestamp)
        
        # 构建顶层结构
        json_log = {
            "version": "1.0.0",
            "task_id": task_id,
            "instruction": instruction,
            "metadata": {
                "start_timestamp": start_timestamp,
                "start_time_iso": self._timestamp_to_iso(start_timestamp),
                "end_timestamp": end_timestamp,
                "end_time_iso": self._timestamp_to_iso(end_timestamp),
                "total_duration": total_duration
            },
            "agents": {
                "plan_agent": self._build_plan_agent_log(execution_log, start_timestamp, end_timestamp, total_duration),
                "gui_agents": [],
                "code_agents": []
            },
            "summary": {}
        }
        
        # 从 execution_log 中提取 GUI 和 Code agent 的信息
        gui_agents, code_agents = self._extract_sub_agents(execution_log, recording_paths)
        json_log["agents"]["gui_agents"] = gui_agents
        json_log["agents"]["code_agents"] = code_agents
        
        # 生成全局摘要
        json_log["summary"] = self._build_global_summary(json_log, total_duration)
        
        return json_log
    
    def _build_plan_agent_log(
        self,
        execution_log: Dict[str, Any],
        start_timestamp: float,
        end_timestamp: float,
        total_duration: float
    ) -> Dict[str, Any]:
        """构建 Plan Agent 的日志"""
        rounds_data = execution_log.get("rounds", [])
        
        plan_agent_log = {
            "recording": {
                "start_timestamp": start_timestamp,
                "start_time_iso": self._timestamp_to_iso(start_timestamp),
                "end_timestamp": end_timestamp,
                "end_time_iso": self._timestamp_to_iso(end_timestamp),
                "duration": total_duration
            },
            "rounds": [],
            "summary": {
                "total_rounds": 0,
                "rounds_with_action": 0,
                "total_model_time": 0.0,
                "total_action_time": 0.0,
                "average_model_time": 0.0,
                "average_action_time": 0.0,
                "dispatched_gui_agents": 0,
                "dispatched_code_agents": 0
            }
        }
        
        total_model_time = 0.0
        total_action_time = 0.0
        rounds_with_action = 0
        gui_count = 0
        code_count = 0
        
        for round_data in rounds_data:
            round_num = round_data.get("round", 0)
            round_timestamp = round_data.get("timestamp", start_timestamp)
            tool_calls = round_data.get("tool_calls", [])
            
            # 计算 model_prediction 的时间（从 round 开始到第一个 tool_call 开始）
            if tool_calls:
                first_tool_start = min(tc.get("start_timestamp", round_timestamp) for tc in tool_calls)
                model_duration = first_tool_start - round_timestamp
                
                # 计算 action_execution 的时间（所有 tool_calls 的总时间）
                action_start = first_tool_start
                action_end = max(tc.get("end_timestamp", first_tool_start) for tc in tool_calls)
                action_duration = action_end - action_start
            else:
                # 没有 tool_calls，整个 round 都是 model_prediction
                model_duration = 0.1  # 估算一个小值
                action_start = round_timestamp
                action_end = round_timestamp
                action_duration = 0.0
            
            # 构建 actions 列表
            actions = []
            dispatched_agents = []
            for tc in tool_calls:
                function = tc.get("function", "")
                arguments = tc.get("arguments", {})
                task_desc = arguments.get("task_description", "")
                
                action_dict = {
                    "type": function,
                    "task": task_desc
                }
                
                # 添加 VM 信息（如果有）
                if "vm_assigned" in tc:
                    action_dict["vm_assigned"] = tc["vm_assigned"]
                
                actions.append(action_dict)
                
                # 统计分发的 agent 数量
                if function == "call_gui_agent":
                    agent_id = f"gui_agent_{gui_count}"
                    dispatched_agents.append(agent_id)
                    gui_count += 1
                elif function == "call_code_agent":
                    agent_id = f"code_agent_{code_count}"
                    dispatched_agents.append(agent_id)
                    code_count += 1
            
            # 构建 round 记录
            round_record = {
                "round": round_num - 1,  # 从 0 开始
                "model_prediction": {
                    "start_timestamp": round_timestamp,
                    "start_time_iso": self._timestamp_to_iso(round_timestamp),
                    "end_timestamp": round_timestamp + model_duration,
                    "end_time_iso": self._timestamp_to_iso(round_timestamp + model_duration),
                    "duration": model_duration,
                    "response": round_data.get("thought", ""),
                    "actions": actions
                },
                "action_execution": {
                    "start_timestamp": action_start,
                    "start_time_iso": self._timestamp_to_iso(action_start),
                    "end_timestamp": action_end,
                    "end_time_iso": self._timestamp_to_iso(action_end),
                    "duration": action_duration,
                    "result": {
                        "status": "success" if all(tc.get("status") == "success" for tc in tool_calls) else "failed",
                        "returncode": 0 if all(tc.get("status") == "success" for tc in tool_calls) else 1,
                        "output": f"Successfully dispatched {len(tool_calls)} agent(s)" if tool_calls else "",
                        "error": "",
                        "dispatched_agents": dispatched_agents
                    }
                } if tool_calls else None,
                "total_duration": model_duration + action_duration
            }
            
            plan_agent_log["rounds"].append(round_record)
            
            # 统计
            total_model_time += model_duration
            if tool_calls:
                total_action_time += action_duration
                rounds_with_action += 1
        
        # 完成 summary
        total_rounds = len(rounds_data)
        plan_agent_log["summary"]["total_rounds"] = total_rounds
        plan_agent_log["summary"]["rounds_with_action"] = rounds_with_action
        plan_agent_log["summary"]["total_model_time"] = total_model_time
        plan_agent_log["summary"]["total_action_time"] = total_action_time
        plan_agent_log["summary"]["average_model_time"] = total_model_time / total_rounds if total_rounds > 0 else 0.0
        plan_agent_log["summary"]["average_action_time"] = total_action_time / rounds_with_action if rounds_with_action > 0 else 0.0
        plan_agent_log["summary"]["dispatched_gui_agents"] = gui_count
        plan_agent_log["summary"]["dispatched_code_agents"] = code_count
        
        return plan_agent_log
    
    def _extract_sub_agents(
        self,
        execution_log: Dict[str, Any],
        recording_paths: Optional[Dict[str, str]] = None
    ) -> tuple:
        """从 execution_log 中提取 GUI 和 Code agents 的详细信息"""
        gui_agents = []
        code_agents = []
        
        gui_counter = 0
        code_counter = 0
        
        rounds_data = execution_log.get("rounds", [])
        
        for round_num, round_data in enumerate(rounds_data):
            tool_calls = round_data.get("tool_calls", [])
            
            for tc in tool_calls:
                function = tc.get("function", "")
                arguments = tc.get("arguments", {})
                task_desc = arguments.get("task_description", "")
                
                start_ts = tc.get("start_timestamp", 0)
                end_ts = tc.get("end_timestamp", start_ts)
                duration = tc.get("duration", end_ts - start_ts)
                
                result = tc.get("result", {})
                status = tc.get("status", "unknown")
                
                if function == "call_gui_agent":
                    agent_id = f"gui_agent_{gui_counter}"
                    
                    # 构建 GUI agent 日志
                    gui_agent_log = {
                        "agent_id": agent_id,
                        "task": task_desc,
                        "parent_agent": "plan_agent",
                        "parent_round": round_num,
                        "recording": {
                            "path": recording_paths.get(tc.get("vm_assigned", "vm1"), "") if recording_paths else "",
                            "start_timestamp": start_ts,
                            "start_time_iso": self._timestamp_to_iso(start_ts),
                            "end_timestamp": end_ts,
                            "end_time_iso": self._timestamp_to_iso(end_ts),
                            "duration": duration
                        },
                        "rounds": self._extract_agent_rounds(result),
                        "summary": self._build_agent_summary(result, status)
                    }
                    
                    gui_agents.append(gui_agent_log)
                    gui_counter += 1
                
                elif function == "call_code_agent":
                    agent_id = f"code_agent_{code_counter}"
                    
                    # 构建 Code agent 日志
                    code_agent_log = {
                        "agent_id": agent_id,
                        "task": task_desc,
                        "code_type": "python",  # 默认为 python
                        "parent_agent": "plan_agent",
                        "parent_round": round_num,
                        "recording": {
                            "start_timestamp": start_ts,
                            "start_time_iso": self._timestamp_to_iso(start_ts),
                            "end_timestamp": end_ts,
                            "end_time_iso": self._timestamp_to_iso(end_ts),
                            "duration": duration
                        },
                        "rounds": self._extract_agent_rounds(result),
                        "summary": self._build_agent_summary(result, status)
                    }
                    
                    code_agents.append(code_agent_log)
                    code_counter += 1
        
        return gui_agents, code_agents
    
    def _extract_agent_rounds(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 agent 的执行结果中提取 rounds 信息"""
        # 这部分需要根据实际的 agent 返回结果结构来解析
        # 目前简化处理，返回一个包含基本信息的单轮记录
        
        steps = result.get("steps", [])
        agent_rounds = []
        
        if not steps:
            # 如果没有 steps，创建一个简化的记录
            agent_rounds.append({
                "round": 0,
                "model_prediction": {
                    "start_timestamp": time.time(),
                    "start_time_iso": self._timestamp_to_iso(time.time()),
                    "end_timestamp": time.time(),
                    "end_time_iso": self._timestamp_to_iso(time.time()),
                    "duration": 0.0,
                    "response": result.get("result", ""),
                    "actions": []
                },
                "action_execution": None,
                "total_duration": 0.0
            })
        else:
            # 从 steps 中提取信息，并计算实际的 duration
            for idx, step in enumerate(steps):
                step_result = step.get("output", "") if isinstance(step.get("output"), str) else str(step.get("output", ""))
                step_status = step.get("status", "unknown")
                step_timestamp = step.get("timestamp", time.time())
                
                # 尝试获取下一个 step 的时间戳，用于计算 duration
                next_timestamp = steps[idx + 1].get("timestamp", time.time()) if idx + 1 < len(steps) else step_timestamp + 1.0
                step_duration = next_timestamp - step_timestamp
                
                # 将 duration 分配给 model_prediction 和 action_execution
                # 假设 model 预测占 20%，action 执行占 80%（可以调整）
                model_duration = step_duration * 0.2
                action_duration = step_duration * 0.8
                
                model_start = step_timestamp
                model_end = model_start + model_duration
                action_start = model_end
                action_end = action_start + action_duration
                
                agent_rounds.append({
                    "round": idx,
                    "model_prediction": {
                        "start_timestamp": model_start,
                        "start_time_iso": self._timestamp_to_iso(model_start),
                        "end_timestamp": model_end,
                        "end_time_iso": self._timestamp_to_iso(model_end),
                        "duration": model_duration,
                        "response": step.get("thought", "") if "thought" in step else step_result,  # 不截断
                        "actions": step.get("actions", []) if isinstance(step.get("actions"), list) else []
                    },
                    "action_execution": {
                        "start_timestamp": action_start,
                        "start_time_iso": self._timestamp_to_iso(action_start),
                        "end_timestamp": action_end,
                        "end_time_iso": self._timestamp_to_iso(action_end),
                        "duration": action_duration,
                        "result": {
                            "status": step_status,
                            "returncode": 0 if step_status in ["success", "executed"] else 1,
                            "output": step_result,  # 不截断
                            "error": step.get("error", "")
                        }
                    } if step_status not in ["waiting", "pending"] else None,
                    "total_duration": step_duration
                })
        
        return agent_rounds
    
    def _build_agent_summary(self, result: Dict[str, Any], status: str) -> Dict[str, Any]:
        """构建 agent 的 summary"""
        steps = result.get("steps", [])
        
        # 计算实际的时间统计
        total_model_time = 0.0
        total_action_time = 0.0
        rounds_with_action = 0
        
        for idx, step in enumerate(steps):
            step_status = step.get("status", "unknown")
            step_timestamp = step.get("timestamp", time.time())
            
            # 计算 duration
            next_timestamp = steps[idx + 1].get("timestamp", time.time()) if idx + 1 < len(steps) else step_timestamp + 1.0
            step_duration = next_timestamp - step_timestamp
            
            # 分配时间
            model_duration = step_duration * 0.2
            action_duration = step_duration * 0.8
            
            total_model_time += model_duration
            
            if step_status not in ["waiting", "pending"]:
                total_action_time += action_duration
                rounds_with_action += 1
        
        total_rounds = len(steps) if steps else 1
        
        return {
            "total_rounds": total_rounds,
            "rounds_with_action": rounds_with_action,
            "total_model_time": total_model_time,
            "total_action_time": total_action_time,
            "average_model_time": total_model_time / total_rounds if total_rounds > 0 else 0.0,
            "average_action_time": total_action_time / rounds_with_action if rounds_with_action > 0 else 0.0,
            "final_status": status
        }
    
    def _build_global_summary(self, json_log: Dict[str, Any], total_duration: float) -> Dict[str, Any]:
        """构建全局摘要"""
        plan_agent = json_log["agents"]["plan_agent"]
        gui_agents = json_log["agents"]["gui_agents"]
        code_agents = json_log["agents"]["code_agents"]
        
        # 计算总的 model 和 action 时间
        total_model_time = plan_agent["summary"]["total_model_time"]
        total_action_time = plan_agent["summary"]["total_action_time"]
        
        for agent in gui_agents:
            total_model_time += agent["summary"]["total_model_time"]
            total_action_time += agent["summary"]["total_action_time"]
        
        for agent in code_agents:
            total_model_time += agent["summary"]["total_model_time"]
            total_action_time += agent["summary"]["total_action_time"]
        
        # 计算总轮次
        total_gui_rounds = sum(agent["summary"]["total_rounds"] for agent in gui_agents)
        total_code_rounds = sum(agent["summary"]["total_rounds"] for agent in code_agents)
        
        # 判断是否成功
        all_success = plan_agent["summary"]["rounds_with_action"] > 0
        for agent in gui_agents:
            if agent["summary"]["final_status"] != "success":
                all_success = False
        for agent in code_agents:
            if agent["summary"]["final_status"] != "success":
                all_success = False
        
        return {
            "total_duration": total_duration,
            "total_model_time": total_model_time,
            "total_action_time": total_action_time,
            "plan_agent_rounds": plan_agent["summary"]["total_rounds"],
            "gui_agents_count": len(gui_agents),
            "code_agents_count": len(code_agents),
            "total_gui_rounds": total_gui_rounds,
            "total_code_rounds": total_code_rounds,
            "success": all_success
        }
    
    def save_json_log(self, json_log: Dict[str, Any]) -> None:
        """保存 JSON 日志到文件"""
        with open(self.output_path, 'w', encoding='utf-8') as f:
            json.dump(json_log, f, indent=2, ensure_ascii=False)
        
        print(f"✓ JSON log saved to: {self.output_path}")
    
    def generate_and_save(
        self,
        execution_log: Dict[str, Any],
        task_id: str,
        instruction: str,
        recording_paths: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        生成并保存 JSON 日志（便捷方法）
        
        Returns:
            生成的 JSON 日志字典
        """
        json_log = self.generate_json_log(execution_log, task_id, instruction, recording_paths)
        self.save_json_log(json_log)
        return json_log
