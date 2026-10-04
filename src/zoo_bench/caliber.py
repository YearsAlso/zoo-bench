"""开销数字的适用口径。

**"框架开销"是一个减式**：每任务端到端（墙钟 ÷ 并发度）减去执行体自报的耗时。它只在两者**可比**
时成立，而可比性有一个容易漏掉的前提——执行体自报的是它自己的墙钟。机器的并行能力不够时，那份
自报值里含被抢占的等待，而每任务端到端反映的是真实并行吞吐，两者量的不是同一件事。

实测（CI runner，4 逻辑核，asyncio_pool / 并发 64 / 10000 微秒档）：每任务端到端 10.5 毫秒，
执行体自报耗时中位 **106.5 毫秒**（超订把它抬高了约 10.6 倍），相减得 **-913.8%**，并衍生出
`变化 1483.013x` 这类倍数。这不是"开销偏高"的噪声，是一个没有意义的减式结果。

**两个触发器缺一不可**：

1. **结构性**：并发度超过同一次运行环境自述里的机器并行能力（逻辑核数）。
2. **证据性**：算出来的开销**为负**。框架只会加时间不会减时间，故负值出现即是"两个量不可比"
   的直接证据；它同时兜住第一个触发器漏掉的情形——容器 CPU 配额可能低于 runner 报的核数，
   那种环境下核数会高估真实并行能力。

判据与说明共用在这里，是为了**报告与对比给同一句原因**：两处各写一份措辞，迟早一处改了另一处
没改，而读者会拿两种说法去解释同一件事。
"""

from __future__ import annotations

from typing import Any

#: 撤下开销数字时给出的原因。报告与对比共用。
UNINTERPRETABLE_REASON = (
    "该并发度超过这次运行所在机器的并行能力：执行体自报的耗时里含超订带来的调度等待，"
    "与「墙钟 / 并发度」不是同一件事，相减的结果没有意义（实测出现过负值）。"
    "端到端、吞吐与执行体自报值仍然有效，故照旧给出。"
)


def logical_cores_from_environment(environment: dict[str, Any] | None) -> int | None:
    """环境自述里的逻辑核数。

    **取不到时为 ``None``**，此时不据此判否（见下），由"开销为负"那条证据性触发兜底：
    环境自述缺一项就把整列开销抹掉，比给一个可能无效的数字更坏。

    判据必须取自**同一次运行**的自述——跨运行借用另一台机器的核数就会判错。
    """
    if not isinstance(environment, dict):
        return None
    hardware = environment.get("hardware")
    if not isinstance(hardware, dict):
        return None
    cores = hardware.get("logical_cores")
    return int(cores) if isinstance(cores, int) else None


def exceeds_machine_parallelism(concurrency: int, logical_cores: int | None) -> bool:
    """该并发度是否超出机器并行能力。

    **核数取不到时判否**：宁可给一个可能无效的数字（并由下一条兜底），也不因为环境自述缺一项
    就把整列抹掉。
    """
    return logical_cores is not None and concurrency > logical_cores


def overhead_is_interpretable(
    *, concurrency: int, logical_cores: int | None, overhead_seconds: float | None
) -> bool:
    """该单元的开销数字是否可当作开销来读。"""
    if exceeds_machine_parallelism(concurrency, logical_cores):
        return False
    # 负开销不可能是合法结果：框架只会加时间不会减时间。出现即说明两个量不可比。
    return overhead_seconds is None or overhead_seconds >= 0
