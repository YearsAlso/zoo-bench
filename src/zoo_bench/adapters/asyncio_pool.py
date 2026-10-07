"""标准库其他并发模型之一：``asyncio`` 事件循环。

口径说明（务必先读）：契约中的执行体是**同步**函数，因此本档做的是"以事件循环调度同步
执行体"（等价于 ``loop.run_in_executor``），**不是原生协程并发**。它与线程池的差别在于
事件循环自身的开销，而不是"协程比线程轻"。

之所以不做原生协程对照：那要求执行体提供异步变体，同一个执行体就得写两份、且两份的
工作量难以证明等价——这与 design D7 拒绝给裸写法补实现语义是同一个理由。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .base import BaseAdapter, Tier
from .registry import register


@register
class AsyncioPoolAdapter(BaseAdapter):
    """事件循环 + 线程池执行器调度同步执行体。"""

    name = "asyncio_pool"
    tier = Tier.STDLIB
    notes = "adapters.notes.asyncio_pool"

    def setup(self, *, workers: int) -> None:
        self._loop = asyncio.new_event_loop()
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._futures: list[asyncio.Future[float]] = []

    def submit(self, body: Callable[[], float]) -> None:
        # run_in_executor 返回的是 Future 而非协程，不能交给 create_task
        self._futures.append(self._loop.run_in_executor(self._executor, body))

    def drain(self) -> list[float]:
        futures, self._futures = self._futures, []
        return list(self._loop.run_until_complete(asyncio.gather(*futures)))

    def teardown(self) -> None:
        self._executor.shutdown(wait=True)
        self._loop.close()
