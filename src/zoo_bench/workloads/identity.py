"""以"耗时即身份"判定任务是否各执行一次。

用于适配器的等价性验证：给每个执行体一个**互不相同**的耗时，则"观测到的耗时集合与期望
集合一一对应"等价于"提交的每个执行体都执行了、且各执行一次"。

为什么不用外部账本：最初用文件账本记录"哪个执行体跑过"，实测在 Windows 上不可靠——
``os.open`` 的 ``O_APPEND`` 在 CRT 里是"先 seek 到末尾再写"、不是原子追加，并发写会互相
覆盖（12 个并发写线程 5 轮得到 10/12/12/12/9 行）。用耗时做身份不需要任何共享状态、
天然跨进程可用，且顺带验证了"执行体必须自报耗时"这条契约本身。
"""

from __future__ import annotations

import time

#: 相邻槽位的耗时间隔（秒）。远大于测量噪声，使身份可由耗时唯一确定。
DEFAULT_SLOT_SECONDS = 0.005

#: 容忍度。应小于槽位间隔的一半，否则相邻槽位的区间会重叠、判据失去鉴别力。
DEFAULT_TOLERANCE_SECONDS = 0.002


class SlotBody:
    """第 ``index`` 个执行体睡 ``slot_seconds * (index + 1)`` 秒——**耗时即身份**。"""

    def __init__(self, index: int, slot_seconds: float = DEFAULT_SLOT_SECONDS) -> None:
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


def expected_durations(count: int, slot_seconds: float = DEFAULT_SLOT_SECONDS) -> list[float]:
    """``count`` 个 :class:`SlotBody` 各自应报出的耗时。"""
    return [slot_seconds * (index + 1) for index in range(count)]


def match_one_to_one(
    observed: list[float],
    expected: list[float],
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """观测值是否与期望值**一一对应**。

    条数不等、或某个期望槽位被零个/多个观测值命中，都判否。
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
