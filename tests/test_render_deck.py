"""deck 报告页的验证（spec: comparison-report 的 deck 形态要求）。

这里的断言分三类，**对象刻意不同**：

- **框架块**：设计交付物里标了 DO NOT EDIT 的部分原样 vendor。钉的是**字节**（哈希），因为
  "没被改过"这件事只能逐字节说清——"看起来还在"说明不了什么。
- **内容覆盖**：不利档位在 deck 里必须**一个不少**。钉的是**集合相等**，不是"包含若干"。
- **同源**：deck 与 Markdown 是两条独立路径，断言写成"同一个数在两边都出现且相等"。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from zoo_bench.render import blocks as blocks_module
from zoo_bench.render import deck as deck_module
from zoo_bench.render import markdown as markdown_renderer
from zoo_bench.render.assets import DECK_FRAMEWORK_JS, deck_asset

#: 四份 vendor 资产的 sha256（**按行尾归一后的文本算**，见 :func:`_asset_digest`）。它们取自一次
#: 设计交付物、且标着 DO NOT EDIT，故改动只应来自"换了一版设计稿"——那时连同这个哈希一起更新，
#: 改动便留下了痕迹。改坏了样式而哈希没动，这里是拦得住的那道门。
_VENDORED_SHA256 = {
    "deck_framework.css": "2a2e0819a1e7aefcdfecd14d3f8028381244bef8659173dbc36b1d76a6b1dfd6",
    "deck_slides.css": "5a81e693a5a73a0e386f6a44e63e250b7fa9b19da1ed7a6e920d0e6fe36047d1",
    "deck_chrome.html": "07d5b0490a81e25dafa8e0cb8e424adae19d4466d866b96c22cf8a0d9c646187",
    "deck_framework.js": "e6149af9ee1c5e1ea5fe1255ce2c084a7a8f3a62435da634fae54713ff7f3034",
}


def _asset_digest(path: Path) -> str:
    """资产的 sha256，**按行尾归一后的文本算**。

    按原始字节算会在换行风格上翻车：本机 ``core.autocrlf=true``，一次全新检出就把 LF 变成 CRLF，
    哈希随即对不上——而那份差异与"框架块有没有被改动"毫无关系。故先按通用换行读成文本再编码，
    让这道门只对**内容**说话。
    """
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


#: 真实 slide 才带 ``data-screen-label``——框架 CSS 的注释里也含 ``<section class="slide">``
#: 这段字面量，只按标签切会从注释切起。
_SLIDE = re.compile(
    r'<section class="slide[^>]*data-screen-label="([^"]*)"[^>]*>(.*?)</section>', re.S
)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td>(.*?)</td>", re.S)


def _model(
    *, ratio: float = 0.0157, baselines: tuple[str, ...] = ("bare_thread",)
) -> dict[str, Any]:
    """一份小而有代表性的模型：两个方案、两个档位、两个并发度。

    不利数据用 ``baselines`` 逐条复制出来，故条数由调用方定——"一条不少"那类断言要靠它放大。
    """
    latency_rows = []
    overhead_rows = []
    throughput_rows = []
    for concurrency in (1, 4):
        for tier in (40.0, 10000.0):
            latency_rows.append(
                {
                    "adapter": "zoo",
                    "concurrency": concurrency,
                    "body_tier_us": tier,
                    "end_to_end_per_task": {
                        "median": 0.0002,
                        "p95": 0.0003,
                        "p99": 0.0004,
                        "relative_spread": 0.05,
                        "n": 30,
                    },
                }
            )
            overhead_rows.append(
                {
                    "adapter": "zoo",
                    "concurrency": concurrency,
                    "body_tier_us": tier,
                    "framework_overhead_seconds": ratio * 0.0002,
                    "framework_overhead_ratio": ratio,
                    "body_seconds": 0.0002,
                    "interpretable": True,
                    "note": "",
                }
            )
            throughput_rows.append(
                {
                    "adapter": "zoo",
                    "concurrency": concurrency,
                    "body_tier_us": tier,
                    "throughput_per_second": 5000.0 * concurrency,
                }
            )
            if tier == 40.0:
                continue
            for baseline in baselines:
                latency_rows.append(
                    {
                        "adapter": baseline,
                        "concurrency": concurrency,
                        "body_tier_us": tier,
                        "end_to_end_per_task": {
                            "median": 0.00025,
                            "p95": 0.0003,
                            "p99": 0.0004,
                            "relative_spread": 0.05,
                            "n": 30,
                        },
                    }
                )
                overhead_rows.append(
                    {
                        "adapter": baseline,
                        "concurrency": concurrency,
                        "body_tier_us": tier,
                        "framework_overhead_seconds": ratio * 0.00025,
                        "framework_overhead_ratio": 0.021,
                        "body_seconds": 0.00025,
                        "interpretable": True,
                        "note": "",
                    }
                )
                throughput_rows.append(
                    {
                        "adapter": baseline,
                        "concurrency": concurrency,
                        "body_tier_us": tier,
                        "throughput_per_second": 4500.0 * concurrency,
                    }
                )

    unfavorable = [
        {
            "baseline": baseline,
            "concurrency": concurrency,
            "body_tier_us": tier,
            "subject_median_seconds": 0.0002,
            "baseline_ratio_vs_subject": 0.8,
            "band": 0.04,
            "gap": f"{baseline} 比 zoo 快 1.25x",
        }
        for baseline in baselines
        for concurrency in (1, 4)
        for tier in (40.0, 10000.0)
    ]

    return {
        # 真实留档路径来自 CLI，是**绝对路径**（本机 `F:\...`、CI `/home/runner/...`）
        "source": {
            "path": r"F:\Python\zoo\zoo-bench\results\zoo-framework-9.9.9\20260101T000000Z.json"
        },
        "subject": "zoo",
        "environment": {
            "hardware": {"cpu_model": "测试 CPU", "logical_cores": 8, "platform": "Test-AMD64"},
            "os": {"system": "TestOS", "release": "1", "version": "1.0"},
            "python": {"version": "3.13.0", "implementation": "CPython", "executable": "/py"},
            "subject": {
                "dist_version": "9.9.9",
                "module_version": "1.0",
                "note": "以发行元数据为准",
            },
            "harness": {"version": "0.0.1", "commit": "deadbeef"},
            "command": ["zoo-bench", "run"],
        },
        "run": {"unit_count": 16, "failed_unit_count": 0, "started_at_epoch": 1_767_225_600.0},
        "load": {"is_stand_in": True, "composition": "JSON 编解码", "caveat": "负载是替身"},
        "conclusion": {
            "overhead_threshold": 0.15,
            "overhead_crossings": [],
            "relative_turnings": [],
            "summary": ["结论一句。"],
            "note": "单点加速比没有选型含义",
        },
        "dimensions": {
            "latency": {
                "title": "延迟分位数与抖动",
                "note": "端到端 / 并发度",
                "rows": latency_rows,
            },
            "overhead": {
                "title": "框架自身开销占比",
                "note": "同一次运行内埋点",
                "rows": overhead_rows,
            },
            "throughput": {
                "title": "吞吐与并发伸缩",
                "note": "并发度 / 端到端",
                "rows": throughput_rows,
            },
            "semantics": {
                "title": "调度语义的代价",
                "status": "not_measured",
                "scope_note": "内部开关对照",
                "items": [],
                "reason": "不可测",
            },
        },
        "favorable": {"items": [], "found": False, "note": "同源"},
        "unfavorable": {"items": unfavorable, "found": True, "note": "缺失即不合格"},
        "tied": {"items": [], "found": False, "note": "无"},
        "caveats": [{"kind": "被测层级", "text": "调度派发层"}],
        "absolute": {"note": "不可跨运行比较", "process_isolation": {"all_distinct": True}},
        "self_check": {"ok": True},
    }


def _deck(model: dict[str, Any]) -> str:
    return deck_module.render_deck(model, blocks_module.build_blocks(model, []))


def _slides(html: str) -> list[tuple[str, str]]:
    return [(label, body) for label, body in _SLIDE.findall(html)]


# ------------------------------------------------------------------ 框架块原样


def test_vendored_assets_are_unchanged() -> None:
    """标着 DO NOT EDIT 的四份资产必须与 vendor 时逐字节一致。

    **为什么钉哈希而不是"看起来还在"**：框架块的改动不会让任何一条功能断言变红——样式走样
    只在浏览器里看得见。故"没被改过"这件事只能逐字节说清。
    """
    directory = Path(deck_module.__file__).parent

    for name, expected in _VENDORED_SHA256.items():
        assert _asset_digest(directory / name) == expected, (
            f"{name} 与 vendor 时不一致。若是有意换了一版设计稿，请连同本用例里的哈希一起更新。"
        )


def test_framework_blocks_reach_the_page_verbatim() -> None:
    """框架块不只是"存在仓库里"，而是**原样进了页面**。"""
    page = _deck(_model())

    for name in _VENDORED_SHA256:
        text = (Path(deck_module.__file__).parent / name).read_text(encoding="utf-8").rstrip("\n")
        assert text in page, f"{name} 的内容没有原样出现在页面里"


def test_do_not_edit_markers_survive() -> None:
    """那两句 DO NOT EDIT 必须还在——它们是下一个改样式的人唯一的提示。"""
    page = _deck(_model())

    assert "DO NOT EDIT" in page
    assert "Framework chrome" in page


# ------------------------------------------------------------------ 封面


def test_cover_carries_the_three_facts_the_design_asks_for() -> None:
    """封面只放三件事：**哪一版、哪一天、哪台机器**，标题分两行。

    逻辑核数、Python 版本、复现命令都在「运行环境」那页——封面堆满反而看不清。
    """
    cover = _slides(_deck(_model()))[0][1]

    assert '<p class="eyebrow">zoo-bench · 2026-01-01 · 测试 CPU</p>' in cover
    assert '<h1 class="h-hero">zoo-framework 9.9.9<br>性能评测</h1>' in cover
    assert "共 1 个对照方案的同口径对比" in cover


def test_cover_and_closing_show_a_repository_path_not_the_build_machine() -> None:
    """出处写**仓库内相对路径**，不写构建机的绝对路径。

    留档路径由 CLI 给出、天生是绝对的（本地 ``F:\\...``、CI ``/home/runner/...``），两种印在
    公开站点上都指不到读者能打开的位置。故从 ``results`` 那一段起截。
    """
    page = _deck(_model())

    expected = "<code>results/zoo-framework-9.9.9/20260101T000000Z.json</code>"
    assert page.count(expected) == 2, "封面与末页各应有一处"
    assert "F:\\" not in page, "绝对路径不应出现在报告里"


# ------------------------------------------------------------------ 一页一个结论


def test_every_slide_states_where_its_numbers_come_from() -> None:
    """每页都有页脚出处。deck 会被单独转发（截图、PPT），页脚是唯一还在的出处。"""
    slides = _slides(_deck(_model()))

    assert slides, "至少该有封面"
    for label, body in slides:
        assert '<p class="src">' in body, f"这一页没有数据出处：{label}"


def test_no_slide_is_empty() -> None:
    """空页 = 渲染把内容丢了，而版面看不出来。"""
    for label, body in _slides(_deck(_model())):
        assert re.search(r"<(p|h2|h3|ul|table|div|img)\b", body), f"这一页没有内容：{label}"


# ------------------------------------------------------------------ 不利数据一条不少


def _unfavorable_tiers_in(html: str) -> Counter[tuple[str, int, float]]:
    found: Counter[tuple[str, int, float]] = Counter()
    for label, body in _slides(html):
        # **续页也算**：标签是「公开的不利数据（续）」，只匹配首片会把后面几页的行全漏掉，
        # 而"漏掉"正是这条用例要拦的东西。
        if not label.split(" ", 1)[1].startswith(blocks_module.UNFAVORABLE_SECTION_TITLE):
            continue
        for row in _ROW.findall(body):
            cells = _CELL.findall(row)
            if len(cells) < 3:
                continue
            found[(cells[0], int(cells[1]), float(cells[2]))] += 1
    return found


def test_unfavorable_slides_cover_every_tier_and_nothing_more() -> None:
    """deck 里的不利档位与模型里的**完全相等**。

    spec 的要求是"不利数据不因版面而变小"：只列一部分、或添上模型里没有的档位，两条都是不合格。
    故断的是集合（含重复计数）相等，不是"包含了"。

    **夹具必须大到会触发分页上限**：条数少于一页时，截断这件事不会发生，这条断言也就无从证明
    ——实测用 12 条时，把不利数据的豁免去掉它照样通过。
    """
    model = _model(baselines=tuple(f"rival_{index:02d}" for index in range(8)))
    expected = Counter(
        (item["baseline"], int(item["concurrency"]), float(item["body_tier_us"]))
        for item in model["unfavorable"]["items"]
    )

    assert len(expected) > deck_module.SLIDE_ROW_CAP * deck_module.SLIDES_PER_SECTION, (
        "夹具要跨过每节上限，否则这条断言证明不了「不许只列一部分」"
    )
    assert _unfavorable_tiers_in(_deck(model)) == expected


def test_unfavorable_is_not_capped_like_other_sections() -> None:
    """不利数据不吃"每节最多两页"的上限——分页续排到底，且**不该出现指引页**。

    这一条防的是"顺手给所有章节加上限"：那样改动只在不利数据多起来时才露出来，而它恰恰是
    报告最不能少的一节。

    断言「没有明细指引页」而不是「页数够多」：指引页的标签也以本节名开头，按页数数会把它算进
    去——实测那样写时，去掉豁免这条用例照样通过。
    """
    baselines = tuple(f"rival_{index:02d}" for index in range(8))
    model = _model(baselines=baselines)
    labels = [label.split(" ", 1)[1] for label, _ in _slides(_deck(model))]

    assert (
        len(model["unfavorable"]["items"])
        > deck_module.SLIDE_ROW_CAP * deck_module.SLIDES_PER_SECTION
    )
    assert not [label for label in labels if "明细指引" in label and "不利数据" in label], (
        "不利数据被截断了——它多出来的部分被送去全文，而不是续排"
    )


def test_the_disadvantage_summary_names_every_rival() -> None:
    """归类页必须把每个赢过被测框架的方案都点到名。"""
    model = _model(baselines=("bare_thread", "thread_pool"))
    page = _deck(model)
    summary = [body for label, body in _slides(page) if label.endswith("劣势归类")]

    assert len(summary) == 1
    for baseline in ("bare_thread", "thread_pool"):
        assert baseline in summary[0]


# ------------------------------------------------------------------ 数值以条形编码


def test_bars_print_the_value_beside_each_bar() -> None:
    """条形只表达相对长短，精度由旁边的数字承担——故每个条形都要有数。"""
    page = _deck(_model())
    # 条形结构：`<div class="bar-track"><div class="bar …"></div></div><span class="bar-value">`
    bars = re.findall(
        r'<div class="bar [^"]*"[^>]*></div></div><span class="bar-value">([^<]*)</span>', page
    )

    assert bars, "延迟/开销/吞吐三个维度都该有条形图"
    for value in bars:
        assert value.strip() and value.strip() != "—", f"条形旁没有数：{value!r}"


def test_subject_bar_is_the_baseline_of_one() -> None:
    """被测框架自己恒为 1.0——倍数一律"越长越差"，读者不必按维度换算方向。"""
    page = _deck(_model())
    subject_bars = re.findall(
        r'<span class="bar-label">zoo</span>.*?style="--v: ([\d.]+)"', page, re.S
    )

    assert subject_bars
    assert all(float(value) == 1.0 for value in subject_bars)


def test_each_charted_dimension_keeps_its_value_table() -> None:
    """图形不替代数值表：条形给结论，表给可核对的数。"""
    page = _deck(_model())

    for title in ("延迟分位数与抖动", "框架自身开销占比", "吞吐与并发伸缩"):
        assert f"{blocks_module.DIMENSION_SECTION_PREFIX}{title}" in page, f"少了维度章节：{title}"
        assert f'<h2 class="h-md">{title}：并发度' in page, f"少了 {title} 的条形图页"


# ------------------------------------------------------------------ 同源


def test_hero_number_equals_the_markdown_figure() -> None:
    """核心结论那个大数字必须与全文里的同一个数相等。

    两条路径（deck 取模型的原始浮点、全文取块层的格式化字符串）若各算各的，第一个不一致的
    地方就是这里，而它恰恰是读者最先记住的数。
    """
    model = _model(ratio=0.0157)
    page = _deck(model)
    markdown = markdown_renderer.render_markdown(model, [])

    match = re.search(r'<div class="stat-num">([\d.]+)<span class="unit">%</span>', page)
    assert match, "少了核心结论的大数字页"
    assert f"{match.group(1)}%" in markdown


def test_disadvantage_count_matches_the_full_text() -> None:
    """归类页那句"在 N 个档位上更慢"的 N 必须等于不利数据的实际条数。"""
    model = _model(baselines=("bare_thread", "thread_pool", "process_pool"))
    page = _deck(model)

    count = len(model["unfavorable"]["items"])
    assert f"在 {count} 个档位上更慢" in page


# ------------------------------------------------------------------ 滚轮翻页与入场动画（追加层）


def test_wheel_paging_lives_in_an_added_layer_not_in_the_framework() -> None:
    """滚轮翻页必须在**追加**的脚本里，框架脚本一个字都不能动。

    框架块是设计交付物、还被 sha256 钉着；把滚轮塞进去会同时毁掉"原样"与"可独立替换"两件事。
    故这条两头都断：页面里有滚轮处理器，而 vendored 脚本里**没有** `wheel`。
    """
    page = _deck(_model())
    framework = deck_asset(DECK_FRAMEWORK_JS)

    assert "addEventListener('wheel'" in page
    assert "wheel" not in framework, "框架脚本里出现了 wheel——那说明它被改写了"


#: 极简 CSS 规则抽取：只取"选择器 { 声明 }"这一层。`@` 排除在外，故 at-rule 的前缀不会被当成
#: 选择器；`from` / `to` 是关键帧内部的步骤名，另行排除。
_CSS_RULE = re.compile(r"([^{}@]+)\{([^{}]*)\}", re.S)
_KEYFRAME_STEPS = frozenset({"from", "to"})


def _css_rules(page: str) -> list[tuple[str, str]]:
    style = "".join(re.findall(r"<style>(.*?)</style>", page, re.S))
    return [
        (selector.strip(), declarations)
        for selector, declarations in _CSS_RULE.findall(style)
        if selector.strip() not in _KEYFRAME_STEPS
    ]


def test_animation_initial_state_hangs_on_a_javascript_only_class() -> None:
    """动画的初始态必须挂在 **JS 才可能加的类** 上。

    渲染时若让动画（或 `opacity: 0`）落在普通选择器上，禁用 JavaScript 的读者看到的是空白页
    ——而这种错在开了 JS 的浏览器里永远看不见。

    **判据是逐条规则查，不是查子串、也不是逐行查**：先前两版都不行——查子串时 `nth-child` 那几条
    延时规则里也含 `.slide.active.deck-anim > *`；逐行查时选择器与声明不在同一行，会把合法的那条
    也判成违规。两版都在注入违规实现时暴露了（前者漏判、后者误判）。
    """
    page = _deck(_model())

    assert "from { opacity: 0; transform: translateY(12px); }" in page, "关键帧里该有初始态"
    offenders = [
        selector
        for selector, declarations in _css_rules(page)
        if ("deck-enter" in declarations or "opacity: 0;" in declarations)
        and "deck-anim" not in selector
    ]
    assert not offenders, f"这些规则会让未启用 JavaScript 的读者看不到内容：{offenders}"


def test_print_and_reduced_motion_explicitly_turn_the_animation_off() -> None:
    """打印与「减少动态效果」都要**显式**关掉动画。

    打印那条尤其要紧：`animation-fill-mode: both` 在动画尚未开始时呈现的正是初始态（opacity:0），
    打印会抓到它——那一页打出来是空白。不能指望"动画自己会跑完"。
    """
    page = _deck(_model())

    assert "animation: none !important; opacity: 1 !important; transform: none !important;" in page
    assert "@media (prefers-reduced-motion: reduce)" in page
    assert ".slide > *, .slide .bar { animation: none !important; }" in page
    # **孙子层也要管到**：条形在 .chart 里面，`.slide > *` 覆盖不到它——只写直接子元素的话，
    # 打印会抓到 scaleX(0)（零长条形），reduced-motion 下条形也会从零长起来。
    assert page.count(".slide > *, .slide .bar {") == 2, (
        "打印与 reduced-motion 两条都要含 .slide .bar"
    )


def test_bars_grow_from_zero_only_when_javascript_runs() -> None:
    """条形增长必须是"JS 在时才播"的动画，静止态就是终值。

    用 `scaleX` 而不是动画 `width`：后者的目标是 `calc(var(--v) / var(--max) * 100%)`，得在 JS 里
    读计算值或把那条 calc 复制一份。故断言 `scaleX(` **只出现在关键帧里**——出现在别处的规则里就
    意味着元素有了一个"静止时也是缩小"的状态，禁用 JavaScript 的读者会看到零长条形。
    """
    page = _deck(_model())

    assert "@keyframes deck-bar-grow" in page
    assert ".slide.active.deck-anim .bar {" in page
    assert "transform-origin: left center" in page
    assert "--deck-bar-delay" in page, "条形要按行错开，延时变量得在场"
    # **按规则查，不按子串**：注释里也会出现 `scaleX(`，按子串计数会被注释骗（实测被自己的注释
    # 骗过一次）。`_css_rules` 已排除关键帧的 from/to，故这里只要出现就等于"静止时也是缩放态"。
    assert not [
        selector for selector, declarations in _css_rules(page) if "scaleX(" in declarations
    ], "缩放只能写在关键帧里，否则禁用 JavaScript 时条形是零长的"


def _lines_starting_with(page: str, prefix: str) -> list[str]:
    """页面里以某段代码**开头的行**（去掉缩进后比对）。

    **不按子串查**：子串会被"包一层"绕过——实测把处理器前面加上 `if (false)`，子串照样在场，
    断言照过。查行首则要求那一步是它自己的一行、没有被套进任何条件里。
    """
    return [line.strip() for line in page.splitlines() if line.strip().startswith(prefix)]


def test_numeric_animation_never_invents_a_number() -> None:
    """大数字的滚动**不得**成为数值的来源。

    三条一起成立才算数：① 渲染出来的 DOM 里就是原值（禁用 JavaScript 的读者看到的就是真值）；
    ② 滚动收尾把 `nodeValue` **逐字**写回捕获到的原值（不是重新 `toFixed`——那会把 `4.7` 改成
    `4.70`）；③ 打印恰好发生在滚动途中时有 `beforeprint` 强制收尾，否则印出来的是中间值。
    """
    page = _deck(_model())

    assert '<div class="stat-num">1.57<span class="unit">%</span></div>' in page, "DOM 里就该是真值"
    assert _lines_starting_with(page, "node.nodeValue = original"), "收尾必须逐字写回原值"
    assert _lines_starting_with(page, "window.addEventListener('beforeprint'"), (
        "打印途中要强制收尾——而且必须是无条件的，不能被套进任何判断里"
    )


def test_wheel_debounce_parameters_are_pinned() -> None:
    """阈值、冷却与行模式换算都要在场。

    冷却被"顺手删掉"不会让任何功能断言变红——只有触控板用户会撞到"一划跳五页"；行模式的换算
    删掉则 Firefox 上滚轮彻底失灵（它的 `deltaY` 只有几，会被阈值全部挡掉）。两者都钉住。
    """
    page = _deck(_model())

    assert "COOLDOWN_MS = 450" in page
    assert "THRESHOLD_PX = 24" in page
    assert "event.deltaMode === 1" in page


def test_print_releases_the_slides_that_are_not_active() -> None:
    """打印必须放开**非当前页**：不补这一条，一份 38 页的 deck 打印出来只有 1 页。

    原因在选择器具体度：屏幕规则 `.slide:not(.active) { display: none !important }` 比框架打印
    规则里的 `.slide { display: flex !important }` 更具体，后者因此失效——实测浏览器打印导出
    只出当前那一页。故补一条同具体度、且排在其后的规则。
    """
    page = _deck(_model())

    assert ".slide:not(.active) { display: flex !important; }" in page


# ------------------------------------------------------------------ 溢出是硬失败，不是静默裁剪


def test_an_oversized_piece_raises_instead_of_being_cut() -> None:
    """装不下的片段必须**抛**——版面裁掉的后果是文件照样生成、只是少一截。

    这条直接测守卫本身：真实内容都会先被切开（见下一条），故守卫是最后一道，而不是常用路径。
    """
    with pytest.raises(deck_module.DeckOverflowError):
        deck_module._chunk(
            [deck_module._Piece(rows=deck_module.SLIDE_ROW_CAP + 1, html="<p>太长了</p>")]
        )


def test_long_prose_is_paginated_not_failed() -> None:
    """长散文要分页而不是让导出失败——"告警文案长了一点"不是渲染错误。"""
    model = _model()
    long_text = "这是一句很长的告警文案。" * 80
    model["caveats"] = [{"kind": "被测层级", "text": long_text}]

    page = _deck(model)

    # 全文都在（切开后分页续排），且没有"省略"之类的说法
    assert len(page) > 0
    assert "省略" not in page
    assert page.count("这是一句很长的告警文案。") == 80


def test_wide_charts_split_across_pages() -> None:
    """对照方案多到一组条形放不下时也要分页，而不是失败。

    一条组最多放 ``SLIDE_ROW_CAP - 1`` 根条形，故用比它更多的对照方案来逼出分片。
    """
    baselines = tuple(f"rival_{index:02d}" for index in range(deck_module.SLIDE_ROW_CAP + 1))
    page = _deck(_model(baselines=baselines))

    # 被测框架 + 14 个对照 = 15 根条形 → 切成 (1/2)、(2/2)
    assert "（1/2）" in page
    assert "（2/2）" in page
