"""Modbus RTU 响应帧解析——**本仓自带**的 Python 参照实现。

角色（design D7）：该线要对照被测框架的三种执行形态，其中两种（纯解释、路由的解释臂）
需要一个**与原生臂语义等价**的执行体，另外还必须有一份独立参照去证明两臂真的等价。
两者是同一个实现：这个模块。

**为什么自带而不取框架仓 ``native/reference/``**：那一份不随包分发、也不属于发布接口，
取它就把本仓的测量绑在另一个仓的文件布局上。这里按同一任务语义**独立重写**，写法沿用
位移式 CRC（非查表）：两个独立实现互为判据，比"同一算法换语言"更能暴露双方共同的误读。
正确性由已知答案锚定：CRC-16/MODBUS 对 ``b"123456789"`` 的标准校验值是 0x4B37
（见 :data:`CRC_CHECK_VALUE`，与 :func:`crc16_modbus` 互相独立）。

**与原生扩展（Rust 侧 ``native/src/modbus.rs``）的对齐要求**（等价性门禁按此逐字节比对）：

- 输出结构逐字段一致，**键序也一致**：正常响应 ``slave / function / byte_count / registers``，
  异常响应 ``slave / function / exception``（:func:`to_json_bytes` 复现扩展侧的紧凑 JSON）；
- 错误分类逐条一致：结构性输入（帧长 < 4 或 > 256）→ 输入拒绝族；CRC 不符 / 功能码不支持 /
  帧结构不一致 → 受控失败族；
- **消息文本逐字一致**：门禁比对"细节"，两侧文案不同即判否。

**并不 import 被测框架**：本模块的异常族自带（名字与契约的两族一一对应，见
``equivalence.ERROR_FAMILY_BY_REFERENCE``），使参照实现与门禁的用例可在不装框架的环境里跑。
"""

from __future__ import annotations

import json

#: CRC-16/MODBUS 的标准 check 值（独立于本实现的锚点）.
CRC_CHECK_VALUE = 0x4B37

#: RTU ADU 字节上限（地址 1 + 功能码 1 + 数据 253 + CRC 2）. 与扩展侧 ``MAX_ADU_LEN`` 一致.
MAX_ADU_LEN = 256

#: 该线所驱动的原生任务名（扩展侧注册名）.
TASK_NAME = "modbus_rtu.parse_response"

#: 任务契约声明的输入上限（字节）. 判据输入 MUST 落在它之内：超限时被测框架的适配器会在
#: 调用扩展之前先行拒绝，两臂的拒绝理由不同源，比出来的"不等价"是口径假象而非语义分歧.
MAX_INPUT_BYTES = 256

#: 寄存器数据区的字节上限（偶数）：``(MAX_ADU_LEN - 地址 1 - 功能码 1 - 字节计数 1 - CRC 2)``.
MAX_DATA_BYTES = 250


class ReferenceInvalidInput(Exception):
    """结构性输入拒绝（对应契约的 ``NativeInvalidInput`` 语义）：任务根本无法开始."""


class ReferenceTaskFailed(Exception):
    """受控业务失败（对应契约的 ``NativeTaskFailed`` 语义）：解析已开始后的失败."""


def crc16_modbus(data: bytes) -> int:
    """位移式 CRC-16/MODBUS（反射多项式 0xA001，初值 0xFFFF）.

    Args:
        data: 参与校验的字节（不含线上那两个 CRC 字节）

    Returns:
        16 位校验值
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(data: bytes) -> bytes:
    """给数据区追加合法 CRC（线上小端：低字节在前）构成完整 ADU.

    Args:
        data: 地址 + 功能码 + 负载（不含 CRC）

    Returns:
        完整帧
    """
    crc = crc16_modbus(data)
    return data + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def parse_response(frame: bytes) -> dict:
    """解析一条 RTU 响应帧 → dict（键序与扩展侧 ``to_json`` 逐字一致）.

    Args:
        frame: 不含任何传输层封装的裸 ADU

    Returns:
        正常响应 ``{"slave", "function", "byte_count", "registers"}``；
        异常响应 ``{"slave", "function", "exception"}``

    Raises:
        ReferenceInvalidInput: 帧长 < 4 或 > 256（任务无法开始）
        ReferenceTaskFailed: CRC 不符 / 功能码不支持 / 帧结构不一致
    """
    if len(frame) < 4:
        raise ReferenceInvalidInput(
            f"RTU 帧最短 4 字节（地址+功能码+CRC16），实际 {len(frame)} 字节"
        )
    if len(frame) > MAX_ADU_LEN:
        raise ReferenceInvalidInput(f"RTU 帧最长 {MAX_ADU_LEN} 字节，实际 {len(frame)} 字节")

    data = frame[:-2]
    expected = frame[-2] | (frame[-1] << 8)
    actual = crc16_modbus(data)
    if actual != expected:
        raise ReferenceTaskFailed(f"CRC 校验失败：计算值 0x{actual:04X}，帧携带值 0x{expected:04X}")

    slave = data[0]
    function = data[1]
    base = function & 0x7F
    if base not in (0x03, 0x04):
        raise ReferenceTaskFailed(f"不支持的功能码 0x{function:02X}（首版仅 0x03/0x04 及其异常态）")

    if function & 0x80:
        if len(data) != 3:
            raise ReferenceTaskFailed(
                f"异常帧数据区应为 3 字节（地址+功能码+异常码），实际 {len(data)}"
            )
        return {"slave": slave, "function": function, "exception": data[2]}

    if len(data) < 3:
        raise ReferenceTaskFailed("正常响应缺少字节计数字段")
    byte_count = data[2]
    if byte_count % 2 != 0:
        raise ReferenceTaskFailed(f"字节计数 {byte_count} 不是偶数（每寄存器 2 字节）")
    if len(data) != 3 + byte_count:
        raise ReferenceTaskFailed(
            f"字节计数 {byte_count} 与帧长不一致：数据区实际 {len(data) - 3} 字节"
        )

    registers = [(data[i] << 8) | data[i + 1] for i in range(3, len(data), 2)]
    return {
        "slave": slave,
        "function": function,
        "byte_count": byte_count,
        "registers": registers,
    }


def to_json_bytes(result: dict) -> bytes:
    """把解析结果序列化成扩展侧那串紧凑 JSON——**键序与分隔符都对齐**，故可逐字节比对.

    Args:
        result: :func:`parse_response` 的返回值（键序即插入序）

    Returns:
        UTF-8 字节串；内容全为 ASCII（字段均为数值）
    """
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def frame_of_registers(count: int, *, slave: int = 0x01, function: int = 0x03) -> bytes:
    """构造一条**合法**的正常响应帧，含 ``count`` 个寄存器.

    Args:
        count: 寄存器个数（1..125）
        slave: 从站地址
        function: 功能码（0x03 / 0x04）

    Returns:
        完整帧；长度为 ``count * 2 + 5`` 字节

    Raises:
        ValueError: 寄存器个数超出契约可表达的范围
    """
    if count < 1 or count * 2 > MAX_DATA_BYTES:
        raise ValueError(f"寄存器个数须在 1..{MAX_DATA_BYTES // 2}，实际 {count}")
    payload = bytes((index * 7 + 3) & 0xFF for index in range(count * 2))
    return build_frame(bytes([slave, function, count * 2]) + payload)
