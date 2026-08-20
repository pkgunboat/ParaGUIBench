import datetime
from typing import Dict, Any, List


def json_to_markdown(plan: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# 计划视图（只读）")
    task = plan.get("task", "")
    if task:
        lines.append("")
        lines.append(f"任务: {task}")
    lines.append("")
    for n in plan.get("nodes", []):
        status = n.get("status", "pending")
        mark = "x" if status == "success" else " "
        nid = n.get("id", "")
        title = n.get("title", "")
        agent = n.get("agent", "")
        deps = n.get("depends_on", []) or []
        dep_text = "-" if not deps else ",".join(deps)
        lines.append(f"- [{mark}] ({nid}) {title}（{agent}） 依赖: {dep_text}")
    lines.append("")
    ts = datetime.datetime.utcnow().isoformat()
    lines.append(f"_Generated at: {ts}Z_")
    return "\n".join(lines)


