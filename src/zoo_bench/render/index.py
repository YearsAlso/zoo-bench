"""站点首页：每个已留档版本一张卡片，外加跨版本对比页的入口。

卡片上的信息（生成时间、单元数、自检状态）**从留档数据读**而不是从站点文件猜——它们正是读者
判断"这份报告是哪一轮测的、可不可信"的依据。首页放在这里而不是 CLI 里，是为了它能被用例盖到。

**两种语言各出一份**（design D3）：站点首页在语言树根（``index.html`` 与 ``zh/index.html``），
而版本页与对比页的中文版在**各自内容目录下的** ``zh/``（``<dir>/zh/index.html``）——每一页的
另一语言版本因此都是它的邻居，语言切换链接与深度无关。卡片链接按各自语言摆放取文件：某版本
只有单语言报告时，另一语言首页里就不出现它的卡片——链接不能指向空处。
"""

from __future__ import annotations

import datetime
import html as html_module
import json
from dataclasses import dataclass
from pathlib import Path

from .. import storage
from ..i18n import HTML_LANGS, LANG_EN, LANG_ZH, t
from . import links
from .assets import write_style
from .links import INDEX_FILENAME, PDF_FILENAME, directory_path, subtree_path

#: 对比页的目录前缀与机器可读结果的文件名。两者一起定义在这里，使"谁写"（CLI 的 compare）与
#: "谁读"（本模块的索引）不会各记一套规则。
COMPARE_DIR_PREFIX = "compare-"
COMPARE_FILENAME = "compare.json"


@dataclass(frozen=True)
class VersionCard:
    """一个版本的卡片。

    Attributes:
        slug: 版本目录名。
        report_href: 报告页链接（站点根相对路径，渲染时再按语言层深折算）。
        markdown_href: Markdown 全文链接；没有导出过则为 None。**明细在这边**——deck 为
            "一页一个结论"压缩了篇幅，逐单元数值留在全文里，故它不该是"要另外找"的东西。
        pdf_href: PDF 的链接；没有导出过则为 None。
        generated_at: 该轮测量的时间（UTC，已是可读字符串）。
        unit_count: 有效单元数。
        failed_units: 失败单元数。
        self_check_ok: 该轮自检是否通过；读不到留档时为 None。
    """

    slug: str
    report_href: str
    markdown_href: str | None
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
        href: 对比页链接（站点根相对路径，渲染时再按语言层深折算）。
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


def collect_compare_cards(site_dir: str | Path, lang: str) -> list[CompareCard]:
    """收集站点里的跨版本对比页。

    **按目录发现、按内容取名**：对比页是哪两个版本之间的，读它自己写下的 ``compare.json``
    （与留档同源、语言无关），而不是从目录名里解析——slug 本身含连字符，解析出来的标签迟早
    对不上。卡片链接按 ``lang`` 的语言树取：该语言下没有页面的目录不进列表。

    Args:
        site_dir: 站点目录。
        lang: 卡片面向的语言。

    Returns:
        卡片列表，按目录名排序。**该语言下对比页不存在的目录不进列表**——链接不能指向空处。
    """
    site = Path(site_dir)
    cards: list[CompareCard] = []
    for directory in sorted(p for p in site.glob(f"{COMPARE_DIR_PREFIX}*") if p.is_dir()):
        if not (site / directory_path(lang, directory.name, INDEX_FILENAME)).is_file():
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
                href=directory_path(lang, directory.name, INDEX_FILENAME),
                before=before.get("label"),
                after=after.get("label"),
                shared_unit_count=payload.get("shared_unit_count"),
                both_self_checks_ok=(
                    None if any(value is None for value in checks) else all(checks)
                ),
            )
        )
    return cards


def collect_cards(
    results_root: str | Path | None, site_dir: str | Path, lang: str
) -> list[VersionCard]:
    """收集首页要展示的卡片。

    Args:
        results_root: 留档根目录。
        site_dir: 站点目录（用于判断该语言下报告与 PDF 是否已渲染）。
        lang: 卡片面向的语言。

    Returns:
        卡片列表，按版本名排序。**该语言下报告页不存在的版本不进列表**——链接不能指向空处。
    """
    site = Path(site_dir)
    cards: list[VersionCard] = []

    for slug in storage.available_frameworks(root=results_root):
        page = site / directory_path(lang, slug, INDEX_FILENAME)
        if not page.is_file():
            continue

        latest = storage.latest_run(slug, root=results_root)
        run: dict = {}
        self_check_ok: bool | None = None
        if latest is not None:
            archived = storage.load_run(latest)
            run = archived.get("run", {})
            self_check_ok = bool(archived.get("self_check", {}).get("ok"))

        pdf = site / directory_path(lang, slug, PDF_FILENAME)
        markdown = site / directory_path(lang, slug, links.MARKDOWN_FILENAME)
        cards.append(
            VersionCard(
                slug=slug,
                report_href=directory_path(lang, slug, INDEX_FILENAME),
                markdown_href=directory_path(lang, slug, links.MARKDOWN_FILENAME)
                if markdown.is_file()
                else None,
                pdf_href=directory_path(lang, slug, PDF_FILENAME) if pdf.is_file() else None,
                generated_at=_format_epoch(run.get("started_at_epoch")),
                unit_count=run.get("unit_count"),
                failed_units=run.get("failed_unit_count"),
                self_check_ok=self_check_ok,
            )
        )
    return cards


def _card_html(card: VersionCard, lang: str) -> str:
    depth = links.page_depth(lang)
    facts: list[str] = []
    if card.generated_at:
        facts.append(
            f"<dt>{t('site.card.measured_at', lang)}</dt>"
            f"<dd>{html_module.escape(card.generated_at)}</dd>"
        )
    if card.unit_count is not None:
        facts.append(f"<dt>{t('site.card.units', lang)}</dt><dd>{card.unit_count}</dd>")
    if card.failed_units:
        facts.append(f"<dt>{t('site.card.failed_units', lang)}</dt><dd>{card.failed_units}</dd>")
    if card.self_check_ok is not None:
        label = (
            t("site.card.self_check.passed", lang)
            if card.self_check_ok
            else t("site.card.self_check.failed", lang)
        )
        css = "badge ok" if card.self_check_ok else "badge"
        # 空 dt + 非空 dd：让徽章与其它事实对齐在同一列
        facts.append(
            f'<dt>{t("blocks.header.status", lang)}</dt><dd><span class="{css}">{label}</span></dd>'
        )

    actions = [
        f'<a href="{links.href(depth, card.report_href)}">{t("site.card.action.report", lang)}</a>'
    ]
    if card.markdown_href:
        actions.append(
            f'<a href="{links.href(depth, card.markdown_href)}">{t("site.card.action.markdown", lang)}</a>'
        )
    if card.pdf_href:
        actions.append(f'<a href="{links.href(depth, card.pdf_href)}">PDF</a>')

    return "\n".join(
        [
            '<article class="version-card">',
            f"<h2>{html_module.escape(card.slug)}</h2>",
            f"<dl>{''.join(facts)}</dl>",
            f'<p class="actions">{" ".join(actions)}</p>',
            "</article>",
        ]
    )


def _compare_html(card: CompareCard, lang: str) -> str:
    """一个对比页卡片。标题用两侧的标签而不是目录名——目录名是给机器用的。"""
    title = (
        f"{card.before} → {card.after}"
        if card.before and card.after
        else t("site.compare.unlabelled", lang)
    )
    facts: list[str] = []
    if card.shared_unit_count is not None:
        facts.append(
            f"<dt>{t('site.compare.shared_units', lang)}</dt><dd>{card.shared_unit_count}</dd>"
        )
    if card.both_self_checks_ok is not None:
        label = (
            t("site.compare.both_passed", lang)
            if card.both_self_checks_ok
            else t("site.compare.one_failed", lang)
        )
        css = "badge ok" if card.both_self_checks_ok else "badge"
        facts.append(
            f'<dt>{t("blocks.header.status", lang)}</dt><dd><span class="{css}">{label}</span></dd>'
        )

    return "\n".join(
        [
            '<article class="version-card compare-card">',
            f"<h2>{html_module.escape(title)}</h2>",
            f"<dl>{''.join(facts)}</dl>",
            f'<p class="actions"><a href="{links.href(links.page_depth(lang), card.href)}">'
            f"{t('site.compare.action', lang)}</a></p>",
            "</article>",
        ]
    )


def render_site_index(
    cards: list[VersionCard], compares: list[CompareCard] = (), *, lang: str
) -> str:
    """由卡片列表渲染首页 HTML。

    Args:
        cards: 版本卡片。
        compares: 跨版本对比页的卡片；没有对比页时不渲染该章节。
        lang: 首页语言——决定 ``<html lang>``、全部文案与语言切换链接。
    """
    if cards:
        body = (
            f'<div class="version-grid">{"".join(_card_html(card, lang) for card in cards)}</div>'
        )
    else:
        body = f'<p class="empty">{t("site.index.empty", lang)}</p>'

    if compares:
        body += (
            f"<h2>{t('site.compare.title', lang)}</h2>"
            f'<div class="version-grid">{"".join(_compare_html(card, lang) for card in compares)}</div>'
        )

    switch = (
        f'<nav class="lang-switch"><a href="{links.language_switch_href(lang)}">'
        f"{links.language_switch_label(lang)}</a></nav>"
    )
    return "\n".join(
        [
            "<!doctype html>",
            f'<html lang="{HTML_LANGS[lang]}">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{t('site.index.title', lang)}</title>",
            f'<link rel="stylesheet" href="{links.stylesheet_href(lang)}">',
            "</head>",
            "<body>",
            f'<header class="site-header">{switch}'
            f"<h1>{t('site.index.title', lang)}</h1>"
            f"<p>{t('site.index.tagline', lang)}</p></header>",
            body,
            "</body>",
            "</html>",
            "",
        ]
    )


def render(results_root: str | Path | None, site_dir: str | Path) -> Path:
    """写两种语言的站点首页（并确保样式表在站点根下）。

    英文首页在站点根、中文首页在 ``zh/`` 子目录；中文页的样式表走 ``../style.css``——样式表
    只有一份，两个语言树共用。报告与对比页由各自命令写，本函数只保证首页链接不指向空处。

    Args:
        results_root: 留档根目录。
        site_dir: 站点目录。

    Returns:
        英文首页路径（站点根 ``index.html``——英文为主，design D3）。
    """
    site = Path(site_dir)
    site.mkdir(parents=True, exist_ok=True)
    write_style(site)

    (site / subtree_path(LANG_ZH)).mkdir(parents=True, exist_ok=True)
    for lang in (LANG_EN, LANG_ZH):
        cards = collect_cards(results_root, site, lang)
        page = site / subtree_path(lang, INDEX_FILENAME)
        page.write_text(
            render_site_index(cards, collect_compare_cards(site, lang), lang=lang),
            encoding="utf-8",
        )
    return site / INDEX_FILENAME
