"""该线所对照的**三种执行形态**（design D3）.

| 形态 | 执行体 | 决策开销 |
|---|---|---|
| 纯解释 ``pure`` | 双臂基类**不声明**原生任务名 → 框架的关闭语义（零分支零锁） | 无 |
| 原生 ``native`` | 框架的原生任务执行体（直接喂给原生任务，不经决策） | 无 |
| 路由 ``routed`` | 双臂基类 + 启用路由 + 声明原生任务名 | 有（即被测对象） |

**形态的差异落在哪一层**：纯解释与路由用的是**同一个**双臂基类的子类，区别在实例 props 是否
带 ``native_task_name``（框架侧就是这样声明的）；原生形态用的是框架自己的原生任务执行体。
三者的差异因此都是**框架的公开语义**，不是本仓自造的近似——这是"三形态可比"的前提。

**每 (形态, 规模) 一个类**（design D4）：统计与决策在框架侧按 **Worker 类名**索引，同一个类里的
不同规模共享一条统计，故单类混跑所有规模时路由不可能按规模分流（小规模会被大规模学到的东西
拖去走原生臂）。类名与规模的对应进留档。类名里的档位令牌：``Pure`` / ``Direct``（原生形态——
它是框架的直接执行体，类名里再写一次 ``NativeLineNative`` 会被误读成框架之外的东西）/ ``Routed``。

**为什么用 ``type()`` 生成类而不是写死九个类**：档位来自配置（规格：档位 MUST 由配置声明，
MUST NOT 硬编码在测量代码中）。生成类与写死的类在框架眼里没有区别——框架只看
``type(self).__name__`` 与 ``_execute_python`` 的函数身份。

**唯一的新增接缝**（逐条声明，见"为采集而包装的接缝 MUST 逐条声明"）：``_on_error`` 失败观测
钩子。它只把执行体异常记到实例上（框架的 ``settle`` 在失败路径上只记日志、**不投递结果**，
故"失败"与"还在跑"在完成信号上无法区分，不装这个钩子的话一个失败单元要等到整轮超时）。
它**不参与计时**、不改写任何框架内部状态。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from .inputs import TierSpec
from .reference import TASK_NAME, parse_response

#: 三种形态的标识（进留档；**不含语言**，故不随语言变）.
FORM_PURE = "pure"
FORM_NATIVE = "native"
FORM_ROUTED = "routed"

#: 该线对照的形态清单（顺序即留档里的顺序）.
FORMS: tuple[str, ...] = (FORM_PURE, FORM_NATIVE, FORM_ROUTED)

#: 框架侧两臂的标识（对应 ``zoo_framework.core.adaptive.bandit`` 的 ``ARM_NATIVE`` / ``ARM_PYTHON``）。
#: 本仓不臆造这套词表，但**父进程侧不 import 框架**（该线未开启时框架可能不在场），故这里落字面量；
#: 与框架的一致性由用例对着框架的常量核验。
ARM_NATIVE = "native"
ARM_PYTHON = "python"

#: 形态 → 它在路由形态里对应的那一臂。纯解释形态即"走解释臂"、原生形态即"走原生臂"，逐臂次数
#: 与两形态组的耗时因此能并排比较。**臂名与形态名不是同一套词表**（``pure`` 形态 ↔ ``python``
#: 臂），两者混用会让"路由到的那一臂"与"划算的那一形态"对不上而不报错。
ARM_OF_FORM: dict[str, str] = {FORM_PURE: ARM_PYTHON, FORM_NATIVE: ARM_NATIVE}

#: 形态 → 类名里的档位令牌.
_NAME_TOKEN: dict[str, str] = {FORM_PURE: "Pure", FORM_NATIVE: "Direct", FORM_ROUTED: "Routed"}

#: (形态, 档位) → 已生成的类。缓存使同一进程内同一格的类**只有一个对象**：统计 key 与
#: ``isinstance`` 都要求它稳定。
_CLASS_CACHE: dict[tuple[str, str], type] = {}


def class_name(form: str, tier: TierSpec) -> str:
    """该 (形态, 规模) 格的 Worker 类名.

    类名是框架侧的统计与决策 key，故**必须**与规模一一对应（D4）；命名规则本身也进留档，
    使读者能把留档里的统计条目对回规模。
    """
    return f"NativeLine{_NAME_TOKEN[form]}{tier.label}"


def _execute_python(self: Any) -> dict:
    """解释臂执行体：用本仓参照实现解析 ``props["input"]`` 里的帧.

    与原生臂的语义等价性由等价性门禁证明（:mod:`zoo_bench.native_line.equivalence`），且门禁的
    正常输入里就含本线的全部测量帧——故"测的帧"与"证过的帧"是同一批。
    """
    return parse_response(self._props["input"])


def _observe_failure(self: Any) -> None:
    """失败观测钩子（本仓新增的唯一接缝）：记下执行体异常并通知驱动面.

    框架的 ``BaseWorker.run()`` 在失败路径上先调本钩子、再把异常向上抛，而结算
    ``settle`` 收到 error 后**不投递结果**——只靠结果响应器判定完成时，"失败"与"还在跑"
    不可区分。本钩子只做记录。
    """
    self.error = sys.exc_info()[1]
    hook: Callable[[Any], None] | None = self.on_failure
    if hook is not None:
        hook(self)


def worker_class(form: str, tier: TierSpec) -> type:
    """取（必要时生成）该格的 Worker 类.

    Args:
        form: ``FORM_*`` 之一。
        tier: 规模档位。

    Returns:
        类对象。三形态的基类分别是：纯解释/路由 → ``DualArmWorker``；原生 →
        ``NativeTaskWorker``（框架的直接原生执行体）。

    Raises:
        KeyError: 形态标识不在 ``FORMS`` 内。
    """
    if form not in FORMS:
        raise KeyError(f"未知形态 {form!r}；可用：{list(FORMS)}")

    key = (form, tier.label)
    cached = _CLASS_CACHE.get(key)
    if cached is not None:
        return cached

    # 框架侧符号只在**生成类时**取（即配置就位之后）——模块级 import 会把框架 import 提前
    # 到配置目录就位之前，参数会冻结在默认值（design D13）
    if form == FORM_NATIVE:
        from zoo_framework.native.worker import NativeTaskWorker

        base: type = NativeTaskWorker
    else:
        from zoo_framework.workers import DualArmWorker

        base = DualArmWorker

    generated = type(
        class_name(form, tier),
        (base,),
        {
            "__doc__": f"该线的{form}形态 Worker（规模 {tier.registers} 寄存器）；"
            "由 :func:`zoo_bench.native_line.forms.worker_class` 按配置生成。",
            "__module__": __name__,
            # 类属性给默认值：钩子与异常记录在任何形态下都必须存在（否则属性访问会炸）
            "on_failure": None,
            "error": None,
            "_on_error": _observe_failure,
        }
        | ({"_execute_python": _execute_python} if form != FORM_NATIVE else {}),
    )
    _CLASS_CACHE[key] = generated
    return generated


def worker_props(form: str, tier: TierSpec, *, seq: int) -> dict:
    """构造一次提交的 props.

    ``input`` 是该档位的帧；``name`` 逐次唯一——框架的在飞表以实例名为键，重名会让后一个
    Worker 因"已在飞"被静默跳过，并发直接失效。

    Args:
        form: ``FORM_*`` 之一。
        tier: 规模档位。
        seq: 该组内的提交序号（保证实例名唯一）。
    """
    props: dict = {
        "name": f"zoo-bench-native-line-{form}-{tier.label}-{seq}",
        "is_loop": False,
        "delay_time": 0,
        "input": tier.frame,
    }
    if form == FORM_ROUTED:
        # 双臂基类的原生臂声明：**有**（路由形态的被测之处）
        props["native_task_name"] = TASK_NAME
    elif form == FORM_NATIVE:
        # 框架原生执行体的必需键（与双臂基类取名的键不同，这是框架的既有形状）
        props["task_name"] = TASK_NAME
    return props


def build_worker(
    form: str, tier: TierSpec, *, seq: int, on_failure: Callable[[Any], None] | None = None
) -> Any:
    """构造一个提交单元.

    Args:
        form: ``FORM_*`` 之一。
        tier: 规模档位。
        seq: 该组内的提交序号。
        on_failure: 失败观测回调（由驱动面传入）。
    """
    worker = worker_class(form, tier)(worker_props(form, tier, seq=seq))
    worker.on_failure = on_failure
    return worker
