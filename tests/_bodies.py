"""可 pickle 的执行体与判据辅助。

单独成模块的原因：多进程档位要把执行体送到子进程，**闭包与嵌套函数不可 pickle**，
执行体必须是模块级的可被 pickle 的对象。文件名不以 ``test_`` 开头，故 pytest 不收集它。

**为什么用"自报耗时"当身份依据，而不是外部账本**：最初用文件账本记录"哪个执行体跑过"，
实测在 Windows 上不可靠——``os.open`` 的 ``O_APPEND`` 在 CRT 里是"先 seek 到末尾再写"、
不是原子追加，同一批并发写会互相覆盖（实测 12 个并发写线程，5 轮得到 10 / 12 / 12 / 12 / 9
行）。改用耗时做判据有三个好处：不需要任何共享状态、天然跨进程可用、且顺带验证了
"执行体必须自报耗时"这条契约本身。
"""

from __future__ import annotations

import time

#: 相邻槽位的耗时间隔（秒）。取 5 ms，远大于测量噪声，使"哪个执行体跑过"可由耗时唯一确定，
#: 同时把 12 个执行体的单轮耗时控制在 60 ms 量级。
SLOT_SECONDS = 0.005


class SlotBody:
    """按序号睡一个可区分的时长，并返回它在自身内部实测的耗时。

    第 ``index`` 个执行体睡 ``SLOT_SECONDS * (index + 1)`` 秒——**耗时即身份**。
    """

    def __init__(self, index: int, slot_seconds: float = SLOT_SECONDS) -> None:
        self.index = index
        self.slot_seconds = slot_seconds

    @property
    def expected_duration(self) -> float:
        """本执行体应报出的耗时。"""
        return self.slot_seconds * (self.index + 1)

    def __call__(self) -> float:
        start = time.perf_counter()
        time.sleep(self.expected_duration)
        return time.perf_counter() - start


class BoomBody:
    """必定抛异常的执行体，用于验证异常向上传播而非被吞掉。"""

    def __init__(self, message: str = "boom") -> None:
        self.message = message

    def __call__(self) -> float:
        raise RuntimeError(self.message)


def expected_durations(count: int, slot_seconds: float = SLOT_SECONDS) -> list[float]:
    """``count`` 个 :class:`SlotBody` 各自应报出的耗时。"""
    return [slot_seconds * (index + 1) for index in range(count)]


def match_one_to_one(
    observed: list[float], expected: list[float], tolerance: float = 0.002
) -> bool:
    """观测值是否与期望值**一一对应**。

    容忍度应小于槽位间隔的一半，否则相邻槽位的区间会重叠、判据失去鉴别力。
    """
    if len(observed) != len(expected):
        return False

    remaining = list(observed)
    for target in expected:
        hits = [value for value in remaining if abs(value - target) <= tolerance]
        if len(hits) != 1:
            return False
        remaining.remove(hits[0])
    return True
