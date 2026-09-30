"""报告的块结构：报告模型 → **与输出格式无关**的块列表。

为什么要有这一层：Markdown、HTML、PDF 三个输出后端需要同一份内容。若各自从模型里拼章节，
措辞与结构就会各写一份、必然漂移——而漂移的表现是"三个格式说的不是一回事"，这种不一致很难
被肉眼发现，读者只会以为其中一份是旧的。

所以"报告里有哪些块、每块说什么"只在这里出现一次；各后端只负责把块序列化成自己的语法。

章节顺序按读者的问题排：

「这是在哪测的」→「负载是什么」→「结论是什么」→「各维度的数」→「我在哪输了」→「口径局限」

顺序不是随意的：环境与负载的限定必须在结论**之前**给出，否则读者会先接受结论再看到适用条件；
而「我在哪输了」与「口径局限」必须独立成块、不得藏进表格脚注。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HEADING = "heading"
PARAGRAPH = "paragraph"
BULLETS = "bullets"
TABLE = "table"
IMAGE = "image"
NOTE = "note"


@dataclass(frozen=True)
class Block:
    """一个报告块。

    Attributes:
        kind: 块类型，取本模块的 ``HEADING`` / ``PARAGRAPH`` / ``BULLETS`` / ``TABLE`` /
            ``IMAGE`` / ``NOTE`` 之一。
        text: 段落文本、标题文本或图片替代文本。
        level: 标题层级（``HEADING`` 用）。
        items: 条目（``BULLETS`` 用）。
        headers: 表头（``TABLE`` 用）。
        rows: 表体（``TABLE`` 用）。
        src: 图路径（``IMAGE`` 用）。
    """

    kind: str
    text: str = ""
    level: int = 2
    items: tuple[str, ...] = field(default_factory=tuple)
    headers: tuple[str, ...] = field(default_factory=tuple)
    rows: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    src: str = ""


def format_seconds(value: float | None) -> str:
    """秒 → 便于阅读的单位。报告同时给数值与单位，省去读者换算。

    **每个分支都要把秒换算成该单位**。实测踩过这个坑：毫秒分支印的是原始秒数却标 "ms"，于是
    2.7 毫秒被印成 "0.003 ms"——**读起来像 3 微秒，差 1000 倍**。数字被低估三个数量级的报告，
    比没有报告更坏。
    """
    if value is None:
        return "—"
    if value >= 1.0:
        return f"{value:.3f} s"
    if value >= 1e-3:
        return f"{value * 1e3:.3f} ms"
    return f"{value * 1e6:.2f} µs"


def format_ratio(value: float | None) -> str:
    """比值 → 百分比。"""
    return "—" if value is None else f"{value * 100:.2f}%"


def _heading(text: str, level: int = 2) -> Block:
    return Block(HEADING, text=text, level=level)


def _environment_blocks(model: dict[str, Any]) -> list[Block]:
    environment = model.get("environment")
    if environment is None:
        return [
            _heading("运行环境"),
            Block(PARAGRAPH, text="**本报告缺少环境自述，不应被发布。**"),
        ]

    hardware = environment.get("hardware", {})
    operating_system = environment.get("os", {})
    python = environment.get("python", {})
    subject = environment.get("subject", {})
    harness = environment.get("harness", {})

    cpu = hardware.get("cpu_model") or f"不可用（{hardware.get('cpu_model_source')}）"
    rows = (
        ("CPU 型号", str(cpu)),
        ("逻辑核数", str(hardware.get("logical_cores"))),
        ("平台", str(hardware.get("platform"))),
        (
            "操作系统",
            f"{operating_system.get('system')} {operating_system.get('release')}"
            f"（{operating_system.get('version')}）",
        ),
        ("Python", f"{python.get('implementation')} {python.get('version')}"),
        ("解释器", str(python.get("executable"))),
        ("被测框架（发行元数据）", str(subject.get("dist_version"))),
        ("被测框架（模块 __version__）", f"{subject.get('module_version')}（仅附注，不作版本判据）"),
        ("harness", f"zoo-bench {harness.get('version')} @ {harness.get('commit')}"),
        ("复现命令", "`" + " ".join(environment.get("command", [])) + "`"),
    )

    blocks = [_heading("运行环境"), Block(TABLE, headers=("项", "值"), rows=rows)]
    if subject.get("note"):
        blocks.append(Block(NOTE, text=str(subject["note"])))
    return blocks


def _load_blocks(model: dict[str, Any]) -> list[Block]:
    load = model.get("load", {})
    return [
        _heading("负载"),
        Block(
            BULLETS,
            items=(
                f"构成：{load.get('composition')}",
                str(load.get("caveat", "")),
            ),
        ),
    ]


def _conclusion_blocks(model: dict[str, Any]) -> list[Block]:
    conclusion = model.get("conclusion", {})
    blocks = [
        _heading("结论摘要"),
        Block(BULLETS, items=tuple(conclusion.get("summary", []))),
    ]
    if conclusion.get("note"):
        blocks.append(Block(NOTE, text=str(conclusion["note"])))

    crossings = conclusion.get("overhead_crossings", [])
    if crossings:
        blocks += [
            _heading("开销阈值交叉点", level=3),
            Block(
                TABLE,
                headers=("方案", "并发度", "阈值", "首个不高于阈值的档位（微秒）", "所测档位（微秒）"),
                rows=tuple(
                    (
                        crossing["adapter"],
                        str(crossing["concurrency"]),
                        format_ratio(crossing["threshold"]),
                        "—"
                        if crossing["first_tier_at_or_below_threshold_us"] is None
                        else f"{crossing['first_tier_at_or_below_threshold_us']:g}",
                        ", ".join(f"{tier:g}" for tier in crossing["tiers_us"]),
                    )
                    for crossing in crossings
                ),
            ),
        ]

    turnings = conclusion.get("relative_turnings", [])
    if turnings:
        blocks += [
            _heading("相对转折点", level=3),
            Block(
                TABLE,
                headers=("对照方案", "并发度", "该方案不再更快的档位（微秒）", "说明"),
                rows=tuple(
                    (
                        turning["baseline"],
                        str(turning["concurrency"]),
                        "—"
                        if turning["first_tier_baseline_not_faster_us"] is None
                        else f"{turning['first_tier_baseline_not_faster_us']:g}",
                        turning["note"],
                    )
                    for turning in turnings
                ),
            ),
        ]
    return blocks


def _chart_blocks(charts: list[dict[str, Any]], figures_rel: str) -> list[Block]:
    if not charts:
        return []

    blocks: list[Block] = [_heading("图表")]
    for chart in charts:
        svg = chart["paths"].get("svg")
        if not svg:
            continue
        blocks.append(Block(IMAGE, text=chart["figure"], src=f"{figures_rel}/{Path(svg).name}"))
        if chart.get("scope"):
            blocks.append(Block(NOTE, text=str(chart["scope"])))
    return blocks


def _dimension_blocks(model: dict[str, Any]) -> list[Block]:
    dimensions = model.get("dimensions", {})
    blocks: list[Block] = []

    latency = dimensions.get("latency", {})
    blocks += [
        _heading(f"维度：{latency.get('title', '延迟')}"),
        Block(NOTE, text=str(latency.get("note", ""))),
        Block(
            TABLE,
            headers=("方案", "并发度", "执行体档位（微秒）", "中位数", "p95", "p99", "相对离散度"),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_seconds(row["end_to_end_per_task"]["median"]),
                    format_seconds(row["end_to_end_per_task"]["p95"]),
                    format_seconds(row["end_to_end_per_task"]["p99"]),
                    f"{float(row['end_to_end_per_task']['relative_spread']):.3f}",
                )
                for row in latency.get("rows", [])
            ),
        ),
    ]

    overhead = dimensions.get("overhead", {})
    blocks += [
        _heading(f"维度：{overhead.get('title', '框架开销')}"),
        Block(NOTE, text=str(overhead.get("note", ""))),
        Block(
            TABLE,
            headers=("方案", "并发度", "执行体档位（微秒）", "框架开销", "开销占比", "执行体实测"),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_seconds(row["framework_overhead_seconds"]),
                    format_ratio(row["framework_overhead_ratio"]),
                    format_seconds(row["body_seconds"]),
                )
                for row in overhead.get("rows", [])
            ),
        ),
    ]

    throughput = dimensions.get("throughput", {})
    blocks += [
        _heading(f"维度：{throughput.get('title', '吞吐')}"),
        Block(NOTE, text=str(throughput.get("note", ""))),
        Block(
            TABLE,
            headers=("方案", "并发度", "执行体档位（微秒）", "吞吐（任务/秒）"),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    f"{row['throughput_per_second']:.1f}",
                )
                for row in throughput.get("rows", [])
            ),
        ),
    ]

    semantics = dimensions.get("semantics", {})
    blocks += [
        _heading(f"维度：{semantics.get('title', '调度语义的代价')}"),
        Block(NOTE, text=f"口径：{semantics.get('scope_note', '')}"),
        Block(PARAGRAPH, text=f"**状态：{semantics.get('status')}**"),
    ]
    if semantics.get("reason"):
        blocks.append(Block(PARAGRAPH, text=str(semantics["reason"])))
    if semantics.get("items"):
        blocks.append(
            Block(
                TABLE,
                headers=("语义项", "层级", "探查方式", "可测", "证据 / 原因"),
                rows=tuple(
                    (
                        item["item"],
                        item["layer"],
                        item["probe"],
                        "是" if item["usable"] else "否",
                        item.get("reason") or item.get("evidence", ""),
                    )
                    for item in semantics["items"]
                ),
            )
        )
    if semantics.get("recheck_on_version_bump"):
        blocks.append(Block(NOTE, text=str(semantics["recheck_on_version_bump"])))
    return blocks


def _unfavorable_blocks(model: dict[str, Any]) -> list[Block]:
    unfavorable = model.get("unfavorable", {})
    blocks = [
        _heading("公开的不利数据"),
        Block(NOTE, text=str(unfavorable.get("note", ""))),
    ]

    items = unfavorable.get("items", [])
    if not items:
        blocks.append(Block(PARAGRAPH, text="所测档位内未出现被测框架处于劣势的情形。"))
        return blocks

    blocks.append(
        Block(
            TABLE,
            headers=("对照方案", "并发度", "执行体档位（微秒）", "差距", "被测框架中位数"),
            rows=tuple(
                (
                    item["baseline"],
                    str(item["concurrency"]),
                    f"{item['body_tier_us']:g}",
                    item["gap"],
                    format_seconds(item["subject_median_seconds"]),
                )
                for item in items
            ),
        )
    )
    return blocks


def caveat_text(caveat: dict[str, Any]) -> str:
    """一条口径局限的文字。适配器名可选——不是每条局限都归属于某个方案。"""
    adapter = f"（{caveat['adapter']}）" if caveat.get("adapter") else ""
    return f"**{caveat.get('kind', '')}**{adapter}：{caveat.get('text', '')}"


def _caveat_blocks(model: dict[str, Any]) -> list[Block]:
    caveats = model.get("caveats", [])
    items = tuple(caveat_text(caveat) for caveat in caveats)
    blocks = [_heading("口径局限与偏差来源")]
    blocks.append(Block(BULLETS, items=items or ("（无）",)))
    blocks.append(Block(NOTE, text=f"绝对耗时：{model.get('absolute', {}).get('note', '')}"))
    return blocks


def _self_check_blocks(model: dict[str, Any]) -> list[Block]:
    self_check = model.get("self_check", {})
    return [
        _heading("自检"),
        Block(
            BULLETS,
            items=(
                f"测量自检整体：{'通过' if self_check.get('ok') else '**未通过**'}",
                f"进程隔离：{model.get('absolute', {}).get('process_isolation', {})}",
            ),
        ),
    ]


def _header_blocks(model: dict[str, Any]) -> list[Block]:
    """标题与原始数据出处。

    放在块层而不是各序列化器里——三个后端都得有同一个标题与同一句出处，各写一遍就是三处漂移点。
    """
    blocks = [Block(HEADING, text="zoo-bench 性能报告", level=1)]
    source = model.get("source")
    if source and source.get("path"):
        blocks.append(Block(PARAGRAPH, text=f"原始数据：`{source['path']}`"))
    return blocks


def build_blocks(
    model: dict[str, Any], charts: list[dict[str, Any]], *, figures_rel: str = "figures"
) -> list[Block]:
    """由报告模型抽出与输出格式无关的块列表。

    Args:
        model: :func:`zoo_bench.report.build_model` 的返回值。
        charts: :func:`zoo_bench.render.charts.render_all` 的返回值。
        figures_rel: 图表目录相对报告文件的路径。

    Returns:
        块列表。
    """
    blocks: list[Block] = []
    for builder in (
        _header_blocks,
        _environment_blocks,
        _load_blocks,
        _conclusion_blocks,
    ):
        blocks += builder(model)
    blocks += _chart_blocks(charts, figures_rel)
    blocks += _dimension_blocks(model)
    blocks += _unfavorable_blocks(model)
    blocks += _caveat_blocks(model)
    blocks += _self_check_blocks(model)
    return blocks
