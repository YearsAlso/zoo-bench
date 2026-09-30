"""调度语义维度的探查（design D7）。

D7 要求"**先枚举**可开启的语义项并确认每项存在'关闭'状态；无法关闭的项被剔除而非用近似状态
代替"。本模块把这条要求做成**可运行的探查**，而不是一份手写清单——手写清单会随被测版本演进
漂移，探查则每次都在当前装着的版本上重跑，结论自带证据。

两类探查：

- **行为探查**：真跑一遍，看该语义的动作是否发生（例：把执行体的耗时设得超过 ``run_timeout``，
  量它在飞表里待了多久——若超时生效，它会在 ``run_timeout`` 附近被摘掉）
- **结构探查**：看某层执行所需的符号在当前版本是否存在（例：事件层要执行反应器，得先有
  ``perform``）

**为什么值得这么写**：这一维度在 0.6.0 上的结论是"没有一项可测"，而"没测"与"测了发现不可测"
在报告里必须区分得开——前者是遗漏，后者是结论。证据（实测值与 ``file:line`` 坐标）是二者的
分界，也让版本升级后能立刻看出结论是否仍然成立。
"""

from __future__ import annotations

import time
from typing import Any

#: 探查用执行体的耗时，以及设给它的 ``run_timeout``。前者远大于后者，使"是否被摘掉"有清晰分界。
PROBE_BODY_SECONDS = 0.15
PROBE_LIMIT_SECONDS = 0.01

#: 判据：摘除时刻若不到执行体耗时的一半，说明是被超时摘掉的，而不是等它自然结束。
ENFORCEMENT_RATIO = 0.5

SCOPE_NOTE = (
    "本维度采用**内部开关对照**（同一 workload 下语义全关 vs 逐个开启），"
    "**不与对照方案横向比较**——裸写法没有优先级/超时/重试的对应物，"
    "故只能回答“本框架的语义值多少钱”，不能回答“比裸写法贵多少”"
)


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


def probe_dispatch_timeout() -> dict[str, Any]:
    """超时语义是否真的生效（派发层，行为探查）。"""
    from concurrent.futures import ThreadPoolExecutor

    from zoo_framework.constant import WaiterConstant
    from zoo_framework.core.waiter.base_waiter import BaseWaiter
    from zoo_framework.workers import BaseWorker

    class _SlowWorker(BaseWorker):
        def __init__(self) -> None:
            super().__init__(
                {
                    "name": f"semantics-probe-{time.time_ns()}",
                    "is_loop": False,
                    "delay_time": 0,
                    "run_timeout": PROBE_LIMIT_SECONDS,
                }
            )

        def _execute(self) -> str:
            time.sleep(PROBE_BODY_SECONDS)
            return "done"

    waiter = BaseWaiter()
    waiter.worker_mode = WaiterConstant.WORKER_MODE_THREAD_POOL
    waiter.pool_enable = True
    waiter.pool_size = 1
    waiter.resource_pool = ThreadPoolExecutor(max_workers=1)

    worker = _SlowWorker()
    started = time.perf_counter()
    waiter.workers = [worker]
    waiter.execute_service()

    deadline = started + PROBE_BODY_SECONDS * 4
    while waiter.worker_props and time.perf_counter() < deadline:
        time.sleep(0.001)
    observed = time.perf_counter() - started
    waiter.resource_pool.shutdown(wait=True)

    enforced = timeout_is_enforced(observed, PROBE_BODY_SECONDS, PROBE_LIMIT_SECONDS)
    return {
        "item": "超时",
        "layer": "派发层",
        "probe": "行为探查（量在飞表摘除时刻）",
        "enforced": enforced,
        "observed_seconds": observed,
        "body_seconds": PROBE_BODY_SECONDS,
        "limit_seconds": PROBE_LIMIT_SECONDS,
        "evidence": "core/waiter/base_waiter.py 的 worker_band 里，取消/摘除动作整段是注释",
        "usable": enforced,
        "reason": ""
        if enforced
        else "超时判定存在但**执行动作未实现**——开启它只多算一次减法，量到的不是语义的代价",
    }


def probe_event_layer() -> dict[str, Any]:
    """优先级与重试是否可测量（事件层，结构探查）。"""
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
        ("优先级", "EventPriorityCalculator 存在（fifo/node/event_fifo_node.py）但未接入 drain "
                   "循环——workers/event_worker.py 里“根据优先级排序”只有一句注释"),
        ("重试", "retry_times 的默认 0 是干净的关闭态，但触发条件是“没有匹配到反应器”，"
                 "而反应器路径本身不可执行"),
    ):
        items.append(
            {
                "item": name,
                "layer": "事件层",
                "probe": "结构探查（检查执行所需的符号是否存在）",
                "enforced": None,
                "evidence": blocker or extra,
                "usable": has_perform,
                "reason": "" if has_perform else f"{blocker}。{extra}",
            }
        )
    return {"layer": "事件层", "items": items}


def probe_semantics() -> dict[str, Any]:
    """枚举语义项并逐项探查。

    Returns:
        可直接进报告模型的语义维度块。``status`` 为 ``not_measured`` 时，
        ``reason`` 里是"为何不可测"的证据，而非一句"尚未测量"——后者会被读成遗漏。
    """
    dispatch = probe_dispatch_timeout()
    event = probe_event_layer()
    items = [dispatch, *event["items"]]
    usable = [item for item in items if item["usable"]]

    return {
        "title": "调度语义的代价",
        "status": "ok" if usable else "not_measured",
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
