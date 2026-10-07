"""报告模型：由原始数据抽出各输出后端共用的中间数据。

**这是 design D11「同源」的落点**——Markdown、HTML、PDF 三个后端都只消费这里产出的模型，
谁都不自己解析原始数据。理由：spec 要求"同一份数据可重复渲染出相同结果"，若某个后端自己
算一遍，两处口径必然漂移，而口径漂移正是本项目要消灭的缺陷类型。

模型里承载的是报告**实质性主张**，不是排版：

- ``conclusion``：交叉点。单点加速比没有选型含义——同一个框架开销，在 40 微秒 的执行体上占
  端到端七成，在 10 ms 上只占百分之几。所以结论必须落在"多大的任务时长下用哪个方案"。
- ``unfavorable``：被测框架处于劣势的档位。**缺失该项的报告不合格**（spec 的硬要求）：
  一份只展示自己赢的报告，读者有理由认为它不可信。
- ``caveats``：各维度的口径局限与偏差来源，逐条列明，供读者自行判断适用性。
"""

from __future__ import annotations

from typing import Any

from .attribution import SEGMENT_KEYS
from .caliber import (
    logical_cores_from_environment,
    overhead_is_interpretable,
    uninterpretable_reason,
)
from .i18n import LANG_ZH, t
from .metrics import summarize
from .runner import queueing_contaminated_groups

#: 项目文档里的框架开销阈值。开销占比降到它以下，选用该框架的代价才算可忽略。
#: 注意这是**离散档位上的近似**：真实交叉点落在相邻两档之间。
OVERHEAD_THRESHOLD = 0.15

MODEL_SCHEMA = "zoo-bench/report-model/1"


def _successful(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [unit for unit in units if unit["status"] == "ok"]


def _unit_overhead_is_interpretable(unit: dict[str, Any], logical_cores: int | None) -> bool:
    """某个测量单元的开销数字是否可当作开销来读。"""
    return overhead_is_interpretable(
        concurrency=int(unit["spec"]["concurrency"]),
        logical_cores=logical_cores,
        overhead_seconds=float(unit["absolute"]["framework_overhead_seconds"]),
    )


def withheld_overhead_groups(units: list[dict[str, Any]], logical_cores: int | None) -> list[str]:
    """被撤下开销数字的 (适配器/并发度) 组，供口径章节点名。"""
    return sorted(
        {
            f"{unit['spec']['adapter']}/{unit['spec']['concurrency']}"
            for unit in _successful(units)
            if not _unit_overhead_is_interpretable(unit, logical_cores)
        }
    )


def subject_name(units: list[dict[str, Any]]) -> str | None:
    """被测框架的适配器标识（tier 为 ``under_test`` 的那个）。"""
    for unit in units:
        if unit.get("adapter", {}).get("tier") == "under_test":
            return str(unit["spec"]["adapter"])
    return None


def _overhead_crossings(
    units: list[dict[str, Any]],
    contaminated: set[tuple[str, int]],
    logical_cores: int | None,
    lang: str,
) -> list[dict[str, Any]]:
    """各方案在多大的执行体时长下，框架开销占比降到阈值以下。

    **受排队污染的组不给交叉点**：并发度超出环境容量时端到端里含等待，除以并发度得到的
    "每任务开销"随之上升，此时算出来的交叉点不是框架开销的交叉点。原始值仍如实保留，
    只是不参与结论——否则头条会被一个排队数字左右。

    **开销数字本身不可读的组也不给交叉点**：并发度超过机器并行能力时，那串比值不是一个可读的
    序列（实测出现过负值），从它推交叉点等于从噪声里读结论。这类组连比值都不给，见
    :mod:`zoo_bench.caliber`。
    """
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for unit in _successful(units):
        key = (unit["spec"]["adapter"], unit["spec"]["concurrency"])
        grouped.setdefault(key, []).append(unit)

    crossings: list[dict[str, Any]] = []
    for (adapter, concurrency), group in sorted(grouped.items()):
        rows = sorted(group, key=lambda unit: unit["spec"]["body_tier_us"])
        tiers = [float(row["spec"]["body_tier_us"]) for row in rows]
        polluted = (adapter, concurrency) in contaminated
        interpretable = all(_unit_overhead_is_interpretable(row, logical_cores) for row in rows)
        ratios = (
            [float(row["absolute"]["framework_overhead_ratio"]) for row in rows]
            if interpretable
            else None
        )

        first_below = (
            None
            if polluted or not interpretable
            else next(
                (
                    tier
                    for tier, ratio in zip(tiers, ratios or [], strict=True)
                    if ratio <= OVERHEAD_THRESHOLD
                ),
                None,
            )
        )
        if not interpretable:
            note = uninterpretable_reason(lang)
        elif polluted:
            note = t("report.crossing.polluted", lang)
        elif first_below is not None:
            note = t("report.crossing.smallest_match", lang)
        else:
            note = t("report.crossing.none_below_threshold", lang)

        crossings.append(
            {
                "adapter": adapter,
                "concurrency": concurrency,
                "threshold": OVERHEAD_THRESHOLD,
                "tiers_us": tiers,
                "overhead_ratios": ratios,
                "first_tier_at_or_below_threshold_us": first_below,
                "queueing_contaminated": polluted,
                "uninterpretable": not interpretable,
                "note": note,
            }
        )
    return crossings


def _relative_turnings(
    result: dict[str, Any], subject: str | None, lang: str
) -> list[dict[str, Any]]:
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
                (row["body_tier_us"] for row in ordered if float(row["ratio"]) >= 1.0),
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
                        t("report.turning.smallest_match", lang)
                        if first_not_slower is not None
                        else t("report.turning.never_slower", lang)
                    ),
                }
            )
    return turnings


def _summary(
    crossings: list[dict[str, Any]],
    turnings: list[dict[str, Any]],
    subject: str | None,
    favorable: dict[str, Any],
    lang: str,
) -> list[str]:
    """结论摘要的句子。三个后端都直接用这几句，避免各写一遍导致措辞漂移。

    **开销交叉点只说被测框架那一行**。实测踩过这个坑：取所有方案里的最小值，于是头条写着
    "约 40 微秒 时开销降到 15% 以下"——那是某个对照方案的数字，而被测框架自己是 2700 微秒。
    头条说错了对象，整份报告的结论就被误读了。
    """
    lines: list[str] = []

    # 只从**可读的**交叉点里下结论：并发度超出机器并行能力的那些组根本没有可读的比值，
    # 从它们推结论等于从噪声里读结论（见 zoo_bench.caliber）。
    readable = [crossing for crossing in crossings if not crossing.get("uninterpretable")]
    subject_readable = [crossing for crossing in readable if crossing["adapter"] == subject]
    subject_crossings = [
        crossing
        for crossing in subject_readable
        if crossing["first_tier_at_or_below_threshold_us"] is not None
    ]
    if subject_crossings:
        shortest = min(
            crossing["first_tier_at_or_below_threshold_us"] for crossing in subject_crossings
        )
        lines.append(
            t(
                "report.summary.crossing_found",
                lang,
                subject=subject,
                tier=shortest,
                threshold=OVERHEAD_THRESHOLD,
            )
        )
    elif subject_readable:
        tiers = sorted({tier for crossing in subject_readable for tier in crossing["tiers_us"]})
        lines.append(
            t(
                "report.summary.crossing_missing",
                lang,
                subject=subject,
                threshold=OVERHEAD_THRESHOLD,
                tiers=tiers,
            )
        )
    else:
        lines.append(t("report.summary.overhead_all_unreadable", lang, subject=subject))

    # 正面那一半也要有一句：只讲"对照方案从多大档位起不再更快"是个负向表述，读者得自己反推
    # 优势。两个方向对称，句子结构与上面那几句一致（数字来自同一份比值，不含评价词）。
    items = favorable.get("items") or []
    if items:
        best = max(items, key=lambda item: float(item["baseline_ratio_vs_subject"]))
        lines.append(
            t(
                "report.summary.favourable_best",
                lang,
                concurrency=best["concurrency"],
                tier=best["body_tier_us"],
                baseline=best["baseline"],
                multiple=float(best["baseline_ratio_vs_subject"]),
            )
        )

    for turning in turnings:
        if turning["first_tier_baseline_not_faster_us"] is not None:
            lines.append(
                t(
                    "report.summary.turning_found",
                    lang,
                    concurrency=turning["concurrency"],
                    tier=turning["first_tier_baseline_not_faster_us"],
                    baseline=turning["baseline"],
                )
            )
        else:
            lines.append(
                t(
                    "report.summary.turning_missing",
                    lang,
                    concurrency=turning["concurrency"],
                    baseline=turning["baseline"],
                )
            )
    return lines


#: 判定"分不出胜负"所需的最少采样轮数。少于它，跨轮离散度没有意义——那时**不判定**，
#: 而不是拿一个不可靠的带宽去分类。
MIN_ROUNDS_FOR_BAND = 4


def _unit_round_spread(unit: dict[str, Any]) -> float | None:
    """某个测量单元"每任务中位数"的跨轮相对离散度（标准差 / 中位数）。

    **与逐单元表里的离散度不是一回事**：表里的 p95/p99 描述单元内任务之间的分布，而这里要的是
    "这一组数值换个时候再测会差多少"——两个方案之间的差异得比它大，才谈得上分辨。故按轮先算出
    每任务中位数，再取这些中位数的相对离散度。

    样本不足（或老留档没有 ``rounds``）时返回 None：那时不判，见 :func:`_classify_relative`。
    """
    rounds = unit.get("rounds") or []
    samples = [
        float(item["wall_seconds"]) / float(item["batch"]) for item in rounds if item.get("batch")
    ]
    if len(samples) < MIN_ROUNDS_FOR_BAND:
        return None
    return float(summarize(samples)["relative_spread"])


def _classify_relative(
    result: dict[str, Any],
    subject: str | None,
    spreads: dict[tuple[str, int, float], float | None],
    lang: str,
) -> dict[str, list[dict[str, Any]]]:
    """把每个 (对照方案, 并发度, 档位) 归入：更快 / 更慢 / 分不出胜负。

    **带宽由同一次运行的两侧离散度相加得到**（design D1）：写死一个百分比在噪声小的机器上过宽
    （把真差异藏掉）、在噪声大的机器上过窄（把噪声当信号），两个方向都会把结论带偏。故带宽随样本
    变化，并随报告发布，使读者能自己复核。

    样本不足时**不判定**，把该档位列入"分不出胜负"并注明原因——编一个默认带宽等于让一个无法
    复核的数参与结论。
    """
    favorable: list[dict[str, Any]] = []
    unfavorable: list[dict[str, Any]] = []
    tied: list[dict[str, Any]] = []

    if subject is None:
        return {"favorable": favorable, "unfavorable": unfavorable, "tied": tied}

    for comparison in result.get("relative", {}).get("comparisons", []):
        concurrency = int(comparison["concurrency"])
        tier = float(comparison["body_tier_us"])
        subject_spread = spreads.get((subject, concurrency, tier))
        for baseline, ratio in sorted(comparison.get("ratios_vs_subject", {}).items()):
            other_spread = spreads.get((baseline, concurrency, tier))
            band = (
                None
                if subject_spread is None or other_spread is None
                else subject_spread + other_spread
            )
            common = {
                "baseline": baseline,
                "concurrency": concurrency,
                "body_tier_us": tier,
                "subject_median_seconds": comparison["subject_median_seconds"],
                "baseline_ratio_vs_subject": ratio,
                "band": band,
            }
            value = float(ratio)
            if band is None:
                tied.append(
                    {
                        **common,
                        "reason": t("report.classify.no_rounds", lang),
                    }
                )
            elif value > 1.0 + band:
                favorable.append(
                    {
                        **common,
                        "margin": t(
                            "report.classify.margin", lang, baseline=baseline, multiple=value
                        ),
                    }
                )
            elif value < 1.0 - band:
                unfavorable.append(
                    {
                        **common,
                        "gap": t(
                            "report.classify.gap",
                            lang,
                            baseline=baseline,
                            subject=subject,
                            multiple=1 / value,
                        ),
                    }
                )
            else:
                tied.append(
                    {
                        **common,
                        "reason": t(
                            "report.classify.tied",
                            lang,
                            difference=abs(value - 1.0),
                            band=band,
                        ),
                    }
                )
    return {"favorable": favorable, "unfavorable": unfavorable, "tied": tied}


def _relative_classification(
    result: dict[str, Any], units: list[dict[str, Any]], subject: str | None, lang: str
) -> dict[str, Any]:
    """逐档相对比 -> 三类（更快 / 更慢 / 分不出胜负），每类各配说明。

    **优势与不利同源**：两者都是同一份 ``ratios_vs_subject`` 的一半，只是一个取大于「1 + 带宽」
    的、一个取小于「1 - 带宽」的，其余进「分不出胜负」。同源意味着覆盖面相同——谁都不能只挑对
    自己有利的档位来呈现。
    """
    spreads = {
        (
            unit["spec"]["adapter"],
            int(unit["spec"]["concurrency"]),
            float(unit["spec"]["body_tier_us"]),
        ): _unit_round_spread(unit)
        for unit in _successful(units)
    }
    classes = _classify_relative(result, subject, spreads, lang)
    bands = [
        float(item["band"])
        for key in ("favorable", "unfavorable", "tied")
        for item in classes[key]
        if item["band"] is not None
    ]

    return {
        "favorable": {
            "items": classes["favorable"],
            "found": bool(classes["favorable"]),
            "note": t("report.favourable.note", lang),
        },
        "unfavorable": {
            "items": classes["unfavorable"],
            "found": bool(classes["unfavorable"]),
            "note": t("report.unfavourable.note", lang),
        },
        "tied": {
            "items": classes["tied"],
            "found": bool(classes["tied"]),
            "note": t("report.tied.note", lang),
            "band_min": min(bands) if bands else None,
            "band_max": max(bands) if bands else None,
        },
        "band_definition": t("report.tied.band_definition", lang),
    }


def _caveats(
    units: list[dict[str, Any]],
    verification: dict[str, Any],
    self_check: dict[str, Any],
    logical_cores: int | None,
    lang: str,
) -> list[dict[str, Any]]:
    """口径局限与偏差来源，逐条列明。"""
    caveats: list[dict[str, Any]] = []

    drive_levels = {
        unit["adapter"]["drive_level"]
        for unit in _successful(units)
        if unit["adapter"].get("drive_level")
    }
    # `drive_level` 是**适配器自述的元数据**（会随留档进报告）。它是目录键而不是散文，
    # 渲染时才按语言解析（design D8）——留档因此是语言无关的数据。
    caveats.extend(
        {"kind": t("report.caveat.level.kind", lang), "text": level}
        for level in sorted(drive_levels)
    )

    # 每个方案**自己声明的口径**都进报告。原先按关键字挑（只挑含"序列化"的）是刻意的省事，
    # 但那是"我替读者判断哪条重要"，而代价是漏掉没被关键字命中的成本说明。
    seen: set[str] = set()
    for unit in _successful(units):
        adapter = unit["spec"]["adapter"]
        notes = unit["adapter"].get("notes", "")
        if not notes or adapter in seen:
            continue
        seen.add(adapter)
        caveats.append(
            {
                "kind": (
                    t("report.caveat.incomparable.kind", lang)
                    if unit["adapter"].get("comparable") is False
                    else t("report.caveat.scope.kind", lang)
                ),
                "adapter": adapter,
                "text": notes,
            }
        )

    failed = [unit for unit in units if unit["status"] != "ok"]
    if failed:
        caveats.append(
            {
                "kind": t("report.caveat.failed_units.kind", lang),
                "count": len(failed),
                "adapters": sorted({unit["spec"]["adapter"] for unit in failed}),
                "text": t("report.caveat.failed_units.text", lang),
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
                    "kind": t("report.caveat.equivalence.kind", lang),
                    "adapters": sorted(bad),
                    "text": t("report.caveat.equivalence.text", lang),
                }
            )
    contaminated = queueing_contaminated_groups(self_check)
    if contaminated:
        caveats.append(
            {
                "kind": t("report.caveat.queueing.kind", lang),
                "count": len(contaminated),
                "text": t("report.caveat.queueing.text", lang),
                "groups": [f"{group['adapter']}/{group['concurrency']}" for group in contaminated],
            }
        )

    withheld = withheld_overhead_groups(units, logical_cores)
    if withheld:
        caveats.append(
            {
                "kind": t("report.caveat.withheld.kind", lang),
                "count": len(withheld),
                "text": t("report.caveat.withheld.text", lang),
                "groups": withheld,
            }
        )

    ungated = [
        check
        for check in self_check.get("body_deviation", {}).get("checks", [])
        if not check.get("gated", True)
    ]
    if ungated:
        tiers = sorted(
            {
                round(float(check["body_target_seconds"]) * 1e6)
                for check in ungated
                if "body_target_seconds" in check
            }
        )
        caveats.append(
            {
                "kind": t("report.caveat.ungated.kind", lang),
                "count": len(ungated),
                "text": t("report.caveat.ungated.text", lang),
                "tiers_us": tiers,
            }
        )

    return caveats


def _latency_dimension(units: list[dict[str, Any]], lang: str) -> dict[str, Any]:
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
        "title": t("report.dimension.latency.title", lang),
        "unit": t("unit.seconds_per_task", lang),
        "rows": rows,
        "note": t("report.dimension.latency.note", lang),
    }


def _overhead_dimension(
    units: list[dict[str, Any]], logical_cores: int | None, lang: str
) -> dict[str, Any]:
    """框架开销维度。

    **不可读的组不给数字**（见 :mod:`zoo_bench.caliber`）：并发度超出机器并行能力时，执行体自报
    的耗时含超订的调度等待，与每任务端到端不可比。此时把两个数字置为 ``None`` 而不是留着让渲染
    层去判断——留在模型里，任何一个后端都可能顺手把它印出来。
    """
    rows: list[dict[str, Any]] = []
    for unit in _successful(units):
        interpretable = _unit_overhead_is_interpretable(unit, logical_cores)
        rows.append(
            {
                "adapter": unit["spec"]["adapter"],
                "concurrency": unit["spec"]["concurrency"],
                "body_tier_us": unit["spec"]["body_tier_us"],
                "framework_overhead_seconds": (
                    unit["absolute"]["framework_overhead_seconds"] if interpretable else None
                ),
                "framework_overhead_ratio": (
                    unit["absolute"]["framework_overhead_ratio"] if interpretable else None
                ),
                "body_seconds": unit["absolute"]["body_seconds"]["median"],
                "interpretable": interpretable,
                "note": "" if interpretable else uninterpretable_reason(lang),
            }
        )
    return {
        "title": t("report.dimension.overhead.title", lang),
        "unit": t("unit.seconds", lang),
        "rows": rows,
        "note": t("report.dimension.overhead.note", lang),
    }


def _throughput_dimension(units: list[dict[str, Any]], lang: str) -> dict[str, Any]:
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
        "title": t("report.dimension.throughput.title", lang),
        "unit": t("unit.tasks_per_second", lang),
        "rows": rows,
        "note": t("report.dimension.throughput.note", lang),
    }


#: 四段的显示名。键与顺序取自探针（:data:`zoo_bench.attribution.SEGMENT_KEYS`）——三个后端共用
#: 同一套，各写一份必然漂移。文案在消息目录里，这里只做"探针键 -> 目录键"的映射。
_SEGMENT_MESSAGE_KEYS: dict[str, str] = {
    "submit_side_seconds": "report.attribution.segment.submit_side",
    "handoff_seconds": "report.attribution.segment.handoff",
    "body_seconds": "report.attribution.segment.body",
    "return_seconds": "report.attribution.segment.return",
}

#: 被测框架提交侧的细分桶与显示名。
_DRILL_MESSAGE_KEYS: dict[str, str] = {
    "scheduling_round_seconds": "report.attribution.drill.scheduling_round",
    "dispatch_seconds": "report.attribution.drill.dispatch",
    "policy_lookup_seconds": "report.attribution.drill.policy_lookup",
    "submit_side_other_seconds": "report.attribution.drill.submit_side_other",
}


def attribution_segment_label(key: str, lang: str) -> str:
    """四段之一的显示名（按语言）。"""
    return t(_SEGMENT_MESSAGE_KEYS[key], lang)


def attribution_drill_label(key: str, lang: str) -> str:
    """提交侧细分桶之一的显示名（按语言）。"""
    return t(_DRILL_MESSAGE_KEYS[key], lang)


def _attribution_findings(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐 (档位, 并发度)：被测框架相对各对照方案，超出部分落在哪一段。

    **结论必须指到段**：只给"多花 57 微秒"读者不知道下一步动哪里，而那正是这一维存在的理由。
    故每个对照方案都给一份逐段差额，并点出差额最大的那一段。
    """
    by_key: dict[tuple[float, int], dict[str, dict[str, Any]]] = {}
    for group in groups:
        if group.get("status") != "ok":
            continue
        by_key.setdefault((float(group["tier_us"]), int(group["concurrency"])), {})[
            group["adapter"]
        ] = group

    findings: list[dict[str, Any]] = []
    for (tier, concurrency), group in sorted(by_key.items()):
        subject = next(
            (item for item in group.values() if item.get("adapter_tier") == "under_test"), None
        )
        if subject is None:
            continue

        rows: list[dict[str, Any]] = []
        for adapter, other in sorted(group.items()):
            if adapter == subject["adapter"] or not other.get("comparable", True):
                continue
            excess = {
                key: subject["segments"][key] - other["segments"][key] for key in SEGMENT_KEYS
            }
            dominant = max(excess, key=lambda key: excess[key])
            rows.append(
                {
                    "adapter": adapter,
                    "subject_end_to_end_seconds": subject["end_to_end_seconds"],
                    "other_end_to_end_seconds": other["end_to_end_seconds"],
                    "excess_seconds": subject["end_to_end_seconds"] - other["end_to_end_seconds"],
                    "segment_excess_seconds": excess,
                    "dominant_segment": dominant,
                }
            )
        if rows:
            findings.append(
                {
                    "tier_us": tier,
                    "concurrency": concurrency,
                    "subject": subject["adapter"],
                    "subject_segments": subject["segments"],
                    "subject_drill_down": subject.get("drill_down") or {},
                    "per_adapter": rows,
                }
            )
    return findings


def _attribution_summary(findings: list[dict[str, Any]], lang: str) -> list[str]:
    """结论摘要：超出部分落在哪一段，以及被测框架自己的提交侧由什么构成。

    **整体不慢于对照时不给"落在哪一段"**：逐段差额之和可能为负，而某一段仍为正——那时说
    "多花 -164 微秒、主要落在手交 +2853 微秒"是自相矛盾的（实测出现过）。两种情况分开表述。
    """
    lines: list[str] = []
    for finding in findings:
        for row in finding["per_adapter"]:
            dominant = row["dominant_segment"]
            excess = row["excess_seconds"]
            if excess <= 0:
                lines.append(
                    t(
                        "report.attribution.finding_not_slower",
                        lang,
                        concurrency=finding["concurrency"],
                        tier=finding["tier_us"],
                        baseline=row["adapter"],
                        excess=excess * 1e6,
                    )
                )
                continue
            lines.append(
                t(
                    "report.attribution.finding_slower",
                    lang,
                    concurrency=finding["concurrency"],
                    tier=finding["tier_us"],
                    baseline=row["adapter"],
                    excess=excess * 1e6,
                    segment=attribution_segment_label(dominant, lang),
                    share=row["segment_excess_seconds"][dominant] * 1e6,
                )
            )
            opposing = sorted(
                ((key, value) for key, value in row["segment_excess_seconds"].items() if value < 0),
                key=lambda item: item[1],
            )
            if opposing:
                key, value = opposing[0]
                lines.append(
                    t(
                        "report.attribution.finding_mixed",
                        lang,
                        segment=attribution_segment_label(key, lang),
                        share=value * 1e6,
                    )
                )
        drill = finding["subject_drill_down"]
        if drill:
            # 分隔符随语言：中文顿号在英文产物里是非 ASCII，会被导出前的字符门禁拦下
            separator = "、" if lang == LANG_ZH else ", "
            parts = separator.join(
                f"{attribution_drill_label(key, lang)} {drill[key] * 1e6:.0f}"
                for key in _DRILL_MESSAGE_KEYS
                if key in drill
            )
            lines.append(t("report.attribution.submit_side_composition", lang, parts=parts))
    return lines


def _not_probed_attribution(lang: str) -> dict[str, Any]:
    """归因在**连探查都没跑**时的占位。

    与"跑了但某组不可测"不同：那是结论，这是遗漏。报告里必须能看出是哪一种。
    """
    return {
        "title": t("report.attribution.title", lang),
        "status": "not_probed",
        "scope_note": t("report.attribution.scope_note", lang),
        "segment_labels": {
            key: attribution_segment_label(key, lang) for key in _SEGMENT_MESSAGE_KEYS
        },
        "drill_labels": {key: attribution_drill_label(key, lang) for key in _DRILL_MESSAGE_KEYS},
        "groups": [],
        "findings": [],
        "summary": [],
        "reason": t("report.attribution.not_in_run", lang),
    }


def _attribution_dimension(result: dict[str, Any], lang: str) -> dict[str, Any]:
    """开销归因维度：四段、细分与逐段结论。"""
    attribution = result.get("attribution")
    if not attribution:
        return _not_probed_attribution(lang)

    groups = list(attribution.get("groups") or [])
    findings = _attribution_findings(groups)
    measured = [group for group in groups if group.get("status") == "ok"]
    return {
        "title": t("report.attribution.title", lang),
        "status": "ok" if measured else "not_measured",
        "scope_note": t("report.attribution.detail_note", lang),
        "segment_labels": {
            key: attribution_segment_label(key, lang) for key in _SEGMENT_MESSAGE_KEYS
        },
        "drill_labels": {key: attribution_drill_label(key, lang) for key in _DRILL_MESSAGE_KEYS},
        "cores": attribution.get("cores"),
        "tiers_us": attribution.get("tiers_us", []),
        "concurrencies": attribution.get("concurrencies", []),
        "over_subscribed_concurrencies": attribution.get("over_subscribed_concurrencies", []),
        "note": attribution.get("note", ""),
        "groups": groups,
        "findings": findings,
        "summary": _attribution_summary(findings, lang),
        "reason": "" if measured else t("report.attribution.none_measured", lang),
    }


def _unmeasured_semantics(lang: str) -> dict[str, Any]:
    """语义维度在**连探查都没跑**时的占位。

    与"探查跑了、结论是不可测"不同：那是结论，这是遗漏。两者的区分正是 ``reason`` 与
    ``items`` 的有无——报告里必须能看出是哪一种。
    """
    return {
        "title": t("report.semantics.title", lang),
        "status": "not_probed",
        "scope_note": t("report.semantics.scope_note", lang),
        "items": [],
        "reason": t("report.semantics.not_in_run", lang),
    }


def build_model(
    result: dict[str, Any],
    *,
    lang: str,
    environment: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    semantics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """由一次运行结果抽取出报告模型。

    Args:
        result: :func:`zoo_bench.runner.measure_matrix` 的返回值。
        lang: **模型的语言**。必填、无默认值——模型里承载的是报告的文字（维度名、结论句、
            口径局限），故一份模型必然是某一种语言的；下游（块层、三个后端、图表）都从
            ``model["lang"]`` 读，不再各自传一次。
        environment: 环境自述；None 时取 ``result["environment"]``（由 ``zoo-bench run``
            采集并随留档存入），两者都没有时模型里标为缺失——渲染层据此拒绝发布。
        source: 原始数据的来源信息（路径、框架规格、留档时间）。
        semantics: 调度语义维度的测量结果。**缺省时取 ``result["semantics"]``**（由
            :func:`zoo_bench.runner.measure_matrix` 的探查产出）；两者都没有时如实标注为
            未探查。**无论如何都不省略该维度**——省略会让读者以为它不存在，而它恰恰是被测
            框架最主要的差异化。

    Returns:
        可直接 JSON 序列化的报告模型。三个输出后端都只消费它。
    """
    units = result.get("units", [])
    subject = subject_name(units)
    self_check = result.get("self_check", {})
    contaminated = {
        (group["adapter"], group["concurrency"])
        for group in queueing_contaminated_groups(self_check)
    }
    resolved_semantics = semantics if semantics is not None else result.get("semantics")
    resolved_environment = environment if environment is not None else result.get("environment")
    # 机器并行能力取自**同一次运行**的环境自述：它是"开销这个减式成立与否"的判据，
    # 跨运行借用另一台机器的核数就会判错（见 zoo_bench.caliber）。
    logical_cores = logical_cores_from_environment(resolved_environment)

    crossings = _overhead_crossings(units, contaminated, logical_cores, lang)
    turnings = _relative_turnings(result, subject, lang)
    classification = _relative_classification(result, units, subject, lang)

    return {
        "schema": MODEL_SCHEMA,
        "lang": lang,
        "source": source,
        "environment": resolved_environment,
        "run": result.get("run", {}),
        "load": {
            "is_stand_in": True,
            "composition": t("report.load.composition", lang),
            "caveat": t("report.load.caveat", lang),
        },
        "subject": subject,
        "conclusion": {
            "overhead_threshold": OVERHEAD_THRESHOLD,
            "overhead_crossings": crossings,
            "relative_turnings": turnings,
            "summary": _summary(crossings, turnings, subject, classification["favorable"], lang),
            "note": t("report.conclusion.note", lang),
        },
        "dimensions": {
            "latency": _latency_dimension(units, lang),
            "overhead": _overhead_dimension(units, logical_cores, lang),
            "throughput": _throughput_dimension(units, lang),
            "semantics": resolved_semantics
            if resolved_semantics is not None
            else _unmeasured_semantics(lang),
            "attribution": _attribution_dimension(result, lang),
        },
        "favorable": classification["favorable"],
        "unfavorable": classification["unfavorable"],
        "tied": classification["tied"],
        "tie_band": {"definition": classification["band_definition"]},
        "caveats": _caveats(units, result.get("verification", {}), self_check, logical_cores, lang),
        "absolute": {
            "note": result.get("run", {}).get("absolute_note"),
            "process_isolation": result.get("process_isolation", {}),
        },
        "self_check": self_check,
    }
