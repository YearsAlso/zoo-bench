"""被测框架自身：``zoo-framework``。

**驱动层级**：调度派发层——直接驱动 ``BaseWaiter`` 一次派发一个 worker，不走 ``Master``
的完整运行循环。依据是实测：``Master`` 的循环是 ``while True: execute_service();
await asyncio.sleep(1)``，经它的真实使用延迟被 1 秒轮询支配，与派发原语开销差三个数量级，
且与对照方案不可比。该层级说明会随报告出现（spec: adapter-contract 的"被测层级被声明"）。

**两代驱动面**：被测框架的派发面重构过，本档**在同一个适配器内按世代分流**（design D1），
对外的适配器标识与契约不变。世代判定与两代的形态差异见
:mod:`zoo_bench.generations`；本模块只负责"每一代各自怎么驱动"：

===================================  ==========================================
上一代                                当前代
===================================  ==========================================
构造后赋属性再赋值 ``workers``        构造参数装配 + ``call_workers([...])``
拦 ``worker_running_callback``        框架自己的结果响应器 ``on_result``
``resource_pool.shutdown()``          ``waiter.shutdown(wait=True)``
===================================  ==========================================

**为什么完成信号两代不同**：上一代的 ``EventReactorManager.dispatch`` 以 ``reactor_name``
（默认 ``"default"``）而不是 topic 去查响应器表，取不到就返回空列表、随后对空列表调
``.execute``——**每次调用必抛** ``AttributeError``，故那一代没有可用的框架投递路径，只能拦
内部回调。当前代把该处改成 ``reactor_name=None`` 不过滤，结果响应器的投递可用，于是走框架
自己的路径（那是真实使用形态的一部分，design D3）。
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zoo_framework.constant import WaiterConstant
from zoo_framework.core.waiter.base_waiter import BaseWaiter
from zoo_framework.reactor.waiter_result_reactor import WaiterResultReactor
from zoo_framework.workers import BaseWorker

from ..generations import GENERATION_CURRENT, probe_drive_generation
from .base import DRAIN_TIMEOUT_SECONDS, BaseAdapter, Tier
from .registry import register


class _BodyWorker(BaseWorker):
    """跑一个被测执行体、并按契约取回它自报耗时。

    ``on_failure`` **只在当前代需要**：那一代的 ``run()`` 把执行体异常**重抛**，``settle``
    收到 error 后只记日志、不投递结果，故成功路径的响应器收不到失败——只靠响应器判定完成，
    一个失败单元要等到 ``drain`` 超时。上一代相反：``run()`` 吞掉异常并照常返回结果，
    ``worker_running_callback`` 仍会触发，补位通知会与它重复计数，故那里传 None。
    """

    def __init__(
        self,
        name: str,
        body: Callable[[], float],
        *,
        on_failure: Callable[[BaseWorker], None] | None = None,
    ) -> None:
        super().__init__({"name": name, "is_loop": False, "delay_time": 0})
        self._body = body
        self._on_failure = on_failure
        self.duration = 0.0
        self.error: BaseException | None = None

    def _execute(self) -> float:
        self.duration = self._body()
        return self.duration

    def _on_error(self) -> None:
        # 框架会把执行体抛出的异常吞掉（上一代）或重抛（当前代）——两种情形下都要把它记下来，
        # 否则失败会伪装成"耗时 0"的正常数据。
        self.error = sys.exc_info()[1]
        if self._on_failure is not None:
            self._on_failure(self)


class _ObservingWaiter(BaseWaiter):
    """上一代的完成信号观测点。

    观测点只能选 ``worker_running_callback``：它是框架注销在飞表的那个调用，且**参数是真正的
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
    """驱动 ``BaseWaiter`` 的派发路径，线程池模式。按世代分流（design D1）。

    Attributes:
        generation: ``setup`` 探测到的驱动面世代；未 ``setup`` 时该属性不存在。
        drive_surface: ``setup`` 的完整探测结果（含逐项形态），供留档回答"按哪一代驱动"。
    """

    name = "zoo"
    tier = Tier.UNDER_TEST
    #: 自述存**目录键**而不是散文：留档因此是语言无关的数据，同一份留档能出两种语言的报告
    #: （design D8）。渲染侧经 ``resolve_text`` 解析，见 ``render/blocks.py``。
    drive_level = "adapters.drive_level.zoo"
    notes = "adapters.notes.zoo"

    def setup(self, *, workers: int) -> None:
        # 世代判定先于任何驱动动作：判定不出来就抛出，**不留下可驱动的状态**
        surface = probe_drive_generation()
        self.generation = str(surface["generation"])
        self.drive_surface = surface

        self._cond = threading.Condition()
        self._durations: list[float] = []
        self._errors: list[BaseException] = []
        self._pending: dict[str, BaseWorker] = {}
        self._done = 0
        self._expected = 0
        self._seq = 0

        if self.generation == GENERATION_CURRENT:
            self._setup_current(workers)
        else:
            self._setup_previous(workers)

    # ------------------------------------------------------------------ 装配

    def _setup_current(self, workers: int) -> None:
        """当前代装配：模式与池尺寸经构造参数交给调度器；完成信号取自框架的结果响应器。

        ``on_result`` 是框架为结果响应器设计的对外入口（``WaiterResultReactor`` 的文档写明
        "已接收的结果通过可选的 on_result 回调转交"），且该响应器是**进程级唯一实例**，与
        调度器登记在结果主题上的正是同一个对象——本档不新增响应器，故不给派发路径加额外开销。
        """
        self._waiter = BaseWaiter(
            model_name=WaiterConstant.WORKER_MODE_THREAD_POOL, pool_size=workers
        )
        self._reactor = WaiterResultReactor()
        self._reactor.on_result = self._on_result

    def _setup_previous(self, workers: int) -> None:
        """上一代装配：构造后赋值覆盖从配置读到的值，完成信号取自框架内部回调。"""
        self._waiter = _ObservingWaiter()
        # 覆盖 __init__ 从 WorkerParams 读到的值（无 config.json 时是默认值，不是本档要的并发度）
        self._waiter.worker_mode = WaiterConstant.WORKER_MODE_THREAD_POOL
        self._waiter.pool_enable = True
        self._waiter.pool_size = workers
        self._waiter.resource_pool = ThreadPoolExecutor(max_workers=workers)
        self._waiter.on_worker_finished = self._record

    # ------------------------------------------------------------------ 完成信号

    def _record(self, worker: BaseWorker) -> None:
        """记下一次完成（成功或失败），并唤醒 ``drain``。两代共用的收口。"""
        with self._cond:
            self._pending.pop(worker.name, None)
            error = getattr(worker, "error", None)
            if error is not None:
                self._errors.append(error)
            else:
                self._durations.append(getattr(worker, "duration", 0.0))
            self._done += 1
            self._cond.notify_all()

    def _on_result(self, result: Any) -> None:
        """当前代的完成信号：框架把 ``WorkerResult`` 交给结果响应器时回调。

        只认本档提交过、且尚未记入的 worker（按 ``worker_name`` 查在册表）：结果响应器是
        进程级唯一实例，别的调度器产生的结果也会走到这里。
        """
        worker = self._pending.get(getattr(result, "worker_name", None))
        if worker is not None:
            self._record(worker)

    # ------------------------------------------------------------------ 驱动

    def submit(self, body: Callable[[], float]) -> None:
        self._seq += 1
        # 唯一名字是必需的：在飞表以 worker.name 为键，重名会互相覆盖，后一个 worker
        # 因"已在飞"而被静默跳过，并发直接失效
        worker = _BodyWorker(
            f"zoo-bench-{self._seq}",
            body,
            on_failure=self._record if self.generation == GENERATION_CURRENT else None,
        )
        with self._cond:
            self._expected += 1
            self._pending[worker.name] = worker

        if self.generation == GENERATION_CURRENT:
            # 当前代的调度列表由内核持有，只能经 call_workers 送入（workers 是只读属性）
            self._waiter.call_workers([worker])
        else:
            self._waiter.workers = [worker]
        self._waiter.execute_service()

    def drain(self) -> list[float]:
        with self._cond:
            drained = self._cond.wait_for(
                lambda: self._done >= self._expected, timeout=DRAIN_TIMEOUT_SECONDS
            )
            errors, self._errors = self._errors, []
            out, self._durations = self._durations, []
            pending, self._pending = self._pending, {}
            expected, self._expected = self._expected, 0
            done, self._done = self._done, 0

        if not drained:
            raise TimeoutError(self._stalled_message(pending, expected, done))

        if not errors:
            # 失败路径的通知发生在摘在飞表**之前**（见 _BodyWorker），那时两者本就不一致
            self._check_inflight_agrees()

        if errors:
            raise errors[0]
        return out

    def _check_inflight_agrees(self) -> None:
        """交叉验证完成判定：完成信号说"都完成了"，在飞表就必须已经空了。

        当前代的 ``settle`` 是**先摘在飞表、再投递结果**，故收齐完成信号时在飞表必为空。
        两者不一致说明完成判定不可信（信号可能来自别处），此时不给数字比给错数字好。
        """
        core = getattr(self._waiter, "core", None)
        if core is None:
            return
        inflight = int(core.metrics()["inflight"])
        if inflight:
            raise RuntimeError(
                f"完成判定不自洽：结果响应器报告的执行体都已结束，但在飞表里仍有 {inflight} 个"
            )

    def _stalled_message(self, pending: dict[str, BaseWorker], expected: int, done: int) -> str:
        """超时的失败说明：把完成信号的自洽性一并给出，否则排查只能靠猜。"""
        message = (
            f"zoo 档在 {DRAIN_TIMEOUT_SECONDS:.0f}s 内未排空（已提交 {expected}，已完成 {done}）"
        )
        core = getattr(self._waiter, "core", None)
        if core is None:
            return message

        still_inflight = [worker for worker in pending.values() if core.is_inflight(worker)]
        if still_inflight:
            return message + f"；其中 {len(still_inflight)} 个仍在在飞表里，说明它们尚未执行完"
        return message + "；未完成的 worker 已不在在飞表里（已执行完但完成信号未送达，或未被派发）"

    def teardown(self) -> None:
        if self.generation == GENERATION_CURRENT:
            # 只撤销本档装上的回调：响应器是进程级唯一实例，留着它会把后续调度器的结果
            # 投给一个已经结束的适配器实例
            if self._reactor.on_result == self._on_result:
                self._reactor.on_result = None
            self._waiter.shutdown(wait=True)
            return

        pool = getattr(self._waiter, "resource_pool", None)
        if pool is not None:
            # 先摘引用再关池：`__del__` 只是兜底，显式收尾才是这条路径的终点；也让本方法幂等
            self._waiter.resource_pool = None
            pool.shutdown(wait=True)
