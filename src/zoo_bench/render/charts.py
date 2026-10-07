"""图表：由报告模型出静态图，**同一份 figure 同时导出 SVG 与 PNG**。

两种格式都要，理由不同——站点用 SVG（矢量、任意缩放不糊），PDF 用 PNG（SVG 不能直接嵌进
PDF，要额外依赖 svglib）。**同一份 figure 出两种格式**，故不存在两套画法、也不会出现两处
数字不一致。

SVG 与 PNG 都把字形**按轮廓/像素嵌入**（matplotlib 的 `svg.fonttype` 保持默认的 ``path``），
因此**不依赖阅读端是否装了中文字体**——若改为保留 `<text>` 引用字体，没装中文字体的读者会
看到方框，那正是我们要避免的失败。代价是 SVG 里的文字不可检索。

中文字体由 :mod:`.fonts` 显式解析；解析失败即明确报错，不产出中文变方框的图。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

# 无显示环境（CI）必须显式指定后端，否则 import pyplot 就会失败
matplotlib.use("Agg")

from matplotlib import pyplot as plt

from ..i18n import LANG_ZH, t
from .fonts import apply_to_matplotlib, find_cjk_font_covering

#: PNG 用；SVG 是矢量的，不受它影响。
CHART_DPI = 200


def _figures_dir(outdir: str | Path) -> Path:
    directory = Path(outdir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _render(figure: Any, *, outdir: str | Path, name: str) -> dict[str, str]:
    """把 figure 导出为 SVG + PNG，返回各自的路径。"""
    directory = _figures_dir(outdir)
    paths: dict[str, str] = {}
    for suffix in (".svg", ".png"):
        path = directory / f"{name}{suffix}"
        figure.savefig(path, dpi=CHART_DPI)
        paths[suffix.lstrip(".")] = str(path)
    plt.close(figure)
    return paths


def _largest(rows: list[dict[str, Any]], key: str) -> Any:
    values = {row[key] for row in rows}
    return max(values) if values else None


def _smallest(rows: list[dict[str, Any]], key: str) -> Any:
    values = {row[key] for row in rows}
    return min(values) if values else None


def overhead_ratio_chart(
    model: dict[str, Any], outdir: str | Path, *, threshold: float
) -> dict[str, Any]:
    """框架开销占比 vs 执行体档位 —— 选型的主图。

    横轴取对数：档位跨 40 微秒 到 10 ms（两个半数量级），线性轴会把短档全挤在左端。纵向虚线是
    开销阈值——低于它，选用该方案的代价才算可忽略。

    Args:
        model: 报告模型。
        outdir: 图输出目录。
        threshold: 开销阈值。

    Returns:
        ``{"figure": 名称, "font": 字体路径, "paths": {格式: 路径}, "scope": 说明}``。
    """
    lang = model["lang"]
    readable = [
        row
        for row in model["dimensions"]["overhead"]["rows"]
        # **只取比值可读的那些行**：并发度超出机器并行能力时开销数字被撤下（None），
        # 拿它们作 y 会让整条序列退化成 NaN，对数轴随即报 "all values are <= 0"
        # （实测：4 核 runner 上最大的并发度整组被撤下，图直接崩掉渲染）
        if row.get("framework_overhead_ratio") is not None
    ]
    all_rows = model["dimensions"]["overhead"]["rows"]
    concurrency = _largest(readable, "concurrency")
    scoped = [row for row in readable if row["concurrency"] == concurrency]

    series: dict[str, list[tuple[float, float]]] = {}
    for row in scoped:
        series.setdefault(row["adapter"], []).append(
            (row["body_tier_us"], row["framework_overhead_ratio"])
        )

    title = t("charts.title.overhead", lang, concurrency=concurrency)
    xlabel = t("charts.xlabel.body_tier", lang)
    ylabel = t("charts.ylabel.overhead_ratio", lang)
    threshold_label = t("charts.threshold", lang, threshold=f"{threshold:.0%}")

    # 先建好标签再选字体：这几个字必须由所选字体认得，否则图上只会静默变成方框。
    # 英文产物的文字全 ASCII（design D6），matplotlib 默认字体即可覆盖——不要求中文字体，
    # 否则"装中文字体"会变成英文产物的假前置条件（design D6）。
    if lang == LANG_ZH:
        font = find_cjk_font_covering("".join((title, xlabel, ylabel, threshold_label)))
        apply_to_matplotlib(font)
        font_used = str(font)
    else:
        font_used = "default (matplotlib built-ins, ASCII-only text)"

    figure, axes = plt.subplots(figsize=(8.0, 4.5))
    for label, points in sorted(series.items()):
        points.sort()
        axes.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            marker="o",
            linewidth=1.6,
            markersize=4,
            label=label,
        )

    axes.axhline(threshold, linestyle="--", linewidth=1.0, color="grey", label=threshold_label)
    axes.set_xscale("log")
    axes.set_title(title)
    axes.set_xlabel(xlabel)
    # 刻意不用 微秒 与 ÷：实测 SimHei 缺 MICRO SIGN 字形，图上会变成方框（见 render/fonts.py）
    axes.set_ylabel(ylabel)
    axes.grid(True, linestyle=":", alpha=0.5)
    axes.legend(fontsize=8)
    figure.tight_layout()

    # 有并发度被撤下时必须说出来：不然读者会以为这张图覆盖了矩阵里的全部并发度
    withheld = sorted(
        {row["concurrency"] for row in all_rows} - {row["concurrency"] for row in readable}
    )
    scope = t("charts.scope.overhead", lang, concurrency=concurrency)
    if withheld:
        scope += t(
            "charts.scope.overhead_withheld",
            lang,
            withheld=", ".join(str(value) for value in withheld),
        )
    return {
        "figure": "overhead_ratio",
        "font": font_used,
        "paths": _render(figure, outdir=outdir, name=f"{lang}-overhead_ratio"),
        "scope": scope,
    }


def throughput_chart(model: dict[str, Any], outdir: str | Path) -> dict[str, Any]:
    """吞吐 vs 并发度 —— 看并发上去后是否继续伸缩。

    **取最短的执行体档位**：框架开销在短任务上占比最高，伸缩性被它限制的样子在这一档最清楚；
    长任务档位上各方案都接近线性，看不出差异。

    Args:
        model: 报告模型。
        outdir: 图输出目录。

    Returns:
        同 :func:`overhead_ratio_chart` 的返回结构。
    """
    rows = model["dimensions"]["throughput"]["rows"]
    lang = model["lang"]
    tier = _smallest(rows, "body_tier_us")
    scoped = [row for row in rows if row["body_tier_us"] == tier]

    series: dict[str, list[tuple[float, float]]] = {}
    for row in scoped:
        series.setdefault(row["adapter"], []).append(
            (row["concurrency"], row["throughput_per_second"])
        )

    title = t("charts.title.throughput", lang, tier=tier)
    xlabel = t("charts.xlabel.concurrency", lang)
    ylabel = t("charts.ylabel.throughput", lang)

    # 英文产物的文字全 ASCII（design D6）：不需要中文字体，matplotlib 默认字体即可
    if lang == LANG_ZH:
        font = find_cjk_font_covering("".join((title, xlabel, ylabel)))
        apply_to_matplotlib(font)
        font_used = str(font)
    else:
        font_used = "default (matplotlib built-ins, ASCII-only text)"

    figure, axes = plt.subplots(figsize=(8.0, 4.5))
    for label, points in sorted(series.items()):
        points.sort()
        axes.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            marker="o",
            linewidth=1.6,
            markersize=4,
            label=label,
        )

    axes.set_xscale("log", base=2)
    axes.set_xticks(sorted({row["concurrency"] for row in scoped}))
    axes.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    axes.set_title(title)
    axes.set_xlabel(xlabel)
    axes.set_ylabel(ylabel)
    axes.grid(True, linestyle=":", alpha=0.5)
    axes.legend(fontsize=8)
    figure.tight_layout()

    return {
        "figure": "throughput",
        "font": font_used,
        "paths": _render(figure, outdir=outdir, name=f"{lang}-throughput"),
        "scope": t("charts.scope.throughput", lang, tier=tier),
    }


def relative_multiple_chart(model: dict[str, Any], outdir: str | Path) -> dict[str, Any]:
    """相对各对照方案的倍数 vs 执行体档位 —— **正面那一面的图**。

    纵轴是"对照方案耗时 / 被测框架耗时"：**大于 1 表示被测框架更快**。纵向虚线是打平线（1.0）。
    横轴取对数（档位跨两个半数量级），纵轴也取对数（倍数本身就是乘性量，线性轴上 0.5x 与 2x
    距打平线的距离会不一样）。

    **每个所测并发度各一个面板**：挑一个并发度作图就是挑对自己有利的呈现，而这恰恰是这类图最
    容易失守的地方——分面让全部并发度同时在场，读者自己看形状。

    Args:
        model: 报告模型。
        outdir: 图输出目录。

    Returns:
        同 :func:`overhead_ratio_chart` 的返回结构。
    """
    lang = model["lang"]
    turnings = [
        turning
        for turning in model["conclusion"].get("relative_turnings", [])
        if any(float(ratio) > 0 for ratio in turning["ratios_vs_subject"])
    ]
    concurrencies = sorted({turning["concurrency"] for turning in turnings})

    title = t("charts.title.relative", lang)
    xlabel = t("charts.xlabel.body_tier", lang)
    ylabel = t("charts.ylabel.relative", lang)
    tie_label = t("charts.tie_line", lang)

    # 英文产物的文字全 ASCII（design D6）：不需要中文字体，matplotlib 默认字体即可
    if lang == LANG_ZH:
        font = find_cjk_font_covering("".join((title, xlabel, ylabel, tie_label)))
        apply_to_matplotlib(font)
        font_used = str(font)
    else:
        font_used = "default (matplotlib built-ins, ASCII-only text)"

    columns = min(2, len(concurrencies))
    rows = -(-len(concurrencies) // columns)
    figure, axes_grid = plt.subplots(rows, columns, figsize=(8.0, 3.2 * rows), squeeze=False)
    panels = [axes_grid[index // columns][index % columns] for index in range(rows * columns)]

    for panel, concurrency in zip(panels, concurrencies, strict=False):
        series: dict[str, list[tuple[float, float]]] = {}
        for turning in turnings:
            if turning["concurrency"] != concurrency:
                continue
            points = [
                (tier, ratio)
                for tier, ratio in zip(
                    turning["tiers_us"], turning["ratios_vs_subject"], strict=True
                )
                if float(ratio) > 0
            ]
            series.setdefault(turning["baseline"], []).extend(points)

        low, high = 1.0, 1.0
        for label, points in sorted(series.items()):
            points.sort()
            low = min(low, *(point[1] for point in points))
            high = max(high, *(point[1] for point in points))
            panel.plot(
                [point[0] for point in points],
                [point[1] for point in points],
                marker="o",
                linewidth=1.6,
                markersize=4,
                label=label,
            )

        panel.axhline(1.0, linestyle="--", linewidth=1.0, color="grey")
        panel.set_xscale("log")
        panel.set_yscale("log")
        # 对数刻度的默认标签走 mathtext，指数带 U+2212 减号，而中文字体没有这个字形——
        # matplotlib 会把它换成一个方块（与报告正文被导出前门禁拦下的那次是同一个坑）。故显式给
        # 刻度并强制普通格式，让标签全是 ASCII：几何仍是对数的，文字安全。
        ticks = [
            value
            for value in (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 10.0)
            if low * 0.9 <= value <= high * 1.1
        ]
        if len(ticks) >= 2:
            panel.set_yticks(ticks)
        # 普通格式**无条件**设：候选刻度不足两个时（比值范围超出候选带）默认的 LogFormatter
        # 正是产生 4×10⁻¹ 这类标签的那一支
        panel.get_yaxis().set_major_formatter(plt.ScalarFormatter())
        # 次刻度标签一并关掉：它不走主格式，而是 `$\mathdefault{...}$`，而 \mathdefault 取的是
        # **正文字体**——中文侧那是 CJK 字体、缺 U+2212，于是主刻度改完仍出方框（实测该图的
        # 全部警告都来自次刻度的这一类标签）
        panel.get_yaxis().set_minor_formatter(plt.NullFormatter())
        panel.set_title(t("charts.panel.title", lang, concurrency=concurrency))
        panel.set_xlabel(xlabel)
        panel.grid(True, linestyle=":", alpha=0.5)
        panel.legend(fontsize=7)

    for unused in panels[len(concurrencies) :]:
        unused.set_visible(False)

    panels[0].set_ylabel(ylabel)
    if concurrencies:
        panels[0].text(
            0.0, 1.02, tie_label, fontsize=7, color="grey", transform=panels[0].transAxes
        )
    figure.suptitle(title)
    figure.tight_layout()

    return {
        "figure": "relative_multiple",
        "font": font_used,
        "paths": _render(figure, outdir=outdir, name=f"{lang}-relative_multiple"),
        "scope": t("charts.scope.relative", lang, count=len(concurrencies)),
    }


def render_all(
    model: dict[str, Any], outdir: str | Path, *, threshold: float
) -> list[dict[str, Any]]:
    """出全部图表。

    Args:
        model: 报告模型。
        outdir: 图输出目录。
        threshold: 开销阈值。

    Returns:
        各图的描述列表，供渲染层写进报告。
    """
    charts: list[dict[str, Any]] = []
    # 一张已撤下的开销数字做不出可读的图：那种情况下**不出这张图**，而不是出一张空图
    if any(
        row.get("framework_overhead_ratio") is not None
        for row in model["dimensions"]["overhead"]["rows"]
    ):
        charts.append(overhead_ratio_chart(model, outdir, threshold=threshold))
    if model["dimensions"]["throughput"]["rows"]:
        charts.append(throughput_chart(model, outdir))
    # 有可比对象才有这张图：没有逐档倍数时画不出"谁更快"
    if model["conclusion"].get("relative_turnings"):
        charts.append(relative_multiple_chart(model, outdir))
    return charts
