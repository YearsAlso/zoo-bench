"""被测框架自身：``zoo-framework``。

**驱动层级**：调度派发层——直接驱动 ``BaseWaiter`` 一次派发一个 worker，不走 ``Master``
的完整运行循环。依据是实测：``Master`` 的循环是 ``while True: execute_service();
await asyncio.sleep(1)``（``core/master.py:288-291``），经它的真实使用延迟被 1 秒轮询支配，
与派发原语开销差三个数量级，且与对照方案不可比。该层级说明会随报告出现（spec:
adapter-contract 的"被测层级被声明"）。

**端到端终点**取"worker 完成并在飞表注销"那一刻。框架随后还有一步 ``worker_report``
（把 ``WorkerResult`` 交给事件反应器），本档不计入——实测该步骤在 0.6.0 上**每次调用都抛**
``AttributeError``：``EventReactorManager.dispatch`` 以 ``reactor_name``（默认 ``"default"``）
为键去查 ``reactor_map``（``reactor/event_reactor_manager.py:44`` 与 ``:74``），而结果反应器
被注册在 **topic** 键 ``"waiter"`` 上（``core/waiter/base_waiter.py:41``）——查不到就返回
``[]``，随后对空列表调 ``.execute``。计入的话只是在测一次必定失败的调用。

**三处与框架 0.6.0 实现耦合的点，都已在探针中实测确认**：

1. ``execute_service()`` 只在 worker **不在** ``worker_props`` 里时才派发，故 ``submit``
   先把 ``workers`` 设成本轮这一个，再调用一次——复现"派发一次"的语义
2. ``worker.name`` 是 ``f"{props['name']}_{num}"`` 而 ``num`` 恒为 1，故**同名 worker 会在
   在飞表里互相覆盖**；本档给每个 worker 唯一名字，否则并发直接失效
3. ``BaseWaiter.__init__`` 会读 ``WorkerParams``；无 ``config.json`` 时退化为默认值
   （实测 ``pool_size=5``、``pool_enable=False``），本档构造后显式覆盖
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from zoo_framework.constant import WaiterConstant
from zoo_framework.core.waiter.base_waiter import BaseWaiter
from zoo_framework.workers import BaseWorker

from .base import DRAIN_TIMEOUT_SECONDS, BaseAdapter, Tier
from .registry import register


class _BodyWorker(BaseWorker):
    """跑一个被测执行体、并按契约取回它自报耗时。"""

    def __init__(self, name: str, body: Callable[[], float]) -> None:
        super().__init__({"name": name, "is_loop": False, "delay_time": 0})
        self._body = body
        self.duration = 0.0
        self.error: BaseException | None = None

    def _execute(self) -> float:
        self.duration = self._body()
        return self.duration

    def _on_error(self) -> None:
        # 框架的 BaseWorker.run() 会 catch Exception 并继续返回 WorkerResult，执行体抛出的
        # 异常被静默吞掉——那样失败会伪装成"耗时 0"的正常数据。这里把它记下来，由 drain 重新抛出。
        self.error = sys.exc_info()[1]


class _ObservingWaiter(BaseWaiter):
    """在框架的派发路径上插入一个观测点。

    观测点选 ``worker_running_callback``：它是框架注销在飞表的那个调用，且**参数是真正的
    worker**（``worker_report`` 收到的是 Future，拿不到 worker 自身）。先调用父类实现，
    保证观测不改变框架原本行为。
    """

    def __init__(self) -> None:
        super().__init__()
        self.on_worker_finished: Callable[[BaseWorker], None] | None = None

    def worker_running_callback(self, worker: BaseWorker) -> None:
        super().worker_running_callback(worker)
        if self.on_worker_finished is not None:
            self.on_worker_finished(worker)


@register
class ZooAdapter(BaseAdapter):
    """驱动 ``BaseWaiter`` 的派发路径，线程池模式。"""

    name = "zoo"
    tier = Tier.UNDER_TEST
    drive_level = (
        "调度派发层：直接驱动 BaseWaiter.execute_service()（线程池模式），"
        "不走 Master 的完整运行循环——后者的服务循环每秒一次，会把延迟量级从微秒抬到秒"
    )
    notes = (
        "端到端终点为「worker 完成并在飞表注销」；框架随后的 worker_report（把 WorkerResult "
        "交给事件反应器）不计入——实测该调用在 0.6.0 上必抛 AttributeError"
        "（dispatch 按 reactor_name 而非 topic 查反应器）"
    )

    def setup(self, *, workers: int) -> None:
        self._cond = threading.Condition()
        self._durations: list[float] = []
        self._errors: list[BaseException] = []
        self._done = 0
        self._expected = 0
        self._seq = 0

        self._waiter = _ObservingWaiter()
        # 覆盖 __init__ 从 WorkerParams 读到的值（无 config.json 时是默认值，不是本档要的并发度）
        self._waiter.worker_mode = WaiterConstant.WORKER_MODE_THREAD_POOL
        self._waiter.pool_enable = True
        self._waiter.pool_size = workers
        self._waiter.resource_pool = ThreadPoolExecutor(max_workers=workers)
        self._waiter.on_worker_finished = self._on_worker_finished

    def _on_worker_finished(self, worker: BaseWorker) -> None:
        with self._cond:
            error = getattr(worker, "error", None)
            if error is not None:
                self._errors.append(error)
            else:
                self._durations.append(getattr(worker, "duration", 0.0))
            self._done += 1
            self._cond.notify_all()

    def submit(self, body: Callable[[], float]) -> None:
        self._seq += 1
        # 唯一名字是必需的：worker_props 以 worker.name 为键，重名会互相覆盖，
        # 第二个 worker 因"已在飞"而被静默跳过，并发直接失效
        worker = _BodyWorker(f"zoo-bench-{self._seq}", body)
        with self._cond:
            self._expected += 1
        self._waiter.workers = [worker]
        self._waiter.execute_service()

    def drain(self) -> list[float]:
        with self._cond:
            not_timed_out = self._cond.wait_for(
                lambda: self._done >= self._expected, timeout=DRAIN_TIMEOUT_SECONDS
            )
            if not not_timed_out:
                raise TimeoutError(
                    f"zoo 档在 {DRAIN_TIMEOUT_SECONDS:.0f}s 内未排空"
                    f"（已提交 {self._expected}，已完成 {self._done}）"
                )
            errors, self._errors = self._errors, []
            out, self._durations = self._durations, []
            self._done = 0
            self._expected = 0

        if errors:
            raise errors[0]
        return out

    def teardown(self) -> None:
        self._waiter.resource_pool.shutdown(wait=True)
