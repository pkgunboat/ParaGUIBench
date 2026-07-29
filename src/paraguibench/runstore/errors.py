"""RunStore 调用方可处理的领域异常。"""


class RunStoreConflictError(RuntimeError):
    """表示稳定标识已经绑定到不同的不可变记录。

    输入参数：
        继承 ``RuntimeError`` 的错误消息参数。
    输出返回值：
        该异常由 RunStore 在 task snapshot 或 Attempt 身份冲突时抛出，
        调用方不得把它降级为覆盖写入。
    """
