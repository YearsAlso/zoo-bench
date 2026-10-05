"""deck 形态的报告页：一页一个结论，数值直接编码成条形。

**为什么要这一层**：长文档适合核查，不适合三十秒内知道结论。deck 把"一页一个结论"变成结构约束
——一个章节开一页，正文按行数上限切分，切不完的分页续排，**不按重要性截断**。

**同源**（design D1）：文字取自 :func:`zoo_bench.render.blocks.build_blocks` 的块，与 Markdown /
PDF 逐字一致；条形图取自模型里的**原始数值**——块的表格里已经是格式化后的字符串，从字符串反解
数值等于把格式当数据，单位一改就静默取错。故本模块的入参是 ``(model, blocks)``，由
:func:`zoo_bench.render.report.render` 同一次调用传入。

**框架块原样**（design D3）：``deck_framework.css`` / ``deck_chrome.html`` /
``deck_framework.js`` 只做拼装，不重写——它们是设计交付物，改了就没有语法高亮可 review，也与
设计稿对不上了。
"""

from __future__ import annotations

import datetime
import html as html_module
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .assets import (
    DECK_CHROME_HTML,
    DECK_FRAMEWORK_CSS,
    DECK_FRAMEWORK_JS,
    DECK_SLIDES_CSS,
    deck_asset,
)
from .blocks import (
    APPENDIX_SECTION_PREFIX,
    BULLETS,
    CHART_SECTION_TITLE,
    CONCLUSION_SECTION_TITLE,
    DIMENSION_SECTION_PREFIX,
    HEADING,
    IMAGE,
    NOTE,
    PARAGRAPH,
    TABLE,
    UNFAVORABLE_SECTION_TITLE,
    Block,
    format_ratio,
    format_seconds,
    repository_relative_path,
)
from .html import inline

#: 每页正文的行数上限。**这是硬约束，不是排版偏好**：``.deck-stage`` 与 ``.slide`` 都设了
#: ``overflow: hidden``，超出的部分不会报错、只会静默消失——而"少了一截"恰恰是最难被发现的那种
#: 错。故正文按此上限切分，任何**不可再切**的单块（如一个超长段落）超过它就抛
#: :class:`DeckOverflowError`。
SLIDE_ROW_CAP = 14

#: 估算一段文本占几行：正文约 19px、内容宽约 1680px，中文约 76 字/行；取 64 留余量。
#: 英文与数字更窄，故宁可高估——高估只是多分一页，低估会被裁掉。
_CHARS_PER_ROW = 64

#: 每个章节在 deck 里最多占几页。**deck 的价值是压缩，不是把文档重新分页**：不设上限时实测一份
#: 报告会摊成 57 页（结论摘要 8 页、开销归因 12 页），读者比读全文还慢。超出的明细由一页指引
#: 送去全文——**指引页点名了送去哪一章**，故不是"把东西藏起来"。
SLIDES_PER_SECTION = 2

#: 不设上限的章节：不利数据在 deck 里必须**覆盖完整**（spec 的「不利数据不因版面而变小」），
#: 分页续排到底、不截断。
_UNLIMITED_SECTIONS = frozenset({UNFAVORABLE_SECTION_TITLE})

#: 单段散文最多占几行。留出余量给标题与页脚，故小于整页上限。
_PROSE_ROWS = SLIDE_ROW_CAP - 4

#: 断句点：中文句读与 ASCII 句末标点。切在这里读起来是"接着上一句"，不是"断在半句话上"。
_SENTENCE_BREAK = re.compile(r"(?<=[。；！？;!?])")

#: deck 自己的补充样式（在逐页样式之后）。只放逐页样式块里没有的类。
_EXTRA_CSS = """
    /* ── deck 渲染器补充（逐页样式的槽位）──── */
    .deck-text { font-size: 19px; line-height: 1.75; margin-top: 20px; max-width: 74ch; }
    .deck-sub { font-size: 24px; font-weight: 700; line-height: 1.4; margin-top: 28px; }
    .deck-note {
      font-size: 17px; line-height: 1.75; color: var(--muted); margin-top: 20px;
      border-left: 2px solid var(--border-c); padding-left: 18px; max-width: 74ch;
    }
    .deck-list { margin-top: 16px; max-width: 76ch; }
    .deck-list li {
      font-size: 18px; line-height: 1.7; list-style: none;
      padding-left: 22px; position: relative; margin-top: 10px;
    }
    .deck-list li::before { content: "·"; position: absolute; left: 6px; color: var(--accent); }
    .deck-table { width: 100%; border-collapse: collapse; margin-top: 28px; }
    .deck-table th, .deck-table td {
      text-align: left; font-size: 14px; line-height: 1.5;
      padding: 6px 10px; border-bottom: 1px solid var(--border-c); vertical-align: top;
    }
    .deck-table th { font-weight: 700; white-space: nowrap; }
    /* 大数字的字号是 300px、行高 1——那是给模板自带的 Berkeley Mono 定的。换到回退字体后
       行盒装不下字形的上下沿，实测在核心结论页量到 25px 的内容落在盒子外（页面 overflow:hidden，
       也就是被裁掉）。故留出余量，不让它贴着边。 */
    .stat-num { line-height: 1.2; }
"""


class DeckOverflowError(RuntimeError):
    """一页装不下且**无法再切**。

    刻意抛而不是让版面裁掉：裁掉的结果是文件照样生成、数字照样在，只是少了一截——读者不会看到
    "这里本该还有内容"。宁可渲染失败。
    """


@dataclass(frozen=True)
class Slide:
    """一张幻灯片。

    Attributes:
        label: ``data-screen-label`` 的文字部分（序号由渲染时统一加，见 :func:`render_deck`）。
        tone: ``light`` / ``dark``；留空表示按位置交替。
        layout: 追加的版面类（``hero`` / ``center``）。
        eyebrow: 标题上方的小字。
        heading: 大标题；其中的换行渲染成 ``<br>``（封面用）。
        heading_class: 标题字号类（封面用 ``h-hero``，正文用 ``h-md``）。
        heading_tag: 标题标签名（封面用 ``h1``）。
        lead: 标题下方的引言段。
        body: 正文 HTML（已转义并已成型）。
        source: 页脚 `.src` 的数据出处。
        notes: 演讲备注（页面上隐藏）。
    """

    label: str
    tone: str = ""
    layout: str = ""
    eyebrow: str = ""
    heading: str = ""
    heading_class: str = "h-md"
    heading_tag: str = "h2"
    lead: str = ""
    body: str = ""
    source: str = ""
    notes: str = ""


@dataclass(frozen=True)
class _Piece:
    """一段可整块放进一页的内容。

    Attributes:
        rows: 占几行（见 :data:`SLIDE_ROW_CAP`）。
        html: 已渲染的 HTML。
        list_item: 是列表项——同一页里连续的行内项要裹进同一个 ``<ul>``。
    """

    rows: int
    html: str
    list_item: bool = False


@dataclass(frozen=True)
class _Section:
    """块列表里的一段：一个一级/二级标题及其后的块。"""

    heading: Block
    blocks: tuple[Block, ...]

    @property
    def title(self) -> str:
        return self.heading.text


@dataclass(frozen=True)
class _ChartSpec:
    """一个度量维度的条形图口径。

    Attributes:
        numeric: 取用于画条的数值（秒 / 任务每秒）。
        display: 条形旁打印的文字（含单位）——条形只表达相对长短，精度由它承担。
        higher_is_better: 数值越大越好（吞吐）。**决定倍数怎么算**：条形一律"越长越差"。
        legend: 该维度这一列是什么的名目。
    """

    numeric: Callable[[dict[str, Any]], float | None]
    display: Callable[[dict[str, Any]], str]
    higher_is_better: bool
    legend: str


def _format_rate(value: float | None) -> str:
    """吞吐 -> 任务/秒。带千位分隔，因为这一列从 5,673 到 319,989。"""
    return "—" if value is None else f"{value:,.0f} 任务/秒"


def _latency_median(row: dict[str, Any]) -> float | None:
    """延迟维度里的端到端是**统计摘要**（中位数、分位数、离散度），画条取中位数。

    与块层的透视表同一口径——两处取不同的统计量，同一个维度就会在 deck 与全文里出现两个数。
    """
    summary = row.get("end_to_end_per_task")
    return float(summary["median"]) if isinstance(summary, dict) else summary


_CHART_SPECS: dict[str, _ChartSpec] = {
    "latency": _ChartSpec(
        numeric=_latency_median,
        display=lambda row: format_seconds(_latency_median(row)),
        higher_is_better=False,
        legend="端到端 / 并发度",
    ),
    "overhead": _ChartSpec(
        numeric=lambda row: row.get("framework_overhead_seconds"),
        display=lambda row: format_ratio(row.get("framework_overhead_ratio")),
        higher_is_better=False,
        legend="框架自身开销",
    ),
    "throughput": _ChartSpec(
        numeric=lambda row: row.get("throughput_per_second"),
        display=lambda row: _format_rate(row.get("throughput_per_second")),
        higher_is_better=True,
        legend="吞吐",
    ),
}


def _paragraph_rows(text: str) -> int:
    return max(2, -(-len(text) // _CHARS_PER_ROW))


def _split_text(text: str, limit: int) -> list[str]:
    """把一段散文切到每片不超过 ``limit`` 行。

    **切不动也要切**：被版面裁掉的部分不报错，而"告警文案长了一点"是迟早会出现的事。故先按句读
    切，单句仍超限就硬切——对中文来说硬切是安全的（字与字之间都能断行）。
    """
    budget = max(1, limit) * _CHARS_PER_ROW
    if len(text) <= budget:
        return [text]

    chunks: list[str] = []
    current = ""
    for segment in _SENTENCE_BREAK.split(text):
        if not segment:
            continue
        if len(segment) > budget:
            if current:
                chunks.append(current)
                current = ""
            chunks += [segment[i : i + budget] for i in range(0, len(segment), budget)]
        elif len(current) + len(segment) > budget:
            chunks.append(current)
            current = segment
        else:
            current += segment
    if current:
        chunks.append(current)
    return chunks


def _sections(blocks: list[Block]) -> list[_Section]:
    """按一级/二级标题把块列表切成段。"""
    sections: list[_Section] = []
    heading: Block | None = None
    pending: list[Block] = []
    for block in blocks:
        if block.kind == HEADING and block.level <= 2:
            if heading is not None:
                sections.append(_Section(heading, tuple(pending)))
            heading, pending = block, []
            continue
        if heading is None:
            # 首个标题之前的内容如实保留，不静默丢弃（当前块列表不会走到这里）
            heading = Block(HEADING, text="", level=2)
        pending.append(block)
    if heading is not None:
        sections.append(_Section(heading, tuple(pending)))
    return sections


def _table_pieces(block: Block, lead: tuple[_Piece, ...] = ()) -> list[_Piece]:
    """一张表按行切成若干块，**每块各带表头**——续页没有表头就没法读。

    ``lead`` 是紧跟在这张表之前的说明（见 :func:`_section_pieces`）：它与表的第一片**并成
    一个不可分片段**，否则贪心装箱会把说明单独留在一页、把表推到下一页——而那句"带 — 的格
    是什么意思"正是给这张表看的，分到两页等于让读者猜。
    """
    head = "".join(f"<th>{inline(header)}</th>" for header in block.headers)
    lead_rows = sum(piece.rows for piece in lead)
    lead_html = "".join(piece.html for piece in lead)

    rows = list(block.rows)
    pieces: list[_Piece] = []
    start = 0
    while start < len(rows):
        # 第一片给前面的说明留出位置，其后各片吃满整页
        is_first = not pieces
        capacity = max(1, SLIDE_ROW_CAP - 1 - (lead_rows if is_first else 0))
        chunk = rows[start : start + capacity]
        start += len(chunk)
        body = "".join(
            "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>" for row in chunk
        )
        table = (
            f'<table class="deck-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
        )
        pieces.append(
            _Piece(
                rows=len(chunk) + 1 + (lead_rows if is_first else 0),
                html=lead_html + table if is_first else table,
            )
        )
    return pieces


def _section_pieces(blocks: tuple[Block, ...]) -> list[_Piece]:
    """一节的块 -> 片段；说明与其后的表并成一片（见 :func:`_table_pieces`）。"""
    pieces: list[_Piece] = []
    lead: list[_Piece] = []
    for block in blocks:
        if block.kind == TABLE:
            # 说明太长时**不并**：并进去只会让"说明 + 表"这一片撑破上限而抛。那种说明本身就
            # 已是一页的量，让它独占一页反而读得顺。
            if sum(piece.rows for piece in lead) <= SLIDE_ROW_CAP - 3:
                pieces += _table_pieces(block, tuple(lead))
            else:
                pieces += lead
                pieces += _table_pieces(block)
            lead = []
            continue
        if block.kind in (PARAGRAPH, NOTE):
            # 先攒着，它后面若紧跟一张表就与表同页；否则下一轮照常入列
            lead += _pieces(block)
            continue
        pieces += lead
        lead = []
        pieces += _pieces(block)
    return pieces + lead


def _pieces(block: Block) -> list[_Piece]:
    """把一个块拆成可排进版面的片段。"""
    if block.kind == PARAGRAPH:
        return [
            _Piece(_paragraph_rows(part), f'<p class="deck-text">{inline(part)}</p>')
            for part in _split_text(block.text, _PROSE_ROWS)
        ]
    if block.kind == NOTE:
        return [
            _Piece(_paragraph_rows(part), f'<p class="deck-note">{inline(part)}</p>')
            for part in _split_text(block.text, _PROSE_ROWS)
        ]
    if block.kind == BULLETS:
        return [
            _Piece(_paragraph_rows(part), f"<li>{inline(part)}</li>", list_item=True)
            for item in block.items
            for part in _split_text(item, _PROSE_ROWS)
        ]
    if block.kind == TABLE:
        return _table_pieces(block)
    if block.kind == HEADING:
        return [_Piece(1, f'<h3 class="deck-sub">{inline(block.text)}</h3>')]
    if block.kind == IMAGE:
        # 图表章不出一页：deck 用条形自己编码同一批数值（见 _chart_slides）。
        # 图片仍留在 Markdown / PDF 与 figures/ 里，故不是"把图丢了"。
        return []
    return []


def _chunk(pieces: list[_Piece], cap: int = SLIDE_ROW_CAP) -> list[list[_Piece]]:
    """把片段按行数上限切成若干页的量。

    Raises:
        DeckOverflowError: 单个片段本身就超过上限（不可再切）。
    """
    for piece in pieces:
        if piece.rows > cap:
            raise DeckOverflowError(
                f"单块内容有 {piece.rows} 行、超过每页上限 {cap} 行，且无法再切："
                f"{html_module.escape(piece.html[:60])}…"
                "\n需要调小它的粒度（如分段）或调大 SLIDE_ROW_CAP。"
            )

    chunks: list[list[_Piece]] = []
    current: list[_Piece] = []
    used = 0
    for piece in pieces:
        if current and used + piece.rows > cap:
            chunks.append(current)
            current, used = [], 0
        current.append(piece)
        used += piece.rows
    if current:
        chunks.append(current)
    return chunks


def _chunk_html(pieces: list[_Piece]) -> str:
    """渲染一页的正文，连续的行内项裹进同一个列表。"""
    parts: list[str] = []
    pending: list[str] = []
    for piece in pieces:
        if piece.list_item:
            pending.append(piece.html)
            continue
        if pending:
            parts.append(f'<ul class="deck-list">{"".join(pending)}</ul>')
            pending = []
        parts.append(piece.html)
    if pending:
        parts.append(f'<ul class="deck-list">{"".join(pending)}</ul>')
    return "\n".join(parts)


def _format_date(epoch: Any) -> str | None:
    """留档时间 -> 日期。封面只放到日：那一页的三件事是"哪一版、哪一天、哪台机器"。"""
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(float(epoch), datetime.UTC).strftime("%Y-%m-%d")


def _subject_version(model: dict[str, Any]) -> str | None:
    environment = model.get("environment") or {}
    return (environment.get("subject") or {}).get("dist_version")


def _title(model: dict[str, Any]) -> str:
    version = _subject_version(model)
    return f"zoo-framework {version} 性能评测" if version else "zoo-framework 性能评测"


def _cover_heading(model: dict[str, Any]) -> str:
    """封面的标题分两行（型号一行、评测名一行）——一行摆不下时字会缩到看不清。"""
    version = _subject_version(model)
    return f"zoo-framework {version}\n性能评测" if version else "zoo-framework\n性能评测"


def _cover_slide(model: dict[str, Any], subject: str | None) -> Slide:
    environment = model.get("environment") or {}
    hardware = environment.get("hardware") or {}
    run = model.get("run") or {}
    source = model.get("source") or {}

    adapters = {
        row["adapter"]
        for dimension in (model.get("dimensions") or {}).values()
        if isinstance(dimension, dict)
        for row in dimension.get("rows", [])
    }
    others = sorted(adapter for adapter in adapters if adapter != subject)
    eyebrow = " · ".join(
        part
        for part in (
            "zoo-bench",
            _format_date(run.get("started_at_epoch")),
            hardware.get("cpu_model"),
        )
        if part
    )

    return Slide(
        label="封面",
        tone="dark",
        layout="hero center",
        eyebrow=eyebrow,
        heading=_cover_heading(model),
        heading_class="h-hero",
        heading_tag="h1",
        # 没有对照方案时如实说"这份报告撑不起对比"，而不是拼出"与  共 0 个"这种空话
        lead=(
            f"与 {'、'.join(others)} 共 {len(others)} 个对照方案的同口径对比；"
            "全部数值由 zoo-bench 在本机重新测量、逐档公开，包括本框架输掉的档位。"
            if others
            else "本轮的对照方案清单为空：只有被测框架自己的数，不足以支撑横向对比。"
        ),
        source=(
            f"原始数据：`{repository_relative_path(str(source['path']))}`"
            if source.get("path")
            else "原始数据出处未记录"
        ),
        notes="开场：强调同口径、可复现，为后面所有数字建立可信度。",
    )


def _content_slides(section: _Section) -> list[Slide]:
    """一个章节 -> 一到多页；超过每节上限的部分由一页指引送去全文。"""
    pieces = _section_pieces(section.blocks)
    if not pieces:
        return []

    chunks = _chunk(pieces)
    limit = len(chunks) if section.title in _UNLIMITED_SECTIONS else SLIDES_PER_SECTION
    truncated = len(chunks) > limit

    slides: list[Slide] = []
    for index, chunk in enumerate(chunks[:limit]):
        suffix = "" if index == 0 else "（续）"
        slides.append(
            Slide(
                label=f"{section.title}{suffix}" or "正文",
                eyebrow=section.title if index else "zoo-bench 报告",
                heading=f"{section.title}{suffix}" if section.title else "",
                body=_chunk_html(chunk),
                source=f"数据：{section.title}",
            )
        )
    if truncated:
        slides.append(_pointer_slide_for(section, len(chunks) - limit))
    return slides


def _pointer_slide_for(section: _Section, dropped: int) -> Slide:
    """本章装不下的那部分明细去哪找——**点名章节**，不写"见报告其余部分"。"""
    return Slide(
        label=f"{section.title}·明细指引",
        eyebrow=section.title,
        heading="本章其余明细在全文里",
        lead=(
            f"幻灯片为了「一页一个结论」只放了本章的前 "
            f"{SLIDES_PER_SECTION} 页；余下 {dropped} 页的逐单元数值、分位数与原始样本"
            "都在全文的同一章节里，逐字可核。"
        ),
        source=f"全文：同目录 `report.md` 的「{section.title}」章节",
        notes="被压缩的只有排版，没有数值。",
    )


def _multiple(spec: _ChartSpec, value: float, base: float) -> float:
    """条形长度：**一律"越长越差"**，故被测框架自己恒为 1.0。

    延迟与开销越小越好（倍数 = 对照 / 被测框架），吞吐越大越好（倍数 = 被测框架 / 对照）。
    两个方向统一成同一个读法，读者不必按图换算方向。
    """
    if value <= 0 or base <= 0:
        return 0.0
    return value / base if not spec.higher_is_better else base / value


@dataclass(frozen=True)
class _BarGroup:
    """一档执行体下的一组条形。"""

    label: str
    bars: tuple[tuple[float, str, str], ...]  # (倍数, 方案名, 打印的数值)


def _nice_max(value: float) -> float:
    """把纵轴上限抬到一位小数的整数上——柱高用 `calc(var(--v) / var(--max) * 100%)`，上限太贴近
    峰值会让最高的那根顶满，看不出它是不是还有余量。"""
    return max(1.0, math.ceil(value * 10) / 10)


def _bar_html(name: str, value_text: str, multiple: float, subject: str) -> str:
    css = "bar-zoo" if name == subject else "bar-ra"
    return (
        f'<div class="bar-row"><span class="bar-label">{inline(name)}</span>'
        f'<div class="bar-track"><div class="bar {css}" style="--v: {multiple:.4f}"></div></div>'
        f'<span class="bar-value">{inline(value_text)}</span></div>'
    )


def _concurrency_of(rows: list[dict[str, Any]]) -> int:
    """取"可读行最多"的并发度（并列取小）。**不取最有利的那个**：那是挑数据。"""
    counts = Counter(int(row["concurrency"]) for row in rows)
    top = max(counts.values())
    return min(concurrency for concurrency, count in counts.items() if count == top)


def _chart_groups(rows: list[dict[str, Any]], spec: _ChartSpec, subject: str) -> list[_BarGroup]:
    """把同一并发度下的行按档位分组；**组内被测框架排最前**（读者拿它比别的）。"""
    by_key = {(row["adapter"], row["body_tier_us"]): row for row in rows}
    groups: list[_BarGroup] = []
    for tier in sorted({row["body_tier_us"] for row in rows}):
        base_row = by_key.get((subject, tier))
        base = spec.numeric(base_row) if base_row else None
        if not base:
            # 没有被测框架这一格就无从谈"相对倍数"，故该档位不入图（而不是编一个基线）
            continue
        members = sorted(
            (row for row in rows if row["body_tier_us"] == tier),
            key=lambda row: (row["adapter"] != subject, row["adapter"]),
        )
        bars = tuple(
            (multiple, row["adapter"], spec.display(row))
            for row in members
            if (value := spec.numeric(row)) is not None
            and (multiple := _multiple(spec, value, base))
        )
        if bars:
            groups.append(_BarGroup(f"执行体 {tier:g} 微秒", bars))
    return groups


def _chart_slides(
    subject: str | None, key: str, dimension: dict[str, Any], spec: _ChartSpec
) -> list[Slide]:
    """一个度量维度的条形图：一个并发度、全部方案、全部档位，装不下就续排。"""
    if subject is None:
        return []
    rows = [row for row in dimension.get("rows", []) if spec.numeric(row) is not None]
    if not rows:
        return []

    concurrency = _concurrency_of(rows)
    rows = [row for row in rows if int(row["concurrency"]) == concurrency]
    groups = _chart_groups(rows, spec, subject)
    if not groups:
        return []

    # 分组按行数切：一组 = 1 行档位标签 + 每根条形 1 行。**组本身也要能切**——对照方案加到
    # 十几个时，一组就装不下一页，那时该分页而不是失败。
    expanded: list[_BarGroup] = []
    for group in groups:
        width = SLIDE_ROW_CAP - 1
        if len(group.bars) <= width:
            expanded.append(group)
            continue
        parts = [group.bars[i : i + width] for i in range(0, len(group.bars), width)]
        expanded += [
            _BarGroup(f"{group.label}（{index}/{len(parts)}）", part)
            for index, part in enumerate(parts, start=1)
        ]

    chunked: list[list[_BarGroup]] = []
    current: list[_BarGroup] = []
    used = 0
    for group in expanded:
        size = 1 + len(group.bars)
        if current and used + size > SLIDE_ROW_CAP:
            chunked.append(current)
            current, used = [], 0
        current.append(group)
        used += size
    if current:
        chunked.append(current)

    withheld = len(dimension.get("rows", [])) - len(
        [row for row in dimension.get("rows", []) if spec.numeric(row) is not None]
    )
    title = str(dimension.get("title", key))

    slides: list[Slide] = []
    for index, groups_in_slide in enumerate(chunked):
        cap = _nice_max(max(multiple for group in groups_in_slide for multiple, _, _ in group.bars))
        lines = [
            f'<p class="eyebrow">并发度 {concurrency} · {spec.legend}</p>',
            f'<div class="chart" style="--max: {cap:g}">',
        ]
        for group in groups_in_slide:
            lines.append(f'<div class="cg-label">{inline(group.label)}</div>')
            for multiple, name, value_text in group.bars:
                lines.append(_bar_html(name, value_text, multiple, subject))
        lines.append("</div>")
        lines.append(
            '<div class="legend">'
            '<span class="legend-chip"><i class="c-zoo"></i>被测框架</span>'
            '<span class="legend-chip"><i class="c-ra"></i>对照方案</span>'
            '<span class="legend-chip">条形 = 相对被测框架的倍数，1.0 为持平，越长越差</span>'
            "</div>"
        )
        suffix = "" if index == 0 else "（续）"
        notes = ["条形只表达相对长短，每根旁边的数字才是该点的实测值。"]
        if withheld:
            notes.append(f"另有 {withheld} 个单元格无可读开销，未入图（见口径局限）。")
        slides.append(
            Slide(
                label=f"{title}{suffix}",
                eyebrow=f"{DIMENSION_SECTION_PREFIX}{title}",
                heading=f"{title}：并发度 {concurrency}{suffix}",
                body="\n".join(lines),
                source=(
                    f"数据：zoo-bench「{title}」· 并发度 {concurrency} · "
                    "逐单元数值与分位数见 report.md 的同一章节"
                ),
                notes=" ".join(notes),
            )
        )
    return slides


def _stat_slides(model: dict[str, Any], subject: str | None) -> list[Slide]:
    """核心结论的大数字：**最大的执行体档位**上，被测框架的开销占比。

    取最大档位不是挑对自己有利的数——报告自己的选型框架就是"多大的执行体时长下开销才可忽略"
    （见结论摘要的 `note`），最大档位正是该问题的另一端；且标题、页脚与同档对照都写明了是哪个
    档位、哪个并发度。**还必给一句同档结论**（更低的有几家）——大字本身不带方向，不写这一句
    会被读成"这就是最低的那个"。
    """
    if subject is None:
        return []
    rows = [
        row
        for row in model.get("dimensions", {}).get("overhead", {}).get("rows", [])
        if row.get("framework_overhead_ratio") is not None
    ]
    if not rows:
        return []

    concurrency = min(int(row["concurrency"]) for row in rows)
    at_concurrency = [row for row in rows if int(row["concurrency"]) == concurrency]
    tier = max(row["body_tier_us"] for row in at_concurrency)
    same_cell = [row for row in at_concurrency if row["body_tier_us"] == tier]
    subject_row = next((row for row in same_cell if row["adapter"] == subject), None)
    if subject_row is None:
        return []

    ratio = float(subject_row["framework_overhead_ratio"])
    rivals = sorted(
        (row for row in same_cell if row["adapter"] != subject),
        key=lambda row: row["adapter"],
    )
    comparison = " · ".join(
        f"{row['adapter']} {format_ratio(row['framework_overhead_ratio'])}" for row in rivals
    )
    # **同档的结论必须和大数字一起给**：一个 300px 的数字单摆着，读者会读成"这就是最低的那个"。
    # 实测该档位有三家比被测框架更低，故这一句不是客套而是纠偏。
    lower = [row for row in rivals if float(row["framework_overhead_ratio"]) < ratio]
    verdict = (
        "该档位被测框架的占比最低。"
        if not lower
        else f"该档位有 {len(lower)} 个方案更低："
        + "、".join(str(row["adapter"]) for row in lower)
        + "。"
    )
    caption = f"执行体 {tier:g} 微秒 时，{subject} 的框架自身开销占比。<br>{inline(verdict)}" + (
        f'<br><span class="dim">同档对比：{inline(comparison)}</span>' if comparison else ""
    )

    return [
        Slide(
            label="核心结论",
            layout="hero center",
            eyebrow=f"{CONCLUSION_SECTION_TITLE} · 框架自身开销占比",
            heading="",
            body=(
                f'<div class="stat-num">{ratio * 100:.2f}<span class="unit">%</span></div>'
                f'<p class="stat-caption">{caption}</p>'
            ),
            source=(
                f"数据：zoo-bench「框架自身开销占比」· 并发度 {concurrency} · "
                f"执行体 {tier:g} 微秒 档"
            ),
            notes="取所测最大的执行体档位：报告自己的选型框架就是「多大的任务才划算」。",
        )
    ]


def _unfavorable_summary_slides(model: dict[str, Any]) -> list[Slide]:
    """劣势的**归类页**：按是哪个对照方案赢的归组，点出最差的那一格。"""
    items = (model.get("unfavorable") or {}).get("items") or []
    if not items:
        return []

    by_baseline: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_baseline.setdefault(str(item["baseline"]), []).append(item)

    cards: list[str] = []
    for baseline, rows in sorted(by_baseline.items()):
        worst = min(rows, key=lambda row: float(row["baseline_ratio_vs_subject"]))
        gap = 1 / float(worst["baseline_ratio_vs_subject"])
        cells = "、".join(
            f"并发 {int(row['concurrency'])} · {row['body_tier_us']:g} 微秒"
            for row in sorted(rows, key=lambda row: (int(row["concurrency"]), row["body_tier_us"]))
        )
        cards.append(
            '<div class="pt">'
            f"<h3>{inline(baseline)} · {len(rows)} 个档位更快</h3>"
            f'<p>最差 <span class="num">{gap:.2f}x</span>（并发 {int(worst["concurrency"])} · '
            f"{worst['body_tier_us']:g} 微秒）。全部档位：{inline(cells)}</p>"
            "</div>"
        )

    return [
        Slide(
            label="劣势归类",
            layout="hero",
            eyebrow=f"{UNFAVORABLE_SECTION_TITLE} · 归类",
            heading=f"被测框架在 {len(items)} 个档位上更慢，来自 {len(by_baseline)} 个对照方案。",
            body=f'<div class="pt-grid">{"".join(cards)}</div>',
            source="数据：zoo-bench「公开的不利数据」· 逐档明细见其后的页与 report.md",
            notes="先把劣势归纳清楚，再逐档列出——不按重要性截断。",
        )
    ]


def _pointer_slides(model: dict[str, Any]) -> list[Slide]:
    """收尾：deck 装不下的明细去哪找，说清楚。"""
    run = model.get("run") or {}
    source = model.get("source") or {}
    items = [
        "逐单元明细（分位数、离散度、每轮原始样本）：同目录的 `report.md`",
        "图表（SVG 与 PNG）：同目录的 `figures/`，另见 `report.pdf`",
    ]
    if source.get("path"):
        items.append(f"原始留档：`{repository_relative_path(str(source['path']))}`")
    if run.get("unit_count") is not None:
        items.append(f"本轮有效单元 {run['unit_count']}，失败单元 {run.get('failed_unit_count')}")

    return [
        Slide(
            label="全文与原始数据",
            tone="dark",
            layout="center",
            eyebrow="附录",
            heading="每一页都可在全文里逐字复核",
            lead="幻灯片为了「一页一个结论」而压缩了篇幅；被压缩的只有排版，没有数值。",
            body=(
                '<ul class="deck-list">'
                + "".join(f"<li>{inline(item)}</li>" for item in items)
                + "</ul>"
            ),
            source="明细有一百多行，压进幻灯片等于让读者失去核对的手段。",
        )
    ]


def _render_slide(slide: Slide, *, index: int) -> str:
    tone = slide.tone or ("dark" if index % 2 else "light")
    classes = " ".join(part for part in ("slide", tone, slide.layout) if part)
    label = html_module.escape(f"{index + 1:02d} {slide.label}", quote=True)

    lines = [
        f'<section class="{classes}" data-screen-label="{label}" data-od-id="slide-{index + 1:02d}">'
    ]
    if slide.eyebrow:
        lines.append(f'<p class="eyebrow">{inline(slide.eyebrow)}</p>')
    if slide.heading:
        # 标题里的换行渲染成 <br>：封面的「型号 / 评测名」两行就是这么来的
        heading = "<br>".join(inline(part) for part in slide.heading.split("\n"))
        lines.append(f'<{slide.heading_tag} class="{slide.heading_class}">{heading}</{slide.heading_tag}>')
    if slide.lead:
        lines.append(f'<p class="lead">{inline(slide.lead)}</p>')
    if slide.body:
        lines.append(slide.body)
    if slide.source:
        lines.append(f'<p class="src">{inline(slide.source)}</p>')
    if slide.notes:
        lines.append(f'<aside class="notes">{inline(slide.notes)}</aside>')
    lines.append("</section>")
    return "\n".join(lines)


def _assemble(slides: list[Slide], title: str) -> str:
    """把幻灯片、框架块与逐页样式拼成一份完整的 deck 文档。"""
    body = "\n".join(_render_slide(slide, index=index) for index, slide in enumerate(slides))
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN" data-od-deck-protocol="1">',
            "<head>",
            '<meta charset="utf-8" />',
            '<meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"<title>{html_module.escape(title)}</title>",
            "<style>",
            deck_asset(DECK_FRAMEWORK_CSS),
            "</style>",
            "<style>",
            deck_asset(DECK_SLIDES_CSS),
            _EXTRA_CSS.rstrip("\n"),
            "</style>",
            "</head>",
            "<body>",
            '<div class="deck-shell">',
            '<div class="deck-stage" id="deck-stage">',
            "",
            body,
            "",
            "</div>",
            "</div>",
            "",
            deck_asset(DECK_CHROME_HTML),
            "<script>",
            deck_asset(DECK_FRAMEWORK_JS),
            "</script>",
            "</body>",
            "</html>",
            "",
        ]
    )


def render_deck(model: dict[str, Any], blocks: list[Block], *, title: str | None = None) -> str:
    """由报告模型与块列表渲染一份 deck。

    Args:
        model: :func:`zoo_bench.report.build_model` 的返回值。
        blocks: :func:`zoo_bench.render.blocks.build_blocks` 的返回值（与 Markdown / PDF 同源）。
        title: 文档标题；缺省由模型里的被测框架版本拼出。

    Returns:
        完整的 HTML 文本。

    Raises:
        DeckOverflowError: 有内容装不进一页且无法再切。
    """
    subject = model.get("subject")
    resolved_title = title or _title(model)
    dimensions = model.get("dimensions") or {}

    slides: list[Slide] = [_cover_slide(model, subject)]
    for section in _sections(blocks):
        if section.heading.level == 1:
            # 一级标题是报告的题头，封面已承担
            continue
        if (
            section.title.startswith(APPENDIX_SECTION_PREFIX)
            or section.title == CHART_SECTION_TITLE
        ):
            # 附录的逐单元明细与图表章不进幻灯片：明细交给 report.md（末页给指针），
            # 图表由本模块的条形自己编码同一批数值
            continue

        if section.title.startswith(DIMENSION_SECTION_PREFIX):
            slides += _dimension_chart_slides(section, dimensions, subject)
        if section.title == UNFAVORABLE_SECTION_TITLE:
            slides += _unfavorable_summary_slides(model)

        slides += _content_slides(section)

        if section.title == CONCLUSION_SECTION_TITLE:
            slides += _stat_slides(model, subject)

    slides += _pointer_slides(model)
    return _assemble(slides, resolved_title)


def _dimension_chart_slides(
    section: _Section, dimensions: dict[str, Any], subject: str | None
) -> list[Slide]:
    """该章节若是某个度量维度，给它配条形图页（排在数值表之前）。"""
    title = section.title[len(DIMENSION_SECTION_PREFIX) :]
    for key, dimension in dimensions.items():
        if not isinstance(dimension, dict) or dimension.get("title") != title:
            continue
        spec = _CHART_SPECS.get(key)
        if spec is None:
            return []
        return _chart_slides(subject, key, dimension, spec)
    return []
