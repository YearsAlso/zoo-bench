"""Markdown 报告渲染。

**只消费报告模型**（design D11 的同源要求），不自己解析原始数据。章节顺序按读者的问题排：

「这是在哪测的」→「负载是什么」→「结论是什么」→「各维度的数」→「我在哪输了」→「口径局限」

顺序不是随意的：环境与负载的限定必须在结论之前给出，否则读者会先接受结论再看到适用条件；
而「我在哪输了」与「口径局限」必须独立成节、不得藏在表格脚注里（spec 的硬要求）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .charts import render_all


def format_seconds(value: float | None) -> str:
    """秒 → 便于阅读的单位。报告同时给数值与单位，省去读者换算。"""
    if value is None:
        return "—"
    if value >= 1.0:
        return f"{value:.3f} s"
    if value >= 1e-3:
        return f"{value:.3f} ms"
    return f"{value * 1e6:.2f} µs"


def format_ratio(value: float | None) -> str:
    """比值 → 百分比。"""
    return "—" if value is None else f"{value * 100:.2f}%"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """GitHub 风格的 Markdown 表格。行数为 0 时也给出表头。"""
    head = "| " + " | ".join(headers) + " |"
    divider = "|" + "|".join(["---"] * len(headers)) + "|"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([head, divider, *body])


def _environment_section(model: dict[str, Any]) -> list[str]:
    environment = model.get("environment")
    if environment is None:
        return ["## 运行环境", "", "**本报告缺少环境自述，不应被发布。**", ""]

    hardware = environment.get("hardware", {})
    operating_system = environment.get("os", {})
    python = environment.get("python", {})
    subject = environment.get("subject", {})
    harness = environment.get("harness", {})

    rows = [
        ["CPU 型号", hardware.get("cpu_model") or f"不可用（{hardware.get('cpu_model_source')}）"],
        ["逻辑核数", str(hardware.get("logical_cores"))],
        ["平台", str(hardware.get("platform"))],
        [
            "操作系统",
            f"{operating_system.get('system')} {operating_system.get('release')}"
            f"（{operating_system.get('version')}）",
        ],
        ["Python", f"{python.get('implementation')} {python.get('version')}"],
        ["解释器", str(python.get("executable"))],
        ["被测框架（发行元数据）", str(subject.get("dist_version"))],
        ["被测框架（模块 __version__）", f"{subject.get('module_version')}（仅附注，不作版本判据）"],
        ["harness", f"zoo-bench {harness.get('version')} @ {harness.get('commit')}"],
        ["复现命令", "`" + " ".join(environment.get("command", [])) + "`"],
    ]

    return ["## 运行环境", "", table(["项", "值"], rows), "", f"> {subject.get('note', '')}", ""]


def _load_section(model: dict[str, Any]) -> list[str]:
    load = model.get("load", {})
    return [
        "## 负载",
        "",
        f"- 构成：{load.get('composition')}",
        f"- {load.get('caveat')}",
        "",
    ]


def _conclusion_section(model: dict[str, Any]) -> list[str]:
    conclusion = model.get("conclusion", {})
    lines = ["## 结论摘要", ""]

    lines.extend(f"- {sentence}" for sentence in conclusion.get("summary", []))

    lines += ["", f"> {conclusion.get('note', '')}", ""]

    crossings = conclusion.get("overhead_crossings", [])
    if crossings:
        lines += [
            "### 开销阈值交叉点",
            "",
            table(
                ["方案", "并发度", "阈值", "首个不高于阈值的档位（微秒）", "所测档位（微秒）"],
                [
                    [
                        crossing["adapter"],
                        str(crossing["concurrency"]),
                        format_ratio(crossing["threshold"]),
                        "—" if crossing["first_tier_at_or_below_threshold_us"] is None
                        else f"{crossing['first_tier_at_or_below_threshold_us']:g}",
                        ", ".join(f"{tier:g}" for tier in crossing["tiers_us"]),
                    ]
                    for crossing in crossings
                ],
            ),
            "",
        ]

    turnings = conclusion.get("relative_turnings", [])
    if turnings:
        lines += [
            "### 相对转折点",
            "",
            table(
                ["对照方案", "并发度", "该方案不再更快的档位（微秒）", "说明"],
                [
                    [
                        turning["baseline"],
                        str(turning["concurrency"]),
                        "—" if turning["first_tier_baseline_not_faster_us"] is None
                        else f"{turning['first_tier_baseline_not_faster_us']:g}",
                        turning["note"],
                    ]
                    for turning in turnings
                ],
            ),
            "",
        ]
    return lines


def _charts_section(charts: list[dict[str, Any]], figures_rel: str) -> list[str]:
    if not charts:
        return []
    lines = ["## 图表", ""]
    for chart in charts:
        svg = chart["paths"].get("svg")
        if svg:
            name = Path(svg).name
            lines += [f"![{chart['figure']}]({figures_rel}/{name})", "", f"> {chart['scope']}", ""]
    return lines


def _dimension_sections(model: dict[str, Any]) -> list[str]:
    dimensions = model.get("dimensions", {})
    lines: list[str] = []

    latency = dimensions.get("latency", {})
    lines += [
        f"## 维度：{latency.get('title', '延迟')}",
        "",
        f"> {latency.get('note', '')}",
        "",
        table(
            ["方案", "并发度", "执行体档位（微秒）", "中位数", "p95", "p99", "相对离散度"],
            [
                [
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_seconds(row["end_to_end_per_task"]["median"]),
                    format_seconds(row["end_to_end_per_task"]["p95"]),
                    format_seconds(row["end_to_end_per_task"]["p99"]),
                    f"{float(row['end_to_end_per_task']['relative_spread']):.3f}",
                ]
                for row in latency.get("rows", [])
            ],
        ),
        "",
    ]

    overhead = dimensions.get("overhead", {})
    lines += [
        f"## 维度：{overhead.get('title', '框架开销')}",
        "",
        f"> {overhead.get('note', '')}",
        "",
        table(
            ["方案", "并发度", "执行体档位（微秒）", "框架开销", "开销占比", "执行体实测"],
            [
                [
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_seconds(row["framework_overhead_seconds"]),
                    format_ratio(row["framework_overhead_ratio"]),
                    format_seconds(row["body_seconds"]),
                ]
                for row in overhead.get("rows", [])
            ],
        ),
        "",
    ]

    throughput = dimensions.get("throughput", {})
    lines += [
        f"## 维度：{throughput.get('title', '吞吐')}",
        "",
        f"> {throughput.get('note', '')}",
        "",
        table(
            ["方案", "并发度", "执行体档位（微秒）", "吞吐（任务/秒）"],
            [
                [
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    f"{row['throughput_per_second']:.1f}",
                ]
                for row in throughput.get("rows", [])
            ],
        ),
        "",
    ]

    semantics = dimensions.get("semantics", {})
    lines += [
        f"## 维度：{semantics.get('title', '调度语义的代价')}",
        "",
        f"> 口径：{semantics.get('scope_note', '')}",
        "",
        f"**状态：{semantics.get('status')}**",
        "",
    ]
    if semantics.get("reason"):
        lines += [f"{semantics['reason']}", ""]
    if semantics.get("items"):
        lines += [
            table(
                ["语义项", "层级", "探查方式", "可测", "证据 / 原因"],
                [
                    [
                        item["item"],
                        item["layer"],
                        item["probe"],
                        "是" if item["usable"] else "否",
                        item.get("reason") or item.get("evidence", ""),
                    ]
                    for item in semantics["items"]
                ],
            ),
            "",
        ]
    if semantics.get("recheck_on_version_bump"):
        lines += [f"> {semantics['recheck_on_version_bump']}", ""]
    return lines


def _unfavorable_section(model: dict[str, Any]) -> list[str]:
    unfavorable = model.get("unfavorable", {})
    lines = ["## 公开的不利数据", "", f"> {unfavorable.get('note', '')}", ""]

    items = unfavorable.get("items", [])
    if not items:
        lines += ["所测档位内未出现被测框架处于劣势的情形。", ""]
        return lines

    lines += [
        table(
            ["对照方案", "并发度", "执行体档位（微秒）", "差距", "被测框架中位数"],
            [
                [
                    item["baseline"],
                    str(item["concurrency"]),
                    f"{item['body_tier_us']:g}",
                    item["gap"],
                    format_seconds(item["subject_median_seconds"]),
                ]
                for item in items
            ],
        ),
        "",
    ]
    return lines


def _caveat_line(caveat: dict[str, Any]) -> str:
    """一条口径局限。适配器名可选——不是每条局限都归属于某个方案。"""
    adapter = f"（{caveat['adapter']}）" if caveat.get("adapter") else ""
    return f"- **{caveat.get('kind', '')}**{adapter}：{caveat.get('text', '')}"


def _caveats_section(model: dict[str, Any]) -> list[str]:
    lines = ["## 口径局限与偏差来源", ""]
    caveats = model.get("caveats", [])
    if not caveats:
        lines += ["（无）", ""]
        return lines

    lines.extend(_caveat_line(caveat) for caveat in caveats)

    lines += ["", f"> 绝对耗时：{model.get('absolute', {}).get('note', '')}", ""]
    return lines


def render_markdown(
    model: dict[str, Any], charts: list[dict[str, Any]], *, figures_rel: str = "figures"
) -> str:
    """由报告模型渲染 Markdown。

    Args:
        model: :func:`zoo_bench.report.build_model` 的返回值。
        charts: :func:`zoo_bench.render.charts.render_all` 的返回值。
        figures_rel: 图表目录相对报告文件的路径。

    Returns:
        Markdown 文本。
    """
    lines: list[str] = ["# zoo-bench 性能报告", ""]

    source = model.get("source")
    if source:
        lines += [f"原始数据：`{source.get('path', '')}`", ""]

    lines += _environment_section(model)
    lines += _load_section(model)
    lines += _conclusion_section(model)
    lines += _charts_section(charts, figures_rel)
    lines += _dimension_sections(model)
    lines += _unfavorable_section(model)
    lines += _caveats_section(model)

    self_check = model.get("self_check", {})
    lines += [
        "## 自检",
        "",
        f"- 测量自检整体：{'通过' if self_check.get('ok') else '**未通过**'}",
        f"- 进程隔离：{model.get('absolute', {}).get('process_isolation', {})}",
        "",
    ]
    return "\n".join(lines)


def render(model: dict[str, Any], outdir: str | Path, *, threshold: float) -> dict[str, Any]:
    """出图表并写出 Markdown 报告。

    Args:
        model: 报告模型。
        outdir: 输出目录；图表写入其下的 ``figures/``。
        threshold: 开销阈值（传给图表）。

    Returns:
        ``{"report": 报告路径, "charts": [...], "font": 字体路径}``。
    """
    directory = Path(outdir)
    directory.mkdir(parents=True, exist_ok=True)

    charts = render_all(model, directory / "figures", threshold=threshold)
    text = render_markdown(model, charts, figures_rel="figures")

    report_path = directory / "report.md"
    report_path.write_text(text, encoding="utf-8")

    return {
        "report": str(report_path),
        "charts": charts,
        "font": charts[0]["font"] if charts else None,
    }
