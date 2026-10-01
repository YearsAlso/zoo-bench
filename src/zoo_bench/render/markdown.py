"""Markdown 序列化器。

只做一件事：把块列表写成 Markdown。**内容与结构全部来自** :mod:`.blocks`——这样 Markdown、
HTML 与 PDF 说的必然是同一件事，各写一遍措辞是必然的漂移源。
"""

from __future__ import annotations

from typing import Any

from .blocks import BULLETS, HEADING, IMAGE, NOTE, PARAGRAPH, TABLE, Block, build_blocks


def _table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    """GitHub 风格的表格。行数为 0 时仍给出表头——空表比没有表更说明问题。"""
    head = "| " + " | ".join(headers) + " |"
    divider = "|" + "|".join(["---"] * len(headers)) + "|"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([head, divider, *body])


def blocks_to_markdown(blocks: list[Block]) -> str:
    """把块列表序列化成 Markdown。

    Args:
        blocks: :func:`zoo_bench.render.blocks.build_blocks` 的返回值。

    Returns:
        Markdown 文本。
    """
    lines: list[str] = []
    for block in blocks:
        if block.kind == HEADING:
            lines += ["#" * block.level + " " + block.text, ""]
        elif block.kind == PARAGRAPH:
            lines += [block.text, ""]
        elif block.kind == BULLETS:
            lines += [f"- {item}" for item in block.items]
            lines += [""]
        elif block.kind == NOTE:
            lines += [f"> {block.text}", ""]
        elif block.kind == TABLE:
            lines += [_table(block.headers, block.rows), ""]
        elif block.kind == IMAGE:
            lines += [f"![{block.text}]({block.src})", ""]
    return "\n".join(lines)


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
    return blocks_to_markdown(build_blocks(model, charts, figures_rel=figures_rel))
