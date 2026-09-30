"""报告模型：由原始数据抽出各输出后端共用的中间数据。

**这是 design D11「同源」的落点**——Markdown、HTML、PDF 三个后端都只消费这里产出的模型，
谁都不自己解析原始数据。理由：spec 要求"同一份数据可重复渲染出相同结果"，若某个后端自己
算一遍，两处口径必然漂移，而口径漂移正是本项目要消灭的缺陷类型。

模型里承载的是报告**实质性主张**，不是排版：

- ``conclusion``：交叉点。单点加速比没有选型含义——同一个框架开销，在 40 µs 的执行体上占
  端到端七成，在 10 ms 上只占百分之几。所以结论必须落在"多大的任务时长下用哪个方案"。
- ``unfavorable``：被测框架处于劣势的档位。**缺失该项的报告不合格**（spec 的硬要求）：
  一份只展示自己赢的报告，读者有理由认为它不可信。
- ``caveats``：各维度的口径局限与偏差来源，逐条列明，供读者自行判断适用性。
"""

from __future__ import annotations

from typing import Any

#: 项目文档里的框架开销阈值。开销占比降到它以下，选用该框架的代价才算可忽略。
#: 注意这是**离散档位上的近似**：真实交叉点落在相邻两档之间。
OVERHEAD_THRESHOLD = 0.15

MODEL_SCHEMA = "zoo-bench/report-model/1"

LOAD_COMPOSITION = (
    "单位工作 = JSON 序列化 + 反序列化 + 字符串拼接，重复若干次以达到目标耗时；"
    "档位由 matrix.yaml 的 body_tiers_us 声明"
)

LOAD_CAVEAT = (
    "**负载是替身，不是真实 trace**：形状接近“取一帧数据 → 序列化 → 解析 → 字符串处理”"
    "这类业务动作，但仓库内没有真实样本，结论的适用性以此为前提"
)


def _successful(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [unit for unit in units if unit["status"] == "ok"]


def subject_name(units: list[dict[str, Any]]) -> str | None:
    """被测框架的适配器标识（tier 为 ``under_test`` 的那个）。"""
    for unit in units:
        if unit.get("adapter", {}).get("tier") == "under_test":
            return str(unit["spec"]["adapter"])
    return None


def _overhead_crossings(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """各方案在多大的执行体时长下，框架开销占比降到阈值以下。"""
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for unit in _successful(units):
        key = (unit["spec"]["adapter"], unit["spec"]["concurrency"])
        grouped.setdefault(key, []).append(unit)

    crossings: list[dict[str, Any]] = []
    for (adapter, concurrency), group in sorted(grouped.items()):
        rows = sorted(group, key=lambda unit: unit["spec"]["body_tier_us"])
        ratios = [float(row["absolute"]["framework_overhead_ratio"]) for row in rows]
        tiers = [float(row["spec"]["body_tier_us"]) for row in rows]

        first_below = next(
            (
                tier
                for tier, ratio in zip(tiers, ratios, strict=True)
                if ratio <= OVERHEAD_THRESHOLD
            ),
            None,
        )
        crossings.append(
            {
                "adapter": adapter,
                "concurrency": concurrency,
                "threshold": OVERHEAD_THRESHOLD,
                "tiers_us": tiers,
                "overhead_ratios": ratios,
                "first_tier_at_or_below_threshold_us": first_below,
                "note": (
                    "该档位是所测档位中最小的满足者；真实交叉点落在它与前一档之间"
                    if first_below is not None
                    else "所测档位内没有一档的开销占比降到阈值以下"
                ),
            }
        )
    return crossings


def _relative_turnings(result: dict[str, Any], subject: str | None) -> list[dict[str, Any]]:
    """各对照方案从多大档位起不再快于被测框架。

    **实测可能不存在这样的档位**——那就如实报"未观测到"，不硬造一个。造一个假的交叉点比
    不报更坏：读者会拿它做决策。
    """
    if subject is None:
        return []

    comparisons = result.get("relative", {}).get("comparisons", [])
    by_adapter: dict[str, list[dict[str, Any]]] = {}
    for comparison in comparisons:
        for adapter, ratio in comparison.get("ratios_vs_subject", {}).items():
            by_adapter.setdefault(adapter, []).append(
                {
                    "concurrency": comparison["concurrency"],
                    "body_tier_us": comparison["body_tier_us"],
                    "ratio": ratio,
                }
            )

    turnings: list[dict[str, Any]] = []
    for adapter, rows in sorted(by_adapter.items()):
        for concurrency in sorted({row["concurrency"] for row in rows}):
            ordered = sorted(
                (row for row in rows if row["concurrency"] == concurrency),
                key=lambda row: row["body_tier_us"],
            )
            first_not_slower = next(
                (
                    row["body_tier_us"]
                    for row in ordered
                    if float(row["ratio"]) >= 1.0
                ),
                None,
            )
            turnings.append(
                {
                    "baseline": adapter,
                    "concurrency": concurrency,
                    "subject": subject,
                    "tiers_us": [row["body_tier_us"] for row in ordered],
                    "ratios_vs_subject": [row["ratio"] for row in ordered],
                    "first_tier_baseline_not_faster_us": first_not_slower,
                    "note": (
                        "该档位是所测档位中最小的满足者"
                        if first_not_slower is not None
                        else "**未观测到**：所测档位内该对照方案始终快于被测框架"
                    ),
                }
            )
    return turnings


def _summary(crossings: list[dict[str, Any]], turnings: list[dict[str, Any]]) -> list[str]:
    """结论摘要的句子。三个后端都直接用这几句，避免各写一遍导致措辞漂移。"""
    lines: list[str] = []

    subject_crossings = [
        crossing for crossing in crossings if crossing["first_tier_at_or_below_threshold_us"] is not None
    ]
    if subject_crossings:
        shortest = min(crossing["first_tier_at_or_below_threshold_us"] for crossing in subject_crossings)
        lines.append(
            f"执行体时长达到约 {shortest:g} µs 及以上时，框架开销占端到端的比例降到 "
            f"{OVERHEAD_THRESHOLD:.0%} 以下；短于此档位，选用本框架的主要代价就是框架开销本身。"
        )
    else:
        tiers = sorted({tier for crossing in crossings for tier in crossing["tiers_us"]})
        lines.append(
            "在所测档位范围内，没有任何一档的框架开销占比降到 "
            f"{OVERHEAD_THRESHOLD:.0%} 以下；所测档位为 {tiers}。"
        )

    for turning in turnings:
        if turning["first_tier_baseline_not_faster_us"] is not None:
            lines.append(
                f"并发度 {turning['concurrency']} 下，执行体时长超过约 "
                f"{turning['first_tier_baseline_not_faster_us']:g} µs 后，{turning['baseline']} "
                f"不再快于被测框架。"
            )
        else:
            lines.append(
                f"并发度 {turning['concurrency']} 下，所测档位内 {turning['baseline']} "
                f"**始终快于**被测框架——这类档位正是本报告的公开不利数据。"
            )
    return lines


def _unfavorable(result: dict[str, Any], subject: str | None) -> dict[str, Any]:
    """被测框架处于劣势的档位。**空缺即报告不合格**（spec 的硬要求）。"""
    items: list[dict[str, Any]] = []
    if subject is not None:
        for comparison in result.get("relative", {}).get("comparisons", []):
            for adapter, ratio in sorted(comparison.get("ratios_vs_subject", {}).items()):
                if float(ratio) < 1.0:
                    items.append(
                        {
                            "baseline": adapter,
                            "concurrency": comparison["concurrency"],
                            "body_tier_us": comparison["body_tier_us"],
                            "subject_median_seconds": comparison["subject_median_seconds"],
                            "baseline_ratio_vs_subject": ratio,
                            "gap": f"{adapter} 比 {subject} 快 {1 / float(ratio):.2f}x",
                        }
                    )

    return {
        "items": items,
        "note": (
            "被测框架处于劣势的档位。**这部分缺失的报告不合格**——一份只展示自己赢的报告，"
            "读者有理由认为它不可信。框架开销是纯增量，故短任务档位上出现劣势是预期结果。"
        ),
        "found": bool(items),
    }


def _caveats(units: list[dict[str, Any]], verification: dict[str, Any]) -> list[dict[str, Any]]:
    """口径局限与偏差来源，逐条列明。"""
    caveats: list[dict[str, Any]] = []

    drive_levels = {
        unit["adapter"]["drive_level"]
        for unit in _successful(units)
        if unit["adapter"].get("drive_level")
    }
    caveats.extend({"kind": "被测层级", "text": level} for level in sorted(drive_levels))

    for unit in _successful(units):
        if unit["adapter"].get("comparable") is False and unit["adapter"].get("notes"):
            caveats.append(
                {
                    "kind": "不可直接对标",
                    "adapter": unit["spec"]["adapter"],
                    "text": unit["adapter"]["notes"],
                }
            )
            break

    for unit in _successful(units):
        notes = unit["adapter"].get("notes", "")
        if "序列化" in notes:
            caveats.append({"kind": "口径偏差", "adapter": unit["spec"]["adapter"], "text": notes})
            break

    failed = [unit for unit in units if unit["status"] != "ok"]
    if failed:
        caveats.append(
            {
                "kind": "未完成的单元",
                "count": len(failed),
                "adapters": sorted({unit["spec"]["adapter"] for unit in failed}),
                "text": "这些单元失败，其数据不出现在报告中；失败原因见原始数据",
            }
        )

    if verification:
        bad = [
            adapter
            for adapter, outcome in verification.items()
            if outcome.get("status") != "ok" or not outcome.get("payload", {}).get("ok")
        ]
        if bad:
            caveats.append(
                {
                    "kind": "未通过等价性验证的适配器",
                    "adapters": sorted(bad),
                    "text": "未通过“提交的任务全部执行且各执行一次”的验证，其数据不可信",
                }
            )
    return caveats


def _latency_dimension(units: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "adapter": unit["spec"]["adapter"],
            "concurrency": unit["spec"]["concurrency"],
            "body_tier_us": unit["spec"]["body_tier_us"],
            "end_to_end_per_task": unit["absolute"]["end_to_end_per_task_seconds"],
        }
        for unit in _successful(units)
    ]
    return {
        "title": "延迟分位数与抖动",
        "unit": "秒/任务",
        "rows": rows,
        "note": "端到端 ÷ 并发度；分位数与相对离散度同表给出，只给中位数的度量不合格",
    }


def _overhead_dimension(units: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "adapter": unit["spec"]["adapter"],
            "concurrency": unit["spec"]["concurrency"],
            "body_tier_us": unit["spec"]["body_tier_us"],
            "framework_overhead_seconds": unit["absolute"]["framework_overhead_seconds"],
            "framework_overhead_ratio": unit["absolute"]["framework_overhead_ratio"],
            "body_seconds": unit["absolute"]["body_seconds"]["median"],
        }
        for unit in _successful(units)
    ]
    return {
        "title": "框架自身开销占比",
        "unit": "秒",
        "rows": rows,
        "note": "开销 = 端到端/并发度 − 同一次运行内实测的执行体耗时（不做跨运行减法）",
    }


def _throughput_dimension(units: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "adapter": unit["spec"]["adapter"],
            "concurrency": unit["spec"]["concurrency"],
            "body_tier_us": unit["spec"]["body_tier_us"],
            "throughput_per_second": unit["absolute"]["throughput_per_second"],
        }
        for unit in _successful(units)
    ]
    return {
        "title": "吞吐与并发伸缩",
        "unit": "任务/秒",
        "rows": rows,
        "note": "吞吐 = 并发度 ÷ 端到端中位数；随并发度是否继续上升即为伸缩性",
    }


def build_model(
    result: dict[str, Any],
    *,
    environment: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    semantics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """由一次运行结果抽取出报告模型。

    Args:
        result: :func:`zoo_bench.runner.measure_matrix` 的返回值。
        environment: 环境自述；None 时模型里标注为缺失（渲染层应拒绝发布）。
        source: 原始数据的来源信息（路径、框架规格、留档时间）。
        semantics: 调度语义维度的测量结果。**缺失时如实标注为未测量**，不省略该维度——
            省略会让读者以为它不存在，而它恰恰是被测框架最主要的差异化。

    Returns:
        可直接 JSON 序列化的报告模型。三个输出后端都只消费它。
    """
    units = result.get("units", [])
    subject = subject_name(units)
    crossings = _overhead_crossings(units)
    turnings = _relative_turnings(result, subject)

    return {
        "schema": MODEL_SCHEMA,
        "source": source,
        "environment": environment,
        "run": result.get("run", {}),
        "load": {
            "is_stand_in": True,
            "composition": LOAD_COMPOSITION,
            "caveat": LOAD_CAVEAT,
        },
        "subject": subject,
        "conclusion": {
            "overhead_threshold": OVERHEAD_THRESHOLD,
            "overhead_crossings": crossings,
            "relative_turnings": turnings,
            "summary": _summary(crossings, turnings),
            "note": "单点加速比没有选型含义；结论以“多大的执行体时长下选哪个方案”表述"
            "（同一份框架开销，在 40 µs 的任务上占七成，在 10 ms 上只占百分之几）",
        },
        "dimensions": {
            "latency": _latency_dimension(units),
            "overhead": _overhead_dimension(units),
            "throughput": _throughput_dimension(units),
            "semantics": semantics
            if semantics is not None
            else {
                "title": "调度语义的代价",
                "status": "not_measured",
                "scope_note": (
                    "本维度采用**内部开关对照**（同一 workload 下语义全关 vs 逐个开启），"
                    "**不与对照方案横向比较**——裸写法没有优先级/超时/重试的对应物，"
                    "故只能回答“本框架的语义值多少钱”，不能回答“比裸写法贵多少”"
                ),
                "reason": "尚未测量",
            },
        },
        "unfavorable": _unfavorable(result, subject),
        "caveats": _caveats(units, result.get("verification", {})),
        "absolute": {
            "note": result.get("run", {}).get("absolute_note"),
            "process_isolation": result.get("process_isolation", {}),
        },
        "self_check": result.get("self_check", {}),
    }
