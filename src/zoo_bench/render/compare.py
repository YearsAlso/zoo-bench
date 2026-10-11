"""跨版本对比的渲染：对比结果 -> 块列表 -> Markdown / HTML。

与报告走同一套块结构（design D11 的同源要求），故对比文档以后同样能出 PDF，不必再写一套。

**文案全部键化**：对比结果里的散文（方向、口径注）已由计算层按 ``lang`` 落值，这里的固定
文案也一律走 :mod:`zoo_bench.i18n`——英文对比页不留中文原文（spec 场景「英文产物不留中文原文」）。
"""

from __future__ import annotations

from typing import Any

from ..compare import VERDICT_SUBSTANTIVE, VERDICT_WITHIN_BAND
from ..i18n import t
from .blocks import (
    BULLETS,
    HEADING,
    NOTE,
    PARAGRAPH,
    TABLE,
    Block,
    format_ratio,
    list_join,
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


def _fold(
    rows: list[dict[str, Any]], *, lang: str, key_field: str
) -> tuple[list[dict[str, Any]], list[Block]]:
    """把没有判读意义的行折成说明，只留下有实质变化的行。

    三类被折掉的行，成因不同、**必须分开说**：

    - **不可读**（任一侧开销数字被撤下）：逐行铺开时读者从中读不出任何版本差异——那 56 行
      说的是同一件事（该并发度超出机器并行能力）。
    - **落在带宽内**：变化量与本次运行的噪声同量级，分不出是版本差异还是抖动。
    - **不判定**（样本不足以算出带宽）：编一个带宽等于让一个无法复核的数参与结论。

    ``key_field`` 是首列那一列的字段名：两张变化表的"谁"不同（开销表按方案、倍数表按对照方案），
    点名折叠掉的那几组时要按各自的列取。
    """
    listed: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    within_band: list[dict[str, Any]] = []
    no_verdict: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("interpretable", True):
            unreadable.append(row)
        elif row.get("verdict") == VERDICT_SUBSTANTIVE:
            listed.append(row)
        elif row.get("verdict") == VERDICT_WITHIN_BAND:
            within_band.append(row)
        else:
            no_verdict.append(row)

    notes: list[Block] = []
    if unreadable:
        groups = _group_names(unreadable, key_field)
        notes.append(
            Block(
                NOTE,
                text=t(
                    "compare.withheld.note",
                    lang,
                    count=len(unreadable),
                    groups=list_join(groups, lang),
                    note=unreadable[0].get("note") or "",
                ),
            )
        )
    if within_band:
        notes.append(
            Block(
                NOTE,
                text=t("compare.folded.band", lang, count=len(within_band)),
                # 判据与报告同源：带宽 = 两侧跨轮相对离散度之和
            )
        )
    if no_verdict:
        notes.append(
            Block(NOTE, text=t("compare.folded.no_verdict", lang, count=len(no_verdict)))
        )
    if not listed:
        notes.append(Block(PARAGRAPH, text=t("compare.folded.none", lang)))
    return listed, notes


def _group_names(rows: list[dict[str, Any]], key_field: str) -> list[str]:
    return sorted({f"{row[key_field]}/{row['concurrency']}" for row in rows})


def _change_table(
    rows: list[dict[str, Any]],
    *,
    first_header: str,
    first_key: str,
    before_label: str,
    after_label: str,
    value_of: Any,
    lang: str,
) -> Block:
    """变化表。只包含传进来的行——调用方已经把折叠掉的行剔除了。"""
    return Block(
        TABLE,
        headers=(
            first_header,
            t("blocks.header.concurrency", lang),
            t("blocks.header.tier", lang),
            before_label,
            after_label,
            t("compare.header.change", lang),
        ),
        rows=tuple(
            (
                row[first_key],
                str(row["concurrency"]),
                f"{row['body_tier_us']:g}",
                value_of(row.get("before"), lang),
                value_of(row.get("after"), lang),
                _ratio_cell(row),
            )
            for row in rows
        ),
    )


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
    listed, notes = _fold(comparison["overhead_ratio"], lang=lang, key_field="adapter")
    blocks += notes
    blocks.append(
        _change_table(
            listed,
            first_header=t("compare.header.adapter", lang),
            first_key="adapter",
            before_label=before["label"],
            after_label=after["label"],
            value_of=lambda value, lang: "—" if value is None else format_ratio(value, lang),
            lang=lang,
        )
    )

    blocks.append(Block(HEADING, text=t("compare.section.speedup", lang)))
    blocks.append(Block(NOTE, text=comparison["metric_semantics"]["speedup_vs_baseline"]))
    listed, notes = _fold(
        comparison["speedup_vs_baseline"], lang=lang, key_field="baseline"
    )
    blocks += notes
    blocks.append(
        _change_table(
            listed,
            first_header=t("blocks.header.baseline", lang),
            first_key="baseline",
            before_label=before["label"],
            after_label=after["label"],
            value_of=lambda value, lang: "—" if value is None else f"{value:.3f}x",
            lang=lang,
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
