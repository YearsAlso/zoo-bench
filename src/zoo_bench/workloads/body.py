"""被测执行体。

契约要求执行体**返回它在自身内部实测的耗时**（design D5），且必须**可 pickle**——多进程
档位要把它送到子进程，故这里全是模块级的可 pickle 对象，没有闭包。

负载形状沿用 ``bench/workload.py`` 的近似：JSON 编解码 + 字符串处理。它是**真实业务 trace
的替身**，这一事实会随数据出现在报告里（spec: measurement-protocol 要求声明它的构成）。
"""

from __future__ import annotations

import json
import statistics
import time

#: 单位工作的样本载荷。形状接近"取一帧数据 → 序列化 → 解析 → 做字符串处理"这类业务动作。
_PAYLOAD = {
    "device": "zoo-bench",
    "seq": 0,
    "tags": ["alpha", "beta", "gamma", "delta"],
    "readings": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
    "meta": {"site": "edge", "unit": "celsius"},
}


def unit_work(seed: int) -> int:
    """一次单位工作。返回值由调用方累计，用作校验和。"""
    text = json.dumps({**_PAYLOAD, "seq": seed})
    parsed = json.loads(text)
    joined = "|".join(parsed["tags"] * 3)
    total = sum(parsed["readings"])
    return len(text) + len(joined) + int(total * 10) + seed


def run_iterations(iterations: int) -> tuple[float, int]:
    """跑 ``iterations`` 次单位工作，返回 (耗时秒, 校验和)。"""
    start = time.perf_counter()
    checksum = 0
    for index in range(iterations):
        checksum += unit_work(index)
    return time.perf_counter() - start, checksum


def measure_unit_work(repeats: int = 64) -> float:
    """一次单位工作的中位耗时（秒），用于把档位目标耗时换算成迭代次数。"""
    samples = []
    for index in range(repeats):
        start = time.perf_counter()
        unit_work(index)
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


def calibrate_iterations(
    target_seconds: float,
    *,
    tolerance: float = 0.05,
    max_passes: int = 4,
    samples: int = 3,
) -> int:
    """求出使执行体耗时接近 ``target_seconds`` 的迭代次数。

    **每一步都取多次采样的中位数，并迭代到收敛**，原因是一次实测就定格会让校准受单次噪声
    支配——实测在机器有负载时，单轮修正会把 10 ms 档偏到 +33%，而自检随后会因此随机失败。
    迭代到 ``tolerance`` 以内或 ``max_passes`` 用完为止。

    Args:
        target_seconds: 目标耗时（秒）。
        tolerance: 收敛判据。
        max_passes: 最大修正轮数。
        samples: 每轮取几次采样的中位数。

    Returns:
        迭代次数（至少 1）。
    """
    unit_seconds = measure_unit_work()
    if unit_seconds <= 0:
        return 1

    iterations = max(1, round(target_seconds / unit_seconds))
    for _ in range(max_passes):
        observed = statistics.median(
            run_iterations(iterations)[0] for _ in range(samples)
        )
        if observed <= 0:
            break
        if abs(observed - target_seconds) / target_seconds <= tolerance:
            break
        iterations = max(1, round(iterations * target_seconds / observed))
    return iterations


class RepresentativeBody:
    """跑 ``iterations`` 次单位工作，返回自己在内部实测的耗时。"""

    def __init__(self, target_seconds: float, iterations: int) -> None:
        self.target_seconds = target_seconds
        self.iterations = iterations
        self.checksum = 0

    def __call__(self) -> float:
        elapsed, checksum = run_iterations(self.iterations)
        self.checksum = checksum
        return elapsed


def body_for_tier(target_microseconds: float) -> RepresentativeBody:
    """按档位目标耗时（微秒）构造执行体，迭代次数已校准。"""
    target_seconds = target_microseconds / 1_000_000.0
    return RepresentativeBody(target_seconds, calibrate_iterations(target_seconds))
