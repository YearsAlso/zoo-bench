"""裸写法之二：``concurrent.futures.ThreadPoolExecutor``。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from .base import BaseAdapter, Tier
from .registry import register


@register
class ThreadPoolAdapter(BaseAdapter):
    """标准库线程池，池大小 = 并发度。"""

    name = "thread_pool"
    tier = Tier.BARE
    notes = "adapters.notes.thread_pool"

    def setup(self, *, workers: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers)
        self._futures: list[Future[float]] = []

    def submit(self, body: Callable[[], float]) -> None:
        self._futures.append(self._pool.submit(body))

    def drain(self) -> list[float]:
        futures, self._futures = self._futures, []
        # .result() 在首个异常上抛出，使该测量单元被标记失败而非产出可疑数字
        return [future.result() for future in futures]

    def teardown(self) -> None:
        self._pool.shutdown(wait=True)
