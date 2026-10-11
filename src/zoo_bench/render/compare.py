"""跨版本对比的渲染：对比结果 -> 块列表 -> Markdown / HTML。

与报告走同一套块结构（design D11 的同源要求），故对比文档以后同样能出 PDF，不必再写一套。

**文案全部键化**：对比结果里的散文（方向、口径注）已由计算层按 ``lang`` 落值，这里的固定
文案也一律走 :mod:`zoo_bench.i18n`——英文对比页不留中文原文（spec 场景「英文产物不留中文原文」）。
"""

from __future__ import annotations

from typing import Any

from ..i18n import has_translation, resolve_reason, t
from .blocks import (
    BULLETS,
    HEADING,
    NOTE,
    PARAGRAPH,
    TABLE,
    Block,
    _list_join,
    _tier_list,
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


def _form_label(form: str, lang: str) -> str:
    """执行形态的显示名。**形态与臂不是同一套词表**，故各自取名；本仓没收录的形态原样透传。"""
    key = f"native_line.form.{form}"
    return t(key, lang) if has_translation(key) else str(form)


def _native_line_side_line(side: dict[str, Any], lang: str) -> str:
    """一侧该线的状态：那一轮没测 / 测了但用不了（带原因）/ 可用（给该侧自己的分界结论）.

    **三态分开说话**：把"旧版本没有这项能力"与"这一轮测砸了"合成一句，读者会以为版本之间少了
    一项能力；把"没测"与"不适用"一起吞成"无数据"，这一节就成了一片空白——而空白最容易被读成
    "两边一样"。
    """
    if not side["present"]:
        return t("compare.native_line.side_absent", lang, label=side["label"])
    if not side["applicable"]:
        return t(
            "compare.native_line.side_unusable",
            lang,
            label=side["label"],
            status=side["status"],
            reason=resolve_reason(side["reason"], lang, side["reason_params"]),
        )
    verdict = side["verdict"] or {}
    return t(
        "compare.native_line.verdict",
        lang,
        label=side["label"],
        concurrency=verdict.get("judgment_concurrency"),
        faster=_tier_list(verdict.get("native_faster_tiers") or [], lang),
        slower=_tier_list(verdict.get("native_slower_tiers") or [], lang),
        undecided=_tier_list(verdict.get("undecided_tiers") or [], lang),
    )


def _native_line_blocks(native_line: dict[str, Any], lang: str) -> list[Block]:
    """原生任务线的跨版本节：两侧各自可用状态 + 逐档逐形态的**同运行内倍数**变化.

    位置在相对倍数节之后、绝对耗时节之前：它比的是与相对倍数同类的量（同运行内的比值），而绝对
    耗时那节是"仅供看量级"的另一类。

    表里**没有每任务中位数**——那是跨运行不可比的量（模块 docstring 第一条纪律）。读者若想看
    量级，去各版本自己的报告；这里只给方向与幅度。
    """
    before, after = native_line["before"], native_line["after"]
    blocks = [
        Block(HEADING, text=t("compare.section.native_line", lang)),
        Block(NOTE, text=t("compare.native_line.note", lang)),
        Block(
            BULLETS,
            items=(
                _native_line_side_line(before, lang),
                _native_line_side_line(after, lang),
            ),
        ),
    ]

    if native_line["rows"]:
        blocks.append(Block(NOTE, text=t("compare.metric.native_line_ratio", lang)))
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.tier", lang),
                    t("blocks.header.concurrency", lang),
                    t("compare.header.form", lang),
                    before["label"],
                    after["label"],
                    t("compare.header.change", lang),
                ),
                rows=tuple(
                    (
                        row["tier"],
                        str(row["concurrency"]),
                        _form_label(row["form"], lang),
                        "—" if row["before"] is None else f"{row['before']:.3f}x",
                        "—" if row["after"] is None else f"{row['after']:.3f}x",
                        _ratio_cell(row),
                    )
                    for row in native_line["rows"]
                ),
            )
        )

    if native_line["verdict_same"] is not None:
        blocks.append(
            Block(
                PARAGRAPH,
                text=t(
                    "compare.native_line.verdict_same"
                    if native_line["verdict_same"]
                    else "compare.native_line.verdict_moved",
                    lang,
                ),
            )
        )

    if native_line["only_in_one"]:
        # 与 only_in_one 那一节同一条纪律：**列出来，不静默取交集**——档位变过时两侧就不是同一
        # 批格子，变化无法归因
        items = [
            t(
                "compare.native_line.item",
                lang,
                tier=item["tier"],
                concurrency=item["concurrency"],
                side=item["side"],
            )
            for item in native_line["only_in_one"]
        ]
        blocks.append(
            Block(
                PARAGRAPH,
                text=t("compare.native_line.only_in_one", lang, items=_list_join(items, lang)),
            )
        )
    return blocks


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

    blocks += _native_line_blocks(comparison["native_line"], lang)

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
