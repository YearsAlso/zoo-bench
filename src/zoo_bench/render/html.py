"""HTML 序列化器。

Pages 发布的是静态站点——Markdown 直接放上去会被当纯文本读，故站点用 HTML。内容同样**只来自**
:mod:`.blocks`，与 Markdown、PDF 同源。

行内标记刻意只支持报告实际用到的那一点子集（``**粗体**`` 与 ``代码``），**不引 Markdown 库**：
为了一小撮语法多一个依赖与一层行为不确定性不划算，而"到底支持哪些语法"写在这里比藏在库的
版本差异里清楚。

**这里额外做两件 Markdown/PDF 不需要的事**：给标题加锚点 `id`、并由二级标题生成目录。它们是
网页特有的可导航性——README 的样式与结构分工里写过：观感归 ``style.css``，**结构归这里**。
"""

from __future__ import annotations

import html as html_module
import re
from typing import Any

from .assets import STYLE_FILENAME
from .blocks import BULLETS, HEADING, IMAGE, NOTE, PARAGRAPH, TABLE, Block, build_blocks

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_SLUG_UNSAFE = re.compile(r"[^\w一-鿿　-〿]+")

TOC_TITLE = "本页内容"
TOC_LEVEL = 2


def inline(text: str) -> str:
    """转义 HTML，再把报告用到的行内标记换成标签。

    **先转义再替换**——顺序反了会把用户数据当标记注入。
    """
    escaped = html_module.escape(text)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _CODE.sub(r"<code>\1</code>", escaped)


def slug(text: str, *, taken: set[str]) -> str:
    """由标题文本生成锚点 id。

    保留汉字（中文标题的锚点理应可读），其余不可用字符折成 ``-``；重名时补数字后缀，
    因为报告里"维度：…"这类标题的前缀相同，仅靠文本可能撞车。
    """
    base = _SLUG_UNSAFE.sub("-", text).strip("-").lower() or "section"
    candidate = base
    index = 2
    while candidate in taken:
        candidate = f"{base}-{index}"
        index += 1
    taken.add(candidate)
    return candidate


def _table_html(block: Block) -> str:
    head = "".join(f"<th>{inline(header)}</th>" for header in block.headers)
    rows = "".join(
        "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>" for row in block.rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def _image_html(block: Block) -> str:
    source = html_module.escape(block.src, quote=True)
    alt = html_module.escape(block.text, quote=True)
    return f'<img src="{source}" alt="{alt}">'


def _toc_html(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return ""
    items = "".join(f'<li><a href="#{anchor}">{inline(text)}</a></li>' for text, anchor in entries)
    return f'<nav class="toc"><p>{TOC_TITLE}</p><ol>{items}</ol></nav>'


def blocks_to_html(
    blocks: list[Block], *, title: str = "zoo-bench 性能报告", stylesheet: str = STYLE_FILENAME
) -> str:
    """把块列表序列化成一份完整的 HTML 文档。

    Args:
        blocks: :func:`zoo_bench.render.blocks.build_blocks` 的返回值。
        title: 文档标题。
        stylesheet: 样式表的相对路径；文档用 ``<link>`` 引用它。

    Returns:
        完整的 HTML 文本。
    """
    taken: set[str] = set()
    anchors = {
        index: slug(block.text, taken=taken) for index, block in enumerate(blocks) if block.kind == HEADING
    }
    toc = [
        (block.text, anchors[index])
        for index, block in enumerate(blocks)
        if block.kind == HEADING and block.level == TOC_LEVEL
    ]

    body: list[str] = []
    for index, block in enumerate(blocks):
        if block.kind == HEADING:
            level = min(block.level, 6)
            body.append(f'<h{level} id="{anchors[index]}">{inline(block.text)}</h{level}>')
            # 目录插在文档首个标题之后——放在最前面会与标题抢位置，放在末尾则没人看得到
            if block.level == 1:
                body.append(_toc_html(toc))
        elif block.kind == PARAGRAPH:
            body.append(f"<p>{inline(block.text)}</p>")
        elif block.kind == BULLETS:
            items = "".join(f"<li>{inline(item)}</li>" for item in block.items)
            body.append(f"<ul>{items}</ul>")
        elif block.kind == NOTE:
            body.append(f"<blockquote>{inline(block.text)}</blockquote>")
        elif block.kind == TABLE:
            body.append(_table_html(block))
        elif block.kind == IMAGE:
            body.append(_image_html(block))

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{inline(title)}</title>",
            f'<link rel="stylesheet" href="{html_module.escape(stylesheet, quote=True)}">',
            "</head>",
            "<body>",
            *body,
            "</body>",
            "</html>",
            "",
        ]
    )


def render_html(
    model: dict[str, Any], charts: list[dict[str, Any]], *, figures_rel: str = "figures"
) -> str:
    """由报告模型渲染 HTML。"""
    return blocks_to_html(build_blocks(model, charts, figures_rel=figures_rel))
