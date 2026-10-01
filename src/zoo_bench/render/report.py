"""报告渲染编排：一次出三种格式。

三种格式（HTML 给站点、Markdown 可 diff、PDF 可分发）都从**同一份块列表**序列化，故数字必然
一致——这是 design D11 的核心要求。图表也只生成一次：同一份 figure 出 SVG（站点）与 PNG（PDF）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .blocks import build_blocks
from .charts import render_all
from .html import blocks_to_html
from .markdown import blocks_to_markdown
from .pdf import render_pdf


def render(model: dict[str, Any], outdir: str | Path, *, threshold: float) -> dict[str, Any]:
    """出图表并写出三种格式的报告。

    Args:
        model: 报告模型。
        outdir: 输出目录；图表写入其下的 ``figures/``。
        threshold: 开销阈值（传给图表）。

    Returns:
        ``{"html": 路径, "markdown": 路径, "pdf": 路径, "font": 字体信息, "charts": [...]}``。

    Raises:
        zoo_bench.render.fonts.CjkFontUnavailable: 找不到可用的中文字体。**此时不产出任何文件**
            的一半——宁可没有报告，也不要一份中文变方框或数字不可信的报告。
    """
    directory = Path(outdir)
    directory.mkdir(parents=True, exist_ok=True)
    figures = directory / "figures"

    charts = render_all(model, figures, threshold=threshold)
    blocks = build_blocks(model, charts)

    html_path = directory / "index.html"
    markdown_path = directory / "report.md"
    html_path.write_text(blocks_to_html(blocks), encoding="utf-8")
    markdown_path.write_text(blocks_to_markdown(blocks), encoding="utf-8")

    # PDF 放在最后：字体解析失败时它是唯一会抛的环节，此时 HTML/Markdown 已写好、可先看
    pdf = render_pdf(model, charts, directory / "report.pdf", figures_dir=figures)

    return {
        "html": str(html_path),
        "markdown": str(markdown_path),
        "pdf": pdf["pdf"],
        "font": pdf["font"],
        "charts": charts,
    }
