"""仅测试用的执行体。

带身份的 ``SlotBody`` 与判据辅助已移到 ``zoo_bench.workloads.identity``——**编排层也要用它们**
（runner 的等价性验证），放两份必然漂移。这里只留测试独有的东西。
"""

from __future__ import annotations


class BoomBody:
    """必定抛异常的执行体，用于验证异常向上传播而非被吞掉。"""

    def __init__(self, message: str = "boom") -> None:
        self.message = message

    def __call__(self) -> float:
        raise RuntimeError(self.message)
