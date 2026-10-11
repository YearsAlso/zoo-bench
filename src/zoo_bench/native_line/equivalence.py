"""语义等价性门禁：**先证明两臂等价，再允许给出任何倍数**。

为什么它是前置门禁而不是事后检查（spec: native-task-line 同名要求）：该线要回答的是
"把它交给原生执行快了没有"。若两臂在同一输入上产出的东西不同，"倍数"衡量的就是两件不同的
事——数字照样好看，含义已经没了。故门禁不通过时该线**不产出任何数字**，以失败状态示人并
给出不等价的具体输入。

**比对口径**（写进留档，使读者知道"等价"是按哪把尺子量出来的）：

- 成功路径：两臂输出**逐字节**比对。参照侧把结果序列化成扩展侧那串紧凑 JSON
  （:func:`zoo_bench.native_line.reference.to_json_bytes`，键序与分隔符都对齐），
  与扩展返回的原始字节串直接相等才判过——键序变了也判否。
- 失败路径：比**错误类别与文案**（``(类别名, 消息)``）。只比"都失败了"不够：
  含校验不过的输入被判成"输入拒绝"而不是"受控失败"，是语义分歧，不是分类口味。

**参照实现的错误族名与契约族名不同**（:data:`ERROR_FAMILY_BY_REFERENCE` 明确对应），
门禁按该表归一化后再比——映射是数据，不是散在代码里的 if。
"""

from __future__ import annotations

from collections.abc import Callable

from .inputs import GATE_INPUTS, REQUIRED_GROUPS, GateInput, groups_present, out_of_envelope_inputs
from .reference import MAX_INPUT_BYTES, parse_response, to_json_bytes

#: 参照实现的错误族 → 契约错误族的对应（名字不同、语义一一对应）.
ERROR_FAMILY_BY_REFERENCE: dict[str, str] = {
    "ReferenceInvalidInput": "NativeInvalidInput",
    "ReferenceTaskFailed": "NativeTaskFailed",
}

#: 比对口径标识（进留档；改口径即改此常量）.
COMPARISON_BYTE_IDENTICAL = "byte_identical"


def check_equivalence(
    native_execute: Callable[[bytes], bytes],
    inputs: tuple[GateInput, ...] = GATE_INPUTS,
) -> dict:
    """对整批判据输入比对两臂输出.

    Args:
        native_execute: 原生臂的调用入口（输入帧 → 扩展返回的原始字节串）；
            本模块不知道它背后的适配器与扩展，故门禁可在无扩展环境下用假实现驱动。
        inputs: 判据输入集；缺省取 :data:`~zoo_bench.native_line.inputs.GATE_INPUTS`。

    Returns:
        ``{"ok", "comparison", "checked", "groups", "input_envelope_bytes", "mismatches"}``；
        ``mismatches`` 逐条给出不等价输入的 key、分组、帧的十六进制与两侧结果。

    Raises:
        ValueError: 判据自身不成立（缺必需分组，或有输入超出契约声明的输入上限）——
            那是本仓的错误，MUST NOT 混进"不等价"这个结论里。
    """
    counted = groups_present(inputs)
    missing = [group for group in REQUIRED_GROUPS if not counted.get(group)]
    if missing:
        raise ValueError(f"判据输入集缺少必需分组 {missing}；门禁不许在缺类别的情况下判过")

    oversized = out_of_envelope_inputs(inputs)
    if oversized:
        raise ValueError(
            f"判据输入超出契约输入上限 {MAX_INPUT_BYTES} 字节：{oversized}；"
            "超限输入会先被框架适配器拒绝，两臂的拒绝理由不同源，比出来的不等价是口径假象"
        )

    mismatches: list[dict] = []
    for item in inputs:
        reference_side = _reference_outcome(item.frame)
        native_side = _native_outcome(native_execute, item.frame)
        if reference_side != native_side:
            mismatches.append(
                {
                    "key": item.key,
                    "group": item.group,
                    "frame_hex": item.frame.hex(),
                    "frame_bytes": len(item.frame),
                    "reference": reference_side,
                    "native": native_side,
                }
            )

    return {
        "ok": not mismatches,
        "comparison": COMPARISON_BYTE_IDENTICAL,
        "checked": len(inputs),
        "groups": counted,
        "input_envelope_bytes": MAX_INPUT_BYTES,
        "mismatches": mismatches,
    }


def _reference_outcome(frame: bytes) -> dict:
    """参照侧的结果：成功给逐字节输出，失败给归一到契约族名后的 ``(类别, 文案)``."""
    try:
        return {
            "kind": "output",
            "class": None,
            "text": to_json_bytes(parse_response(frame)).decode("utf-8"),
        }
    except Exception as exc:
        return {
            "kind": "error",
            "class": ERROR_FAMILY_BY_REFERENCE.get(type(exc).__name__, type(exc).__name__),
            "text": str(exc),
        }


def _native_outcome(native_execute: Callable[[bytes], bytes], frame: bytes) -> dict:
    """原生侧的结果：成功给扩展返回的原始字节串（按 UTF-8 读，读不动就转义），
    失败给 ``(类别, 文案)``——类别取实际抛出的类名，故"抛出契约族之外的异常"同样会被判否.
    """
    try:
        raw = native_execute(frame)
        if not isinstance(raw, bytes):
            raw = bytes(raw)
        return {"kind": "output", "class": None, "text": raw.decode("utf-8", "backslashreplace")}
    except Exception as exc:
        return {"kind": "error", "class": type(exc).__name__, "text": str(exc)}
