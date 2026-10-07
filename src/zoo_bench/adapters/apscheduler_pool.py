"""同生态调度库之一：``APScheduler``。

口径说明：APScheduler 面向的是"定时/周期作业"，不是"派发一段执行体并收回完成信号"。
本档用 ``BackgroundScheduler`` + 线程池执行器、以一次性立即执行的作业模拟派发，因此它的
数字里含有调度器的作业登记与触发判定成本——这是该库的固有账，不是本 harness 的偏差。

另有一处必须在报告里说清的语义差异：**APScheduler 会吞掉作业里抛出的异常**（记日志后
继续）。因此本档自行捕获并存下异常，在 ``drain`` 时重新抛出，以免"失败的任务静默消失、
数字看起来反而更好"。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from .base import DRAIN_TIMEOUT_SECONDS, BaseAdapter, Tier
from .registry import register


@register
class APSchedulerAdapter(BaseAdapter):
    """``BackgroundScheduler`` + 线程池执行器，一次性立即执行的作业。"""

    name = "apscheduler"
    tier = Tier.ECOSYSTEM
    notes = "adapters.notes.apscheduler_pool"

    def setup(self, *, workers: int) -> None:
        self._cond = threading.Condition()
        self._durations: list[float] = []
        self._errors: list[BaseException] = []
        self._submitted = 0
        self._done = 0
        self._scheduler = BackgroundScheduler(
            executors={"default": APSThreadPoolExecutor(max_workers=workers)},
            job_defaults={"misfire_grace_time": 60},
        )
        self._scheduler.start()

    def _run(self, body: Callable[[], float]) -> None:
        try:
            value = body()
        except BaseException as exc:
            with self._cond:
                self._errors.append(exc)
                self._done += 1
                self._cond.notify_all()
            return

        with self._cond:
            self._durations.append(value)
            self._done += 1
            self._cond.notify_all()

    def submit(self, body: Callable[[], float]) -> None:
        with self._cond:
            self._submitted += 1
        # trigger 为 None 且给定 next_run_time：一次性立即执行的作业，跑完即被移除
        # （与周期作业不同，它不会被重复调度）
        self._scheduler.add_job(self._run, args=(body,), next_run_time=datetime.now())

    def drain(self) -> list[float]:
        with self._cond:
            not_timed_out = self._cond.wait_for(
                lambda: self._done >= self._submitted, timeout=DRAIN_TIMEOUT_SECONDS
            )
            if not not_timed_out:
                raise TimeoutError(
                    f"APScheduler 档在 {DRAIN_TIMEOUT_SECONDS:.0f}s 内未排空"
                    f"（已提交 {self._submitted}，已完成 {self._done}）"
                )
            errors, self._errors = self._errors, []
            durations = self._durations
            self._durations = []
            self._done = 0
            self._submitted = 0

        if errors:
            raise errors[0]
        return durations

    def teardown(self) -> None:
        self._scheduler.shutdown(wait=True)
