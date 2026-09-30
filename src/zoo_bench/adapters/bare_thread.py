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
    notes = "每任务派生一个 threading.Thread，无池化、无复用；线程创建成本计入端到端"

    def setup(self, *, workers: int) -> None:
        # workers 对本档无约束作用：并发度等于提交数。保留参数以符合契约。
        self._threads: list[threading.Thread] = []
        self._durations: list[float] = []
        self._errors: list[BaseException] = []
        self._lock = threading.Lock()

    def submit(self, body: Callable[[], float]) -> None:
        def _run() -> None:
            # 线程里的异常不会自动传到调用方；不存下来就会变成"少一条结果"的静默失败，
            # 而契约要求异常向上传播
            try:
                value = body()
            except BaseException as exc:
                with self._lock:
                    self._errors.append(exc)
                return

            with self._lock:
                self._durations.append(value)

        thread = threading.Thread(target=_run)
        thread.start()
        self._threads.append(thread)

    def drain(self) -> list[float]:
        pending, self._threads = self._threads, []
        for thread in pending:
            thread.join()
        with self._lock:
            errors, self._errors = self._errors, []
            out, self._durations = self._durations, []

        if errors:
            raise errors[0]
        return out

    def teardown(self) -> None:
        for thread in self._threads:
            thread.join()
        self._threads.clear()
