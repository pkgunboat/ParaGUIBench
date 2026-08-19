import sys
import os
import json
import time
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parallel_agents_as_tools.agent_tool_registry import AgentToolRegistry
from desktop_env.controllers.python import PythonController
from prompts.plan_agent_prompt import MINIMAL_PARALLEL_PLANNER_PROMPT


class PlanAgent:
    """Planner-executor that produces a JSON plan and orchestrates GUI/Code agents.

    JSON is the source of truth; Markdown views can be derived externally.
    """

    def __init__(
        self,
        controller: PythonController,
        registry: AgentToolRegistry,
        formatting_feedback: Optional[str] = None,
        max_workers: int = 4,
        vm_controllers: Optional[List[PythonController]] = None,
    ) -> None:
        self.controller = controller
        self.registry = registry
        self.formatting_feedback = (
            formatting_feedback
            or "Your previous response was not valid JSON. Return only valid JSON matching the schema."
        )
        self.vlm = OpenAI(
            api_key="${OPENAI_API_KEY}", 
            base_url="https://api.deerapi.com/v1/",
        )
        self.max_workers = max_workers
        
        # 支持多虚拟机：用于并行 GUI Agent 任务
        # vm_controllers[0] 用于第一个 GUI Agent, vm_controllers[1] 用于第二个，以此类推
        self.vm_controllers = vm_controllers or [controller]
        self.gui_agent_vm_usage = {}  # 跟踪哪个 GUI Agent 任务分配到了哪个 VM

    # ---------- Planning ----------
    def plan(self, task: str, context: Optional[str] = None, retry: int = 1) -> Dict[str, Any]:
        user_prompt = self._build_planner_user_prompt(task, context)
        # 1) 调用已配置的大模型生成计划文本
        text = self._call_llm(user_prompt)
        plan, ok = self._try_parse_plan(text)
        if ok:
            return plan
        if retry > 0:
            repair_prompt = f"{self.formatting_feedback}\n\nOriginal response:\n{text}\n\nTask:{task}"
            text2 = self._call_llm(repair_prompt)
            plan2, ok2 = self._try_parse_plan(text2)
            if ok2:
                return plan2
        # 最后再尝试一次
        text3 = self._call_llm(user_prompt)
        plan3, ok3 = self._try_parse_plan(text3)
        if ok3:
            return plan3
        # Fallback to minimal linear plan
        return {
            "task": task,
            "nodes": [
                {
                    "id": "n1",
                    "title": task,
                    "agent": "gui_agent",
                    "params": {},
                    "depends_on": [],
                    "status": "pending",
                }
            ],
            "metadata": {"version": 1},
        }

    def _build_planner_user_prompt(self, task: str, context: Optional[str]) -> str:
        parts = [f"TASK: {task}"]
        if context:
            parts.append("\nCONTEXT:\n" + context[:2000])
        return "\n".join(parts)

    def _try_parse_plan(self, text: str) -> Tuple[Dict[str, Any], bool]:
        try:
            plan = json.loads(text)
        except Exception:
            # try to extract a json substring
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    plan = json.loads(text[start : end + 1])
                except Exception:
                    return {}, False
            else:
                return {}, False
        if not self._validate_plan(plan):
            return {}, False
        return plan, True

    def _validate_plan(self, plan: Dict[str, Any]) -> bool:
        if not isinstance(plan, dict):
            return False
        if "nodes" not in plan or not isinstance(plan["nodes"], list):
            return False
        ids = set()
        for n in plan["nodes"]:
            if not isinstance(n, dict):
                return False
            for k in ("id", "title", "agent", "depends_on", "status"):
                if k not in n:
                    return False
            if n["id"] in ids:
                return False
            ids.add(n["id"])
            if n["agent"] not in ("code_agent", "gui_agent"):
                return False
            if not isinstance(n["depends_on"], list):
                return False
        return True

    def _call_llm(self, user_prompt: str) -> str:
        response = self.vlm.chat.completions.create(
            model="gpt-5-2025-08-07",
            messages=[
                {"role": "system", "content": MINIMAL_PARALLEL_PLANNER_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=1200,
        )
        text = response.choices[0].message.content or ""
        return text.strip()

    # ---------- Execution ----------
    def execute_plan(
        self,
        plan: Dict[str, Any],
        max_rounds_per_task: int = 8,
        timeout_per_task: int = 600,
    ) -> Dict[str, Any]:
        start_time = time.time()
        nodes = {n["id"]: n for n in plan["nodes"]}
        history: List[Dict[str, Any]] = []

        while True:
            ready = [
                n
                for n in nodes.values()
                if n["status"] == "pending"
                and all(nodes[d]["status"] == "success" for d in n.get("depends_on", []))
            ]
            if not ready:
                # done or blocked
                break

            results: List[Tuple[str, Dict[str, Any]]] = []
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(ready))) as ex:
                futures = {}
                for n in ready:
                    n["status"] = "running"
                    futures[ex.submit(
                        self._run_single_task,
                        n,
                        max_rounds_per_task,
                        timeout_per_task,
                        history,
                    )] = n["id"]
                for f in as_completed(futures):
                    nid = futures[f]
                    try:
                        res = f.result()
                    except Exception as e:
                        res = {"status": "failure", "result": "", "steps": [], "error": str(e)}
                    results.append((nid, res))

            for nid, res in results:
                node = nodes[nid]
                node["status"] = "success" if res.get("status") == "success" else "failed"
                node["last_result"] = res.get("result", "")
                node["last_error"] = res.get("error", "")
                node["last_steps"] = res.get("steps", [])
                history.append({"node_id": nid, "result": res})

            # stop criteria: all done or no progress
            if all(n["status"] in ("success", "failed", "skipped") for n in nodes.values()):
                break

        success = all(n["status"] == "success" for n in nodes.values())
        return {
            "status": "success" if success else "partial_success",
            "elapsed_seconds": time.time() - start_time,
            "plan": plan,
            "history": history,
        }

    def _run_single_task(
        self,
        node: Dict[str, Any],
        max_rounds: int,
        timeout: int,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # 构建指令，包含依赖任务的结果
        instruction = self._build_instruction(node, history)
        
        # 如果是 GUI Agent 任务，需要分配虚拟机
        if node["agent"] == "gui_agent":
            # 为这个任务分配一个空闲的虚拟机
            vm_controller = self._allocate_vm_for_gui_task(node["id"])
            
            # 使用指定 VM 的 registry 执行
            from parallel_agents_as_tools.agent_tool_registry import AgentToolRegistry
            temp_registry = AgentToolRegistry(vm_controller)
            
            try:
                result = temp_registry.execute(
                    tool_name=node["agent"],
                    task=instruction,
                    max_rounds=max_rounds,
                    timeout=timeout,
                )
            finally:
                # 任务完成后释放 VM
                self._release_vm_for_gui_task(node["id"])
            
            return result
        else:
            # Code Agent 使用默认 controller
            return self.registry.execute(
                tool_name=node["agent"],
                task=instruction,
                max_rounds=max_rounds,
                timeout=timeout,
            )
    
    def _allocate_vm_for_gui_task(self, task_id: str) -> PythonController:
        """
        为 GUI Agent 任务分配一个虚拟机
        采用简单的轮询策略分配
        """
        # 统计每个 VM 当前的使用情况
        vm_usage_count = [0] * len(self.vm_controllers)
        for allocated_vm_index in self.gui_agent_vm_usage.values():
            vm_usage_count[allocated_vm_index] += 1
        
        # 选择使用最少的 VM
        vm_index = vm_usage_count.index(min(vm_usage_count))
        self.gui_agent_vm_usage[task_id] = vm_index
        
        print(f"[PlanAgent] 分配 VM{vm_index+1} ({self.vm_controllers[vm_index].http_server}) 给任务 {task_id}")
        return self.vm_controllers[vm_index]
    
    def _release_vm_for_gui_task(self, task_id: str):
        """释放 GUI Agent 任务使用的虚拟机"""
        if task_id in self.gui_agent_vm_usage:
            vm_index = self.gui_agent_vm_usage.pop(task_id)
            print(f"[PlanAgent] 释放 VM{vm_index+1} (任务 {task_id} 完成)")

    def _build_instruction(self, node: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
        """
        构建任务指令，包含依赖任务的结果
        
        Args:
            node: 当前任务节点
            history: 历史执行记录
        
        Returns:
            完整的任务指令（包含上下文）
        """
        pieces: List[str] = [str(node.get("title", ""))]
        
        # 添加任务参数
        params = node.get("params")
        if params:
            pieces.append("\nParameters:\n" + json.dumps(params, ensure_ascii=False, indent=2))
        
        # 收集依赖任务的结果
        depends_on = node.get("depends_on", [])
        if depends_on:
            pieces.append("\n--- Context from Dependent Tasks ---")
            
            dependency_results = {}
            for dep_id in depends_on:
                # 从 history 中找到依赖任务的结果
                for record in history:
                    if record["node_id"] == dep_id:
                        result = record.get("result", {})
                        if result.get("status") == "success":
                            # 提取任务结果
                            task_result = {
                                "task_id": dep_id,
                                "result": result.get("result", ""),
                                "steps": result.get("steps", [])
                            }
                            dependency_results[dep_id] = task_result
                        break
            
            if dependency_results:
                pieces.append("\nResults from previous tasks (JSON format):")
                pieces.append("```json")
                pieces.append(json.dumps(dependency_results, ensure_ascii=False, indent=2))
                pieces.append("```")
                pieces.append("\nPlease use the above results to complete your task.")
            else:
                pieces.append("\n(No successful results from dependent tasks)")
        
        return "\n".join(pieces)
