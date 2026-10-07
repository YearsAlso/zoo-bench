"""报告渲染编排：一次出三种格式。

三种格式（HTML 给站点、Markdown 可 diff、PDF 可分发）都从**同一份块列表**序列化，故数字必然
一致——这是 design D11 的核心要求。图表也只生成一次：同一份 figure 出 SVG（站点）与 PNG（PDF）。

**HTML 是 deck 形态**（见 :mod:`.deck`）：一页一个结论、数值以条形编码。它的文字同样取自块列表，
条形取自模型的原始数值，故与另外两份同源。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..i18n import LANG_EN, LANG_ZH
from .blocks import build_blocks
from .charts import render_all
from .deck import render_deck
from .links import subtree_path
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
    blocks = build_blocks(model, charts, lang=model["lang"])

    html_path = directory / "index.html"
    markdown_path = directory / "report.md"
    html_path.write_text(render_deck(model, blocks), encoding="utf-8")
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


def render_site(
    models: dict[str, dict[str, Any]], outdir: str | Path, *, threshold: float
) -> dict[str, dict[str, Any]]:
    """一次写出整棵双语站点（design D3/D7）。

    英文挂在 ``outdir`` 根、中文挂在 ``outdir/zh/``——两棵语言树互为镜像，各走一遍单语言
    :func:`render`（图表、三种格式、PDF 字体规则都按各自语言来）。``models`` 必须同时给出
    两种语言的模型：少一种等于"双语站点缺了一半"，让它 KeyError 比悄悄只出一半诚实。

    Args:
        models: ``{"en": 英文模型, "zh": 中文模型}``，由调用方各调一次 :func:`build_model` 得到。
        outdir: 站点根目录。
        threshold: 开销阈值（传给图表）。

    Returns:
        ``{"en": render 的返回值, "zh": render 的返回值}``。
    """
    root = Path(outdir)
    return {
        LANG_EN: render(models[LANG_EN], root, threshold=threshold),
        LANG_ZH: render(models[LANG_ZH], root / subtree_path(LANG_ZH), threshold=threshold),
    }
