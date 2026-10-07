"""跨版本对比。

## 只比"同运行内的相对量"，不比绝对耗时

design D6 定下"绝对耗时不可跨运行比较"，而**版本间变化本质上就是跨运行比较**——所以这里存在
一个陷阱：直接比两次运行的微秒数，会得到一份看着权威的垃圾（共享 runner 的漂移足以淹没版本
差异）。可比的是**同运行内的相对量**：

- **开销占比**：框架开销 / 端到端，是比值，与环境快慢无关
- **相对倍数**：对照方案的中位数 / 被测框架的中位数，同一次运行内两两对照得出，对整体漂移
  不敏感

绝对耗时仍然列出，但**标为不可跨运行比较**——读者的诚实做法是拿它看量级，而不是看变化。

## 三处必须显式报告而不是静默处理

- **只在一侧出现的单元**：档位或适配器变过，两侧就不是同一批单元，差异无法归因。列出来，
  而不是取交集悄悄丢掉。
- **两次运行的环境是否一致**：不是同一台机器/同一平台时，对比的效力依赖读者的判断，故把两边
  的环境都摆出来并标出差异字段。
- **两侧的驱动面机制是否相同**：完成信号的取得方式随版本变过，而它**计入被测框架的端到端**。
  机制不同时，两侧差异里有一部分是机制自身的成本——那属于版本变化，但不能整体读成性能改进。
"""

from __future__ import annotations

from typing import Any

from .caliber import (
    logical_cores_from_environment,
    overhead_is_interpretable,
    uninterpretable_reason,
)
from .i18n import t

_ENVIRONMENT_KEYS = (
    "hardware.cpu_model",
    "hardware.logical_cores",
    "hardware.platform",
    "python.version",
)


def _dig(source: dict[str, Any], path: str) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _index(run: dict[str, Any]) -> dict[tuple[str, int, float], dict[str, Any]]:
    """把测量单元索引成 (适配器, 并发度, 档位微秒) -> 单元。"""
    return {
        (
            unit["spec"]["adapter"],
            int(unit["spec"]["concurrency"]),
            float(unit["spec"]["body_tier_us"]),
        ): unit
        for unit in run.get("units", [])
        if unit.get("status") == "ok"
    }


def _metric_semantics(lang: str) -> dict[str, str]:
    """参与对比的相对量。每一项都说明"上升意味着什么"——方向本身不带好坏含义，含义写在度量里。"""
    return {
        "overhead_ratio": t("compare.metric.overhead_ratio", lang),
        "speedup_vs_baseline": t("compare.metric.speedup_vs_baseline", lang),
    }


def _change(before: float | None, after: float | None, lang: str) -> dict[str, Any]:
    """两次取值的变化。``ratio`` 为 None 表示基准为 0、比值无意义。"""
    if before is None or after is None:
        return {
            "before": before,
            "after": after,
            "ratio": None,
            "direction": t("compare.direction.missing", lang),
        }
    if before == 0:
        return {
            "before": before,
            "after": after,
            "ratio": None,
            "direction": t("compare.direction.zero_baseline", lang),
        }

    ratio = after / before
    if abs(ratio - 1.0) < 1e-9:
        direction = t("compare.direction.flat", lang)
    elif ratio > 1:
        direction = t("compare.direction.up", lang)
    else:
        direction = t("compare.direction.down", lang)
    return {
        "before": before,
        "after": after,
        "ratio": ratio,
        "delta": ratio - 1.0,
        "direction": direction,
    }


def _environment_diff(before: dict[str, Any], after: dict[str, Any], lang: str) -> dict[str, Any]:
    differences = {
        key: {"before": _dig(before, key), "after": _dig(after, key)}
        for key in _ENVIRONMENT_KEYS
        if _dig(before, key) != _dig(after, key)
    }
    return {
        "same": not differences,
        "differences": differences,
        "note": (
            t("compare.environment.same", lang)
            if not differences
            else t("compare.environment.different", lang)
        ),
    }


def _drive_generation_diff(
    before_run: dict[str, Any], after_run: dict[str, Any], lang: str
) -> dict[str, Any]:
    """两侧各自按哪一代驱动面测量，以及机制不同时对读数的含义。

    **驱动机制会随版本变**（实测：完成信号一代取自框架内部回调、一代取自框架自身的结果响应器），
    而两者的开销**都计入被测框架的端到端**。所以两侧开销占比的差异里有一部分是**驱动机制自身
    的成本**——它确实是这次版本变化的一部分，但不能整体读成一般意义的性能改进。不写出来，
    读者会把它全部读成改进。
    """

    def side(run: dict[str, Any]) -> dict[str, Any]:
        subject = _dig(run.get("environment") or {}, "subject.drive_generation")
        subject = subject if isinstance(subject, dict) else {}
        return {"generation": subject.get("generation"), "label": subject.get("label")}

    before, after = side(before_run), side(after_run)
    if before["generation"] is None or after["generation"] is None:
        note = t("compare.generation.missing", lang)
    elif before["generation"] == after["generation"]:
        note = t("compare.generation.same", lang, generation=before["generation"])
    else:
        note = t(
            "compare.generation.different",
            lang,
            before_gen=before["generation"],
            after_gen=after["generation"],
        )
    return {
        "before": before,
        "after": after,
        "same": before["generation"] == after["generation"],
        "note": note,
    }


def compare_versions(
    before_run: dict[str, Any],
    after_run: dict[str, Any],
    *,
    before_label: str,
    after_label: str,
    lang: str,
) -> dict[str, Any]:
    """对比两次留档运行。

    Args:
        before_run: 旧版本的运行结果。
        after_run: 新版本的运行结果。
        before_label: 旧版本的显示标签。
        after_label: 新版本的显示标签。
        lang: 对比报告语言（``"en"`` 或 ``"zh"``）。

    Returns:
        含两侧元信息、环境差异、各维度相对量变化与单元差集的对比结果。可 JSON 序列化。
    """
    before_units = _index(before_run)
    after_units = _index(after_run)
    shared = sorted(set(before_units) & set(after_units))

    # 两侧各自的机器并行能力：跨版本对比里两次运行可能不在同一台机器上（那一点由
    # `environment` 那一节负责报警），故核数必须逐侧取，不能共用。
    before_cores = logical_cores_from_environment(before_run.get("environment"))
    after_cores = logical_cores_from_environment(after_run.get("environment"))

    overhead: list[dict[str, Any]] = []
    speedups: list[dict[str, Any]] = []
    absolute: list[dict[str, Any]] = []

    for key in shared:
        adapter, concurrency, tier = key
        old, new = before_units[key], after_units[key]

        readable = overhead_is_interpretable(
            concurrency=concurrency,
            logical_cores=before_cores,
            overhead_seconds=float(old["absolute"]["framework_overhead_seconds"]),
        ) and overhead_is_interpretable(
            concurrency=concurrency,
            logical_cores=after_cores,
            overhead_seconds=float(new["absolute"]["framework_overhead_seconds"]),
        )
        # 任一侧不可读，则"变化"没有意义——它比的是两个不可读的数之差。此时连两侧的原始
        # 比值都不给：读者会拿它自己去算一个数出来（见 zoo_bench.caliber）。
        overhead.append(
            {
                "adapter": adapter,
                "concurrency": concurrency,
                "body_tier_us": tier,
                "interpretable": readable,
                "note": "" if readable else uninterpretable_reason(lang),
                **(
                    _change(
                        old["absolute"]["framework_overhead_ratio"],
                        new["absolute"]["framework_overhead_ratio"],
                        lang,
                    )
                    if readable
                    else {
                        "before": None,
                        "after": None,
                        "ratio": None,
                        "direction": t("compare.direction.unreadable", lang),
                    }
                ),
            }
        )
        absolute.append(
            {
                "adapter": adapter,
                "concurrency": concurrency,
                "body_tier_us": tier,
                "before_median_seconds": old["absolute"]["end_to_end_per_task_seconds"]["median"],
                "after_median_seconds": new["absolute"]["end_to_end_per_task_seconds"]["median"],
            }
        )

    # 相对倍数按 (对照方案, 并发度, 档位) 对照——两侧各有自己的相对比，再比它们
    before_ratios = _ratios_by_key(before_run)
    after_ratios = _ratios_by_key(after_run)
    for key in sorted(set(before_ratios) & set(after_ratios)):
        baseline, concurrency, tier = key
        speedups.append(
            {
                "baseline": baseline,
                "concurrency": concurrency,
                "body_tier_us": tier,
                **_change(before_ratios[key], after_ratios[key], lang),
            }
        )

    only_in_one: list[dict[str, Any]] = []
    for key in sorted(set(before_units) ^ set(after_units)):
        adapter, concurrency, tier = key
        only_in_one.append(
            {
                "adapter": adapter,
                "concurrency": concurrency,
                "body_tier_us": tier,
                "present_in": "before" if key in before_units else "after",
            }
        )

    return {
        "before": _side(before_run, before_label),
        "after": _side(after_run, after_label),
        "lang": lang,
        "environment": _environment_diff(
            before_run.get("environment") or {}, after_run.get("environment") or {}, lang
        ),
        "drive_generation": _drive_generation_diff(before_run, after_run, lang),
        "metric_semantics": _metric_semantics(lang),
        "shared_unit_count": len(shared),
        "only_in_one": only_in_one,
        "overhead_ratio": overhead,
        "speedup_vs_baseline": speedups,
        "absolute_seconds": absolute,
        "absolute_note": t("compare.absolute_note", lang),
    }


def _side(run: dict[str, Any], label: str) -> dict[str, Any]:
    """对比中一侧的元信息。自检未通过的一侧其数字不可信，必须标出来。"""
    return {
        "label": label,
        "framework": (run.get("environment") or {}).get("subject", {}).get("dist_version"),
        "self_check_ok": bool(run.get("self_check", {}).get("ok")),
        "unit_count": len(_index(run)),
    }


def _ratios_by_key(run: dict[str, Any]) -> dict[tuple[str, int, float], float]:
    """把该次运行的"相对比"索引成 (对照方案, 并发度, 档位) -> 倍数。"""
    ratios: dict[tuple[str, int, float], float] = {}
    for comparison in run.get("relative", {}).get("comparisons", []):
        for baseline, ratio in comparison.get("ratios_vs_subject", {}).items():
            ratios[
                (baseline, int(comparison["concurrency"]), float(comparison["body_tier_us"]))
            ] = float(ratio)
    return ratios
