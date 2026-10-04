"""跨版本对比的渲染：对比结果 -> 块列表 -> Markdown / HTML。

与报告走同一套块结构（design D11 的同源要求），故对比文档以后同样能出 PDF，不必再写一套。
"""

from __future__ import annotations

from typing import Any

from .blocks import BULLETS, HEADING, NOTE, PARAGRAPH, TABLE, Block, format_ratio


def _side_line(side: dict[str, Any]) -> str:
    mark = "自检通过" if side["self_check_ok"] else "**自检未通过——这一侧的数字不可信**"
    return (
        f"{side['label']}：框架 {side['framework']}，有效单元 {side['unit_count']} 个，{mark}"
    )


def _ratio_cell(change: dict[str, Any]) -> str:
    if change.get("ratio") is None:
        return change.get("direction", "—")
    return f"{change['direction']} {change['ratio']:.3f}x"


def build_blocks(comparison: dict[str, Any]) -> list[Block]:
    """把对比结果抽成块列表。

    Args:
        comparison: :func:`zoo_bench.compare.compare_versions` 的返回值。

    Returns:
        块列表。
    """
    before = comparison["before"]
    after = comparison["after"]

    blocks = [
        Block(HEADING, text=f"zoo-bench 版本对比：{before['label']} -> {after['label']}", level=1),
        Block(BULLETS, items=(_side_line(before), _side_line(after))),
    ]

    if not (before["self_check_ok"] and after["self_check_ok"]):
        blocks.append(
            Block(
                PARAGRAPH,
                text="**有一侧的自检未通过，本对比不作数**：自检覆盖执行体档位偏差、跨档位开销"
                "一致性与适配器等价性，它失败意味着那一侧的数字本身不可信。",
            )
        )

    environment = comparison["environment"]
    generation = comparison["drive_generation"]
    blocks.append(Block(HEADING, text="运行环境与读数口径"))
    blocks.append(Block(NOTE, text=environment["note"]))
    blocks.append(Block(NOTE, text=generation["note"]))
    if environment["differences"]:
        blocks.append(
            Block(
                TABLE,
                headers=("项", before["label"], after["label"]),
                rows=tuple(
                    (key, str(value["before"]), str(value["after"]))
                    for key, value in sorted(environment["differences"].items())
                ),
            )
        )

    blocks.append(Block(HEADING, text="只在一侧出现的单元"))
    if comparison["only_in_one"]:
        blocks.append(
            Block(
                PARAGRAPH,
                text="这些单元不是两侧都有，差异无法归因——**列出而不是取交集悄悄丢掉**。",
            )
        )
        blocks.append(
            Block(
                TABLE,
                headers=("适配器", "并发度", "执行体档位（微秒）", "出现在"),
                rows=tuple(
                    (
                        item["adapter"],
                        str(item["concurrency"]),
                        f"{item['body_tier_us']:g}",
                        item["present_in"],
                    )
                    for item in comparison["only_in_one"]
                ),
            )
        )
    else:
        blocks.append(Block(PARAGRAPH, text="两侧的单元完全一致。"))

    blocks.append(Block(HEADING, text="框架开销占比的变化"))
    blocks.append(Block(NOTE, text=comparison["metric_semantics"]["overhead_ratio"]))
    blocks.append(
        Block(
            TABLE,
            headers=("适配器", "并发度", "档位（微秒）", before["label"], after["label"], "变化"),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_ratio(row["before"]),
                    format_ratio(row["after"]),
                    _ratio_cell(row),
                )
                for row in comparison["overhead_ratio"]
            ),
        )
    )

    blocks.append(Block(HEADING, text="相对各对照方案的倍数的变化"))
    blocks.append(Block(NOTE, text=comparison["metric_semantics"]["speedup_vs_baseline"]))
    blocks.append(
        Block(
            TABLE,
            headers=("对照方案", "并发度", "档位（微秒）", before["label"], after["label"], "变化"),
            rows=tuple(
                (
                    row["baseline"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    "—" if row["before"] is None else f"{row['before']:.3f}x",
                    "—" if row["after"] is None else f"{row['after']:.3f}x",
                    _ratio_cell(row),
                )
                for row in comparison["speedup_vs_baseline"]
            ),
        )
    )

    blocks.append(Block(HEADING, text="绝对耗时（不可跨运行比较）"))
    blocks.append(Block(NOTE, text=comparison["absolute_note"]))
    blocks.append(
        Block(
            TABLE,
            headers=("适配器", "并发度", "档位（微秒）", before["label"], after["label"]),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    f"{row['before_median_seconds'] * 1e6:.2f} 微秒",
                    f"{row['after_median_seconds'] * 1e6:.2f} 微秒",
                )
                for row in comparison["absolute_seconds"]
            ),
        )
    )
    return blocks


def render_markdown(comparison: dict[str, Any]) -> str:
    """把对比结果渲染成 Markdown。"""
    from .markdown import blocks_to_markdown

    return blocks_to_markdown(build_blocks(comparison))


def render_html(comparison: dict[str, Any], *, title: str | None = None) -> str:
    """把对比结果渲染成 HTML。"""
    from .html import blocks_to_html

    heading = title or f"zoo-bench 版本对比：{comparison['before']['label']} -> {comparison['after']['label']}"
    return blocks_to_html(build_blocks(comparison), title=heading)
