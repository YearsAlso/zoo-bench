"""该线的驱动面：在**调度派发层**驱动 Worker 单元（design D2）.

层级与主矩阵的 zoo 档**同一层**：线程池模式的 :class:`BaseWaiter` + 框架自己的结果响应器
（``WaiterResultReactor.on_result``）。层级不变，"被测层级被声明"这条既有结论对该线继续成立；
换掉的只是**执行单元的形状**——主矩阵提交的是合成执行体（``body``），该线提交的是真正的
Worker（三形态各是一个 Worker 类）。这也是它与主矩阵适配器契约不共用的原因：适配器的
``submit`` 只接受 ``Callable[[], float]``，而该线的单元是 Worker 对象。

**完成判定沿用框架自己的投递路径**（与 zoo 档当前代同形）：成功的结果经结算点投递到结果
响应器，由它回调本驱动面。失败的结果**不经投递**（结算点收到 error 时只记日志），故失败由
Worker 侧的 ``_on_error`` 钩子回报——两路合起来才是"全部结束"的完整信号。

**一次提交一个调度轮**（``call_workers`` + ``execute_service``）：调度列表由框架内核持有，只能
经该入口送入。并发靠池的线程数实现——同一轮里先后送入的 Worker 同时停在池里执行。
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from typing import Any

from .forms import build_worker
from .inputs import TierSpec

#: 单轮的排空上限（秒）。该线的单元是微秒级任务，故上限只需覆盖"信号没送来"这一异常情形；
#: 它不是被测指标。
DRAIN_TIMEOUT_SECONDS = 120.0


class WorkerDriver:
    """驱动 Worker 单元的派发层驱动面.

    用法（一个测量组一次）::

        driver = WorkerDriver(workers=4)
        try:
            for _ in range(rounds):
                driver.submit_many(lambda seq: build_worker(...), batch=4)
        finally:
            driver.teardown()

    Attributes:
        generation: 装配时探测到的框架驱动面世代（供留档回答"按哪一代驱动"）。
    """

    def __init__(self, workers: int):
        """Args:
        workers: 池大小（＝该组的并发度）
        """
        from zoo_framework.constant import WaiterConstant
        from zoo_framework.core.waiter.base_waiter import BaseWaiter
        from zoo_framework.reactor.waiter_result_reactor import WaiterResultReactor

        from ..generations import GENERATION_CURRENT, probe_drive_generation

        surface = probe_drive_generation()
        self.generation = str(surface["generation"])
        self.drive_surface = surface
        if self.generation != GENERATION_CURRENT:
            # 该线的驱动面只按当前代装配。装上的是上一代时**明确拒绝**，不猜另一代的写法：
            # 猜错会让"量到了什么"无从判断（与 zoo 档按世代分流不同的是，该线所依赖的能力
            # 只存在于当前代，故这里不需要第二套装配）
            raise RuntimeError(
                f"该线的驱动面只适配当前代（{GENERATION_CURRENT}），"
                f"装的是「{self.generation}」代；驱动面未适配就不能出数"
            )

        self._cond = threading.Condition()
        self._done = 0
        self._expected = 0
        self._errors: list[BaseException] = []
        self._pending: dict[str, Any] = {}
        self._waiter = BaseWaiter(
            model_name=WaiterConstant.WORKER_MODE_THREAD_POOL, pool_size=workers
        )
        # 结果响应器是**进程级唯一实例**，与调度器登记在结果主题上的是同一个对象——本驱动面
        # 不新增响应器，故不给派发路径加额外开销（与 zoo 档同一条理由）
        self._reactor = WaiterResultReactor()
        self._reactor.on_result = self._on_result

    # ---------------------------------------------------------------- 提交与排空

    def submit_many(self, factory: Callable[[int], Any], batch: int) -> None:
        """一轮：构造并提交 ``batch`` 个单元.

        Args:
            factory: 提交序号 → Worker 实例。
            batch: 本轮提交个数（＝该组的并发度）。
        """
        for index in range(batch):
            worker = factory(index)
            with self._cond:
                self._expected += 1
                self._pending[worker.name] = worker
            # 调度列表由内核持有（只读属性），只能经 call_workers 送入
            self._waiter.call_workers([worker])
            self._waiter.execute_service()

    def drain(self) -> int:
        """等到本轮的单元全部结束（成功或失败）.

        Returns:
            本轮完成的单元个数。

        Raises:
            TimeoutError: 到上限仍有单元没结束。
            BaseException: 有单元执行失败时抛出其第一个异常（失败的时长是"没有意义地短"的
                假样本，不能当成一次成功的执行）。
        """
        with self._cond:
            drained = self._cond.wait_for(
                lambda: self._done >= self._expected, timeout=DRAIN_TIMEOUT_SECONDS
            )
            errors, self._errors = self._errors, []
            pending, self._pending = self._pending, {}
            expected, self._expected = self._expected, 0
            done, self._done = self._done, 0

        if not drained:
            raise TimeoutError(
                f"该组在 {DRAIN_TIMEOUT_SECONDS:.0f}s 内未排空（已提交 {expected}，已完成 {done}）；"
                f"仍在册的单元名 {sorted(pending)[:4]}"
            )

        if not errors:
            self._check_inflight_agrees()
        if errors:
            raise errors[0]
        return done

    # ---------------------------------------------------------------- 完成信号

    def _record(self, worker: Any, error: BaseException | None) -> None:
        """记下一次结束（成功或失败）并唤醒 :meth:`drain`."""
        with self._cond:
            self._pending.pop(worker.name, None)
            if error is not None:
                self._errors.append(error)
            self._done += 1
            self._cond.notify_all()

    def _on_result(self, result: Any) -> None:
        """成功路径的完成信号：框架把 ``WorkerResult`` 交给结果响应器时回调.

        只认本驱动面提交过、且尚未记入的单元（按实例名查在册表）：结果响应器是进程级唯一
        实例，别的调度器产生的结果也会走到这里。
        """
        worker = self._pending.get(getattr(result, "worker_name", None))
        if worker is not None:
            self._record(worker, None)

    def _on_failure(self, worker: Any) -> None:
        """失败路径的完成信号：由 :func:`zoo_bench.native_line.forms._observe_failure` 回调."""
        self._record(worker, getattr(worker, "error", None))

    def failure_hook(self) -> Callable[[Any], None]:
        """交给 :func:`~zoo_bench.native_line.forms.build_worker` 的失败观测回调."""
        return self._on_failure

    def _check_inflight_agrees(self) -> None:
        """交叉验证完成判定：说"都完成了"，在飞表就必须已经空了.

        结算点是**先摘在飞表、再投递结果**，故收齐完成信号时在飞表必为空。两者不一致说明
        完成判定不可信（信号可能来自别处），此时不给数字比给错数字好。
        """
        core = getattr(self._waiter, "core", None)
        if core is None:
            return
        inflight = int(core.metrics()["inflight"])
        if inflight:
            raise RuntimeError(
                f"完成判定不自洽：结果响应器报告的执行体都已结束，但在飞表里仍有 {inflight} 个"
            )

    # ---------------------------------------------------------------- 收尾

    def teardown(self) -> None:
        """撤销本驱动面装上的回调并关池（幂等）."""
        if getattr(self._reactor, "on_result", None) == self._on_result:
            # 响应器是进程级唯一实例，留着它会把后续驱动面的结果投给一个已经结束的驱动面
            self._reactor.on_result = None
        self._waiter.shutdown(wait=True)


def build_submission_factory(
    form: str, tier: TierSpec, driver: WorkerDriver
) -> Callable[[int], Any]:
    """构造该组的提交工厂.

    序号在**整组内递增**（不逐轮归零）：框架的在飞表与结果响应器的在册表都以实例名为键，
    全组唯一最省心——逐轮复用同名虽然在本驱动面"上一轮已排空"的前提下也成立，但那是一个
    要靠推理维持的前提，不值得省一个计数器。
    """
    counter = itertools.count()
    hook = driver.failure_hook()
    return lambda _index: build_worker(form, tier, seq=next(counter), on_failure=hook)
