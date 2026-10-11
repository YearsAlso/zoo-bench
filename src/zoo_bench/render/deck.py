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
    choice_section_title,
    dimension_section_prefix,
    format_ratio,
    format_seconds,
    format_tier_us,
    repository_relative_path,
    resolve_text,
    turning_section_title,
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
    /* 表格页的**视觉结论**：7 列裸数字看不出谁赢，故逐表标出最优/最差的那一格。
       （样式沿用既有调色板：最优走 --win，最差走 accent。） */
    .deck-table td.win { color: var(--win); font-weight: 700; }
    .deck-table td.worst { color: var(--accent); font-weight: 700; }
    /* 大数字的字号是 300px、行高 1——那是给模板自带的 Berkeley Mono 定的。换到回退字体后
       行盒装不下字形的上下沿，实测在核心结论页量到 25px 的内容落在盒子外（页面 overflow:hidden，
       也就是被裁掉）。故留出余量，不让它贴着边。 */
    .stat-num { line-height: 1.2; }

    /* ── 一句话结论页的三个大数字（样式取自改版参考稿）──────────── */
    .kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 56px; margin-top: 60px; }
    .kpi { border-top: 2px solid var(--border-c); padding-top: 24px; }
    .kpi .n { font-size: 84px; font-weight: 700; line-height: 1.05; color: var(--accent); }
    .kpi .n small { font-size: 40px; font-weight: 500; margin-left: 6px; }
    .kpi .t { font-size: 19px; line-height: 1.65; color: var(--muted); margin-top: 16px; }
    .kpi .t b { color: inherit; }

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
      var counting = [];   // 正在滚动的大数字：{node, original}

      // 大数字滚动。**收尾必须逐字写回原值**：这是报告，停在中间值等于报了一个错数；而用
      // `toFixed` 重新格式化也不行——那会把 `4.7` 悄悄改成 `4.70`，与渲染时不再一字不差。
      // 一句话结论那一页有三个大数字（`.kpi .n`），故逐个都滚。
      function countUp(root) {
        var elements = root.querySelectorAll('.kpi .n, .stat-num');
        for (var index = 0; index < elements.length; index++) {
          startCounting(elements[index]);
        }
      }

      function startCounting(element) {
        if (!element || !element.firstChild || element.firstChild.nodeType !== 3) return;
        var node = element.firstChild;
        var original = node.nodeValue;
        var target = parseFloat(original);
        if (!isFinite(target)) return;
        var decimals = (original.split('.')[1] || '').length;
        var startedAt = null;
        var entry = { node: node, original: original };
        counting.push(entry);
        var frame = function (now) {
          if (startedAt === null) startedAt = now;
          var progress = Math.min(1, (now - startedAt) / 700);
          var eased = 1 - Math.pow(1 - progress, 3);
          node.nodeValue = (target * eased).toFixed(decimals);
          if (progress < 1) { window.requestAnimationFrame(frame); return; }
          node.nodeValue = original;   // ← 逐字写回，不靠 toFixed 还原
          counting.splice(counting.indexOf(entry), 1);
        };
        window.requestAnimationFrame(frame);
      }

      // 打印恰好发生在滚动途中时**强制收尾**，否则印出来的是中间值
      window.addEventListener('beforeprint', function () {
        for (var index = 0; index < counting.length; index++) {
          counting[index].node.nodeValue = counting[index].original;
        }
        counting = [];
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


def _section_by_title(sections: list[_Section], title: str) -> _Section | None:
    """按标题身份取一节。名字由 :mod:`.blocks` 的辅助函数给出，故改名时两边一起变。"""
    for section in sections:
        if section.title == title:
            return section
    return None


def _compose_slides(
    title: str,
    blocks: tuple[Block, ...],
    lang: str,
    *,
    label: str | None = None,
    eyebrow: str | None = None,
    source: str | None = None,
) -> list[Slide]:
    """把一组块排成**一页一个结论**：能一页就一页，放不下先分栏，再放不下按行续排。

    三种排法**按这个顺序试**，理由各自不同：

    - **一页**是默认，也是"每页一个结论"的直接实现。
    - **分栏**用在"一张短表 + 几条长说明"这种组合上：叠起来会超页并因此多出一页，而分栏的纵向
      占用取两栏的较大者。选型速查、转折点、可复核性都是这个形状。
    - **按行续排**是兜底：分栏也放不下时按行切，标题写明这是第几片。**绝不丢掉后面几片**——deck
      的版面有硬上限，任何"超出就少一截"的写法都会静默生成一份残缺的报告。

    **没有整页指引**：页脚的 ``source`` 点名了去全文的哪一章（spec：指针从整页降级为一行）。
    """
    pieces = _section_pieces(blocks)
    if not pieces:
        return []

    chunks = _chunk(pieces)
    if len(chunks) == 1:
        return [_page_slide(title, _chunk_html(chunks[0]), lang, label, eyebrow, source=source)]

    table = next((block for block in blocks if block.kind == TABLE), None)
    if table is not None and table.rows and len(table.rows) + 1 <= SLIDE_ROW_CAP:
        # 分栏试一把；**分栏也放不下就退回按行续排**，而不是在这里失败
        try:
            rest = tuple(block for block in blocks if block is not table)
            rest_html, rest_rows = _blocks_page(rest, title)
            body, _rows = _stack_or_split(
                _table_piece(table, table.rows),
                len(table.rows) + 1,
                rest_html,
                rest_rows,
                title,
            )
        except DeckOverflowError:
            pass
        else:
            return [_page_slide(title, body, lang, label, eyebrow, source=source)]

    slides: list[Slide] = []
    for index, chunk in enumerate(chunks):
        heading = t("deck.content.part", lang, title=title, index=index + 1, total=len(chunks))
        slides.append(
            _page_slide(
                title,
                _chunk_html(chunk),
                lang,
                # 续排时用**带片号的标题**当标签：同一个标签在两页上重复，读者与用例都分不开它们
                label if len(chunks) == 1 else heading,
                eyebrow if index == 0 else "",
                heading,
                source=source,
            )
        )
    return slides


def _section_slides(
    section: _Section | None, lang: str, *, label: str | None = None
) -> list[Slide]:
    """一节 -> 一页（见 :func:`_compose_slides` 的三种排法）。"""
    if section is None:
        return []
    return _compose_slides(
        section.title,
        section.blocks,
        lang,
        label=label,
        eyebrow=section.title,
        source=t("deck.content.source", lang, section=section.title),
    )


def _page_slide(
    title: str,
    body: str,
    lang: str,
    label: str | None,
    eyebrow: str | None,
    heading: str | None = None,
    *,
    source: str | None = None,
) -> Slide:
    return Slide(
        label=label or heading or title,
        eyebrow=eyebrow if eyebrow is not None else title,
        heading=heading or title,
        body=body,
        source=source if source is not None else t("deck.content.source", lang, section=title),
    )


def _table_piece(
    block: Block,
    rows: tuple[tuple[str, ...], ...],
    marks: dict[tuple[int, int], str] | None = None,
) -> str:
    """把若干行连同表头渲染成一张 deck 表格。

    ``marks`` 给 ``(行号, 列号)`` 打视觉结论的类（``win`` / ``worst``）：deck 上的表原先是一堆
    裸数字，读者看不出哪一格是结论。类只在本模块的追加样式里定义，不动 vendored 样式与调色板。
    """
    marked = marks or {}
    head = "".join(f"<th>{inline(header)}</th>" for header in block.headers)
    body = "".join(
        "<tr>"
        + "".join(
            (
                f'<td class="{marked[(row_index, column)]}">{inline(cell)}</td>'
                if (row_index, column) in marked
                else f"<td>{inline(cell)}</td>"
            )
            for column, cell in enumerate(row)
        )
        + "</tr>"
        for row_index, row in enumerate(rows)
    )
    return f'<table class="deck-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _two_column(left: str, right: str) -> str:
    """左右分栏。**纵向占用取两栏的较大者**，不是相加——这正是分栏能塞进更多内容的原因。"""
    return f'<div class="split"><div>{left}</div><div>{right}</div></div>'


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
    """一档执行体下的一组条形。

    Attributes:
        label: 组标题（渲染好的文字）。
        bars: ``(倍数, 方案名, 打印的数值)``。
        tier_us: 该组的执行体档位（微秒）——分页标题要按它给出"这一页覆盖哪一段档位"。
    """

    label: str
    bars: tuple[tuple[float, str, str], ...]
    tier_us: float = 0.0


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
            groups.append(
                _BarGroup(t("deck.chart.group_tier", lang, tier=tier), bars, float(tier))
            )
    return groups


def _bar_columns(groups: list[_BarGroup], cap: int) -> list[list[_BarGroup]]:
    """把档位分组装进若干"列"，每列的行数不超过 ``cap``。"""
    columns: list[list[_BarGroup]] = []
    current: list[_BarGroup] = []
    used = 0
    for group in groups:
        size = 1 + len(group.bars)
        if current and used + size > cap:
            columns.append(current)
            current, used = [], 0
        current.append(group)
        used += size
    if current:
        columns.append(current)
    return columns


def _chart_column_html(groups: list[_BarGroup], subject: str) -> str:
    """一列的条形：上限取**本列自己的**峰值，两列各自归一。"""
    cap = _nice_max(max(multiple for group in groups for multiple, _, _ in group.bars))
    lines = [f'<div class="chart" style="--max: {cap:g}">']
    for group in groups:
        lines.append(f'<div class="cg-label">{inline(group.label)}</div>')
        for multiple, name, value_text in group.bars:
            lines.append(_bar_html(name, value_text, multiple, subject))
    lines.append("</div>")
    return "\n".join(lines)


def _chart_slides(
    subject: str | None, key: str, dimension: dict[str, Any], spec: _ChartSpec, lang: str
) -> list[Slide]:
    """一个度量维度**一页**：左右分栏容纳全部档位。

    **一个维度只讲一遍**（spec）：条形图留下、透视表留给全文。原先一个维度占四页——条形图两页
    加透视表两页，而两者读法不同（倍数 vs 绝对值），读者要来回换算。

    分栏让"全部档位"挤进一页；档位多到一页装不下时按**档位区间**分页，标题写明覆盖了哪一段，
    而不是加一个「（续）」。
    """
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

    # 分组本身也要能切：对照方案加到十几个时，一组就装不下一列。
    expanded: list[_BarGroup] = []
    for group in groups:
        width = SLIDE_ROW_CAP - 1
        if len(group.bars) <= width:
            expanded.append(group)
            continue
        parts_group = [group.bars[i : i + width] for i in range(0, len(group.bars), width)]
        expanded += [
            _BarGroup(
                t(
                    "deck.chart.group_part",
                    lang,
                    label=group.label,
                    index=index,
                    total=len(parts_group),
                ),
                part,
                group.tier_us,
            )
            for index, part in enumerate(parts_group, start=1)
        ]

    columns = _bar_columns(expanded, SLIDE_ROW_CAP)
    pages = [columns[index : index + 2] for index in range(0, len(columns), 2)]

    withheld = len(dimension.get("rows", [])) - len(
        [row for row in dimension.get("rows", []) if spec.numeric(row) is not None]
    )
    title = str(dimension.get("title", key))
    legend = t(spec.legend_key, lang)

    slides: list[Slide] = []
    for index, page_columns in enumerate(pages):
        tiers = [group.tier_us for column in page_columns for group in column if group.tier_us]
        if len(pages) > 1 and tiers:
            heading = t(
                "deck.chart.heading.range",
                lang,
                title=title,
                concurrency=concurrency,
                low=format_tier_us(min(tiers), lang),
                high=format_tier_us(max(tiers), lang),
            )
        else:
            heading = t("deck.chart.heading", lang, title=title, concurrency=concurrency)

        lines = [
            f'<p class="eyebrow">{t("deck.chart.eyebrow", lang, concurrency=concurrency, legend=legend)}</p>',
            '<div class="split">',
            *(_chart_column_html(column, subject) for column in page_columns),
            "</div>",
            '<div class="legend">'
            f'<span class="legend-chip"><i class="c-zoo"></i>{t("deck.chart.chip.subject", lang)}</span>'
            f'<span class="legend-chip"><i class="c-ra"></i>{t("deck.chart.chip.baseline", lang)}</span>'
            f'<span class="legend-chip">{t("deck.chart.chip.meaning", lang)}</span>'
            "</div>",
        ]
        notes = [t("deck.chart.note.bars", lang)]
        if withheld and index == 0:
            notes.append(t("deck.chart.note.withheld", lang, count=withheld))
        slides.append(
            Slide(
                label=heading,
                eyebrow=dimension_section_prefix(lang) + title,
                heading=heading,
                body="\n".join(lines),
                source=t("deck.chart.source", lang, title=title, concurrency=concurrency),
                notes=" ".join(notes),
            )
        )
    return slides


def _big_number(value_us: float, lang: str) -> tuple[str, str]:
    """大数字与它的单位单独给——单位要用大字号（``small``）跟在数字后面。"""
    if value_us >= 1000:
        return f"{value_us / 1000:.1f}", t("unit.milliseconds", lang)
    return f"{value_us:g}", t("unit.microseconds", lang)


def _kpi_html(value: str, unit: str, caption: str) -> str:
    return (
        f'<div class="kpi"><div class="n">{inline(value)}<small>{inline(unit)}</small></div>'
        f'<p class="t">{inline(caption)}</p></div>'
    )


def _verdict_slides(model: dict[str, Any], subject: str | None, lang: str) -> list[Slide]:
    """一句话结论：三个大数字。

    三个数各回答一个问题——**多大才划算**（框架开销降到阈值以下的档位）、**短任务上代价多大**
    （最短档、最低并发度下被框架开销吃掉的比例）、**输了多远**（本期最大的劣势倍数）。原先这几处
    结论分别埋在内联的 22 条同构 bullet 里与一张 25 行表的行间，读者要自己反推。

    三项中任何一项取不到就**少放一个卡片**；三张都取不到时整页不出——补造一个数比不出一页更坏。
    """
    conclusion = model.get("conclusion", {})
    threshold = float(conclusion.get("overhead_threshold", 0.0))
    cards: list[str] = []

    subject_crossings = [
        crossing
        for crossing in conclusion.get("overhead_crossings", [])
        if crossing["adapter"] == subject
        and crossing["first_tier_at_or_below_threshold_us"] is not None
    ]
    if subject_crossings:
        tier = min(float(crossing["first_tier_at_or_below_threshold_us"]) for crossing in subject_crossings)
        value, unit = _big_number(tier, lang)
        cards.append(
            _kpi_html(
                value,
                unit,
                t(
                    "deck.verdict.kpi.crossing",
                    lang,
                    threshold=threshold,
                    subject=subject,
                ),
            )
        )

    overhead_rows = [
        row
        for row in model.get("dimensions", {}).get("overhead", {}).get("rows", [])
        if row.get("framework_overhead_ratio") is not None
    ]
    if overhead_rows:
        concurrency = min(int(row["concurrency"]) for row in overhead_rows)
        at_concurrency = [
            row for row in overhead_rows if int(row["concurrency"]) == concurrency
        ]
        tier = min(float(row["body_tier_us"]) for row in at_concurrency)
        cell = [row for row in at_concurrency if float(row["body_tier_us"]) == tier]
        subject_row = next((row for row in cell if row["adapter"] == subject), None)
        rivals = sorted(
            (row for row in cell if row["adapter"] != subject),
            key=lambda row: float(row["framework_overhead_ratio"]),
        )
        if subject_row is not None and rivals:
            ratio = float(subject_row["framework_overhead_ratio"])
            cards.append(
                _kpi_html(
                    f"{ratio * 100:.2f}",
                    "%",
                    t(
                        "deck.verdict.kpi.overhead",
                        lang,
                        concurrency=concurrency,
                        tier=format_tier_us(tier, lang),
                        rival=rivals[0]["adapter"],
                        rival_share=float(rivals[0]["framework_overhead_ratio"]),
                    ),
                )
            )

    unfavorable = model.get("unfavorable", {}).get("items") or []
    if unfavorable:
        worst = max(unfavorable, key=lambda item: 1 / float(item["baseline_ratio_vs_subject"]))
        cards.append(
            _kpi_html(
                f"{1 / float(worst['baseline_ratio_vs_subject']):.2f}",
                "x",
                t(
                    "deck.verdict.kpi.gap",
                    lang,
                    baseline=worst["baseline"],
                    subject=subject,
                    concurrency=int(worst["concurrency"]),
                    tier=format_tier_us(float(worst["body_tier_us"]), lang),
                ),
            )
        )

    if not cards or subject is None:
        return []

    return [
        Slide(
            label=t("deck.verdict.label", lang),
            layout="hero",
            eyebrow=t("deck.verdict.eyebrow", lang),
            heading=t("deck.verdict.heading", lang, subject=subject),
            body=(
                f'<div class="kpi-grid">{"".join(cards)}</div>'
                f'<p class="deck-note">{inline(t("deck.verdict.note", lang))}</p>'
            ),
            source=t("deck.verdict.source", lang),
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


def _unfavorable_slides(
    model: dict[str, Any], sections: list[_Section], lang: str
) -> list[Slide]:
    """不利数据：**归类卡片一页 + 完整清单分页续排到底**。

    spec 的「不利数据不因版面而变小」是这一页存在的理由：清单的页数由**数据量**决定，不由任何
    "每节最多几页"的编辑策略决定。续页的标题点名它覆盖哪几个对照方案——那是真正的语义边界，
    也比一个「（续）」说得清。

    该页最差的一行加视觉标识：一行行都是败绩，读者需要一个"最糟的是这里"的落点。
    """
    slides = _unfavorable_summary_slides(model, lang)
    section = _section_by_title(sections, unfavorable_section_title(lang))
    if section is None:
        return slides
    table = next((block for block in section.blocks if block.kind == TABLE), None)
    if table is None or not table.rows:
        return slides

    multiples: dict[tuple[str, int, float], float] = {}
    for item in model.get("unfavorable", {}).get("items") or []:
        multiples[
            (str(item["baseline"]), int(item["concurrency"]), float(item["body_tier_us"]))
        ] = 1 / float(item["baseline_ratio_vs_subject"])

    def key_of(row: tuple[str, ...]) -> tuple[str, int, float]:
        return (row[0], int(row[1]), float(row[2]))

    # **分页切在对照方案的边界上**：一页里出现半个方案的档位，读者会以为那一页就是它的全部。
    # 组序按"最差的那一档差多少"降序——读者第一个要看的正是输得最惨的那家。
    groups: dict[str, list[tuple[str, ...]]] = {}
    for row in table.rows:
        groups.setdefault(row[0], []).append(row)
    ordered_groups = sorted(
        groups.values(),
        key=lambda rows: (-max(multiples.get(key_of(row), 0.0) for row in rows), rows[0][0]),
    )

    capacity = SLIDE_ROW_CAP - 1
    pages: list[list[tuple[str, ...]]] = []
    current: list[tuple[str, ...]] = []
    for rows in ordered_groups:
        # 单组本身就装不下时按行硬切（对照方案的档位数超过一页容量）
        for start in range(0, len(rows), capacity):
            piece = rows[start : start + capacity]
            if current and len(current) + len(piece) > capacity:
                pages.append(current)
                current = []
            current += piece
            if len(current) >= capacity:
                pages.append(current)
                current = []
    if current:
        pages.append(current)

    return slides + _unfavorable_list_slides(table, pages, multiples, key_of, lang)


def _unfavorable_list_slides(
    table: Block,
    pages: list[list[tuple[str, ...]]],
    multiples: dict[tuple[str, int, float], float],
    key_of: Any,
    lang: str,
) -> list[Slide]:
    """完整清单的续排页：一页一段对照方案，页标题点名覆盖了哪几家。"""
    slides: list[Slide] = []
    for rows in pages:
        names = _join(sorted({row[0] for row in rows}), lang)
        heading = (
            t("deck.unfavorable.list_label", lang)
            if len(pages) == 1
            else t("deck.unfavorable.continued", lang, baselines=names)
        )
        marks: dict[tuple[int, int], str] = {}
        worst_row, worst_value = -1, 0.0
        for row_index, row in enumerate(rows):
            value = multiples.get(key_of(row), 0.0)
            if value > worst_value:
                worst_row, worst_value = row_index, value
        if worst_row >= 0:
            # 差距那一列是第 4 列（0 起数第 3 个）
            marks[(worst_row, 3)] = "worst"
        slides.append(
            Slide(
                label=heading,
                eyebrow=unfavorable_section_title(lang),
                heading=heading,
                body=_table_piece(table, tuple(rows), marks),
                source=t("deck.content.source", lang, section=unfavorable_section_title(lang)),
            )
        )
    return slides


def _wins_slides(model: dict[str, Any], sections: list[_Section], lang: str) -> list[Slide]:
    """赢在哪 + 分不出胜负：**同页两段，但各自独立**。

    分不出胜负那一段 MUST NOT 被并入优势数据（spec），故它单独成段、给计数与带宽来源；它的明细
    表（每个分不出的档位一行）留在全文——它有一百多行，而 deck 的职责是给出计数与判据。

    优势清单与不利清单**结构对称**（spec 要求两节同等显著），故它按同一套办法分页：切在对照方案
    的边界上、续页标题点名覆盖了哪几家。实测确有对照方案赢过的档位多到一页装不下（提交的留档里
    有 14 档优势 + 表头共 15 行），那时**必须**续排——`_stack_or_split` 会因为一栏装不下而抛，
    渲染整个失败。
    """
    favorable = model.get("favorable") or {}
    tied = model.get("tied") or {}
    items = favorable.get("items") or []
    tied_items = tied.get("items") or []
    if not items and not tied_items:
        return []

    heading = t("deck.wins.heading", lang, wins=len(items), ties=len(tied_items))
    section = _section_by_title(sections, t("blocks.favourable.title", lang))
    table = next(
        (block for block in (section.blocks if section else ()) if block.kind == TABLE), None
    )

    tied_html = ""
    tied_rows = 0
    if tied_items:
        counts: dict[str, int] = {}
        for item in tied_items:
            counts[str(item["baseline"])] = counts.get(str(item["baseline"]), 0) + 1
        per_baseline = _join(
            [
                t("deck.wins.tied_item", lang, baseline=baseline, count=count)
                for baseline, count in sorted(counts.items())
            ],
            lang,
        )
        text = t(
            "deck.wins.tied",
            lang,
            count=len(tied_items),
            per_baseline=per_baseline,
            low=float(tied["band_min"]) if tied.get("band_min") is not None else 0.0,
            high=float(tied["band_max"]) if tied.get("band_max") is not None else 0.0,
        )
        tied_html = f'<p class="deck-note">{inline(text)}</p>'
        tied_rows = _paragraph_rows(text)

    if table is None or not table.rows:
        return [
            Slide(
                label=t("deck.wins.label", lang),
                eyebrow=t("deck.wins.eyebrow", lang),
                heading=heading,
                body=tied_html,
                source=t("deck.wins.source", lang),
            )
        ]

    # 优势那一列的行按**对照方案**分组；组序按"快得最多"降序——读者第一个要看的是赢得最利落的那家
    best: dict[str, float] = {}
    for item in items:
        name = str(item["baseline"])
        best[name] = max(best.get(name, 0.0), float(item["baseline_ratio_vs_subject"]))
    groups: dict[str, list[tuple[str, ...]]] = {}
    for row in table.rows:
        groups.setdefault(row[0], []).append(row)
    ordered = sorted(groups.values(), key=lambda rows: (-best.get(rows[0][0], 0.0), rows[0][0]))

    capacity = SLIDE_ROW_CAP - 1
    chunks: list[list[tuple[str, ...]]] = []
    current: list[tuple[str, ...]] = []
    for rows in ordered:
        for start in range(0, len(rows), capacity):
            piece = rows[start : start + capacity]
            if current and len(current) + len(piece) > capacity:
                chunks.append(current)
                current = []
            current += piece
            if len(current) >= capacity:
                chunks.append(current)
                current = []
    if current:
        chunks.append(current)

    slides: list[Slide] = []
    for index, rows in enumerate(chunks):
        # 每一页标出**本页快得最多**的那一行：一列都是胜绩，读者需要一个落点
        marks: dict[tuple[int, int], str] = {}
        if rows:
            top = max(range(len(rows)), key=lambda i: best.get(rows[i][0], 0.0))
            marks[(top, 3)] = "win"
        table_html = _table_piece(table, tuple(rows), marks)
        if index == 0 and tied_rows:
            # 叠起来放不下就分栏——分栏本就自带"两段并列"的观感，正好是 spec 要的独立成段
            body, _used = _stack_or_split(
                table_html, len(rows) + 1, tied_html, tied_rows, t("deck.wins.label", lang)
            )
            title = heading
        else:
            body = table_html
            title = t(
                "deck.wins.continued",
                lang,
                baselines=_join(sorted({row[0] for row in rows}), lang),
            )
        slides.append(
            Slide(
                label=t("deck.wins.label", lang) if index == 0 else title,
                eyebrow=t("deck.wins.eyebrow", lang) if index == 0 else "",
                heading=title,
                body=body,
                source=t("deck.wins.source", lang),
            )
        )
    return slides


def _attribution_slides(
    model: dict[str, Any], sections: list[_Section], lang: str
) -> list[Slide]:
    """开销归因：**每任务超出最大的那几档**，外加维度状态。

    这一维在本期是 24 行明细加四十来条结论句——整节铺进幻灯片要十几页，而那正是 deck 存在的
    反面。故只取按超出绝对值排序的前几档（那是"超出的部分落在哪一段"这个问题的答案所在），
    并在页面上**明说这是前几档、完整清单在全文哪一节**：给指针而不是悄悄截断。
    """
    attribution = model.get("dimensions", {}).get("attribution") or {}
    title = dimension_section_prefix(lang) + str(attribution.get("title", ""))
    section = _section_by_title(sections, title)
    if section is None:
        return []

    findings_title = t("blocks.attribution.findings_title", lang)
    table = None
    collecting = False
    for block in section.blocks:
        if block.kind == HEADING:
            collecting = block.text == findings_title
        if collecting and block.kind == TABLE and table is None:
            table = block
    if table is None or not table.rows:
        return []

    # 取模型里的数值（秒）排序，不从渲染好的字符串反解——正负号与单位随语言变
    excesses = [
        abs(float(row["excess_seconds"]))
        for finding in attribution.get("findings", [])
        for row in finding["per_adapter"]
    ]
    order = list(range(len(table.rows)))
    if len(excesses) == len(table.rows):
        order.sort(key=lambda index: excesses[index], reverse=True)

    # 先算能放几行，再把"放了几行"写回说明里：说明本身的长度几乎不随这个数变（差一两个字符），
    # 故用 shown=0 估一次行数即可。
    estimate = t("deck.attribution.note", lang, shown=0, total=len(table.rows), section=title)
    capacity = max(1, SLIDE_ROW_CAP - 1 - _paragraph_rows(estimate) - 1)
    shown = order[:capacity]
    note_text = t(
        "deck.attribution.note",
        lang,
        shown=len(shown),
        total=len(table.rows),
        section=title,
    )
    rows = tuple(table.rows[index] for index in shown)
    marks = {(0, 3): "worst"} if rows else {}

    status = t("blocks.semantics.status", lang, status=attribution.get("status"))
    body = _table_piece(table, rows, marks) + f'<p class="deck-note">{inline(note_text)}</p>'
    if attribution.get("reason"):
        body += f'<p class="deck-note">{inline(str(attribution["reason"]))}</p>'

    return [
        Slide(
            label=title,
            eyebrow=title,
            heading=title,
            lead=status,
            body=body,
            source=t("deck.content.source", lang, section=title),
        )
    ]


def _blocks_page(blocks: tuple[Block, ...], what: str) -> tuple[str, int]:
    """把一小组块排进**一页**；放不下即**抛**。

    **不截断**：只取首片会让版面安静地少一截，而"少了一截"正是本模块要拦的失败形态。放不下就
    明确失败，由调用方决定改用哪种切分（如分栏、或按语义分页）。
    """
    if not blocks:
        return "", 0
    chunks = _chunk(_section_pieces(blocks))
    if len(chunks) > 1:
        raise DeckOverflowError(
            f"「{what}」这一栏的内容有 {len(chunks)} 页的量，装不进一栏。"
            "需要给这一页定义更细的切分，或改用分栏以外的排法。"
        )
    return _chunk_html(chunks[0]), sum(piece.rows for piece in chunks[0])


def _stack_or_split(
    left_html: str, left_rows: int, right_html: str, right_rows: int, what: str
) -> tuple[str, int]:
    """两块内容：叠起来放得下就叠，放不下就分栏；分栏也放不下即**抛**。

    分栏的纵向占用取两栏的较大者——这正是它能多塞内容的原因。
    """
    if not left_html:
        return right_html, right_rows
    if not right_html:
        return left_html, left_rows
    if left_rows + right_rows <= SLIDE_ROW_CAP:
        return left_html + right_html, left_rows + right_rows
    rows_used = max(left_rows, right_rows)
    if rows_used > SLIDE_ROW_CAP:
        raise DeckOverflowError(
            f"「{what}」一页放不下（约 {left_rows} + {right_rows} 行 > {SLIDE_ROW_CAP} 行），"
            "分栏也装不下。需要给这一页定义更细的语义切分。"
        )
    return _two_column(left_html, right_html), rows_used


def _repro_slides(model: dict[str, Any], sections: list[_Section], lang: str) -> list[Slide]:
    """可复核性：运行环境 + 自检结论 + 读数口径的引导。

    **不再印 PID 列表**：自检那一条由 :mod:`.blocks` 给出可读结论（检查了多少个单元、是否通过），
    原始结构只在留档里。

    **三条读数纪律只留引导**（"见 report.md 的〈测量口径〉一节" + 那句"不可跨运行比较"）：它们的
    英文逐字比中文长一倍（实测英文侧 18 行、中文侧 6 行），两栏也装不下，而**页数在中英之间必须
    一致**——按内容长度决定页数会让两种语言的 deck 页数对不上。全文里它们逐字可查。

    分栏是必需的：环境那一表十几行，叠上自检就超出一页——而分栏的纵向占用取两栏的较大者。
    """
    environment = _section_by_title(sections, t("blocks.env.title", lang))
    self_check = _section_by_title(sections, t("blocks.self_check.title", lang))
    if environment is None and self_check is None:
        return []

    # 环境那一节的二级标题去掉（页面标题已经说了这是运行环境）；自检的小标题留着，它给自己的
    # 那几条做标注。
    merged: tuple[Block, ...] = (
        tuple(
            block
            for block in environment.blocks
            if not (block.kind == HEADING and block.level <= 2)
        )
        + tuple(self_check.blocks if self_check else ())
        + (
            Block(
                NOTE,
                text=t(
                    "deck.repro.caliber",
                    lang,
                    section=t("blocks.methodology.title", lang),
                    note=t("report.absolute_note", lang),
                ),
            ),
        )
    )

    return _compose_slides(
        t("deck.repro.heading", lang),
        merged,
        lang,
        label=t("deck.repro.label", lang),
        eyebrow=t("deck.repro.eyebrow", lang),
        source=t("deck.repro.source", lang, path=_archive_path(model)),
    )


def _archive_path(model: dict[str, Any]) -> str:
    source = model.get("source") or {}
    return (
        repository_relative_path(str(source["path"]))
        if source.get("path")
        else t("deck.cover.source.unrecorded", model.get("lang", ""))
    )


def _pointer_slides(model: dict[str, Any], lang: str) -> list[Slide]:
    """收尾：deck 装不下的明细去哪找，说清楚。"""
    absolute = model.get("absolute") or {}
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
    # **有效单元数取真正成功的那一批**：留档的 unit_count 是"计划测多少个"，其中失败的那些不
    # 入统计。原先把它当"有效单元"印出来，于是同一条 bullet 里出现"96 个有效、4 个失败"这种
    # 自相矛盾的读法。
    valid = absolute.get("valid_unit_count")
    failed = absolute.get("failed_unit_count")
    if valid is not None and failed is not None:
        items.append(
            t("deck.pointer_page.item.units", lang, total=int(valid), failed=int(failed))
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
    sections = _sections(blocks)

    # **页序列按语义定，不按"块列表的顺序 + 每节最多几页"定**（design D9）。原先 deck 是全文的
    # 分页版：块列表里有什么就依次摊开，每节截断到两页再补一页整页指引。现在每一页都有明确的
    # 职责，而"哪些内容不进 deck"是**逐节显式声明**的（见下）。
    slides: list[Slide] = [_cover_slide(model, subject, lang)]
    slides += _verdict_slides(model, subject, lang)
    slides += _section_slides(_section_by_title(sections, choice_section_title(lang)), lang)
    slides += _section_slides(_section_by_title(sections, turning_section_title(lang)), lang)
    # 一个度量维度一页（条形），顺序与 _CHART_SPECS 的声明一致
    for key, spec in _CHART_SPECS.items():
        dimension = dimensions.get(key)
        if isinstance(dimension, dict):
            slides += _chart_slides(subject, key, dimension, spec, lang)
    slides += _wins_slides(model, sections, lang)
    slides += _unfavorable_slides(model, sections, lang)
    slides += _attribution_slides(model, sections, lang)
    native_title = dimension_section_prefix(lang) + resolve_text(
        (dimensions.get("semantics") or {}).get("title") or "", lang
    )
    slides += _section_slides(_section_by_title(sections, native_title), lang)
    # **兜底**：任何没在上面被显式处理的「维度：」章节也要出一页。页序是显式声明的（design D9），
    # 而"显式声明"最容易犯的错就是漏——别的变更往报告里加一个新维度时，这一层不会有任何提示，
    # 那个维度就**静默消失**了（实测：native-line 维度正是这样加进来的）。这一条与 spec 的
    # 「MUST NOT 因此省略该维度」是同一件事。
    handled = {
        dimension_section_prefix(lang) + str((dimensions.get(key) or {}).get("title", ""))
        for key in _CHART_SPECS
    }
    handled.add(native_title)
    handled.add(
        dimension_section_prefix(lang)
        + str((dimensions.get("attribution") or {}).get("title", ""))
    )
    for section in sections:
        if section.title.startswith(dimension_section_prefix(lang)) and section.title not in handled:
            slides += _section_slides(section, lang)
    slides += _repro_slides(model, sections, lang)
    slides += _pointer_slides(model, lang)
    return _assemble(slides, resolved_title, lang)
