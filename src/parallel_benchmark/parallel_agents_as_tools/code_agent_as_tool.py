"""
CodeAgent as MCP Tool
将 CodeAgent 封装为可被 Plan Agent 调用的工具
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict
import time
import re
from .base_agent_tool import BaseAgentTool
from parallel_agents.code_agent_new import CodeAgent, FINISH_WORD, WAIT_WORD, FAIL_WORD


class CodeAgentTool(BaseAgentTool):
    """CodeAgent 工具封装"""
    
    def execute(self, task: str, max_rounds: int = 15, timeout: int = 300) -> Dict:
        """
        执行基于代码的任务
        
        Args:
            task: 任务描述
            max_rounds: 最大执行轮次（默认15）
            timeout: 超时时间(秒)
        
        Returns:
            执行结果字典
        """
        start_time = time.time()
        
        print(f"[CODE_AGENT] Starting task with max_rounds={max_rounds}")
        print(f"[CODE_AGENT] Task: {task[:100]}...")
        
        # 1. 创建 CodeAgent 实例
        try:
            agent = CodeAgent(
                platform="ubuntu",
                action_space="code",
                observation_type="text",
                max_trajectory_length=20,
                model_name="gpt-5.2",
                runtime_conf={
                    "language": "English",
                    "history_n": 15,
                    "temperature": 0.0,
                    "top_p": 0.9,
                    "max_tokens": 16384,  # 增加到16K以容纳GPT-5的推理token + 输出内容
                }
            )
        except Exception as e:
            return self.format_result(
                success=False,
                result="",
                steps=[],
                error=f"Failed to initialize CodeAgent: {str(e)}"
            )
        
        # 2. 执行任务循环
        steps = []
        last_output = ""
        
        # 记录详细的轮次时间信息
        rounds_timing = []
        
        for round_num in range(max_rounds):
            print(f"[CODE_AGENT] Round {round_num + 1}/{max_rounds}")
            
            # ========== Round开始 ==========
            round_start_time = time.time()
            
            # 检查超时
            if time.time() - start_time > timeout:
                failure_summary = self._generate_failure_summary(steps, "timeout")
                return self.format_result(
                    success=False,
                    result=f"Task timeout after {timeout} seconds.\n\n{failure_summary}",
                    steps=steps,
                    error=f"Timeout. {failure_summary}",
                    rounds_timing=rounds_timing
                )
            
            # ========== 阶段1: 准备阶段（获取状态）==========
            preparation_start_time = time.time()
            
            # 获取当前状态（这也是 thinking 的一部分，因为需要收集信息给 LLM）
            current_dir = self._get_current_directory()
            observation = {
                "terminal_output": last_output,
                "current_dir": current_dir
            }
            
            preparation_end_time = time.time()
            preparation_time = preparation_end_time - preparation_start_time
            print(f"[TIMING] Preparation: {preparation_time:.3f}s")
            
            # ========== 阶段2: API调用阶段（LLM推理）==========
            think_start_time = time.time()
            
            # 调用 agent.predict() - LLM 推理
            try:
                response, actions, code = agent.predict(task, observation)
                print(f"[CODE_AGENT] Response: {response[:150] if response else 'None'}...")
                print(f"[CODE_AGENT] Code: {code[:100] if code else 'None'}...")
            except Exception as e:
                return self.format_result(
                    success=False,
                    result=f"Error in round {round_num}",
                    steps=steps,
                    error=f"Agent prediction error: {str(e)}",
                    rounds_timing=rounds_timing
                )
            
            think_end_time = time.time()
            api_call_time = think_end_time - think_start_time
            print(f"[TIMING] API Call: {api_call_time:.3f}s")
            
            # ========== 阶段3: 执行阶段（解析并执行代码）==========
            action_start_time = think_end_time
            
            # 记录详细步骤（包含 thought 和 code）
            step_detail = {
                "round": round_num + 1,
                "timestamp": time.time(),  # 添加步骤时间戳，用于视频同步
                "thought": response if response else "",  # LLM 的思考过程（完整保留）
                "code": code if code else "",  # 生成的代码（完整保留）
                "output": "",  # 执行输出
                "status": "pending",
                "think_start": think_start_time,
                "think_end": think_end_time,
                "think_duration": think_end_time - think_start_time
            }
            
            # 处理特殊情况
            if code == FINISH_WORD:
                # FINISH: 没有真正的 action 执行，action_duration ≈ 0
                action_end_time = time.time()
                step_detail["status"] = "finished"
                step_detail["output"] = "Task completed"
                step_detail["action_start"] = action_start_time
                step_detail["action_end"] = action_end_time
                step_detail["action_duration"] = action_end_time - action_start_time
                step_detail["is_terminal"] = True  # 标记为终止步骤
                steps.append(step_detail)
                
                # 记录轮次时间
                rounds_timing.append({
                    "round": round_num + 1,
                    "think_start": think_start_time,
                    "think_end": think_end_time,
                    "think_duration": think_end_time - think_start_time,
                    "action_start": action_start_time,
                    "action_end": action_end_time,
                    "action_duration": action_end_time - action_start_time,
                    "total_duration": action_end_time - round_start_time,
                    "is_terminal": True  # 标记为终止轮次
                })
                
                # 提取最后有效的输出结果
                final_output = ""
                for step in reversed(steps):
                    if step.get("status") == "success" and step.get("output"):
                        output_text = step.get("output", "")
                        if output_text and output_text != "No output (success)":
                            final_output = output_text
                            break
                
                # 构建结果消息
                if final_output:
                    result_message = f"Task completed successfully in {round_num + 1} rounds.\n\nOutput:\n{final_output}"
                else:
                    result_message = f"Task completed successfully in {round_num + 1} rounds"
                
                return self.format_result(
                    success=True,
                    result=result_message,
                    steps=steps,
                    rounds_timing=rounds_timing
                )
            
            elif code == WAIT_WORD:
                # WAIT: action 包含 3 秒等待时间
                step_detail["status"] = "waiting"
                step_detail["output"] = "Waiting for 3 seconds..."
                step_detail["action_start"] = action_start_time
                
                # 先执行等待
                time.sleep(3)
                
                # 等待完成后记录 action_end
                action_end_time = time.time()
                step_detail["action_end"] = action_end_time
                step_detail["action_duration"] = action_end_time - action_start_time
                steps.append(step_detail)
                
                rounds_timing.append({
                    "round": round_num + 1,
                    "think_start": think_start_time,
                    "think_end": think_end_time,
                    "think_duration": think_end_time - think_start_time,
                    "action_start": action_start_time,
                    "action_end": action_end_time,
                    "action_duration": action_end_time - action_start_time,
                    "total_duration": action_end_time - round_start_time,
                    "action_type": "wait"  # 标记为等待动作
                })
                
                last_output = "Waited for 3 seconds"
                continue
            
            elif code == FAIL_WORD:
                # FAIL: 没有真正的 action 执行
                action_end_time = time.time()
                step_detail["status"] = "failed"
                step_detail["output"] = "Agent returned FAIL"
                step_detail["action_start"] = action_start_time
                step_detail["action_end"] = action_end_time
                step_detail["action_duration"] = action_end_time - action_start_time
                step_detail["is_terminal"] = True  # 标记为终止步骤
                steps.append(step_detail)
                
                rounds_timing.append({
                    "round": round_num + 1,
                    "think_start": think_start_time,
                    "think_end": think_end_time,
                    "think_duration": think_end_time - think_start_time,
                    "action_start": action_start_time,
                    "action_end": action_end_time,
                    "action_duration": action_end_time - action_start_time,
                    "total_duration": action_end_time - round_start_time,
                    "is_terminal": True  # 标记为终止轮次
                })
                
                failure_summary = self._generate_failure_summary(steps, "agent_fail")
                return self.format_result(
                    success=False, 
                    result=f"Task failed at round {round_num}.\n\n{failure_summary}",
                    steps=steps,
                    error=f"Agent returned FAIL. {failure_summary}",
                    rounds_timing=rounds_timing
                )
            
            # 执行代码
            try:
                result = self.controller.execute_python_command(code)
                
                if result:
                    status = result.get("status", "unknown")
                    output = result.get("output", "")
                    
                    if status == "success":
                        # 增强反馈：如果是文件操作，尝试验证
                        verification_msg = ""
                        if any(keyword in code.lower() for keyword in ['write', 'open', 'file', 'with open']):
                            # 尝试提取文件路径并验证
                            verification_msg = self._verify_file_operation(code)
                        
                        if output.strip():
                            last_output = f"✓ Code executed successfully.\nOutput:\n{output}"
                        else:
                            last_output = f"✓ Code executed successfully (no output).{verification_msg}"
                        
                        step_detail["status"] = "success"
                        step_detail["output"] = output[:300] if output else "No output (success)"
                    else:
                        last_output = f"✗ Code execution failed.\nError:\n{output}"
                        step_detail["status"] = "error"
                        step_detail["output"] = output[:300]
                else:
                    last_output = "✗ Error: No result returned from code execution"
                    step_detail["status"] = "error"
                    step_detail["output"] = "No result returned"
                    
            except Exception as e:
                last_output = f"✗ Exception during execution: {str(e)}"
                step_detail["status"] = "error"
                step_detail["output"] = str(e)
            
            # 记录 action 结束时间
            action_end_time = time.time()
            execution_time = action_end_time - action_start_time
            print(f"[TIMING] Parsing & Execution: {execution_time:.3f}s")
            
            step_detail["action_start"] = action_start_time
            step_detail["action_end"] = action_end_time
            step_detail["action_duration"] = execution_time
            
            # 总时间
            total_round_time = action_end_time - round_start_time
            print(f"[TIMING] Total Round: {total_round_time:.3f}s")
            print(f"[TIMING] Breakdown: Prep={preparation_time:.3f}s + API={api_call_time:.3f}s + Parse&Exec={execution_time:.3f}s")
            
            steps.append(step_detail)
            
            # 记录轮次时间
            rounds_timing.append({
                "round": round_num + 1,
                "think_start": think_start_time,
                "think_end": think_end_time,
                "think_duration": think_end_time - think_start_time,
                "action_start": action_start_time,
                "action_end": action_end_time,
                "action_duration": action_end_time - action_start_time,
                "total_duration": action_end_time - round_start_time,
                "action_type": "code_execution"  # 标记为代码执行
            })
            
            # 短暂等待
            time.sleep(1)
        
        # 超过最大轮次 - 生成失败原因总结
        failure_summary = self._generate_failure_summary(steps, "max_rounds_exceeded")
        return self.format_result(
            success=False,
            result=f"Task did not complete within {max_rounds} rounds.\n\n{failure_summary}",
            steps=steps,
            error=f"Max rounds exceeded. {failure_summary}",
            rounds_timing=rounds_timing
        )
    
    def _generate_failure_summary(self, steps: list, failure_type: str) -> str:
        """
        生成失败原因总结，帮助 Plan Agent 理解失败原因
        
        Args:
            steps: 执行步骤列表
            failure_type: 失败类型
        
        Returns:
            失败原因总结字符串
        """
        summary_parts = []
        
        # 统计步骤状态
        total_steps = len(steps)
        success_count = sum(1 for s in steps if isinstance(s, dict) and s.get("status") == "success")
        error_count = sum(1 for s in steps if isinstance(s, dict) and s.get("status") == "error")
        
        summary_parts.append(f"Executed {total_steps} steps: {success_count} succeeded, {error_count} failed.")
        
        # 提取最后几步的关键信息
        recent_steps = steps[-3:] if len(steps) >= 3 else steps
        if recent_steps:
            summary_parts.append("\nLast attempts:")
            for step in recent_steps:
                if isinstance(step, dict):
                    round_num = step.get("round", "?")
                    status = step.get("status", "unknown")
                    thought = step.get("thought", "")[:100] if step.get("thought") else ""
                    output = step.get("output", "")[:100] if step.get("output") else ""
                    summary_parts.append(f"  - Round {round_num} ({status}): {thought}... Output: {output}")
        
        # 识别可能的失败模式
        if error_count > success_count:
            summary_parts.append("\nPossible issue: Multiple execution errors occurred.")
        elif total_steps >= 15:
            summary_parts.append("\nPossible issue: Task may be too complex or agent got stuck in a loop.")
        
        return "\n".join(summary_parts)
    
    def _get_current_directory(self) -> str:
        """获取当前工作目录"""
        try:
            result = self.controller.execute_python_command("""
import os
print(os.getcwd())
""")
            if result and result.get("status") == "success":
                return result.get("output", "/home/user").strip()
        except:
            pass
        
        return "/home/user"
    
    def _verify_file_operation(self, code: str) -> str:
        """验证文件操作是否成功"""
        import re
        
        # 尝试从代码中提取文件路径
        path_patterns = [
            r"path\s*=\s*['\"]([^'\"]+)['\"]",
            r"open\(['\"]([^'\"]+)['\"]",
            r"with\s+open\(['\"]([^'\"]+)['\"]",
        ]
        
        file_path = None
        for pattern in path_patterns:
            match = re.search(pattern, code)
            if match:
                file_path = match.group(1)
                break
        
        if not file_path:
            return ""
        
        # 验证文件是否存在
        try:
            verify_code = f"""
import os
if os.path.exists('{file_path}'):
    size = os.path.getsize('{file_path}')
    print(f'File created: {{file_path}} ({{size}} bytes)')
else:
    print('File not found')
"""
            result = self.controller.execute_python_command(verify_code)
            if result and result.get("status") == "success":
                verification = result.get("output", "").strip()
                return f"\nVerification: {verification}"
        except:
            pass
        
        return ""
