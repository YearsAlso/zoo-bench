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

from .i18n import LANG_ZH, t

#: 上一代驱动面：``workers`` 是可写属性，完成信号只能取自框架内部回调。
GENERATION_PREVIOUS = "previous"

#: 当前代驱动面：``core``（调度内核）+ ``model``（调度模型）结构，完成信号取自结果响应器。
GENERATION_CURRENT = "current"

#: 世代标识 -> 目录键。说明会随报告发布，故用词必须是**读者能据此判断适用性**的；
#: 存键而不是散文，留档才是语言无关的数据（design D8）。
GENERATION_LABELS: dict[str, str] = {
    GENERATION_PREVIOUS: "generations.label.previous",
    GENERATION_CURRENT: "generations.label.current",
}

#: 判定为**当前代**所需的驱动面形态。键是目录键（探测项名与形态描述都键化），值是**期望的探测结果**。
CURRENT_REQUIRED: dict[str, bool] = {
    "generations.capability.model_name_param": True,
    "generations.capability.workers_assignable": False,
    "generations.capability.core_set_workers": True,
    "generations.capability.core_is_inflight": True,
    "generations.capability.shutdown": True,
}

#: 判定为**上一代**所需的驱动面形态。
PREVIOUS_REQUIRED: dict[str, bool] = {
    "generations.capability.workers_assignable": True,
    "generations.capability.resource_pool": True,
    "generations.capability.worker_running_callback": True,
}

#: 形态描述：探测项的``(是否成立, 形态键, 形态参数)``里的后两项。
_ASSIGNMENT_MISSING = ("generations.observed.absent", {})
_ASSIGNMENT_ACCEPTED = ("generations.observed.assignable", {})
_CALLABLE = ("generations.observed.callable", {})
_NOT_CALLABLE = ("generations.observed.not_callable", {})
_PRESENT = ("generations.observed.present", {})
_ABSENT = ("generations.observed.absent", {})


class GenerationNotDrivable(RuntimeError):
    """被测框架的驱动面与本 harness 已知的任何一代都不匹配。

    刻意是异常而不是"退回某一代"：静默退回会产出与框架实际驱动方式不符的数字，而那种数字
    看起来一切正常。异常携带的是**语言无关的探测载荷**，CLI 侧按中文渲染（本项目的 CLI 面向
    维护者，保持中文）。
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(format_mismatch(payload, LANG_ZH))


def _probe_assignment(target: Any, attribute: str) -> tuple[bool, tuple[str, dict[str, Any]]]:
    """探测某属性是否接受赋值，并给出**键化的**形态描述。"""
    if not hasattr(target, attribute):
        return False, _ASSIGNMENT_MISSING
    try:
        setattr(target, attribute, [])
    except Exception as exc:
        return False, ("generations.observed.rejected", {"exception": type(exc).__name__})
    return True, _ASSIGNMENT_ACCEPTED


def _probe_drive_surface() -> dict[str, tuple[bool, tuple[str, dict[str, Any]]]]:
    """逐项探测被测框架的驱动面形态。

    Returns:
        ``探测项目录键 -> (是否成立, (形态目录键, 形态参数))``。形态描述刻意是**文本而不是
        布尔值**：判定失败时那份说明要直接回答"探测了什么、结果如何"，否则排查只能靠猜。
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
        "generations.capability.model_name_param": (
            accepts_model_name,
            ("generations.observed.accepted", {})
            if accepts_model_name
            else ("generations.observed.not_accepted", {}),
        ),
        "generations.capability.workers_assignable": (assignable, workers_shape),
        "generations.capability.core_set_workers": (
            set_workers,
            _CALLABLE if set_workers else _NOT_CALLABLE,
        ),
        "generations.capability.core_is_inflight": (
            is_inflight,
            _CALLABLE if is_inflight else _NOT_CALLABLE,
        ),
        "generations.capability.shutdown": (shutdown, _CALLABLE if shutdown else _NOT_CALLABLE),
        "generations.capability.resource_pool": (
            resource_pool,
            _PRESENT if resource_pool else _ABSENT,
        ),
        "generations.capability.worker_running_callback": (
            running_callback,
            _CALLABLE if running_callback else _NOT_CALLABLE,
        ),
    }


def _classify(observed: dict[str, tuple[bool, tuple[str, dict[str, Any]]]]) -> str | None:
    """按探测结果判定世代；两者都不匹配时返回 None。"""
    for generation, required in (
        (GENERATION_CURRENT, CURRENT_REQUIRED),
        (GENERATION_PREVIOUS, PREVIOUS_REQUIRED),
    ):
        if all(observed[name][0] is expected for name, expected in required.items()):
            return generation
    return None


def mismatch_payload(
    observed: dict[str, tuple[bool, tuple[str, dict[str, Any]]]],
) -> dict[str, Any]:
    """驱动面无法识别时的**语言无关**载荷：判据与探测结果都按目录键记，渲染时才解析。"""
    return {
        "detected": [
            {
                "capability": capability,
                "observed": shape_key,
                "observed_params": shape_params,
            }
            for capability, (_, (shape_key, shape_params)) in observed.items()
        ],
        "current_required": [
            {"capability": capability, "expected": expected}
            for capability, expected in CURRENT_REQUIRED.items()
        ],
        "previous_required": [
            {"capability": capability, "expected": expected}
            for capability, expected in PREVIOUS_REQUIRED.items()
        ],
    }


def _requirement_line(item: dict[str, Any], lang: str) -> str:
    """一条世代要求：``<探测项>=<期望的布尔值>``（布尔值本身是 ASCII，无需翻译）。"""
    return f"{t(str(item['capability']), lang)}={item['expected']}"


def format_mismatch(payload: dict[str, Any], lang: str) -> str:
    """把驱动面无法识别的载荷渲染成逐项说明（**按语言解析**，CLI 与报告共用一套）。"""
    requirements = list(payload.get("current_required") or [])
    previous = list(payload.get("previous_required") or [])
    detected = "\n".join(
        t(
            "generations.mismatch.detected_line",
            lang,
            capability=t(str(item["capability"]), lang),
            observed=t(
                str(item["observed"]),
                lang,
                **(item.get("observed_params") or {}),
            ),
        )
        for item in payload.get("detected") or []
    )
    return t(
        "generations.mismatch",
        lang,
        detected=detected,
        current_required=_clause_join([_requirement_line(i, lang) for i in requirements], lang),
        previous_required=_clause_join([_requirement_line(i, lang) for i in previous], lang),
    )


def _clause_join(values: list[str], lang: str) -> str:
    """句子级拼接：中文分号、英文分号加空格（与渲染层的 ``_list_join`` 同一规则）。"""
    return "；".join(values) if lang == LANG_ZH else "; ".join(values)


def probe_drive_generation() -> dict[str, Any]:
    """判定当前装着的被测框架提供哪一代的驱动面。

    Returns:
        ``{"generation": 世代标识, "label": 说明的目录键, "capabilities": [{"capability",
        "observed", "observed_params"}]}`` —— 全部是**目录键**，渲染时按语言解析。

    Raises:
        GenerationNotDrivable: 探测结果与任何已知世代都不匹配。
    """
    observed = _probe_drive_surface()
    generation = _classify(observed)
    if generation is None:
        raise GenerationNotDrivable(mismatch_payload(observed))

    return {
        "generation": generation,
        "label": GENERATION_LABELS[generation],
        "capabilities": [
            {
                "capability": capability,
                "observed": shape_key,
                "observed_params": shape_params,
            }
            for capability, (_, (shape_key, shape_params)) in observed.items()
        ],
    }
