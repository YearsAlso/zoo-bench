"""被测框架的驱动面世代：判定、标识与形态说明。

**这一层为什么独立存在**：它回答的是"当前装着的被测框架提供哪一代驱动面"——**关于被测对象
的事实**，不是某个适配器的私事。三处需要它：适配器（据此选驱动路径）、环境自述（据此回答
"这次测量用的是哪一代"）、语义探查（据此分流并限定结论的适用范围）。把它塞进适配器模块，
会让后两处反向依赖一个适配器。

**判定依据只有能力与形态，没有版本号**：实测发行元数据里的写法与索引不一致（索引叫
``0.7.1b0``、轮子元数据里写着 ``0.7.1-beta``），且未来世代的版本号无从预知。能力探测对未知
世代给出的失败是**可见的**（明确报错并列出探测结果），版本号比较给出的错误则是静默的——
后者会让报告带着一份"看着正常"的数字发布出去。

**两代的驱动面差异**（实测：装对应版本各跑一遍用例，另读框架 ``dev`` 分支源码确认）：

===================  ==========================================  ==================================
                    上一代                                        当前代
===================  ==========================================  ==================================
装配                 构造后赋 ``worker_mode`` / ``pool_size`` /   构造参数 ``model_name`` + ``pool_size``
                     ``resource_pool``
设 worker 列表       ``waiter.workers`` 可写属性                    ``waiter.call_workers([...])``
完成信号             只能拦 ``worker_running_callback``            框架自己的结果响应器
停机                 ``resource_pool.shutdown()``                  ``waiter.shutdown(wait=True)``
在飞表               ``waiter.worker_props``                       ``waiter.core``（含 ``is_inflight``）
===================  ==========================================  ==================================
"""

from __future__ import annotations

import inspect
from typing import Any

from zoo_framework.core.waiter.base_waiter import BaseWaiter

#: 上一代驱动面：``workers`` 是可写属性，完成信号只能取自框架内部回调。
GENERATION_PREVIOUS = "previous"

#: 当前代驱动面：``core``（调度内核）+ ``model``（调度模型）结构，完成信号取自结果响应器。
GENERATION_CURRENT = "current"

#: 世代标识 -> 可读说明。说明会随报告发布，故用词必须是**读者能据此判断适用性**的。
GENERATION_LABELS: dict[str, str] = {
    GENERATION_PREVIOUS: "上一代驱动面（workers 为可写属性，完成信号取自框架内部回调）",
    GENERATION_CURRENT: "当前代驱动面（调度内核 core + 调度模型 model，完成信号取自结果响应器）",
}

#: 判定为**当前代**所需的驱动面形态。值是**期望的探测结果**。
CURRENT_REQUIRED: dict[str, bool] = {
    "构造参数接受 model_name": True,
    "workers 可赋值": False,
    "调度内核 core.set_workers 可调用": True,
    "调度内核 core.is_inflight 可调用": True,
    "停机入口 shutdown 可调用": True,
}

#: 判定为**上一代**所需的驱动面形态。
PREVIOUS_REQUIRED: dict[str, bool] = {
    "workers 可赋值": True,
    "资源池属性 resource_pool 存在": True,
    "完成回调 worker_running_callback 可调用": True,
}


class GenerationNotDrivable(RuntimeError):
    """被测框架的驱动面与本 harness 已知的任何一代都不匹配。

    刻意是异常而不是"退回某一代"：静默退回会产出与框架实际驱动方式不符的数字，而那种数字
    看起来一切正常。
    """


def _probe_assignment(target: Any, attribute: str) -> tuple[bool, str]:
    """探测某属性是否接受赋值，并给出可读的形态描述。"""
    if not hasattr(target, attribute):
        return False, "不存在"
    try:
        setattr(target, attribute, [])
    except Exception as exc:
        return False, f"存在但拒绝赋值（{type(exc).__name__}）"
    return True, "存在且接受赋值"


def _probe_drive_surface() -> dict[str, tuple[bool, str]]:
    """逐项探测被测框架的驱动面形态。

    Returns:
        ``探测项 -> (是否成立, 可读的形态描述)``。形态描述刻意是**文本而不是布尔值**：
        判定失败时那份说明要直接回答"探测了什么、结果如何"，否则排查只能靠猜。
    """
    waiter = BaseWaiter()
    core = getattr(waiter, "core", None)

    assignable, workers_shape = _probe_assignment(waiter, "workers")
    accepts_model_name = "model_name" in inspect.signature(BaseWaiter.__init__).parameters
    set_workers = callable(getattr(core, "set_workers", None))
    is_inflight = callable(getattr(core, "is_inflight", None))
    shutdown = callable(getattr(waiter, "shutdown", None))
    resource_pool = hasattr(waiter, "resource_pool")
    running_callback = callable(getattr(waiter, "worker_running_callback", None))

    return {
        "构造参数接受 model_name": (accepts_model_name, "接受" if accepts_model_name else "不接受"),
        "workers 可赋值": (assignable, workers_shape),
        "调度内核 core.set_workers 可调用": (set_workers, "可调用" if set_workers else "不可调用"),
        "调度内核 core.is_inflight 可调用": (
            is_inflight,
            "可调用" if is_inflight else "不可调用",
        ),
        "停机入口 shutdown 可调用": (shutdown, "可调用" if shutdown else "不可调用"),
        "资源池属性 resource_pool 存在": (
            resource_pool,
            "存在" if resource_pool else "不存在",
        ),
        "完成回调 worker_running_callback 可调用": (
            running_callback,
            "可调用" if running_callback else "不可调用",
        ),
    }


def _classify(observed: dict[str, tuple[bool, str]]) -> str | None:
    """按探测结果判定世代；两者都不匹配时返回 None。"""
    for generation, required in (
        (GENERATION_CURRENT, CURRENT_REQUIRED),
        (GENERATION_PREVIOUS, PREVIOUS_REQUIRED),
    ):
        if all(observed[name][0] is expected for name, expected in required.items()):
            return generation
    return None


def _mismatch_message(observed: dict[str, tuple[bool, str]]) -> str:
    """驱动面无法识别时的失败说明：**逐项给出探测了什么、结果如何**。"""

    def requirements(required: dict[str, bool]) -> str:
        return "；".join(f"{name}={expected}" for name, expected in required.items())

    detected = "\n".join(f"  - {name}：{shape}" for name, (_, shape) in observed.items())
    return (
        "被测框架的驱动面与本 harness 已知的任何一代都不匹配，无法驱动。\n"
        f"探测到的形态：\n{detected}\n"
        f"当前代要求：{requirements(CURRENT_REQUIRED)}\n"
        f"上一代要求：{requirements(PREVIOUS_REQUIRED)}\n"
        "不会退回某一代的默认路径去测——那样得到的数字看起来正常，却对应框架并不具备的驱动方式。"
    )


def probe_drive_generation() -> dict[str, Any]:
    """判定当前装着的被测框架提供哪一代的驱动面。

    Returns:
        ``{"generation": 世代标识, "label": 可读说明, "capabilities": [{"capability", "observed"}]}``。

    Raises:
        GenerationNotDrivable: 探测结果与任何已知世代都不匹配。
    """
    observed = _probe_drive_surface()
    generation = _classify(observed)
    if generation is None:
        raise GenerationNotDrivable(_mismatch_message(observed))

    return {
        "generation": generation,
        "label": GENERATION_LABELS[generation],
        "capabilities": [
            {"capability": name, "observed": shape} for name, (_, shape) in observed.items()
        ],
    }
