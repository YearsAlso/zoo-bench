"""裸写法之一：每任务派生一个线程。

不做池化、不复用；并发度由"提交了多少任务"决定，``workers`` 对本档无约束作用。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from .base import BaseAdapter, Tier
from .registry import register


@register
class BareThreadAdapter(BaseAdapter):
    """每任务一个 ``threading.Thread``。"""

    name = "bare_thread"
    tier = Tier.BARE
    notes = "adapters.notes.bare_thread"

    def setup(self, *, workers: int) -> None:
        # workers 对本档无约束作用：并发度等于提交数。保留参数以符合契约。
        self._threads: list[threading.Thread] = []
        self._durations: list[float] = []
        self._errors: list[BaseException] = []

    def submit(self, body: Callable[[], float]) -> None:
        def _run() -> None:
            # 异常与耗时都直接 append：**刻意不加锁**。list.append 在 GIL 下是原子的，而每任务
            # 一次加锁会让本档在并发度 64 时付出 64 个线程争一把锁的代价——那是插桩的成本，
            # 不是"每任务一线程"这个方案的。混进去，对照就成了插桩的对照。
            try:
                value = body()
            except BaseException as exc:
                self._errors.append(exc)
                return
            self._durations.append(value)

        thread = threading.Thread(target=_run)
        thread.start()
        self._threads.append(thread)

    def drain(self) -> list[float]:
        pending, self._threads = self._threads, []
        for thread in pending:
            thread.join()

        errors, self._errors = self._errors, []
        durations, self._durations = self._durations, []

        if errors:
            raise errors[0]
        return durations

    def teardown(self) -> None:
        for thread in self._threads:
            thread.join()
        self._threads.clear()
