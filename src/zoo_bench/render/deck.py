"""deck 形态的报告页：一页一个结论，数值直接编码成条形。

**为什么要这一层**：长文档适合核查，不适合三十秒内知道结论。deck 把"一页一个结论"变成结构约束
——一个章节开一页，正文按行数上限切分，切不完的分页续排，**不按重要性截断**。

**同源**（design D1）：文字取自 :func:`zoo_bench.render.blocks.build_blocks` 的块，与 Markdown /
PDF 逐字一致；条形图取自模型里的**原始数值**——块的表格里已经是格式化后的字符串，从字符串反解
数值等于把格式当数据，单位一改就静默取错。故本模块的入参是 ``(model, blocks)``，由
:func:`zoo_bench.render.report.render` 同一次调用传入。

**框架块原样**（design D3）：``deck_framework.css`` / ``deck_chrome.html`` /
``deck_framework.js`` 只做拼装，不重写——它们是设计交付物，改了就没有语法高亮可 review，也与
设计稿对不上了。滚轮翻页与入场动画因而落在**追加层**（``_EXTRA_JS`` 与 ``_EXTRA_CSS`` 的末段），
走框架自己留的 ``od:slide`` / ``od:slide-state`` 消息协议。
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

from ..i18n import HTML_LANGS, LANG_ZH, t
from . import links
from .assets import (
    DECK_CHROME_HTML,
    DECK_FRAMEWORK_CSS,
    DECK_FRAMEWORK_JS,
    DECK_SLIDES_CSS,
    deck_asset,
)
from .blocks import (
    BULLETS,
    HEADING,
    IMAGE,
    NOTE,
    PARAGRAPH,
    TABLE,
    Block,
    appendix_section_prefix,
    chart_section_title,
    conclusion_section_title,
    dimension_section_prefix,
    format_ratio,
    format_seconds,
    repository_relative_path,
    unfavorable_section_title,
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
#: 分页续排到底、不截断。判断在 :func:`_content_slides` 里按当场语言取题目比对。

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

    /* ── 入场动画（当前页被切到前台时逐项淡入）──────────────
       初始态只挂在 **JS 加的类** `deck-anim` 上：渲染时不给任何元素写 opacity:0，故禁用
       JavaScript 的读者看到的仍是完整内容，打印也不可能抓到初始态。 */
    @keyframes deck-enter {
      from { opacity: 0; transform: translateY(12px); }
      to   { opacity: 1; transform: none; }
    }
    .slide.active.deck-anim > * {
      animation: deck-enter 420ms cubic-bezier(0.2, 0.7, 0.3, 1) both;
    }
    .slide.active.deck-anim > *:nth-child(1) { animation-delay: 0ms; }
    .slide.active.deck-anim > *:nth-child(2) { animation-delay: 40ms; }
    .slide.active.deck-anim > *:nth-child(3) { animation-delay: 80ms; }
    .slide.active.deck-anim > *:nth-child(4) { animation-delay: 120ms; }
    .slide.active.deck-anim > *:nth-child(n + 5) { animation-delay: 280ms; }

    /* ── 条形从零增长（图表页）──────────────
       用 scaleX 而不是动画 width：后者的目标是 `calc(var(--v) / var(--max) * 100%)`，要么在 JS
       里读计算值、要么把那条 calc 复制一份（两处真源迟早漂移）；scaleX 不需要那个值、也不触发
       逐帧布局。视觉上实心矩形横向缩放与"宽度增长"等价。**静止态不是缩放态**，故禁用 JavaScript
       时条形直接是终值，不经历零。 */
    @keyframes deck-bar-grow {
      from { transform: scaleX(0); }
      to   { transform: scaleX(1); }
    }
    .slide.active.deck-anim .bar {
      transform-origin: left center;
      animation: deck-bar-grow 520ms cubic-bezier(0.2, 0.7, 0.3, 1) both;
      animation-delay: var(--deck-bar-delay, 0ms);
    }

    /* 打印与「减少动态效果」都必须**显式**把动画关掉：前者否则会抓到初始态（opacity:0 是空白、
       scaleX(0) 是零长条形），后者是无障碍要求。两条都不能指望"动画自己会跑完"。
       **选择器要同时管到孙子层**：`.slide > *` 只覆盖直接子元素，条形在 `.chart` 里面，不在其中。 */
    @media print {
      .slide > *, .slide .bar {
        animation: none !important; opacity: 1 !important; transform: none !important;
      }
      /* **这条是必需的，不是冗余**：屏幕规则 `.slide:not(.active) { display: none !important }`
         比框架打印规则里的 `.slide { display: flex !important }` **更具体**，于是把后者压掉——
         实测打印一趟 38 页的 deck 只出 1 页（当前页）。同具体度、并排在它之后即可胜出。 */
      .slide:not(.active) { display: flex !important; }
    }
    @media (prefers-reduced-motion: reduce) {
      .slide > *, .slide .bar { animation: none !important; }
    }

    /* ── 语言切换（页首右上角）──────────────
       fixed 定位挂在 stage 外面：stage 会被 JS 缩放适配视口，放进 stage 里的元素会跟着变形。 */
    .lang-switch {
      position: fixed; top: 14px; right: 18px; z-index: 100;
      font-size: 14px;
    }
    .lang-switch a {
      color: var(--muted); text-decoration: none;
      padding: 4px 12px; border: 1px solid var(--border-c); border-radius: 999px;
    }
    .lang-switch a:hover { color: var(--accent); }
"""

#: 追加在 vendored 框架脚本之后的交互层：滚轮翻页 + 入场动画。
#:
#: **不改框架脚本**：它标着 DO NOT EDIT、且被 sha256 钉在用例里。好在它自己留了扩展口——
#: 监听 `window` 上的 `message` 收 `{type:'od:slide', action:...}` 来驱动翻页，并把
#: `{type:'od:slide-state', active}` 广播到 `window.parent`（顶层文档时即自身）。故这里发消息
#: 驱动它、收广播决定给哪一页挂动画。框架在自身 IIFE 里就 `paint()` 过一次，而 `message` 是
#: 异步投递的——所以这段追加脚本跑完才收到那次广播，首屏同样有动画，不需要额外的初始化。
_EXTRA_JS = """
    (function () {
      var THRESHOLD_PX = 24;   // 小于它的 deltaY 是触控板的惯性尾巴，忽略
      var COOLDOWN_MS = 450;   // 一次手势会连发几十个 wheel 事件，不设冷却就会一划跳五页
      var LINE_PX = 16;        // deltaMode === 1（行模式）时一行约合多少像素
      var lastAt = 0;

      function send(action) {
        window.postMessage({ type: 'od:slide', protocolVersion: 1, action: action }, '*');
      }

      window.addEventListener('wheel', function (event) {
        if (event.ctrlKey) return;   // Ctrl+滚轮是缩放，不该翻页
        // **行模式必须换算**：Firefox 的 deltaY 只有几，不换算会被阈值全部挡掉，表现为
        // "Firefox 上滚轮彻底失灵"。
        var delta = event.deltaMode === 1 ? event.deltaY * LINE_PX : event.deltaY;
        if (Math.abs(delta) < THRESHOLD_PX) return;
        var now = Date.now();
        if (now - lastAt < COOLDOWN_MS) return;
        lastAt = now;
        send(delta > 0 ? 'next' : 'prev');
      }, { passive: true });   // 从不 preventDefault：不抢框架对点击的处理

      var reduce = window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
      var counting = null;   // 正在滚动的大数字：{node, original}

      // 大数字滚动。**收尾必须逐字写回原值**：这是报告，停在中间值等于报了一个错数；而用
      // `toFixed` 重新格式化也不行——那会把 `4.7` 悄悄改成 `4.70`，与渲染时不再一字不差。
      function countUp(root) {
        var element = root.querySelector('.stat-num');
        if (!element || !element.firstChild || element.firstChild.nodeType !== 3) return;
        var node = element.firstChild;
        var original = node.nodeValue;
        var target = parseFloat(original);
        if (!isFinite(target)) return;
        var decimals = (original.split('.')[1] || '').length;
        var startedAt = null;
        counting = { node: node, original: original };
        var frame = function (now) {
          if (startedAt === null) startedAt = now;
          var progress = Math.min(1, (now - startedAt) / 700);
          var eased = 1 - Math.pow(1 - progress, 3);
          node.nodeValue = (target * eased).toFixed(decimals);
          if (progress < 1) { window.requestAnimationFrame(frame); return; }
          node.nodeValue = original;   // ← 逐字写回，不靠 toFixed 还原
          counting = null;
        };
        window.requestAnimationFrame(frame);
      }

      // 打印恰好发生在滚动途中时**强制收尾**，否则印出来的是中间值
      window.addEventListener('beforeprint', function () {
        if (!counting) return;
        counting.node.nodeValue = counting.original;
        counting = null;
      });

      // 条形按行错开：给每一行写一个延迟变量，样式侧读 var(--deck-bar-delay, 0ms)
      function staggerBars(root) {
        var rows = root.querySelectorAll('.chart .bar-row');
        for (var index = 0; index < rows.length; index++) {
          rows[index].style.setProperty('--deck-bar-delay', (index * 60) + 'ms');
        }
      }

      window.addEventListener('message', function (event) {
        var data = event && event.data;
        if (!data || data.type !== 'od:slide-state') return;
        if (reduce) return;   // 声明减少动态效果：CSS 那层之外再加一道
        var slide = slides[data.active];
        if (!slide) return;
        staggerBars(slide);
        // 先移除、强制重排、再加：重复加同一个类不会重启动画，回到看过的页就不播了
        slide.classList.remove('deck-anim');
        void slide.offsetWidth;
        slide.classList.add('deck-anim');
        countUp(slide);
      });
    })();
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
        legend_key: 该维度这一列是什么的名目——存**目录键**，渲染时按语言解析。
    """

    numeric: Callable[[dict[str, Any]], float | None]
    display: Callable[[dict[str, Any], str], str]
    higher_is_better: bool
    legend_key: str


def _format_rate(value: float | None, lang: str) -> str:
    """吞吐 -> 任务/秒。带千位分隔，因为这一列从 5,673 到 319,989。"""
    return (
        t("blocks.empty_marker", lang)
        if value is None
        else f"{value:,.0f} {t('unit.tasks_per_second', lang)}"
    )


def _latency_median(row: dict[str, Any]) -> float | None:
    """延迟维度里的端到端是**统计摘要**（中位数、分位数、离散度），画条取中位数。

    与块层的透视表同一口径——两处取不同的统计量，同一个维度就会在 deck 与全文里出现两个数。
    """
    summary = row.get("end_to_end_per_task")
    return float(summary["median"]) if isinstance(summary, dict) else summary


_CHART_SPECS: dict[str, _ChartSpec] = {
    "latency": _ChartSpec(
        numeric=_latency_median,
        display=lambda row, lang: format_seconds(_latency_median(row), lang),
        higher_is_better=False,
        legend_key="deck.legend.latency",
    ),
    "overhead": _ChartSpec(
        numeric=lambda row: row.get("framework_overhead_seconds"),
        display=lambda row, lang: format_ratio(row.get("framework_overhead_ratio"), lang),
        higher_is_better=False,
        legend_key="deck.legend.overhead",
    ),
    "throughput": _ChartSpec(
        numeric=lambda row: row.get("throughput_per_second"),
        display=lambda row, lang: _format_rate(row.get("throughput_per_second"), lang),
        higher_is_better=True,
        legend_key="deck.legend.throughput",
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


def _join(items: list[str], lang: str) -> str:
    """并列项的连接符按语言取：中文用顿号，英文用逗号。"""
    return ("、" if lang == LANG_ZH else ", ").join(items)


def _sep(lang: str) -> str:
    """eyebrow 的小间隔符。英文的字符门禁只放行 ASCII，间隔点（U+00B7）退成连字符。"""
    return " · " if lang == LANG_ZH else " - "


def _title(model: dict[str, Any], lang: str) -> str:
    version = _subject_version(model)
    return (
        t("deck.title.versioned", lang, version=version) if version else t("deck.title.plain", lang)
    )


def _cover_heading(model: dict[str, Any], lang: str) -> str:
    """封面的标题分两行（型号一行、评测名一行）——一行摆不下时字会缩到看不清。"""
    version = _subject_version(model)
    return (
        t("deck.cover.heading.versioned", lang, version=version)
        if version
        else t("deck.cover.heading.plain", lang)
    )


def _cover_slide(model: dict[str, Any], subject: str | None, lang: str) -> Slide:
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
    eyebrow = _sep(lang).join(
        part
        for part in (
            "zoo-bench",
            _format_date(run.get("started_at_epoch")),
            hardware.get("cpu_model"),
        )
        if part
    )

    return Slide(
        label=t("deck.cover.label", lang),
        tone="dark",
        layout="hero center",
        eyebrow=eyebrow,
        heading=_cover_heading(model, lang),
        heading_class="h-hero",
        heading_tag="h1",
        # 没有对照方案时如实说"这份报告撑不起对比"，而不是拼出"与  共 0 个"这种空话
        lead=(
            t(
                "deck.cover.lead.with_others",
                lang,
                count=len(others),
                baselines=_join(others, lang),
            )
            if others
            else t("deck.cover.lead.no_others", lang)
        ),
        source=(
            t("deck.cover.source", lang, path=repository_relative_path(str(source["path"])))
            if source.get("path")
            else t("deck.cover.source.unrecorded", lang)
        ),
        notes=t("deck.cover.notes", lang),
    )


def _content_slides(section: _Section, lang: str) -> list[Slide]:
    """一个章节 -> 一到多页；超过每节上限的部分由一页指引送去全文。"""
    pieces = _section_pieces(section.blocks)
    if not pieces:
        return []

    chunks = _chunk(pieces)
    limit = len(chunks) if section.title == unfavorable_section_title(lang) else SLIDES_PER_SECTION
    truncated = len(chunks) > limit

    slides: list[Slide] = []
    for index, chunk in enumerate(chunks[:limit]):
        suffix = "" if index == 0 else t("deck.content.suffix", lang)
        slides.append(
            Slide(
                label=f"{section.title}{suffix}" or t("deck.content.label.fallback", lang),
                eyebrow=section.title if index else t("deck.content.eyebrow.default", lang),
                heading=f"{section.title}{suffix}" if section.title else "",
                body=_chunk_html(chunk),
                source=t("deck.content.source", lang, section=section.title),
            )
        )
    if truncated:
        slides.append(_pointer_slide_for(section, len(chunks) - limit, lang))
    return slides


def _pointer_slide_for(section: _Section, dropped: int, lang: str) -> Slide:
    """本章装不下的那部分明细去哪找——**点名章节**，不写"见报告其余部分"。"""
    return Slide(
        label=t("deck.pointer.label", lang, section=section.title),
        eyebrow=section.title,
        heading=t("deck.pointer.heading", lang),
        lead=t("deck.pointer.lead", lang, cap=SLIDES_PER_SECTION, dropped=dropped),
        source=t("deck.pointer.source", lang, section=section.title),
        notes=t("deck.pointer.notes", lang),
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


def _chart_groups(
    rows: list[dict[str, Any]], spec: _ChartSpec, subject: str, lang: str
) -> list[_BarGroup]:
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
            (multiple, row["adapter"], spec.display(row, lang))
            for row in members
            if (value := spec.numeric(row)) is not None
            and (multiple := _multiple(spec, value, base))
        )
        if bars:
            groups.append(_BarGroup(t("deck.chart.group_tier", lang, tier=tier), bars))
    return groups


def _chart_slides(
    subject: str | None, key: str, dimension: dict[str, Any], spec: _ChartSpec, lang: str
) -> list[Slide]:
    """一个度量维度的条形图：一个并发度、全部方案、全部档位，装不下就续排。"""
    if subject is None:
        return []
    rows = [row for row in dimension.get("rows", []) if spec.numeric(row) is not None]
    if not rows:
        return []

    concurrency = _concurrency_of(rows)
    rows = [row for row in rows if int(row["concurrency"]) == concurrency]
    groups = _chart_groups(rows, spec, subject, lang)
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
            _BarGroup(
                t(
                    "deck.chart.group_part",
                    lang,
                    label=group.label,
                    index=index,
                    total=len(parts),
                ),
                part,
            )
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
        legend = t(spec.legend_key, lang)
        lines = [
            f'<p class="eyebrow">{t("deck.chart.eyebrow", lang, concurrency=concurrency, legend=legend)}</p>',
            f'<div class="chart" style="--max: {cap:g}">',
        ]
        for group in groups_in_slide:
            lines.append(f'<div class="cg-label">{inline(group.label)}</div>')
            for multiple, name, value_text in group.bars:
                lines.append(_bar_html(name, value_text, multiple, subject))
        lines.append("</div>")
        lines.append(
            '<div class="legend">'
            f'<span class="legend-chip"><i class="c-zoo"></i>{t("deck.chart.chip.subject", lang)}</span>'
            f'<span class="legend-chip"><i class="c-ra"></i>{t("deck.chart.chip.baseline", lang)}</span>'
            f'<span class="legend-chip">{t("deck.chart.chip.meaning", lang)}</span>'
            "</div>"
        )
        suffix = "" if index == 0 else t("deck.content.suffix", lang)
        notes = [t("deck.chart.note.bars", lang)]
        if withheld:
            notes.append(t("deck.chart.note.withheld", lang, count=withheld))
        slides.append(
            Slide(
                label=f"{title}{suffix}",
                eyebrow=dimension_section_prefix(lang) + title,
                heading=t("deck.chart.heading", lang, title=title, concurrency=concurrency)
                + suffix,
                body="\n".join(lines),
                source=t("deck.chart.source", lang, title=title, concurrency=concurrency),
                notes=" ".join(notes),
            )
        )
    return slides


def _stat_slides(model: dict[str, Any], subject: str | None, lang: str) -> list[Slide]:
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
    comparison = _join(
        [
            f"{row['adapter']} {format_ratio(row['framework_overhead_ratio'], lang)}"
            for row in rivals
        ],
        lang,
    )
    # **同档的结论必须和大数字一起给**：一个 300px 的数字单摆着，读者会读成"这就是最低的那个"。
    # 实测该档位有三家比被测框架更低，故这一句不是客套而是纠偏。
    lower = [row for row in rivals if float(row["framework_overhead_ratio"]) < ratio]
    verdict = (
        t("deck.stat.verdict.lowest", lang)
        if not lower
        else t(
            "deck.stat.verdict.lower",
            lang,
            count=len(lower),
            adapters=_join([str(row["adapter"]) for row in lower], lang),
        )
    )
    caption = (
        t("deck.stat.caption", lang, tier=tier, subject=subject)
        + f"<br>{inline(verdict)}"
        + (
            f'<br><span class="dim">'
            f"{inline(t('deck.stat.same_tier', lang, comparison=comparison))}</span>"
            if comparison
            else ""
        )
    )

    return [
        Slide(
            label=t("deck.stat.label", lang),
            layout="hero center",
            eyebrow=conclusion_section_title(lang) + _sep(lang) + t("deck.stat.eyebrow", lang),
            heading="",
            body=(
                f'<div class="stat-num">{ratio * 100:.2f}<span class="unit">%</span></div>'
                f'<p class="stat-caption">{caption}</p>'
            ),
            source=t("deck.stat.source", lang, concurrency=concurrency, tier=tier),
            notes=t("deck.stat.notes", lang),
        )
    ]


def _unfavorable_summary_slides(model: dict[str, Any], lang: str) -> list[Slide]:
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
        cells = _join(
            [
                t(
                    "deck.unfavorable.cell",
                    lang,
                    concurrency=int(row["concurrency"]),
                    tier=row["body_tier_us"],
                )
                for row in sorted(
                    rows, key=lambda row: (int(row["concurrency"]), row["body_tier_us"])
                )
            ],
            lang,
        )
        cards.append(
            '<div class="pt">'
            f"<h3>{t('deck.unfavorable.card.title', lang, baseline=inline(baseline), count=len(rows))}</h3>"
            f"<p>{t('deck.unfavorable.card.body', lang, gap=f'{gap:.2f}', concurrency=int(worst['concurrency']), tier=worst['body_tier_us'], cells=inline(cells))}</p>"
            "</div>"
        )

    return [
        Slide(
            label=t("deck.unfavorable.label", lang),
            layout="hero",
            eyebrow=unfavorable_section_title(lang)
            + _sep(lang)
            + t("deck.unfavorable.eyebrow", lang),
            heading=t("deck.unfavorable.heading", lang, count=len(items), rivals=len(by_baseline)),
            body=f'<div class="pt-grid">{"".join(cards)}</div>',
            source=t("deck.unfavorable.source", lang),
            notes=t("deck.unfavorable.notes", lang),
        )
    ]


def _pointer_slides(model: dict[str, Any], lang: str) -> list[Slide]:
    """收尾：deck 装不下的明细去哪找，说清楚。"""
    run = model.get("run") or {}
    source = model.get("source") or {}
    items = [
        t("deck.pointer_page.item.markdown", lang),
        t("deck.pointer_page.item.figures", lang),
    ]
    if source.get("path"):
        items.append(
            t(
                "deck.pointer_page.item.archive",
                lang,
                path=repository_relative_path(str(source["path"])),
            )
        )
    if run.get("unit_count") is not None:
        items.append(
            t(
                "deck.pointer_page.item.units",
                lang,
                total=run["unit_count"],
                failed=run.get("failed_unit_count"),
            )
        )

    return [
        Slide(
            label=t("deck.pointer_page.label", lang),
            tone="dark",
            layout="center",
            eyebrow=t("deck.pointer_page.eyebrow", lang),
            heading=t("deck.pointer_page.heading", lang),
            lead=t("deck.pointer_page.lead", lang),
            body=(
                '<ul class="deck-list">'
                + "".join(f"<li>{inline(item)}</li>" for item in items)
                + "</ul>"
            ),
            source=t("deck.pointer_page.source", lang),
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
        lines.append(
            f'<{slide.heading_tag} class="{slide.heading_class}">{heading}</{slide.heading_tag}>'
        )
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


def _assemble(slides: list[Slide], title: str, lang: str) -> str:
    """把幻灯片、框架块与逐页样式拼成一份完整的 deck 文档。"""
    body = "\n".join(_render_slide(slide, index=index) for index, slide in enumerate(slides))
    return "\n".join(
        [
            "<!doctype html>",
            f'<html lang="{HTML_LANGS[lang]}" data-od-deck-protocol="1">',
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
            # 语言切换进页首（spec 场景「语言互链可达」）：deck 是全屏翻页形态，没有页头可用，
            # 故固定在右上角——读者第一眼扫到标题的位置就是回家的路。
            f'<nav class="lang-switch"><a href="{html_module.escape(links.language_switch_href(lang), quote=True)}">'
            f"{inline(links.language_switch_label(lang))}</a></nav>",
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
            "<script>",
            _EXTRA_JS.strip("\n"),
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
    lang = model["lang"]
    resolved_title = title or _title(model, lang)
    dimensions = model.get("dimensions") or {}

    slides: list[Slide] = [_cover_slide(model, subject, lang)]
    for section in _sections(blocks):
        if section.heading.level == 1:
            # 一级标题是报告的题头，封面已承担
            continue
        if section.title.startswith(
            appendix_section_prefix(lang)
        ) or section.title == chart_section_title(lang):
            # 附录的逐单元明细与图表章不进幻灯片：明细交给 report.md（末页给指针），
            # 图表由本模块的条形自己编码同一批数值
            continue

        if section.title.startswith(dimension_section_prefix(lang)):
            slides += _dimension_chart_slides(section, dimensions, subject, lang)
        if section.title == unfavorable_section_title(lang):
            slides += _unfavorable_summary_slides(model, lang)

        slides += _content_slides(section, lang)

        if section.title == conclusion_section_title(lang):
            slides += _stat_slides(model, subject, lang)

    slides += _pointer_slides(model, lang)
    return _assemble(slides, resolved_title, lang)


def _dimension_chart_slides(
    section: _Section, dimensions: dict[str, Any], subject: str | None, lang: str
) -> list[Slide]:
    """该章节若是某个度量维度，给它配条形图页（排在数值表之前）。"""
    title = section.title[len(dimension_section_prefix(lang)) :]
    for key, dimension in dimensions.items():
        if not isinstance(dimension, dict) or dimension.get("title") != title:
            continue
        spec = _CHART_SPECS.get(key)
        if spec is None:
            return []
        return _chart_slides(subject, key, dimension, spec, lang)
    return []
