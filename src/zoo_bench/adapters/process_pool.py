"""标准库其他并发模型之二：``ProcessPoolExecutor``。

口径说明：执行体与返回值都要跨进程序列化，该成本计入端到端。因此本档与多线程档位
**不可直接等价比较**——它的数字里含有 zoo 不承担的那部分成本。该限制写在 ``notes``
里，会随数据出现在报告中（design D3 与 Risks 的同一条要求）。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor

from .base import BaseAdapter, Tier
from .registry import register


@register
class ProcessPoolAdapter(BaseAdapter):
    """多进程池。执行体必须可 pickle（见契约文档）。"""

    name = "process_pool"
    tier = Tier.STDLIB
    notes = "ProcessPoolExecutor：执行体与返回值跨进程序列化的成本计入端到端，与多线程档位不可直接等价比较"

    def setup(self, *, workers: int) -> None:
        self._pool = ProcessPoolExecutor(max_workers=workers)
        self._futures: list[Future[float]] = []

    def submit(self, body: Callable[[], float]) -> None:
        self._futures.append(self._pool.submit(body))

    def drain(self) -> list[float]:
        futures, self._futures = self._futures, []
        return [future.result() for future in futures]

    def teardown(self) -> None:
        self._pool.shutdown(wait=True)
