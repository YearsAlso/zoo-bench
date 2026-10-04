"""站点首页：每个已留档版本一张卡片。

卡片上的信息（生成时间、单元数、自检状态）**从留档数据读**而不是从站点文件猜——它们正是读者
判断"这份报告是哪一轮测的、可不可信"的依据。首页放在这里而不是 CLI 里，是为了它能被用例盖到。
"""

from __future__ import annotations

import datetime
import html as html_module
from dataclasses import dataclass
from pathlib import Path

from .. import storage
from .assets import STYLE_FILENAME, write_style

INDEX_FILENAME = "index.html"

_TITLE = "zoo-bench 性能报告"

_HEADER = (
    "<h1>zoo-bench 性能报告</h1>"
    '<p>Zoo Framework 的维护中性能证据 —— 每个版本重新测量、公开原始数据，'
    "并如实列出本框架输掉的档位。</p>"
)


@dataclass(frozen=True)
class VersionCard:
    """一个版本的卡片。

    Attributes:
        slug: 版本目录名。
        report_href: 报告页的相对链接。
        pdf_href: PDF 的相对链接；没有导出过则为 None。
        generated_at: 该轮测量的时间（UTC，已是可读字符串）。
        unit_count: 有效单元数。
        failed_units: 失败单元数。
        self_check_ok: 该轮自检是否通过；读不到留档时为 None。
    """

    slug: str
    report_href: str
    pdf_href: str | None
    generated_at: str | None
    unit_count: int | None
    failed_units: int | None
    self_check_ok: bool | None


def _format_epoch(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")


def collect_cards(results_root: str | Path | None, site_dir: str | Path) -> list[VersionCard]:
    """收集首页要展示的卡片。

    Args:
        results_root: 留档根目录。
        site_dir: 站点目录（用于判断报告与 PDF 是否已渲染）。

    Returns:
        卡片列表，按版本名排序。**报告页不存在的版本不进列表**——链接不能指向空处。
    """
    site = Path(site_dir)
    cards: list[VersionCard] = []

    for slug in storage.available_frameworks(root=results_root):
        if not (site / slug / INDEX_FILENAME).is_file():
            continue

        latest = storage.latest_run(slug, root=results_root)
        run: dict = {}
        self_check_ok: bool | None = None
        if latest is not None:
            archived = storage.load_run(latest)
            run = archived.get("run", {})
            self_check_ok = bool(archived.get("self_check", {}).get("ok"))

        pdf = site / slug / "report.pdf"
        cards.append(
            VersionCard(
                slug=slug,
                report_href=f"{slug}/{INDEX_FILENAME}",
                pdf_href=f"{slug}/report.pdf" if pdf.is_file() else None,
                generated_at=_format_epoch(run.get("started_at_epoch")),
                unit_count=run.get("unit_count"),
                failed_units=run.get("failed_unit_count"),
                self_check_ok=self_check_ok,
            )
        )
    return cards


def _card_html(card: VersionCard) -> str:
    facts: list[str] = []
    if card.generated_at:
        facts.append(f"<dt>测量于</dt><dd>{html_module.escape(card.generated_at)}</dd>")
    if card.unit_count is not None:
        facts.append(f"<dt>有效单元</dt><dd>{card.unit_count}</dd>")
    if card.failed_units:
        facts.append(f"<dt>失败单元</dt><dd>{card.failed_units}</dd>")
    if card.self_check_ok is not None:
        label = "自检通过" if card.self_check_ok else "自检未通过"
        css = "badge ok" if card.self_check_ok else "badge"
        # 空 dt + 非空 dd：让徽章与其它事实对齐在同一列
        facts.append(f'<dt>状态</dt><dd><span class="{css}">{label}</span></dd>')

    actions = [f'<a href="{card.report_href}">报告</a>']
    if card.pdf_href:
        actions.append(f'<a href="{card.pdf_href}">PDF</a>')

    return "\n".join(
        [
            '<article class="version-card">',
            f"<h2>{html_module.escape(card.slug)}</h2>",
            f"<dl>{''.join(facts)}</dl>",
            f'<p class="actions">{" ".join(actions)}</p>',
            "</article>",
        ]
    )


def render_site_index(cards: list[VersionCard]) -> str:
    """由卡片列表渲染首页 HTML。"""
    if cards:
        body = f'<div class="version-grid">{"".join(_card_html(card) for card in cards)}</div>'
    else:
        body = '<p class="empty">还没有任何已渲染的报告。</p>'

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_TITLE}</title>",
            f'<link rel="stylesheet" href="{STYLE_FILENAME}">',
            "</head>",
            "<body>",
            f'<header class="site-header">{_HEADER}</header>',
            body,
            "</body>",
            "</html>",
            "",
        ]
    )


def render(results_root: str | Path | None, site_dir: str | Path) -> Path:
    """写站点首页（并确保样式表在该目录下）。

    Args:
        results_root: 留档根目录。
        site_dir: 站点目录。

    Returns:
        写入的首页路径。
    """
    site = Path(site_dir)
    site.mkdir(parents=True, exist_ok=True)
    write_style(site)

    path = site / INDEX_FILENAME
    path.write_text(render_site_index(collect_cards(results_root, site)), encoding="utf-8")
    return path
