"""以"返回值即身份"判定任务是否各执行一次。

用于适配器的等价性验证：给每个执行体一个**互不相同的返回值**，则"观测到的返回值集合与期望
集合相等"等价于"提交的每个执行体都执行了、且各执行一次"。

**为什么不用耗时当身份**：那是这个模块最早的做法，实测在共享机器上不成立。并发睡眠的超发量
有一个**绝对量级**的尾部——实测（12 路并发、360 个样本）中位只超 +1ms，但 p95 约 **+25ms**、
最坏 **+77ms**，且**不随睡眠时长缩放**（5ms 档与 50ms 档的 p95 几乎一样）。任何"按耗时落点
判定身份"的判据都会被那层尾部偶发打穿：实测约 15% 的测量跑自检随机判否，而判否的是
"未通过等价性验证"这道可信度门禁——一道会随机变红的门禁，很快就会被学会忽略。
返回值相等是**精确比较**，与负载无关。

**为什么不用外部账本**：也试过。最早用文件账本记录"哪个执行体跑过"，实测在 Windows 上不可靠
——``os.open`` 的 ``O_APPEND`` 在 CRT 里是"先 seek 到末尾再写"、不是原子追加，并发写会互相
覆盖（12 个并发写线程 5 轮得到 10/12/12/12/9 行）。返回值不需要任何共享状态、天然跨进程可用，
且顺带验证了"执行体的返回值确实被回传"这条契约本身。
"""

from __future__ import annotations

import time

#: 执行体的并发重叠时长（秒）。它**不参与身份判定**，只用来让多个执行体同时在飞——
#: 否则"并发下不丢任务"这件事根本没被验证到。取短值即可：身份已不由耗时承载，重叠时长
#: 不再需要留出分辨身份的空间。
DEFAULT_OVERLAP_SECONDS = 0.005

#: 标记的基准值。刻意远离 0：留档或失败信息里出现 ``1002.0`` 时，读者一眼能看出那是标记而不是
#: 一个耗时数字——两者混起来读，就会拿它去解释"这个单元为什么这么快"。
MARKER_BASE = 1000.0


class MarkerBody:
    """第 ``index`` 个执行体：睡一小段以保持并发，返回一个**唯一标记**作为身份。"""

    def __init__(self, index: int, overlap_seconds: float = DEFAULT_OVERLAP_SECONDS) -> None:
        self.index = index
        self.overlap_seconds = overlap_seconds

    @property
    def marker(self) -> float:
        """本执行体的身份（也是它返回的值）。"""
        return MARKER_BASE + self.index

    def __call__(self) -> float:
        time.sleep(self.overlap_seconds)
        return self.marker


def expected_markers(count: int) -> list[float]:
    """``count`` 个 :class:`MarkerBody` 各自的标记。"""
    return [MARKER_BASE + index for index in range(count)]


def matches_expected_set(observed: list[float], expected: list[float]) -> bool:
    """观测到的标记多重集是否与期望**精确相等**。

    比较的是**排序后的多重集**，因而三种错都判否：有任务没跑（某个标记一次都没出现）、
    有任务跑了两次（某个标记多出现一次）、有任务被张冠李戴（出现未知标记）。

    **不能用集合比较代替**：集合会把"重复"抹平——一个任务跑了两次而另一个没跑，观察到的
    集合仍可能与期望集合相等，于是这道门禁在最该抓的那一类错误上恒真。实测注入集合比较时，
    反例用例立刻变红。

    判据里**没有任何容差**：这是它与"按耗时落点匹配"的关键差别——后者的结论随机器负载翻转，
    前者不。
    """
    return sorted(observed) == sorted(expected)
