"""
进度展示与日志基础设施。

功能:
    - ThreadLocalStdout: 线程感知的 stdout 代理，拦截子线程 print() 到独立缓冲区
    - ProgressState: 线程安全的三层进度状态管理
    - DashboardRenderer: Rich Live 仪表板渲染（可选依赖，优雅降级）
"""

import io
import sys
import threading
import time
from typing import Any, Dict, List, Optional


class ThreadLocalStdout:
    """
    线程感知的 stdout 代理，每个线程可以有独立的输出目标。

    功能:
        在子线程中调用 set_buffer(buf) 后，该线程的所有 print() 输出
        都会写入 buf（StringIO），而非真正的 stdout。
        主线程和未设置 buffer 的线程仍然输出到原始 stdout。

    输入:
        original_stdout: 原始的 sys.stdout 对象

    使用方式:
        proxy = ThreadLocalStdout(sys.stdout)
        sys.stdout = proxy  # 安装代理

        # 在子线程中:
        buf = io.StringIO()
        proxy.set_buffer(buf)
        print("这会进入 buf")
        proxy.clear_buffer()
    """

    def __init__(self, original_stdout):
        self._original = original_stdout
        self._local = threading.local()

    def set_buffer(self, buf: io.StringIO):
        """设置当前线程的输出缓冲区。"""
        self._local.buf = buf

    def clear_buffer(self):
        """清除当前线程的输出缓冲区，恢复到原始 stdout。"""
        self._local.buf = None

    def get_buffer(self) -> Optional[io.StringIO]:
        """获取当前线程的输出缓冲区（如果有）。"""
        return getattr(self._local, 'buf', None)

    def write(self, text):
        """
        写入文本。如果当前线程有缓冲区则写入缓冲区，否则写入原始 stdout。

        输入:
            text: 要写入的文本
        """
        buf = getattr(self._local, 'buf', None)
        if buf is not None:
            buf.write(text)
        else:
            self._original.write(text)

    def flush(self):
        """刷新输出。"""
        buf = getattr(self._local, 'buf', None)
        if buf is not None:
            buf.flush()
        else:
            self._original.flush()

    def __getattr__(self, name):
        """转发未知属性到原始 stdout（fileno, encoding, isatty 等）。"""
        return getattr(self._original, name)


class ProgressState:
    """
    线程安全的三层进度状态管理器，供 Rich 仪表板读取。

    功能:
        跟踪 condition → pipeline → task 三层进度，
        以及每个并行线程的实时状态和最近完成的任务。

    线程安全:
        所有写操作都在 self._lock 保护下执行。
        读操作（仪表板渲染）通过 snapshot() 获取一致性快照。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.condition: Dict[str, Any] = {"current": "", "index": 0, "total": 0}
        self.pipeline: Dict[str, Any] = {"current": "", "index": 0, "total": 0}
        self.tasks: Dict[str, int] = {
            "total": 0, "completed": 0, "passed": 0, "failed": 0, "errored": 0
        }
        self.threads: Dict[str, dict] = {}
        self.recent: List[dict] = []

    def set_condition(self, name: str, index: int, total: int):
        """
        更新 condition 层进度。

        输入:
            name: 当前 condition 名称（如 "baseline"）
            index: 当前索引（从 1 开始）
            total: 总数
        """
        with self._lock:
            self.condition = {"current": name, "index": index, "total": total}

    def set_pipeline(self, name: str, index: int, total: int):
        """
        更新 pipeline 层进度。

        输入:
            name: 当前 pipeline 名称（如 "qa"）
            index: 当前索引（从 1 开始）
            total: 总数
        """
        with self._lock:
            self.pipeline = {"current": name, "index": index, "total": total}

    def set_task_total(self, total: int):
        """
        设置当前 pipeline 的任务总数，并重置计数器。

        输入:
            total: 任务总数
        """
        with self._lock:
            self.tasks = {
                "total": total, "completed": 0, "passed": 0, "failed": 0, "errored": 0
            }
            self.threads = {}

    def update_thread(self, thread_name: str, task_id: str, status: str,
                      elapsed: float, **kwargs):
        """
        更新指定线程的当前任务状态。

        输入:
            thread_name: 线程名称（如 "T-1"）
            task_id: 当前任务 ID
            status: 状态描述（如 "Round 2/5 API call..."）
            elapsed: 已运行秒数
            **kwargs: 额外信息（如 round_info）
        """
        with self._lock:
            self.threads[thread_name] = {
                "task_id": task_id, "status": status,
                "elapsed": elapsed, **kwargs
            }

    def clear_thread(self, thread_name: str):
        """
        清除已完成任务的线程槽位。

        输入:
            thread_name: 线程名称
        """
        with self._lock:
            self.threads.pop(thread_name, None)

    def complete_task(self, task_id: str, status: str, elapsed: float,
                      rounds: int, cost: float):
        """
        记录任务完成。

        输入:
            task_id: 任务 ID
            status: "pass" | "fail" | "error"
                - pass: 评估通过 (score == 1.0)
                - fail: 评估不通过 (score < 1.0)
                - error: 执行异常 (API 错误、超时等，未产生有效分数)
            elapsed: 总耗时秒数
            rounds: Plan Agent 轮次数
            cost: 费用（USD）
        """
        with self._lock:
            self.tasks["completed"] += 1
            if status == "pass":
                self.tasks["passed"] += 1
            elif status == "fail":
                self.tasks["failed"] += 1
            else:
                self.tasks["errored"] += 1
            self.recent.insert(0, {
                "task_id": task_id, "status": status,
                "elapsed": elapsed, "rounds": rounds, "cost": cost
            })
            self.recent = self.recent[:5]

    def snapshot(self) -> dict:
        """
        获取当前状态的一致性快照（供仪表板渲染用）。

        输出:
            包含 condition, pipeline, tasks, threads, recent 的字典副本
        """
        with self._lock:
            return {
                "condition": dict(self.condition),
                "pipeline": dict(self.pipeline),
                "tasks": dict(self.tasks),
                "threads": {k: dict(v) for k, v in self.threads.items()},
                "recent": list(self.recent),
            }


# Rich 可选依赖
try:
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


class DashboardRenderer:
    """
    Rich Live 仪表板渲染器。

    功能:
        从 ProgressState 快照渲染三层进度面板，每 0.5s 刷新。
        如果 rich 未安装，所有方法为空操作（优雅降级）。

    输入:
        state: ProgressState 实例
        enabled: 是否启用仪表板（False 时所有方法为空操作）
    """

    def __init__(self, state: ProgressState, enabled: bool = True):
        self._state = state
        self._enabled = enabled and HAS_RICH
        self._live: Optional[Any] = None

    def start(self):
        """启动仪表板。"""
        if not self._enabled:
            return
        self._live = Live(self._render(), refresh_per_second=2)
        self._live.start()

    def stop(self):
        """停止仪表板。"""
        if self._live:
            self._live.stop()
            self._live = None

    def update(self):
        """手动触发一次渲染刷新。"""
        if self._live:
            self._live.update(self._render())

    def _render(self):
        """
        从 ProgressState 快照渲染面板。

        输出:
            rich.panel.Panel 对象
        """
        snap = self._state.snapshot()
        cond = snap["condition"]
        pipe = snap["pipeline"]
        tasks = snap["tasks"]
        threads = snap["threads"]
        recent = snap["recent"]

        # 标题行
        title = f"Ablation: {cond['current']} ({cond['index']}/{cond['total']})"

        # 进度条（纯文本模拟）
        total = tasks["total"] or 1
        completed = tasks["completed"]
        pct = completed / total * 100
        bar_len = 20
        filled = int(bar_len * completed / total)
        bar = "\u2588" * filled + "\u2591" * (bar_len - filled)

        lines = []
        lines.append(f"Pipeline: {pipe['current']} ({pipe['index']}/{pipe['total']})")
        lines.append(
            f"Tasks: {completed}/{total} {bar} {pct:.0f}%   "
            f"Pass: {tasks['passed']}  Fail: {tasks['failed']}  Err: {tasks['errored']}"
        )
        lines.append("")

        # 线程表
        if threads:
            lines.append(
                f"{'Thread':<8} \u2502 {'Task ID':<35} \u2502 {'Status':<20} \u2502 {'Time':>5}"
            )
            lines.append(
                f"{chr(0x2500)*8} \u2502 {chr(0x2500)*35} \u2502 {chr(0x2500)*20} \u2502 {chr(0x2500)*5}"
            )
            for tname in sorted(threads.keys()):
                t = threads[tname]
                tid = t.get("task_id", "")
                if len(tid) > 35:
                    tid = tid[:32] + "..."
                status = t.get("status", "")
                if len(status) > 20:
                    status = status[:17] + "..."
                elapsed = t.get("elapsed", 0)
                lines.append(
                    f"{tname:<8} \u2502 {tid:<35} \u2502 {status:<20} \u2502 {elapsed:>4.0f}s"
                )

        # 最近完成
        if recent:
            lines.append("")
            for r in recent[:3]:
                icon = "\u2713" if r["status"] == "pass" else (
                    "\u2717" if r["status"] == "fail" else "!"
                )
                tid = r["task_id"]
                if len(tid) > 35:
                    tid = tid[:32] + "..."
                lines.append(
                    f"  {icon} {tid}  {r['elapsed']:.1f}s  "
                    f"{r['rounds']}rnd  ${r['cost']:.2f}"
                )

        content = "\n".join(lines)
        return Panel(Text(content), title=title, border_style="blue")
