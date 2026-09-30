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

    横轴取对数：档位跨 40 µs 到 10 ms（两个半数量级），线性轴会把短档全挤在左端。纵向虚线是
    开销阈值——低于它，选用该方案的代价才算可忽略。

    Args:
        model: 报告模型。
        outdir: 图输出目录。
        threshold: 开销阈值。

    Returns:
        ``{"figure": 名称, "font": 字体路径, "paths": {格式: 路径}, "scope": 说明}``。
    """
    rows = model["dimensions"]["overhead"]["rows"]
    concurrency = _largest(rows, "concurrency")
    scoped = [row for row in rows if row["concurrency"] == concurrency]

    series: dict[str, list[tuple[float, float]]] = {}
    for row in scoped:
        series.setdefault(row["adapter"], []).append(
            (row["body_tier_us"], row["framework_overhead_ratio"])
        )

    title = f"框架开销占端到端比例（并发度 {concurrency}）"
    xlabel = "执行体时长（微秒，对数轴）"
    ylabel = "框架开销 / 端到端"
    threshold_label = f"开销阈值 {threshold:.0%}"

    # 先建好标签再选字体：这几个字必须由所选字体认得，否则图上只会静默变成方框
    font = find_cjk_font_covering("".join((title, xlabel, ylabel, threshold_label)))
    apply_to_matplotlib(font)

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
    # 刻意不用 µ 与 ÷：实测 SimHei 缺 MICRO SIGN 字形，图上会变成方框（见 render/fonts.py）
    axes.set_ylabel(ylabel)
    axes.grid(True, linestyle=":", alpha=0.5)
    axes.legend(fontsize=8)
    figure.tight_layout()

    return {
        "figure": "overhead_ratio",
        "font": str(font),
        "paths": _render(figure, outdir=outdir, name="overhead_ratio"),
        "scope": f"仅并发度 {concurrency} 的档位；比值仅在同一次运行内成立",
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
    tier = _smallest(rows, "body_tier_us")
    scoped = [row for row in rows if row["body_tier_us"] == tier]

    series: dict[str, list[tuple[float, float]]] = {}
    for row in scoped:
        series.setdefault(row["adapter"], []).append(
            (row["concurrency"], row["throughput_per_second"])
        )

    title = f"吞吐随并发度的变化（执行体 {tier:g} 微秒档）"
    xlabel = "并发度（对数轴）"
    ylabel = "任务 / 秒"

    font = find_cjk_font_covering("".join((title, xlabel, ylabel)))
    apply_to_matplotlib(font)

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
        "font": str(font),
        "paths": _render(figure, outdir=outdir, name="throughput"),
        "scope": f"仅执行体 {tier:g} µs 档；吞吐仅在同一次运行内成立",
    }


def render_all(model: dict[str, Any], outdir: str | Path, *, threshold: float) -> list[dict[str, Any]]:
    """出全部图表。

    Args:
        model: 报告模型。
        outdir: 图输出目录。
        threshold: 开销阈值。

    Returns:
        各图的描述列表，供渲染层写进报告。
    """
    charts: list[dict[str, Any]] = []
    rows = model["dimensions"]["overhead"]["rows"]
    if rows:
        charts.append(overhead_ratio_chart(model, outdir, threshold=threshold))
    if model["dimensions"]["throughput"]["rows"]:
        charts.append(throughput_chart(model, outdir))
    return charts
