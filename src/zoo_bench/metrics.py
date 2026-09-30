"""度量：分位数与离散度。

只给单一中心值的度量**不合格**（spec: measurement-protocol 要求报告离散度）——一个"中位数
133 µs"在共享 runner 上可能是 40~900 µs 的混合物，读者无从判断它能否作为依据。
"""

from __future__ import annotations

import statistics

DEFAULT_PERCENTILES: tuple[int, ...] = (50, 95, 99)


def percentile(samples: list[float], percent: float) -> float:
    """线性插值分位数。

    Args:
        samples: 样本；可为未排序。
        percent: 0~100。

    Returns:
        该分位数的插值结果。

    Raises:
        ValueError: 样本为空。
    """
    if not samples:
        raise ValueError("样本为空，无法取分位数")

    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(
    samples: list[float], percentiles: tuple[int, ...] = DEFAULT_PERCENTILES
) -> dict[str, float | int]:
    """中心值、分位数与离散度。

    Args:
        samples: 样本。
        percentiles: 要报告的分位数。

    Returns:
        含 ``n`` / ``median`` / ``mean`` / ``min`` / ``max`` / ``stdev`` / ``iqr`` /
        ``relative_spread``（标准差 ÷ 中位数）以及 ``p<p>`` 各项的映射。

    Raises:
        ValueError: 样本为空。
    """
    if not samples:
        raise ValueError("样本为空，无法汇总")

    ordered = sorted(samples)
    median = statistics.median(ordered)
    summary: dict[str, float | int] = {
        "n": len(ordered),
        "median": median,
        "mean": statistics.fmean(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        # 单样本时标准差无定义（statistics.stdev 会抛），按 0 处理
        "stdev": statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
        "iqr": percentile(ordered, 75) - percentile(ordered, 25),
    }
    for percent in percentiles:
        summary[f"p{percent}"] = percentile(ordered, percent)

    summary["relative_spread"] = summary["stdev"] / median if median else 0.0
    return summary
