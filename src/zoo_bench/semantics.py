"""调度语义维度的探查（design D7）。

D7 要求"**先枚举**可开启的语义项并确认每项存在'关闭'状态；无法关闭的项被剔除而非用近似状态
代替"。本模块把这条要求做成**可运行的探查**，而不是一份手写清单——手写清单会随被测版本演进
漂移，探查则每次都在当前装着的版本上重跑，结论自带证据。

两类探查：

- **行为探查**：真跑一遍，看该语义的动作是否发生（例：把执行体的耗时设得超过 ``run_timeout``，
  量它在飞表里待了多久——若超时生效，它会在 ``run_timeout`` 附近被摘掉）
- **结构探查**：看某层执行所需的符号在当前版本是否存在（例：事件层要执行反应器，得先有
  ``perform``）

**两代之间这一层的接线都变过**，故本模块有两条纪律：

1. **驱动按世代分流**（:mod:`zoo_bench.generations`）：上一代的读写法在当前代上直接抛
   ``AttributeError``。
2. **结论带"在哪一代上得出"**：结论连同它的 ``file:line`` 坐标只对得出它的那一代成立。实测
   事件层两代接线不同（上一代 ``EventWorker`` 调 ``reactor.perform`` 而 ``EventReactor``
   只有 ``execute``，反应器一旦匹配即抛；当前代已改成调 ``execute``）。把旧结论写进另一代的
   报告，等于断言一个当前代并不具备的事实——故装上的是另一代时，这些项如实标为**未复核**。

**为什么值得这么写**：这一维度在上一代的结论是"没有一项可测"，而"没测"与"测了发现不可测"
在报告里必须区分得开——前者是遗漏，后者是结论。证据（实测值与 ``file:line`` 坐标）是二者的
分界，也让版本升级后能立刻看出结论是否仍然成立。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .generations import GENERATION_CURRENT, GENERATION_PREVIOUS, probe_drive_generation

#: 探查用执行体的耗时，以及设给它的 ``run_timeout``。前者远大于后者，使"是否被摘掉"有清晰分界。
PROBE_BODY_SECONDS = 0.15
PROBE_LIMIT_SECONDS = 0.01

#: 判据：摘除时刻若不到执行体耗时的一半，说明是被超时摘掉的，而不是等它自然结束。
ENFORCEMENT_RATIO = 0.5

#: 探查的等待上限 = 执行体耗时 × 本系数。执行体自然结束也在这个上限之内，故"没观测到离开
#: 在飞表"一定是异常而不是正常路径——上限值同时进报告，故**只在这里算一次**，避免消息里的
#: 数字与真正用的上限各写一份、日后漂移。
PROBE_DEADLINE_FACTOR = 4

SCOPE_NOTE = (
    "本维度采用**内部开关对照**（同一 workload 下语义全关 vs 逐个开启），"
    "**不与对照方案横向比较**——裸写法没有优先级/超时/重试的对应物，"
    "故只能回答“本框架的语义值多少钱”，不能回答“比裸写法贵多少”"
)

#: 当前代"超时生效"的确切含义。**必须写进证据**：读者很容易把"摘除了"读成"被杀掉了"，而
#: CPython 无法安全中断一个正在执行的线程——框架做的是观测与熔断，不是终止。
REAP_SEMANTICS_NOTE = (
    "；生效的是观测与熔断（记录、标记不健康、摘除在飞登记、停止派发），"
    "**不是终止**仍在执行的 worker——CPython 无法安全中断线程"
)

#: 事件层各项结论是在哪一代上得出的。
#:
#: 结论与它的证据坐标只对这一代成立：两代的接线不同（见模块文档）。装上的是另一代时，本层
#: 标为未复核而不是照抄——"没复核"与"复核过、结论是 X"是两条不同的信息。
_EVENT_LAYER_DERIVED_ON = GENERATION_PREVIOUS

#: 事件层枚举出的语义项。
_EVENT_LAYER_ITEMS = ("优先级", "重试")


def timeout_is_enforced(observed_seconds: float, body_seconds: float, limit_seconds: float) -> bool:
    """由观测到的摘除时刻判断超时是否真的生效。

    抽成纯函数是为了**能被双向验证**：只验"真实探查返回了 False"无法区分"判据有效"与
    "判据恒假"。

    Args:
        observed_seconds: 从派发到该 worker 离开在飞表的耗时。
        body_seconds: 执行体本身的耗时（无干预时应等于摘除时刻）。
        limit_seconds: 设给该 worker 的 ``run_timeout``。

    Returns:
        摘除时刻显著早于执行体结束即判生效。
    """
    if limit_seconds <= 0 or limit_seconds >= body_seconds:
        raise ValueError("探查参数无效：run_timeout 必须为正且小于执行体耗时")
    return observed_seconds < body_seconds * ENFORCEMENT_RATIO


def _source_location(obj: Any) -> str:
    """某个可调用对象的源码坐标（``文件名:行``）。

    **坐标从对象自身取，不写死在文案里**：同一个世代内的小版本升级也会挪动行号，写死的坐标
    迟早变成一条指向别处的断言。取不到时如实说明，不编造。
    """
    code = getattr(obj, "__code__", None)
    if code is None:
        return "源码坐标不可得"
    return f"{Path(str(code.co_filename)).name}:{code.co_firstlineno}"


class _PreviousGenerationSchedule:
    """上一代的探查驱动面：构造后赋属性，在飞表是调度器自己的字典。"""

    def __init__(self, worker: Any) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from zoo_framework.constant import WaiterConstant
        from zoo_framework.core.waiter.base_waiter import BaseWaiter

        self._worker = worker
        self._waiter = BaseWaiter()
        self._waiter.worker_mode = WaiterConstant.WORKER_MODE_THREAD_POOL
        self._waiter.pool_enable = True
        self._waiter.pool_size = 1
        self._waiter.resource_pool = ThreadPoolExecutor(max_workers=1)

    def start(self) -> None:
        self._waiter.workers = [self._worker]
        self._waiter.execute_service()

    def keep_scheduling(self) -> None:
        self._waiter.execute_service()

    def in_flight(self) -> bool:
        return self._worker.name in self._waiter.worker_props

    def judgment_location(self) -> str:
        return _source_location(type(self._waiter).worker_band)

    def shutdown(self) -> None:
        self._waiter.resource_pool.shutdown(wait=True)


class _CurrentGenerationSchedule:
    """当前代的探查驱动面：构造参数装配，在飞表经调度内核查询。"""

    def __init__(self, worker: Any) -> None:
        from zoo_framework.constant import WaiterConstant
        from zoo_framework.core.waiter.base_waiter import BaseWaiter

        self._worker = worker
        self._waiter = BaseWaiter(model_name=WaiterConstant.WORKER_MODE_THREAD_POOL, pool_size=1)

    def start(self) -> None:
        self._waiter.call_workers([self._worker])
        self._waiter.execute_service()

    def keep_scheduling(self) -> None:
        self._waiter.execute_service()

    def in_flight(self) -> bool:
        return bool(self._waiter.core.is_inflight(self._worker))

    def judgment_location(self) -> str:
        return _source_location(type(self._waiter.core).reap_timeout)

    def shutdown(self) -> None:
        self._waiter.shutdown(wait=True)


def _schedule_drive(generation: str, worker: Any) -> Any:
    """按世代取探查用的驱动面。"""
    if generation == GENERATION_CURRENT:
        return _CurrentGenerationSchedule(worker)
    return _PreviousGenerationSchedule(worker)


def _timeout_reason(generation: str, enforced: bool) -> str:
    """超时未生效时给出**能站得住的**原因。

    措辞刻意分开："观测不到"与"观察到了、结论是否"是两回事，而两代的机制不同，混成一句会让
    读者拿上一代的解释去理解当前代。
    """
    if enforced:
        return ""
    if generation == GENERATION_CURRENT:
        return "本轮未观测到摘除：超时判定由调度轮触发，worker 须留在调度列表里才谈得上被判定"
    return (
        "判定存在但**执行动作未实现**：本探查按调度轮驱动、探查 worker 也声明了循环，调度轮"
        "确实跑过，故未摘除只能是判定本身没有执行动作——开启它只多算一次减法，量到的不是"
        "语义的代价"
    )


def probe_dispatch_timeout() -> dict[str, Any]:
    """超时语义是否真的生效（派发层，行为探查）。

    **必须按调度轮反复驱动**：超时判定是调度轮的副产物——每轮对在飞 worker 判定一次，判定
    成立才摘除。只派发一轮的话两代都不会摘除，量到的成了"执行体自己跑了多久"，与语义无关。
    探查 worker 同样要声明循环：非循环 worker 在首轮之后就被移出调度列表，判定再没有对象。
    实测（当前代，``run_timeout`` 0.01s、执行体 0.15s）：按轮反复驱动时在 0.0116s 前后摘除，
    只驱动一轮则要等到 0.1515s（执行体自然结束）。
    """
    from zoo_framework.workers import BaseWorker

    generation = probe_drive_generation()["generation"]

    class _SlowWorker(BaseWorker):
        def __init__(self) -> None:
            super().__init__(
                {
                    "name": f"semantics-probe-{time.time_ns()}",
                    "is_loop": True,
                    "delay_time": 0,
                    "run_timeout": PROBE_LIMIT_SECONDS,
                }
            )

        def _execute(self) -> str:
            time.sleep(PROBE_BODY_SECONDS)
            return "done"

    worker = _SlowWorker()
    drive = _schedule_drive(generation, worker)

    started = time.perf_counter()
    deadline = started + PROBE_BODY_SECONDS * PROBE_DEADLINE_FACTOR
    settled = False
    try:
        drive.start()
        # **先看在飞表、再走下一轮**。反过来的话，执行体一完成就会被再次派发（声明了循环、
        # 且没有熔断标记的那一代不会把它移出调度列表），在飞表于是永远非空——量到的会是
        # 探查上限本身，而报告上看起来像一个"摘除时刻"。实测踩过：上一代读到 0.6012s，
        # 恰是 deadline，不是观测。
        while time.perf_counter() < deadline:
            if not drive.in_flight():
                settled = True
                break
            drive.keep_scheduling()
            time.sleep(0.001)
        observed = time.perf_counter() - started
    finally:
        drive.shutdown()

    enforced = timeout_is_enforced(observed, PROBE_BODY_SECONDS, PROBE_LIMIT_SECONDS)
    note = REAP_SEMANTICS_NOTE if generation == GENERATION_CURRENT and enforced else ""
    # 措辞必须分清三件事：**提前**离开在飞表（超时生效）、**自然结束**时离开（超时没动作）、
    # 以及**一直没离开**（读数是探查上限，不是一个观测值）。混成一句会让人拿"摘除时刻"
    # 去理解一个根本没发生的摘除。
    if not settled:
        observed_text = f"探查上限（{PROBE_BODY_SECONDS * PROBE_DEADLINE_FACTOR:.2f}s）内始终未离开在飞表"
    elif enforced:
        observed_text = f"在执行体结束之前离开在飞表（{observed:.4f}s）"
    else:
        observed_text = f"直到执行体自然结束才离开在飞表（{observed:.4f}s）"
    return {
        "item": "超时",
        "layer": "派发层",
        "probe": "行为探查（按调度轮反复驱动，量在飞表摘除时刻）",
        "enforced": enforced,
        "observed_seconds": observed,
        "settled_observed": settled,
        "body_seconds": PROBE_BODY_SECONDS,
        "limit_seconds": PROBE_LIMIT_SECONDS,
        "derived_on": generation,
        "evidence": (
            f"{'已' if enforced else '未'}观测到超时摘除：{observed_text}"
            f"（执行体 {PROBE_BODY_SECONDS}s、run_timeout {PROBE_LIMIT_SECONDS}s）；"
            f"判定动作在 {drive.judgment_location()}{note}"
        ),
        "usable": enforced,
        "reason": _timeout_reason(generation, enforced),
    }


def _unchecked_event_items(generation: str) -> list[dict[str, Any]]:
    """本层结论是在另一代上得出的时的条目：**如实标未复核，不照抄**。"""
    return [
        {
            "item": name,
            "layer": "事件层",
            "probe": "结构探查（检查执行所需的符号是否存在）",
            "enforced": None,
            "derived_on": _EVENT_LAYER_DERIVED_ON,
            "unchecked_on": generation,
            "evidence": (
                f"本项的结论与其坐标是在「{_EVENT_LAYER_DERIVED_ON}」代上得出的，"
                f"当前装着的是「{generation}」代——两代的事件层接线不同，本项未在当前代上复核"
            ),
            "usable": False,
            "reason": (
                f"未复核：本项的结论是在「{_EVENT_LAYER_DERIVED_ON}」代上得出的，把它当成本代"
                "的事实等于在报告里断言一个当前代并不具备的接线。重新推导需要单独一次探查设计"
                "（每项要有干净的开/关态才谈得上定价）"
            ),
        }
        for name in _EVENT_LAYER_ITEMS
    ]


def probe_event_layer() -> dict[str, Any]:
    """优先级与重试是否可测量（事件层，结构探查）。

    **结论按"在哪一代上得出"限定**（``_EVENT_LAYER_DERIVED_ON``）：装上的是另一代时，本层
    如实标为未复核并给出原因，而不是把旧结论与旧坐标当成本代的事实（design D7）。
    """
    generation = probe_drive_generation()["generation"]
    if generation != _EVENT_LAYER_DERIVED_ON:
        return {
            "layer": "事件层",
            "derived_on": _EVENT_LAYER_DERIVED_ON,
            "items": _unchecked_event_items(generation),
        }

    from zoo_framework.reactor.event_reactor import EventReactor

    has_perform = hasattr(EventReactor, "perform")
    blocker = (
        ""
        if has_perform
        else "事件层执行反应器要先有 perform：EventWorker._execute 调 reactor.perform"
        "（workers/event_worker.py），而 EventReactor 只定义了 execute"
        "（reactor/event_reactor.py）——反应器一旦匹配即抛 AttributeError"
    )

    items: list[dict[str, Any]] = []
    for name, extra in (
        (
            "优先级",
            "EventPriorityCalculator 存在（fifo/node/event_fifo_node.py）但未接入 drain "
            "循环——workers/event_worker.py 里“根据优先级排序”只有一句注释",
        ),
        (
            "重试",
            "retry_times 的默认 0 是干净的关闭态，但触发条件是“没有匹配到反应器”，"
            "而反应器路径本身不可执行",
        ),
    ):
        items.append(
            {
                "item": name,
                "layer": "事件层",
                "probe": "结构探查（检查执行所需的符号是否存在）",
                "enforced": None,
                "derived_on": _EVENT_LAYER_DERIVED_ON,
                "evidence": blocker or extra,
                "usable": has_perform,
                "reason": "" if has_perform else f"{blocker}。{extra}",
            }
        )
    return {"layer": "事件层", "derived_on": _EVENT_LAYER_DERIVED_ON, "items": items}


def probe_semantics() -> dict[str, Any]:
    """枚举语义项并逐项探查。

    Returns:
        可直接进报告模型的语义维度块。``status`` 为 ``not_measured`` 时，``reason`` 里是
        "为何不可测"的证据，而非一句"尚未测量"——后者会被读成遗漏。``derived_on`` 说明这一
        整块的结论是在哪一代驱动面上得出的。
    """
    generation = probe_drive_generation()["generation"]
    dispatch = probe_dispatch_timeout()
    event = probe_event_layer()
    items = [dispatch, *event["items"]]
    usable = [item for item in items if item["usable"]]

    return {
        "title": "调度语义的代价",
        "status": "ok" if usable else "not_measured",
        "derived_on": generation,
        "scope_note": SCOPE_NOTE,
        "items": items,
        "usable_items": [item["item"] for item in usable],
        "reason": (
            ""
            if usable
            else "在当前被测版本上，枚举出的语义项**没有一项可测**：要么其执行动作未实现，"
            "要么所在层无法执行反应器。按 design D7 的逃生口，此类项被剔除而非用近似状态代替"
        ),
        "recheck_on_version_bump": (
            "本结论按被测版本得出，坐标只对该版本成立；往 matrix.yaml 追加版本后须重跑本探查"
        ),
    }
