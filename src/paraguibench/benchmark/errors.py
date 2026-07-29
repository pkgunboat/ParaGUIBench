"""Benchmark 数据准备阶段的公开异常。"""


class TaskMaterializationError(ValueError):
    """表示 canonical task 无法安全、完整地物化。"""
