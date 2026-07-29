"""ParaGUIBench benchmark 数据契约与物化入口。"""

from paraguibench.benchmark.agent_view import build_agent_task_view
from paraguibench.benchmark.errors import TaskMaterializationError
from paraguibench.benchmark.materialization import materialize_task
from paraguibench.benchmark.preparation import (
    PreparedTask,
    TaskPreparationError,
    prepare_release_task,
)
from paraguibench.benchmark.release import (
    ReleaseFixtureRecord,
    ReleaseTaskError,
    ReleaseTaskRecord,
    load_release_fixture,
    load_release_task,
    load_release_task_record,
)

__all__ = [
    "PreparedTask",
    "ReleaseFixtureRecord",
    "ReleaseTaskError",
    "ReleaseTaskRecord",
    "TaskMaterializationError",
    "TaskPreparationError",
    "build_agent_task_view",
    "load_release_fixture",
    "load_release_task",
    "load_release_task_record",
    "materialize_task",
    "prepare_release_task",
]
