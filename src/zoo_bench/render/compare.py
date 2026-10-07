"""跨版本对比的渲染：对比结果 -> 块列表 -> Markdown / HTML。

与报告走同一套块结构（design D11 的同源要求），故对比文档以后同样能出 PDF，不必再写一套。

**文案全部键化**：对比结果里的散文（方向、口径注）已由计算层按 ``lang`` 落值，这里的固定
文案也一律走 :mod:`zoo_bench.i18n`——英文对比页不留中文原文（spec 场景「英文产物不留中文原文」）。
"""

from __future__ import annotations

from typing import Any

from ..i18n import t
from .blocks import (
    BULLETS,
    HEADING,
    NOTE,
    PARAGRAPH,
    TABLE,
    Block,
    format_ratio,
)


def _side_line(side: dict[str, Any], lang: str) -> str:
    mark = (
        t("compare.side.passed", lang) if side["self_check_ok"] else t("compare.side.failed", lang)
    )
    return t(
        "compare.side.line",
        lang,
        label=side["label"],
        framework=side["framework"],
        unit_count=side["unit_count"],
        mark=mark,
    )


def _ratio_cell(change: dict[str, Any]) -> str:
    if change.get("ratio") is None:
        return change.get("direction", "—")
    return f"{change['direction']} {change['ratio']:.3f}x"


def build_blocks(comparison: dict[str, Any]) -> list[Block]:
    """把对比结果抽成块列表。

    Args:
        comparison: :func:`zoo_bench.compare.compare_versions` 的返回值；其 ``lang`` 决定文案语言。

    Returns:
        块列表。
    """
    lang = comparison["lang"]
    before = comparison["before"]
    after = comparison["after"]

    blocks = [
        Block(
            HEADING,
            text=t("compare.heading", lang, before=before["label"], after=after["label"]),
            level=1,
        ),
        Block(BULLETS, items=(_side_line(before, lang), _side_line(after, lang))),
    ]

    if not (before["self_check_ok"] and after["self_check_ok"]):
        blocks.append(Block(PARAGRAPH, text=t("compare.self_check_failed.note", lang)))

    environment = comparison["environment"]
    generation = comparison["drive_generation"]
    blocks.append(Block(HEADING, text=t("compare.section.environment", lang)))
    blocks.append(Block(NOTE, text=environment["note"]))
    blocks.append(Block(NOTE, text=generation["note"]))
    if environment["differences"]:
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("compare.header.item", lang),
                    before["label"],
                    after["label"],
                ),
                rows=tuple(
                    (key, str(value["before"]), str(value["after"]))
                    for key, value in sorted(environment["differences"].items())
                ),
            )
        )

    blocks.append(Block(HEADING, text=t("compare.section.only_in_one", lang)))
    if comparison["only_in_one"]:
        blocks.append(Block(PARAGRAPH, text=t("compare.only_in_one.note", lang)))
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("compare.header.adapter", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.body_tier", lang),
                    t("compare.header.present_in", lang),
                ),
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
        blocks.append(Block(PARAGRAPH, text=t("compare.only_in_one.none", lang)))

    blocks.append(Block(HEADING, text=t("compare.section.overhead_ratio", lang)))
    blocks.append(Block(NOTE, text=comparison["metric_semantics"]["overhead_ratio"]))
    withheld = [row for row in comparison["overhead_ratio"] if not row.get("interpretable", True)]
    if withheld:
        # 表里出现"不可读"时必须当场说明：读者跨到别处才知道，等于让他猜
        blocks.append(
            Block(
                NOTE,
                text=t("compare.withheld.note", lang, note=withheld[0].get("note") or ""),
            )
        )
    blocks.append(
        Block(
            TABLE,
            headers=(
                t("compare.header.adapter", lang),
                t("blocks.header.concurrency", lang),
                t("blocks.header.tier", lang),
                before["label"],
                after["label"],
                t("compare.header.change", lang),
            ),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_ratio(row["before"], lang),
                    format_ratio(row["after"], lang),
                    _ratio_cell(row),
                )
                for row in comparison["overhead_ratio"]
            ),
        )
    )

    blocks.append(Block(HEADING, text=t("compare.section.speedup", lang)))
    blocks.append(Block(NOTE, text=comparison["metric_semantics"]["speedup_vs_baseline"]))
    blocks.append(
        Block(
            TABLE,
            headers=(
                t("blocks.header.baseline", lang),
                t("blocks.header.concurrency", lang),
                t("blocks.header.tier", lang),
                before["label"],
                after["label"],
                t("compare.header.change", lang),
            ),
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

    blocks.append(Block(HEADING, text=t("compare.section.absolute", lang)))
    blocks.append(Block(NOTE, text=comparison["absolute_note"]))
    blocks.append(
        Block(
            TABLE,
            headers=(
                t("compare.header.adapter", lang),
                t("blocks.header.concurrency", lang),
                t("blocks.header.tier", lang),
                before["label"],
                after["label"],
            ),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    t(
                        "compare.cell.microseconds",
                        lang,
                        value=f"{row['before_median_seconds'] * 1e6:.2f}",
                    ),
                    t(
                        "compare.cell.microseconds",
                        lang,
                        value=f"{row['after_median_seconds'] * 1e6:.2f}",
                    ),
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
    """把对比结果渲染成 HTML。

    输出目录自成一棵树（英文页在根、中文页在 ``zh/``），样式表与语言切换链接都由
    :mod:`.links` 算——``compare --out`` 单独跑时页面也要能自己站住（样式表与它同树，
    见 ``cli.compare_command`` 的 ``write_style``）。
    """
    from . import links
    from .html import blocks_to_html

    lang = comparison["lang"]
    heading = title or t(
        "compare.heading",
        lang,
        before=comparison["before"]["label"],
        after=comparison["after"]["label"],
    )
    return blocks_to_html(
        build_blocks(comparison),
        title=heading,
        lang=lang,
        stylesheet=links.stylesheet_href(lang),
        language_switch=links.language_switch_href(lang),
    )
