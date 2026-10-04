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
    UNINTERPRETABLE_REASON,
    logical_cores_from_environment,
    overhead_is_interpretable,
)
from .metrics import summarize
from .runner import queueing_contaminated_groups

#: 项目文档里的框架开销阈值。开销占比降到它以下，选用该框架的代价才算可忽略。
#: 注意这是**离散档位上的近似**：真实交叉点落在相邻两档之间。
OVERHEAD_THRESHOLD = 0.15

MODEL_SCHEMA = "zoo-bench/report-model/1"

LOAD_COMPOSITION = (
    "单位工作 = JSON 序列化 + 反序列化 + 字符串拼接，重复若干次以达到目标耗时；"
    "档位由 matrix.yaml 的 body_tiers_us 声明"
)

LOAD_CAVEAT = (
    "**负载是替身，不是真实 trace**：形状接近“取一帧数据 -> 序列化 -> 解析 -> 字符串处理”"
    "这类业务动作，但仓库内没有真实样本，结论的适用性以此为前提"
)


def _successful(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [unit for unit in units if unit["status"] == "ok"]


def _unit_overhead_is_interpretable(unit: dict[str, Any], logical_cores: int | None) -> bool:
    """某个测量单元的开销数字是否可当作开销来读。"""
    return overhead_is_interpretable(
        concurrency=int(unit["spec"]["concurrency"]),
        logical_cores=logical_cores,
        overhead_seconds=float(unit["absolute"]["framework_overhead_seconds"]),
    )


def withheld_overhead_groups(
    units: list[dict[str, Any]], logical_cores: int | None
) -> list[str]:
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
        interpretable = all(
            _unit_overhead_is_interpretable(row, logical_cores) for row in rows
        )
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
            note = UNINTERPRETABLE_REASON
        elif polluted:
            note = (
                "该组并发度超出环境容量，端到端含排队等待，其开销数字不可当作框架开销，"
                "故不给交叉点（原值仍在场供人工判读）"
            )
        elif first_below is not None:
            note = "该档位是所测档位中最小的满足者；真实交叉点落在它与前一档之间"
        else:
            note = "所测档位内没有一档的开销占比降到阈值以下"

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


def _summary(
    crossings: list[dict[str, Any]],
    turnings: list[dict[str, Any]],
    subject: str | None,
    favorable: dict[str, Any],
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
        shortest = min(crossing["first_tier_at_or_below_threshold_us"] for crossing in subject_crossings)
        lines.append(
            f"被测框架 {subject} 的执行体时长达到约 {shortest:g} 微秒 及以上时，其框架开销占端到端的"
            f"比例降到 {OVERHEAD_THRESHOLD:.0%} 以下；短于此档位，选用它的主要代价就是框架开销本身。"
        )
    elif subject_readable:
        tiers = sorted({tier for crossing in subject_readable for tier in crossing["tiers_us"]})
        lines.append(
            f"在所测档位范围内，被测框架 {subject} 没有任何一档的框架开销占比降到 "
            f"{OVERHEAD_THRESHOLD:.0%} 以下；所测档位为 {tiers}。"
        )
    else:
        lines.append(
            f"被测框架 {subject} 的开销数字全部落在超出该机器并行能力的并发度上，"
            "故本报告不给它的开销交叉点——端到端与吞吐仍然有效，见口径章节。"
        )

    # 正面那一半也要有一句：只讲"对照方案从多大档位起不再更快"是个负向表述，读者得自己反推
    # 优势。两个方向对称，句子结构与上面那几句一致（数字来自同一份比值，不含评价词）。
    items = favorable.get("items") or []
    if items:
        best = max(items, key=lambda item: float(item["baseline_ratio_vs_subject"]))
        lines.append(
            f"并发度 {best['concurrency']}、执行体 {best['body_tier_us']:g} 微秒 下，"
            f"被测框架比 {best['baseline']} 快 {float(best['baseline_ratio_vs_subject']):.2f}x"
            "（该并发度所测档位中最快的一档）。"
        )

    for turning in turnings:
        if turning["first_tier_baseline_not_faster_us"] is not None:
            lines.append(
                f"并发度 {turning['concurrency']} 下，执行体时长超过约 "
                f"{turning['first_tier_baseline_not_faster_us']:g} 微秒 后，{turning['baseline']} "
                f"不再快于被测框架。"
            )
        else:
            lines.append(
                f"并发度 {turning['concurrency']} 下，所测档位内 {turning['baseline']} "
                f"**始终快于**被测框架——这类档位正是本报告的公开不利数据。"
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
        float(item["wall_seconds"]) / float(item["batch"])
        for item in rounds
        if item.get("batch")
    ]
    if len(samples) < MIN_ROUNDS_FOR_BAND:
        return None
    return float(summarize(samples)["relative_spread"])


def _classify_relative(
    result: dict[str, Any], subject: str | None, spreads: dict[tuple[str, int, float], float | None]
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
                        "reason": "缺少逐轮样本（或样本太少），无法判定这点差异是否可分辨",
                    }
                )
            elif value > 1.0 + band:
                favorable.append(
                    {**common, "margin": f"被测框架比 {baseline} 快 {value:.2f}x"}
                )
            elif value < 1.0 - band:
                unfavorable.append(
                    {
                        **common,
                        "gap": f"{baseline} 比 {subject} 快 {1 / value:.2f}x",
                    }
                )
            else:
                tied.append(
                    {
                        **common,
                        "reason": f"两侧差异 {abs(value - 1.0):.1%} 小于带宽 {band:.1%}，分不出胜负",
                    }
                )
    return {"favorable": favorable, "unfavorable": unfavorable, "tied": tied}


def _relative_classification(
    result: dict[str, Any], units: list[dict[str, Any]], subject: str | None
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
    classes = _classify_relative(result, subject, spreads)
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
            "note": (
                "被测框架处于优势的档位（两侧差异大于该档位的带宽）。它与「公开的不利数据」出自"
                "**同一份同运行内的相对比**，只是一个取更快的、一个取更慢的——两者的覆盖面相同，"
                "故谁都挑不了档位。**本节的缺席不构成不合格**：分不出胜负或处处更慢时它就应当是空的"
            ),
        },
        "unfavorable": {
            "items": classes["unfavorable"],
            "found": bool(classes["unfavorable"]),
            "note": (
                "被测框架处于劣势的档位（两侧差异大于该档位的带宽）。**这部分缺失的报告不合格**"
                "——一份只展示自己赢的报告，读者有理由认为它不可信。差异小于带宽的档位不在本节，"
                "而在「分不出胜负」那一节：那是判据的结果，不是把它们藏起来"
            ),
        },
        "tied": {
            "items": classes["tied"],
            "found": bool(classes["tied"]),
            "note": (
                "两侧**分不出胜负**的档位：差异小于该档位的带宽。带宽取自**同一次运行**内两侧各自的"
                "跨轮离散度（每任务中位数的标准差 / 中位数）之和，故它随样本变化，读者可用留档里的"
                "逐轮样本自行复核。它同样覆盖全部所测档位、逐项写明原因——**本节不是藏东西的地方**："
                "把噪声级差异报成「快 1.00x」才是"
            ),
            "band_min": min(bands) if bands else None,
            "band_max": max(bands) if bands else None,
        },
        "band_definition": (
            "带宽 = 被测框架侧与对照侧各自的跨轮相对离散度之和；"
            "单侧的跨轮相对离散度 = 该单元逐轮的「每任务中位数」的标准差 / 中位数"
        ),
    }


def _caveats(
    units: list[dict[str, Any]],
    verification: dict[str, Any],
    self_check: dict[str, Any],
    logical_cores: int | None,
) -> list[dict[str, Any]]:
    """口径局限与偏差来源，逐条列明。"""
    caveats: list[dict[str, Any]] = []

    drive_levels = {
        unit["adapter"]["drive_level"]
        for unit in _successful(units)
        if unit["adapter"].get("drive_level")
    }
    caveats.extend({"kind": "被测层级", "text": level} for level in sorted(drive_levels))

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
                "kind": "不可直接对标"
                if unit["adapter"].get("comparable") is False
                else "口径说明",
                "adapter": adapter,
                "text": notes,
            }
        )

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
    contaminated = queueing_contaminated_groups(self_check)
    if contaminated:
        caveats.append(
            {
                "kind": "受排队污染的开销数字",
                "count": len(contaminated),
                "text": "以下 (适配器, 并发度) 组的并发度超出环境容量，端到端里含排队等待，"
                "故其**框架开销与该组的交叉点不可当作框架开销来读**——原始值仍在报告里，"
                "只是不参与结论。实测这类组的跨档位开销差异可达数十倍，全部来自排队",
                "groups": [
                    f"{group['adapter']}/{group['concurrency']}" for group in contaminated
                ],
            }
        )

    withheld = withheld_overhead_groups(units, logical_cores)
    if withheld:
        caveats.append(
            {
                "kind": "不给出开销数字的组",
                "count": len(withheld),
                "text": "以下 (适配器, 并发度) 组的并发度超过这次运行所在机器的并行能力："
                "执行体自报的耗时里含超订带来的调度等待，与「墙钟 / 并发度」不是同一件事，"
                "相减的结果没有意义（实测出现过负值，并衍生出无意义的变化倍数）。"
                "故本报告对它们**不给框架开销、也不给交叉点**；端到端、吞吐与执行体自报值仍然有效，"
                "原始留档里的字段也一个没删。判据取自环境自述的逻辑核数，另有一条证据性兜底："
                "算出来的开销为负同样判为不可读——框架只会加时间不会减时间",
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
                "kind": "未参与判定的自检项",
                "count": len(ungated),
                "text": "这些单元的执行体档位偏差未参与判定（并发度高于最低档，或档位短到墙钟"
                "受调度颗粒度支配）。原始值仍在场，但那两处的墙钟不足以断定校准是否正确——"
                "**读这些档位的数字时要把它算进去**",
                "tiers_us": tiers,
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
        "note": "端到端 / 并发度；分位数与相对离散度同表给出，只给中位数的度量不合格",
    }


def _overhead_dimension(units: list[dict[str, Any]], logical_cores: int | None) -> dict[str, Any]:
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
                "note": "" if interpretable else UNINTERPRETABLE_REASON,
            }
        )
    return {
        "title": "框架自身开销占比",
        "unit": "秒",
        "rows": rows,
        "note": "开销 = 端到端/并发度 - 同一次运行内实测的执行体耗时（不做跨运行减法）",
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
        "note": "吞吐 = 并发度 / 端到端中位数；随并发度是否继续上升即为伸缩性",
    }


#: 四段的显示名。键与顺序取自探针（:data:`zoo_bench.attribution.SEGMENT_KEYS`），这里只放标签
#: ——三个后端共用同一套，各写一份必然漂移。
ATTRIBUTION_SEGMENT_LABELS: dict[str, str] = {
    "submit_side_seconds": "提交侧",
    "handoff_seconds": "手交",
    "body_seconds": "执行体",
    "return_seconds": "回程",
}

#: 被测框架提交侧的细分桶与显示名。
ATTRIBUTION_DRILL_LABELS: dict[str, str] = {
    "scheduling_round_seconds": "调度轮其余（判定、锁与调度列表维护）",
    "dispatch_seconds": "派发（交给调度模型，含池的入队）",
    "policy_lookup_seconds": "每轮策略查询（周期/相位/超时各查一次配置）",
    "submit_side_other_seconds": "提交侧其他（适配器自己的记账与完成信号状态）",
}


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


def _attribution_summary(findings: list[dict[str, Any]]) -> list[str]:
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
                    f"并发度 {finding['concurrency']}、执行体 {finding['tier_us']:g} 微秒 下，"
                    f"被测框架整体不慢于 {row['adapter']}（每任务 {excess * 1e6:+.0f} 微秒）："
                    "两边的时序结构不同，逐段差额有正有负，故不给「落在哪一段」。"
                )
                continue
            lines.append(
                f"并发度 {finding['concurrency']}、执行体 {finding['tier_us']:g} 微秒 下，"
                f"被测框架相对 {row['adapter']} 每任务多花 {excess * 1e6:.0f} 微秒，"
                f"其中主要落在「{ATTRIBUTION_SEGMENT_LABELS[dominant]}」"
                f"（{row['segment_excess_seconds'][dominant] * 1e6:+.0f} 微秒）。"
            )
            opposing = sorted(
                (
                    (key, value)
                    for key, value in row["segment_excess_seconds"].items()
                    if value < 0
                ),
                key=lambda item: item[1],
            )
            if opposing:
                key, value = opposing[0]
                lines.append(
                    f"　注意：该组逐段差额并非同号——「{ATTRIBUTION_SEGMENT_LABELS[key]}」"
                    f"为 {value * 1e6:+.0f} 微秒，读的时候要一并看。"
                )
        drill = finding["subject_drill_down"]
        if drill:
            parts = "、".join(
                f"{ATTRIBUTION_DRILL_LABELS[key]} {drill[key] * 1e6:.0f}"
                for key in ATTRIBUTION_DRILL_LABELS
                if key in drill
            )
            lines.append(
                f"被测框架在该组的提交侧由以下部分构成（微秒）：{parts}。"
            )
    return lines


def _not_probed_attribution() -> dict[str, Any]:
    """归因在**连探查都没跑**时的占位。

    与"跑了但某组不可测"不同：那是结论，这是遗漏。报告里必须能看出是哪一种。
    """
    return {
        "title": "开销归因",
        "status": "not_probed",
        "scope_note": (
            "把每任务端到端拆成提交侧 / 手交 / 执行体 / 回程四段，并在同一档位与并发度下"
            "**与各对照方案逐段对照**，回答「超出对照的部分落在哪一段」。它**不与对照方案比总开销**"
            "——总开销已经有了；这一维只回答「下一步该看哪里」"
        ),
        "segment_labels": ATTRIBUTION_SEGMENT_LABELS,
        "drill_labels": ATTRIBUTION_DRILL_LABELS,
        "groups": [],
        "findings": [],
        "summary": [],
        "reason": "本轮运行未包含开销归因探查（以 with_attribution=False 运行）",
    }


def _attribution_dimension(result: dict[str, Any]) -> dict[str, Any]:
    """开销归因维度：四段、细分与逐段结论。"""
    attribution = result.get("attribution")
    if not attribution:
        return _not_probed_attribution()

    groups = list(attribution.get("groups") or [])
    findings = _attribution_findings(groups)
    measured = [group for group in groups if group.get("status") == "ok"]
    return {
        "title": "开销归因",
        "status": "ok" if measured else "not_measured",
        "scope_note": (
            "把每任务端到端拆成提交侧 / 手交 / 执行体 / 回程四段，并在同一档位与并发度下"
            "**与各对照方案逐段对照**，回答「超出对照的部分落在哪一段」。它是**诊断**而非主证据："
            "抽样一小批档位，且不与对照方案比总开销。"
            "**手交为负不是错误**：那表示提交调用还没返回、执行体就已经开跑——"
            "把建线程一类工作算进提交侧的方案就会这样（实测 bare_thread 手交 -343 微秒）。"
            "**回程含完成信号的等待**（排空返回前的最后一段），它与主测量是同一口径"
        ),
        "segment_labels": ATTRIBUTION_SEGMENT_LABELS,
        "drill_labels": ATTRIBUTION_DRILL_LABELS,
        "cores": attribution.get("cores"),
        "tiers_us": attribution.get("tiers_us", []),
        "concurrencies": attribution.get("concurrencies", []),
        "note": attribution.get("note", ""),
        "groups": groups,
        "findings": findings,
        "summary": _attribution_summary(findings),
        "reason": "" if measured else "本轮归因的每一组都没能量成，见逐组原因",
    }


def _unmeasured_semantics() -> dict[str, Any]:
    """语义维度在**连探查都没跑**时的占位。

    与"探查跑了、结论是不可测"不同：那是结论，这是遗漏。两者的区分正是 ``reason`` 与
    ``items`` 的有无——报告里必须能看出是哪一种。
    """
    return {
        "title": "调度语义的代价",
        "status": "not_probed",
        "scope_note": (
            "本维度采用**内部开关对照**（同一 workload 下语义全关 vs 逐个开启），"
            "**不与对照方案横向比较**——裸写法没有优先级/超时/重试的对应物，"
            "故只能回答“本框架的语义值多少钱”，不能回答“比裸写法贵多少”"
        ),
        "items": [],
        "reason": "本轮运行未包含语义维度探查（以 with_semantics=False 运行）",
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
    resolved_environment = (
        environment if environment is not None else result.get("environment")
    )
    # 机器并行能力取自**同一次运行**的环境自述：它是"开销这个减式成立与否"的判据，
    # 跨运行借用另一台机器的核数就会判错（见 zoo_bench.caliber）。
    logical_cores = logical_cores_from_environment(resolved_environment)

    crossings = _overhead_crossings(units, contaminated, logical_cores)
    turnings = _relative_turnings(result, subject)
    classification = _relative_classification(result, units, subject)

    return {
        "schema": MODEL_SCHEMA,
        "source": source,
        "environment": resolved_environment,
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
            "summary": _summary(crossings, turnings, subject, classification["favorable"]),
            "note": "单点加速比没有选型含义；结论以“多大的执行体时长下选哪个方案”表述"
            "（同一份框架开销，在 40 微秒 的任务上占七成，在 10 ms 上只占百分之几）",
        },
        "dimensions": {
            "latency": _latency_dimension(units),
            "overhead": _overhead_dimension(units, logical_cores),
            "throughput": _throughput_dimension(units),
            "semantics": resolved_semantics
            if resolved_semantics is not None
            else _unmeasured_semantics(),
            "attribution": _attribution_dimension(result),
        },
        "favorable": classification["favorable"],
        "unfavorable": classification["unfavorable"],
        "tied": classification["tied"],
        "tie_band": {"definition": classification["band_definition"]},
        "caveats": _caveats(units, result.get("verification", {}), self_check, logical_cores),
        "absolute": {
            "note": result.get("run", {}).get("absolute_note"),
            "process_isolation": result.get("process_isolation", {}),
        },
        "self_check": self_check,
    }
