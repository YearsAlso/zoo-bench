"""HTML 序列化器。

Pages 发布的是静态站点——Markdown 直接放上去会被当纯文本读，故站点用 HTML。内容同样**只来自**
:mod:`.blocks`，与 Markdown、PDF 同源。

行内标记刻意只支持报告实际用到的那一点子集（``**粗体**`` 与 ``代码``），**不引 Markdown 库**：
为了一小撮语法多一个依赖与一层行为不确定性不划算，而"到底支持哪些语法"写在这里比藏在库的
版本差异里清楚。
"""

from __future__ import annotations

import html as html_module
import re
from typing import Any

from .blocks import BULLETS, HEADING, IMAGE, NOTE, PARAGRAPH, TABLE, Block, build_blocks

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")

_STYLE = """
:root { color-scheme: light dark; }
body {
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  max-width: 62rem; margin: 0 auto; padding: 2rem 1rem; line-height: 1.65;
}
h1 { border-bottom: 2px solid rgba(128,128,128,0.35); padding-bottom: 0.4rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.92rem; }
th, td { border: 1px solid rgba(128,128,128,0.4); padding: 0.35rem 0.6rem; text-align: left; }
th { background: rgba(128,128,128,0.12); }
blockquote {
  margin: 1rem 0; padding: 0.4rem 1rem;
  border-left: 3px solid rgba(128,128,128,0.5); color: rgba(140,140,140,1);
}
img { max-width: 100%; height: auto; }
code { background: rgba(128,128,128,0.15); padding: 0.1rem 0.3rem; border-radius: 3px; }
"""


def inline(text: str) -> str:
    """转义 HTML，再把报告用到的行内标记换成标签。

    **先转义再替换**——顺序反了会把用户数据当标记注入。
    """
    escaped = html_module.escape(text)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _CODE.sub(r"<code>\1</code>", escaped)


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


def blocks_to_html(blocks: list[Block], *, title: str = "zoo-bench 性能报告") -> str:
    """把块列表序列化成一份完整的 HTML 文档。

    Args:
        blocks: :func:`zoo_bench.render.blocks.build_blocks` 的返回值。
        title: 文档标题。

    Returns:
        完整的 HTML 文本。
    """
    body: list[str] = []
    for block in blocks:
        if block.kind == HEADING:
            level = min(block.level, 6)
            body.append(f"<h{level}>{inline(block.text)}</h{level}>")
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
            f"<style>{_STYLE}</style>",
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
