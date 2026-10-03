"""PDF 序列化器。

与 Markdown / HTML **同源**：三者都只消费 :mod:`.blocks` 的块列表，故 PDF 里的数字与站点报告
必然一致——这是 design D11 的核心要求，也避免了"三个格式说的不是一回事"这种很难被发现的不一致。

中文字体由 :func:`.fonts.find_pdf_font` 解析，它同时要求"覆盖报告文本"与"能被 reportlab 嵌入"
（后者意味着必须是 TrueType 轮廓；CFF 轮廓的 fonts-noto-cjk 只适合 matplotlib）。**解析失败即
明确报错并给出安装指引**，不产出一份中文变方框的 PDF。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .blocks import (
    BULLETS,
    HEADING,
    IMAGE,
    NOTE,
    PARAGRAPH,
    TABLE,
    Block,
    assert_report_text_is_renderable,
    build_blocks,
)
from .fonts import find_pdf_font

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")

PAGE_MARGIN = 18 * mm
LINE_COLOR = colors.HexColor("#999999")
HEADER_BG = colors.HexColor("#EEEEEE")
NOTE_COLOR = colors.HexColor("#666666")

#: 字号。标题按层级递减。
BODY_FONT_SIZE = 9.5
HEADING_SIZES = {1: 18, 2: 13, 3: 11}


def _markup(text: str) -> str:
    """转义 XML，再把报告用到的行内标记换成 reportlab 的 mini-HTML。

    **先转义再替换**——顺序反了会把数据当标记。代码块刻意只用颜色区分、**不换字体**：换字体会
    落到拉丁字体上，中文就变成方框。
    """
    escaped = escape(text)
    escaped = _BOLD.sub(r"<b>\1</b>", escaped)
    return _CODE.sub(r'<font color="#666666">\1</font>', escaped)


def blocks_text(blocks: list[Block]) -> str:
    """把块列表里的全部文字拼起来。

    用途是给字体解析提供"报告实际会渲染的字符集"——否则缺字只能在产出后靠肉眼发现。
    """
    parts: list[str] = []
    for block in blocks:
        parts.extend((block.text, *block.items, *block.headers))
        parts.extend(cell for row in block.rows for cell in row)
    return "".join(parts)


def _styles(font_name: str) -> dict[str, ParagraphStyle]:
    body = ParagraphStyle(
        "zoo-bench-body", fontName=font_name, fontSize=BODY_FONT_SIZE, leading=BODY_FONT_SIZE * 1.6
    )
    styles: dict[str, ParagraphStyle] = {PARAGRAPH: body}
    for level, size in HEADING_SIZES.items():
        styles[f"{HEADING}{level}"] = ParagraphStyle(
            f"zoo-bench-h{level}",
            fontName=font_name,
            fontSize=size,
            leading=size * 1.4,
            spaceBefore=size * 0.8,
            spaceAfter=size * 0.4,
        )
    styles[NOTE] = ParagraphStyle(
        "zoo-bench-note",
        parent=body,
        textColor=NOTE_COLOR,
        leftIndent=6 * mm,
        spaceBefore=2,
        spaceAfter=4,
    )
    styles[BULLETS] = ParagraphStyle(
        "zoo-bench-bullet", parent=body, leftIndent=6 * mm, bulletIndent=1 * mm, spaceAfter=2
    )
    return styles


def _table(block: Block, width: float, font_name: str) -> Table:
    def cell(text: str, *, header: bool) -> Paragraph:
        style = ParagraphStyle(
            "th" if header else "td",
            fontName=font_name,
            fontSize=BODY_FONT_SIZE if header else BODY_FONT_SIZE - 0.5,
        )
        return Paragraph(_markup(text), style)

    data: list[list[Paragraph]] = [[cell(text, header=True) for text in block.headers]]
    data.extend([cell(text, header=False) for text in row] for row in block.rows)

    columns = max(len(block.headers), 1)
    table = Table(data, colWidths=[width / columns] * columns, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, LINE_COLOR),
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _image(src: Path, width: float) -> Image:
    width_px, height_px = ImageReader(str(src)).getSize()
    return Image(str(src), width=width, height=width * height_px / width_px)


def build_story(blocks: list[Block], width: float, font_name: str) -> list[Any]:
    """把块列表变成 reportlab 的 flowable 列表。

    Args:
        blocks: :func:`zoo_bench.render.blocks.build_blocks` 的返回值。
        width: 可用宽度（点）。
        font_name: 已注册的中文字体名。

    Returns:
        flowable 列表。
    """
    styles = _styles(font_name)
    story: list[Any] = []

    for block in blocks:
        if block.kind == HEADING:
            level = min(block.level, 3)
            story.append(Paragraph(_markup(block.text), styles[f"{HEADING}{level}"]))
        elif block.kind == PARAGRAPH:
            story.append(Paragraph(_markup(block.text), styles[PARAGRAPH]))
            story.append(Spacer(1, 2 * mm))
        elif block.kind == NOTE:
            story.append(Paragraph(_markup(block.text), styles[NOTE]))
        elif block.kind == BULLETS:
            story.extend(
                Paragraph(_markup(item), styles[BULLETS], bulletText="-") for item in block.items
            )
            story.append(Spacer(1, 2 * mm))
        elif block.kind == TABLE:
            story.append(_table(block, width, font_name))
            story.append(Spacer(1, 3 * mm))
        elif block.kind == IMAGE:
            source = Path(block.src)
            if source.is_file():
                story.append(KeepTogether(_image(source, width)))
            else:
                # 图缺失时如实说明，不留一块空白让人以为是排版问题
                story.append(Paragraph(f"[缺图：{_markup(block.src)}]", styles[NOTE]))

    return story


def render_pdf(
    model: dict[str, Any],
    charts: list[dict[str, Any]],
    out_path: str | Path,
    *,
    figures_dir: Path | None = None,
) -> dict[str, Any]:
    """由报告模型导出 PDF。

    Args:
        model: 报告模型。
        charts: 图表描述列表（用其中的 PNG；SVG 不能直接嵌进 PDF）。
        out_path: PDF 输出路径。
        figures_dir: 图表所在的绝对目录；给定后把图路径解析到这里。

    Returns:
        ``{"pdf": 路径, "font": 字体信息, "font_text_length": 覆盖检查用的字符数}``。

    Raises:
        zoo_bench.render.blocks.UnsafeReportText: 报告正文里有中文字体不一定有的符号。
        zoo_bench.render.fonts.CjkFontUnavailable: 找不到既覆盖文本又能被嵌入的中文字体。
    """
    blocks = build_blocks(model, _absolute_charts(charts, figures_dir))

    text = blocks_text(blocks)
    # 先查"字符本身是否该出现在中文报告里"，再查"所选字体认不认得它们"。
    # 前者是**根因**（用了中文字体不保证有的排版符号），后者是环境差异；两道都拦，
    # 因为缺字只会变成方框、文件照样生成。实测被 U+2212 卡住过一轮 CI。
    assert_report_text_is_renderable(text)
    font = find_pdf_font(text)

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN,
        title="zoo-bench 性能报告",
        author="zoo-bench",
    )
    width = A4[0] - 2 * PAGE_MARGIN

    story = build_story(blocks, width, str(font["name"]))
    document.build(story)

    return {"pdf": str(path), "font": font, "font_text_length": len(text)}


def _absolute_charts(
    charts: list[dict[str, Any]], figures_dir: Path | None
) -> list[dict[str, Any]]:
    """把图路径解析成绝对路径。

    块里的 ``src`` 是**相对路径**（站点上必须如此，否则换目录就断），而 PDF 要读本地文件。
    """
    if figures_dir is None:
        return charts

    return [
        {
            **chart,
            "paths": {
                fmt: str(figures_dir / Path(path).name) for fmt, path in chart["paths"].items()
            },
        }
        for chart in charts
    ]
