"""原生任务线的语义等价性门禁验证（spec: native-task-line「语义等价性 MUST 在给出任何倍数之前被证明」）。

本文件的重点在**判据的鉴别力**：门禁"通过"这件事只说明它没报警，不说明它抓得住错。故每条
能想出来的实现违规都各有用例：CRC 字节序读反、失败族分类错、文案漂移、输出键序变了、数值
不对——它们必须全部判否（每条注入都对应一种"数字好看但含义没了"的真实故障形态）。

真实扩展只在装了的机器上跑（扩展不在任何包索引上，CI 由该版本同源的源码构建后装入）。
"""

from __future__ import annotations

import json

import pytest
from zoo_framework.native.contract import NativeInvalidInput, NativeTaskFailed

from zoo_bench.native_line.equivalence import ERROR_FAMILY_BY_REFERENCE, check_equivalence
from zoo_bench.native_line.inputs import GATE_INPUTS, GROUP_NORMAL
from zoo_bench.native_line.reference import (
    CRC_CHECK_VALUE,
    ReferenceInvalidInput,
    ReferenceTaskFailed,
    crc16_modbus,
    frame_of_registers,
    parse_response,
    to_json_bytes,
)


def _contract_family(arm):
    """把"参照实现形态"的臂包成"契约形态"的臂.

    真扩展抛的是契约族异常（``NativeInvalidInput`` / ``NativeTaskFailed``），参照实现抛的是
    自己那两个族。门禁比的是**实际类名**——故假臂 MUST 先归一化，否则它的"不等价"是脚手架
    自己制造的。注入用例全部经此包装，使判否只可能来自被注入的那处语义偏差。
    """

    def wrapped(frame: bytes) -> bytes:
        try:
            return arm(frame)
        except ReferenceInvalidInput as exc:
            raise NativeInvalidInput(str(exc)) from exc
        except ReferenceTaskFailed as exc:
            raise NativeTaskFailed(str(exc)) from exc

    return wrapped


#: 与扩展侧行为一致的假原生臂——用作"门禁应判过"的对照侧.
FAITHFUL = _contract_family(lambda frame: to_json_bytes(parse_response(frame)))


# ------------------------------------------------------------------ 参照实现自身的锚定


def test_crc_matches_the_standard_check_value() -> None:
    """CRC-16/MODBUS 的标准 check 值是与实现互相独立的锚点（本仓参照实现的正确性由它锚定）."""
    assert crc16_modbus(b"123456789") == CRC_CHECK_VALUE


def test_constructed_frame_parses_back_to_its_registers() -> None:
    """构造出来的帧本身就是合法的：长度、字节计数与寄存器个数三者自洽."""
    frame = frame_of_registers(8)
    assert len(frame) == 8 * 2 + 5
    parsed = parse_response(frame)
    assert parsed["byte_count"] == 16
    payload = bytes((index * 7 + 3) & 0xFF for index in range(16))
    assert parsed["registers"] == [
        (payload[index] << 8) | payload[index + 1] for index in range(0, 16, 2)
    ]


def test_reference_rejects_structural_inputs_and_controlled_failures() -> None:
    """两族错误是**不同族**：结构性问题在执行前拒绝，业务失败在执行后判定。"""
    from zoo_bench.native_line.reference import ReferenceInvalidInput

    with pytest.raises(ReferenceInvalidInput):
        parse_response(b"\x01\x03")
    with pytest.raises(ReferenceTaskFailed):
        parse_response(b"\x01\x03\x02\x12\x34\x00\x00")


# ------------------------------------------------------------------ 门禁的鉴别力


def test_gate_passes_when_the_native_arm_is_faithful() -> None:
    """忠实原生臂应判过——这是"门禁恒假"的反向对照."""
    outcome = check_equivalence(FAITHFUL)
    assert outcome["ok"] is True
    assert outcome["mismatches"] == []
    assert outcome["checked"] == len(GATE_INPUTS) == 15
    assert outcome["comparison"] == "byte_identical"


def test_gate_turns_red_when_crc_byte_order_is_swapped() -> None:
    """注入：实现把线上 CRC 读成大端（低/高字节调换）. 合法帧会全部被判成校验失败."""

    def big_endian_crc(frame: bytes) -> bytes:
        swapped = frame[:-2] + bytes([frame[-1], frame[-2]])
        return to_json_bytes(parse_response(swapped))

    outcome = check_equivalence(_contract_family(big_endian_crc))
    assert outcome["ok"] is False
    assert outcome["mismatches"], "判否却没给出不等价的输入——结论无法复核"
    assert GROUP_NORMAL in {item["group"] for item in outcome["mismatches"]}
    assert all(item["frame_hex"] for item in outcome["mismatches"])


def test_gate_turns_red_when_a_failure_family_is_misclassified() -> None:
    """注入：校验不过被判成"输入拒绝"（文案不变）. 只比文案的门禁会放过这一条."""

    def wrong_family(frame: bytes) -> bytes:
        try:
            return to_json_bytes(parse_response(frame))
        except ReferenceTaskFailed as exc:
            raise NativeInvalidInput(str(exc)) from exc

    outcome = check_equivalence(_contract_family(wrong_family))
    assert outcome["ok"] is False
    assert outcome["mismatches"]
    assert all(item["reference"]["class"] == "NativeTaskFailed" for item in outcome["mismatches"])
    assert all(item["native"]["class"] == "NativeInvalidInput" for item in outcome["mismatches"])


def test_gate_turns_red_when_the_message_text_drifts() -> None:
    """注入：分类正确、文案改了（"细节"漂移）. 只比"都失败了"的门禁会放过这一条."""

    def reworded(frame: bytes) -> bytes:
        try:
            return to_json_bytes(parse_response(frame))
        except ReferenceTaskFailed:
            raise NativeTaskFailed("CRC check failed") from None

    outcome = check_equivalence(_contract_family(reworded))
    assert outcome["ok"] is False
    assert outcome["mismatches"]
    assert any("CRC" in item["reference"]["text"] for item in outcome["mismatches"])
    assert all(item["native"]["text"] == "CRC check failed" for item in outcome["mismatches"])


def test_gate_turns_red_when_the_output_key_order_changes() -> None:
    """注入：数值全对、JSON 键序变了. 口径是逐字节，故这也必须判否."""

    def sorted_keys(frame: bytes) -> bytes:
        return json.dumps(
            parse_response(frame), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    outcome = check_equivalence(_contract_family(sorted_keys))
    assert outcome["ok"] is False
    assert outcome["mismatches"]
    # 只有成功路径的键序被改；失败路径两臂一致，故判否只可能来自正常输入
    assert {item["group"] for item in outcome["mismatches"]} == {GROUP_NORMAL}


def test_gate_turns_red_when_a_value_differs() -> None:
    """注入：结构不变、某一个寄存器值不对."""

    def off_by_one(frame: bytes) -> bytes:
        result = parse_response(frame)
        if "registers" in result:
            result["registers"] = [value + 1 for value in result["registers"]]
        return to_json_bytes(result)

    outcome = check_equivalence(_contract_family(off_by_one))
    assert outcome["ok"] is False
    assert outcome["mismatches"]
    assert {item["group"] for item in outcome["mismatches"]} == {GROUP_NORMAL}


def test_gate_rejects_inputs_outside_the_contract_envelope() -> None:
    """超限输入会被框架适配器先行拒绝，两臂的拒绝理由不同源——判据自身必须拦住它."""
    from zoo_bench.native_line.inputs import GateInput

    oversized = (GateInput("normal.oversized", GROUP_NORMAL, bytes(257)),)
    # 三类必需输入都在，故拦住它的只能是输入上限那道检查，不是"缺分组"
    with pytest.raises(ValueError, match="输入上限"):
        check_equivalence(FAITHFUL, oversized + GATE_INPUTS)


def test_gate_rejects_a_missing_required_group() -> None:
    """缺任一类必需输入时不许判过——"没比"与"比过了"必须区分得开."""
    only_normal = tuple(item for item in GATE_INPUTS if item.group == GROUP_NORMAL)
    with pytest.raises(ValueError, match="缺少必需分组"):
        check_equivalence(FAITHFUL, only_normal)


def test_the_family_mapping_covers_every_reference_exception() -> None:
    """参照实现的异常族都必须有到契约族名的映射，否则名字会被原样拿去比对而恒不相等."""
    import zoo_bench.native_line.reference as reference

    declared = {
        name
        for name, value in vars(reference).items()
        if isinstance(value, type) and issubclass(value, Exception)
    }
    assert declared == set(ERROR_FAMILY_BY_REFERENCE)


# ------------------------------------------------------------------ 真实扩展（装了才跑）


def test_gate_passes_against_the_real_extension() -> None:
    """真扩展上跑一遍：15 条判据输入全过（含失败路径的类别与文案）."""
    adapter = _ready_adapter()
    outcome = check_equivalence(lambda frame: adapter.execute("modbus_rtu.parse_response", frame))
    assert outcome["checked"] == len(GATE_INPUTS) == 15
    assert outcome["mismatches"] == []
    assert outcome["ok"] is True


def _ready_adapter():
    """装了同源扩展就返回握手后的适配器，否则跳过该用例."""
    try:
        __import__("zoo_framework_native")
    except ImportError:
        pytest.skip("本机未安装 zoo-framework-native 扩展，该用例只在装了扩展的环境成立")
    from zoo_framework.native.adapter import NativeAdapter

    adapter = NativeAdapter()
    adapter.ensure_ready()
    return adapter
