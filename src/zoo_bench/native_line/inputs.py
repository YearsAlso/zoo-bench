"""该线喂进两臂的输入：等价性判据输入集 + 测量档位的帧构造入口。

**判据输入集为什么长这样**（spec: native-task-line「语义等价性 MUST 在给出任何倍数之前被证明」）：

- 三类的**覆盖面**是硬要求：正常输入、受控失败输入、损坏（校验不通过）输入缺一不可。
  只比正常帧时，一个"失败路径分类错了"的扩展照样能过门禁——而两臂在那条路径上不等价，
  恰恰会让"加快了多少"变成"比了两件不同的事"。
- 三类的**内容**按错误族逐条铺开（受控失败族 5 条、输入拒绝族 2 条）：扩展侧每条校验分支
  都有对应输入，故"某条分支的判定与文案漂了"会被点名而不是笼统判否。
- 帧长全部落在契约声明的输入上限内（≤256 字节）。**这不是省事**：超限时被测框架的适配器
  会在调用扩展之前先拒绝，两臂的拒绝理由不同源，比出来的"不等价"是口径假象而非语义分歧
  （2026-10-11 实测：257 字节输入上，参照实现说"帧最长 256 字节"、框架适配器说"超出契约
  上限 256"——同类不同文）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .reference import MAX_INPUT_BYTES, build_frame, frame_of_registers

#: 判据输入的四个分组。前三组即规格要求的三类，``structural`` 是额外的一类
#: （输入拒绝族），使两族的判定都进判据。
GROUP_NORMAL = "normal"
GROUP_CONTROLLED = "controlled_failure"
GROUP_CORRUPTED = "corrupted"
GROUP_STRUCTURAL = "structural"

#: 规格点名要求"必须在判据内"的三类.
REQUIRED_GROUPS: tuple[str, ...] = (GROUP_NORMAL, GROUP_CONTROLLED, GROUP_CORRUPTED)


@dataclass(frozen=True)
class GateInput:
    """一条判据输入.

    Attributes:
        key: 稳定标识（进留档与失败信息；报告侧按它给文案，故 MUST NOT 改成会漂的序号）
        group: 分组（见 ``GROUP_*``）
        frame: 裸 ADU 字节
    """

    key: str
    group: str
    frame: bytes


def _corrupt_last_byte(frame: bytes) -> bytes:
    """把末字节翻反——CRC 高字节变，校验必不通过."""
    return frame[:-1] + bytes([frame[-1] ^ 0xFF])


def _swap_crc_bytes(frame: bytes) -> bytes:
    """把线上两个 CRC 字节对调——小端序被破坏，校验必不通过."""
    return frame[:-2] + bytes([frame[-1], frame[-2]])


#: 判据输入集：三类必需 + 输入拒绝族，全部落在输入上限内.
GATE_INPUTS: tuple[GateInput, ...] = (
    # ── 正常输入（含异常响应帧：它是**解析成功**，异常码是业务结果而非失败）──
    GateInput("normal.single_register", GROUP_NORMAL, frame_of_registers(1)),
    GateInput("normal.eight_registers", GROUP_NORMAL, frame_of_registers(8)),
    GateInput("normal.max_frame", GROUP_NORMAL, frame_of_registers(125)),
    GateInput("normal.function_04", GROUP_NORMAL, frame_of_registers(2, function=0x04)),
    GateInput(
        "normal.exception_frame_83",
        GROUP_NORMAL,
        build_frame(bytes([0x01, 0x83, 0x02])),
    ),
    GateInput(
        "normal.exception_frame_84",
        GROUP_NORMAL,
        build_frame(bytes([0x01, 0x84, 0x03])),
    ),
    # ── 受控失败（CRC 通过之后的业务失败）──
    GateInput(
        "controlled.unsupported_function",
        GROUP_CONTROLLED,
        build_frame(bytes([0x01, 0x05, 0x02, 0x00, 0x01])),
    ),
    GateInput(
        "controlled.odd_byte_count",
        GROUP_CONTROLLED,
        build_frame(bytes([0x01, 0x03, 0x03, 0x00, 0x01, 0x02])),
    ),
    GateInput(
        "controlled.byte_count_mismatch",
        GROUP_CONTROLLED,
        build_frame(bytes([0x01, 0x03, 0x04, 0x00, 0x01])),
    ),
    GateInput(
        "controlled.exception_frame_wrong_length",
        GROUP_CONTROLLED,
        build_frame(bytes([0x01, 0x83, 0x02, 0x00])),
    ),
    GateInput(
        "controlled.missing_byte_count",
        GROUP_CONTROLLED,
        build_frame(bytes([0x01, 0x03])),
    ),
    # ── 损坏（校验不通过）──
    GateInput(
        "corrupted.last_byte_flipped",
        GROUP_CORRUPTED,
        _corrupt_last_byte(frame_of_registers(8)),
    ),
    GateInput(
        "corrupted.crc_bytes_swapped",
        GROUP_CORRUPTED,
        _swap_crc_bytes(frame_of_registers(8)),
    ),
    # ── 结构性输入拒绝（任务无法开始）──
    GateInput("structural.two_bytes", GROUP_STRUCTURAL, b"\x01\x03"),
    GateInput("structural.three_bytes", GROUP_STRUCTURAL, b"\x01\x03\x02"),
)


def out_of_envelope_inputs(inputs: tuple[GateInput, ...] = GATE_INPUTS) -> list[str]:
    """落在契约输入上限之外的判据输入的 key 清单（应为空）.

    判据输入超限会把"两臂的拒绝理由不同源"比成"不等价"，故它属于**判据自身的错误**，
    由门禁在开跑前拦住（design D7 的实测补充）。
    """
    return [item.key for item in inputs if len(item.frame) > MAX_INPUT_BYTES]


def groups_present(inputs: tuple[GateInput, ...] = GATE_INPUTS) -> dict[str, int]:
    """逐分组计数（供门禁断言"三类都在判据内"）."""
    counted: dict[str, int] = {}
    for item in inputs:
        counted[item.group] = counted.get(item.group, 0) + 1
    return counted


# ------------------------------------------------------------------ 规模档位


@dataclass(frozen=True)
class TierSpec:
    """一个规模档位：**寄存器个数**（该任务的输入规模）+ 由它构造出的帧.

    「规模」的语义是任务的输入规模，不是字节数——字节数是它的推论（``registers * 2 + 5``）。
    档位因此由配置声明为寄存器个数，帧由 :func:`~zoo_bench.native_line.reference.frame_of_registers`
    构造，不在源码里硬编码。

    Attributes:
        registers: 寄存器个数（1..125）
    """

    registers: int

    @property
    def frame(self) -> bytes:
        """该档位喂给三种形态的帧（每次调用重新构造，与是否被就地改写无关）."""
        return frame_of_registers(self.registers)

    @property
    def frame_bytes(self) -> int:
        """帧长（字节）：地址 1 + 功能码 1 + 字节计数 1 + 数据 + CRC 2."""
        return self.registers * 2 + 5

    @property
    def label(self) -> str:
        """短标签（进类名与留档的键；**不含单位**，故不随语言变）."""
        return f"R{self.registers}"


def tier_specs(register_counts: Sequence[int]) -> tuple[TierSpec, ...]:
    """把配置里的寄存器个数清单译成档位（并在此拒绝不合法的声明）.

    Args:
        register_counts: 寄存器个数清单（来自配置）。

    Returns:
        档位清单，顺序与声明一致。

    Raises:
        ValueError: 清单为空、有重复，或有档位超出该任务可表达的规模（帧长必须落在契约的
            输入上限之内——超限的帧会被框架适配器先行拒绝，量到的就不是执行体了）。
    """
    counts = [int(value) for value in register_counts]
    if not counts:
        raise ValueError("规模档位清单为空：该线至少要有一个档位，否则它回答不了自己的问题")
    if len(set(counts)) != len(counts):
        raise ValueError(f"规模档位有重复：{counts}——重复档位会让同名 Worker 类出现两次")
    for count in counts:
        frame_bytes = count * 2 + 5
        if count < 1 or frame_bytes > MAX_INPUT_BYTES:
            raise ValueError(
                f"档位 {count} 寄存器（{frame_bytes} 字节）超出该任务的输入上限 "
                f"{MAX_INPUT_BYTES} 字节；超限帧会被框架适配器先行拒绝，量的不是执行体"
            )
    return tuple(TierSpec(count) for count in counts)
