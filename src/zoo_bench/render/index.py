"""站点首页：每个已留档版本一张卡片，外加跨版本对比页的入口。

卡片上的信息（生成时间、单元数、自检状态）**从留档数据读**而不是从站点文件猜——它们正是读者
判断"这份报告是哪一轮测的、可不可信"的依据。首页放在这里而不是 CLI 里，是为了它能被用例盖到。
"""

from __future__ import annotations

import datetime
import html as html_module
import json
from dataclasses import dataclass
from pathlib import Path

from .. import storage
from .assets import STYLE_FILENAME, write_style

INDEX_FILENAME = "index.html"

#: 对比页的目录前缀与机器可读结果的文件名。两者一起定义在这里，使"谁写"（CLI 的 compare）与
#: "谁读"（本模块的索引）不会各记一套规则。
COMPARE_DIR_PREFIX = "compare-"
COMPARE_FILENAME = "compare.json"

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


@dataclass(frozen=True)
class CompareCard:
    """一个跨版本对比页的卡片。

    Attributes:
        href: 对比页的相对链接。
        before: 旧版本的显示标签；读不到为 None。
        after: 新版本的显示标签；读不到为 None。
        shared_unit_count: 两侧共有的单元数；读不到为 None。
        both_self_checks_ok: 两侧是否都通过自检；**读不到时为 None**（不是 False——"不知道"
            与"未通过"是两回事，而两者决定这份对比可不可信）。
    """

    href: str
    before: str | None
    after: str | None
    shared_unit_count: int | None
    both_self_checks_ok: bool | None


def collect_compare_cards(site_dir: str | Path) -> list[CompareCard]:
    """收集站点里的跨版本对比页。

    **按目录发现、按内容取名**：对比页是哪两个版本之间的，读它自己写下的 ``compare.json``
    （与留档同源），而不是从目录名里解析——slug 本身含连字符，解析出来的标签迟早对不上。

    Args:
        site_dir: 站点目录。

    Returns:
        卡片列表，按目录名排序。**对比页不存在的目录不进列表**——链接不能指向空处。
    """
    site = Path(site_dir)
    cards: list[CompareCard] = []
    for directory in sorted(p for p in site.glob(f"{COMPARE_DIR_PREFIX}*") if p.is_dir()):
        if not (directory / INDEX_FILENAME).is_file():
            continue

        payload: dict = {}
        source = directory / COMPARE_FILENAME
        if source.is_file():
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
        before = payload.get("before") or {}
        after = payload.get("after") or {}
        checks = [before.get("self_check_ok"), after.get("self_check_ok")]
        cards.append(
            CompareCard(
                href=f"{directory.name}/{INDEX_FILENAME}",
                before=before.get("label"),
                after=after.get("label"),
                shared_unit_count=payload.get("shared_unit_count"),
                both_self_checks_ok=(
                    None if any(value is None for value in checks) else all(checks)
                ),
            )
        )
    return cards


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


def _compare_html(card: CompareCard) -> str:
    """一个对比页卡片。标题用两侧的标签而不是目录名——目录名是给机器用的。"""
    title = (
        f"{card.before} → {card.after}"
        if card.before and card.after
        else "跨版本对比（两侧标签读不到）"
    )
    facts: list[str] = []
    if card.shared_unit_count is not None:
        facts.append(f"<dt>共有单元</dt><dd>{card.shared_unit_count}</dd>")
    if card.both_self_checks_ok is not None:
        label = "两侧自检均通过" if card.both_self_checks_ok else "有一侧自检未通过——本对比不作数"
        css = "badge ok" if card.both_self_checks_ok else "badge"
        facts.append(f'<dt>状态</dt><dd><span class="{css}">{label}</span></dd>')

    return "\n".join(
        [
            '<article class="version-card compare-card">',
            f"<h2>{html_module.escape(title)}</h2>",
            f"<dl>{''.join(facts)}</dl>",
            f'<p class="actions"><a href="{card.href}">对比</a></p>',
            "</article>",
        ]
    )


def render_site_index(cards: list[VersionCard], compares: list[CompareCard] = ()) -> str:
    """由卡片列表渲染首页 HTML。

    Args:
        cards: 版本卡片。
        compares: 跨版本对比页的卡片；没有对比页时不渲染该章节。
    """
    if cards:
        body = f'<div class="version-grid">{"".join(_card_html(card) for card in cards)}</div>'
    else:
        body = '<p class="empty">还没有任何已渲染的报告。</p>'

    if compares:
        body += (
            "<h2>跨版本对比</h2>"
            f'<div class="version-grid">{"".join(_compare_html(card) for card in compares)}</div>'
        )

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
    cards = collect_cards(results_root, site)
    path.write_text(
        render_site_index(cards, collect_compare_cards(site)), encoding="utf-8"
    )
    return path
