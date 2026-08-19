"""
依赖关系可视化脚本
用于展示 execution_record.json 中的依赖关系
"""

import json
import sys
from pathlib import Path


def load_execution_record(json_path):
    """加载执行记录"""
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def visualize_dependencies(record):
    """可视化依赖关系"""
    print("="*80)
    print("DEPENDENCY ANALYSIS REPORT")
    print("="*80)
    print()
    
    # 获取 coordinator rounds
    coordinator = record.get("coordinator", {})
    rounds = coordinator.get("rounds", [])
    
    if not rounds:
        print("⚠️  No rounds found in execution record")
        return
    
    print(f"Total rounds: {len(rounds)}\n")
    
    # 遍历每一轮
    for round_data in rounds:
        round_num = round_data.get("round", "?")
        thought = round_data.get("model_prediction", {}).get("response", "")
        dependencies = round_data.get("dependencies", {})
        
        print(f"{'─'*80}")
        print(f"ROUND {round_num}")
        print(f"{'─'*80}")
        
        # 显示 thought (简化)
        if thought:
            thought_preview = thought[:150].replace('\n', ' ')
            print(f"💭 Thought: {thought_preview}...")
            print()
        
        # 显示依赖关系（简化格式）
        if not dependencies:
            print("  No dependency information available for this round")
        elif isinstance(dependencies, dict):
            # 新的格式: {"unique_id": {"round": 2, "task": "...", "depends_on": [...]}}
            print(f"  Agents in this round: {len(dependencies)}")
            print()
            
            for agent_id, dep_info in dependencies.items():
                if isinstance(dep_info, dict):
                    # 新格式：包含 round, task, depends_on
                    round_num = dep_info.get("round", "?")
                    task = dep_info.get("task", "")[:60]
                    deps = dep_info.get("depends_on", [])
                    
                    print(f"  🔧 {agent_id}")
                    print(f"     Round: {round_num} | Task: {task}...")
                    
                    if deps:
                        print(f"     Dependencies:")
                        for dep in deps:
                            dep_agent_id = dep.get("agent_id", "?")
                            dep_round = dep.get("round", "?")
                            dep_reason = dep.get("reason", "")
                            print(f"       ⬅️  {dep_agent_id} (Round {dep_round})")
                            if dep_reason:
                                print(f"           Reason: {dep_reason}")
                    else:
                        print(f"     🆕 No dependencies (independent task)")
                elif isinstance(dep_info, list):
                    # 旧格式：仅包含依赖列表
                    if dep_info:
                        deps_str = ", ".join(dep_info)
                        print(f"  🔧 {agent_id}")
                        print(f"     ⬅️  Depends on: {deps_str}")
                    else:
                        print(f"  🔧 {agent_id}")
                        print(f"     🆕 No dependencies (independent task)")
                print()
        else:
            # 最旧格式兼容（列表格式）
            print(f"  Tool Calls: {len(dependencies)}")
            print()
            
            for dep in dependencies:
                if isinstance(dep, dict):
                    agent_id = dep.get("tool_call_id", "unknown")
                    func_name = dep.get("function", "unknown")
                    depends_on = dep.get("depends_on", [])
                    
                    print(f"  🔧 {agent_id} ({func_name})")
                    if depends_on:
                        print(f"     Dependencies:")
                        for prev_dep in depends_on:
                            prev_agent = prev_dep.get("tool_call_id", "?")
                            print(f"       ⬅️  {prev_agent}")
                    else:
                        print(f"     🆕 No dependencies")
                    print()
        
        print()
    
    # 生成依赖图摘要
    print(f"{'='*80}")
    print("DEPENDENCY GRAPH SUMMARY")
    print(f"{'='*80}")
    print()
    
    total_agents = 0
    total_dependencies = 0
    independent_agents = 0
    
    for round_data in rounds:
        dependencies = round_data.get("dependencies", {})
        
        if isinstance(dependencies, dict):
            total_agents += len(dependencies)
            for agent_id, dep_info in dependencies.items():
                if isinstance(dep_info, dict):
                    # 新格式：包含 depends_on 字段
                    deps = dep_info.get("depends_on", [])
                    if deps:
                        total_dependencies += len(deps)
                    else:
                        independent_agents += 1
                elif isinstance(dep_info, list):
                    # 旧格式：dep_info 直接是依赖列表
                    if dep_info:
                        total_dependencies += len(dep_info)
                    else:
                        independent_agents += 1
        else:
            # 最旧格式
            total_agents += len(dependencies)
            for dep in dependencies:
                if isinstance(dep, dict):
                    depends_on = dep.get("depends_on", [])
                    if depends_on:
                        total_dependencies += len(depends_on)
                    else:
                        independent_agents += 1
    
    print(f"Total agents called: {total_agents}")
    print(f"Independent agents (no dependencies): {independent_agents}")
    print(f"Agents with dependencies: {total_agents - independent_agents}")
    print(f"Total dependency links: {total_dependencies}")
    print()
    
    if total_agents > 0:
        print(f"Parallelization potential: {independent_agents}/{total_agents} "
              f"({100*independent_agents/total_agents:.1f}%) agents can run independently")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        # 默认路径
        default_path = Path(__file__).parent.parent / "logs" / "execution_record.json"
        if default_path.exists():
            json_path = default_path
            print(f"Using default path: {json_path}\n")
        else:
            print("Usage: python show_dependencies.py <execution_record.json>")
            print(f"Default path not found: {default_path}")
            sys.exit(1)
    else:
        json_path = sys.argv[1]
    
    try:
        record = load_execution_record(json_path)
        visualize_dependencies(record)
    except FileNotFoundError:
        print(f"❌ Error: File not found: {json_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
