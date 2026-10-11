"""原生任务线：被测框架的三种执行形态（纯解释 / 原生 / 在线路由）在同一轮内对照.

这条线的主矩阵之外的独立测量线（与 ``semantics`` / ``attribution`` 同级，design D1）：
它回答的是"把真实任务交给原生执行、以及交给在线路由决策，值不值"，而不是"派发原语的开销"。

为什么它不能并进主矩阵：主矩阵的适配器维语义是**一个方案**（被测框架只有一个、且为
``Tier.UNDER_TEST``），而这条线的三档都是被测框架自己的执行形态；并进去会让
"适配器 = 一个方案"与交叉点推导都需要特判（design D1 的备选被否理由）。

模块分工：

- :mod:`~zoo_bench.native_line.reference` —— 本仓自带的 Python 参照实现（解释臂的执行体 +
  等价性判据的对照侧）
- :mod:`~zoo_bench.native_line.inputs` —— 判据输入集与档位帧的构造入口
- :mod:`~zoo_bench.native_line.equivalence` —— 语义等价性门禁（先证明等价，再给倍数）
"""

from __future__ import annotations

from .equivalence import check_equivalence
from .inputs import GATE_INPUTS, GateInput
from .reference import (
    MAX_INPUT_BYTES,
    TASK_NAME,
    ReferenceInvalidInput,
    ReferenceTaskFailed,
    build_frame,
    crc16_modbus,
    frame_of_registers,
    parse_response,
    to_json_bytes,
)

__all__ = [
    "GATE_INPUTS",
    "MAX_INPUT_BYTES",
    "TASK_NAME",
    "GateInput",
    "ReferenceInvalidInput",
    "ReferenceTaskFailed",
    "build_frame",
    "check_equivalence",
    "crc16_modbus",
    "frame_of_registers",
    "parse_response",
    "to_json_bytes",
]
