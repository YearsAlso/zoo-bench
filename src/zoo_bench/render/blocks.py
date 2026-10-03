"""报告的块结构：报告模型 -> **与输出格式无关**的块列表。

为什么要有这一层：Markdown、HTML、PDF 三个输出后端需要同一份内容。若各自从模型里拼章节，
措辞与结构就会各写一份、必然漂移——而漂移的表现是"三个格式说的不是一回事"，这种不一致很难
被肉眼发现，读者只会以为其中一份是旧的。

所以"报告里有哪些块、每块说什么"只在这里出现一次；各后端只负责把块序列化成自己的语法。

章节顺序按读者的问题排：

「这是在哪测的」->「负载是什么」->「结论是什么」->「各维度的数」->「我在哪输了」->「口径局限」

顺序不是随意的：环境与负载的限定必须在结论**之前**给出，否则读者会先接受结论再看到适用条件；
而「我在哪输了」与「口径局限」必须独立成块、不得藏进表格脚注。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HEADING = "heading"
PARAGRAPH = "paragraph"
BULLETS = "bullets"
TABLE = "table"
IMAGE = "image"
NOTE = "note"

#: 中文字体普遍具备的非 ASCII 排版字符：中文标点、引号、破折号与全角符号。
#:
#: **之外的数学/排版符号（`微秒`、`−`、`÷`、`×`、`->`、`-` 等）中文字体不一定有**，表格里一旦
#: 缺字就会变方框。实测被 U+2212（减号）卡住过一轮 CI——`wqy-zenhei` 有 `微秒`/`—`/`÷`/`×`/`->`
#: 却**唯独没有减号**，而字体覆盖是逐字符的，猜不得。
#:
#: 故报告正文里的运算符一律用 ASCII（``-`` / ``x`` / ``->``），单位写中文（``微秒``），
#: 由 :func:`unsafe_characters` 在导出前机械检查。
SAFE_NON_ASCII = "、。，；：？！（）「」《》【】“”‘’—…·％"


class UnsafeReportText(RuntimeError):
    """报告正文里出现了中文字体不一定有的字符。

    刻意在**导出前**拦下而不是渲染后肉眼发现：缺字只会变成方框，文件照样生成，而方框在中文
    报告里很容易被当成排版问题忽略过去。
    """


def _is_safe(character: str) -> bool:
    if ord(character) < 0x80:
        return True
    if character in SAFE_NON_ASCII:
        return True
    code = ord(character)
    return (
        0x3000 <= code <= 0x303F  # CJK 标点
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0x4E00 <= code <= 0x9FFF  # 基本汉字
        or 0xF900 <= code <= 0xFAFF  # 兼容汉字
        or 0xFF00 <= code <= 0xFFEF  # 全角形式
        or 0x20000 <= code <= 0x2FFFF  # 扩展 B 及以后
    )


def unsafe_characters(text: str) -> list[str]:
    """列出 ``text`` 里"中文字体不一定有"的字符（去重、按码位排序）。

    Args:
        text: 报告实际会渲染的文本。

    Returns:
        需要替换的字符列表；全部安全时为空。
    """
    return sorted({character for character in text if not _is_safe(character)})


def assert_report_text_is_renderable(text: str) -> None:
    """报告正文只使用中文字体可靠具备的字符。

    Raises:
        UnsafeReportText: 出现非常规符号，并逐字给出可替换的建议。
    """
    unsafe = unsafe_characters(text)
    if not unsafe:
        return

    # 键写成码位转义：这张表按字符本身查，而它的**字面**形式极易被"批量替换那些符号"的改动
    # 连带改坏——实测就被一次 `µs`->`微秒` 的替换把 `"µ"` 键改成了 `"微秒"`，表静默失效。
    suggestions = {
        "µ": "微秒",
        "−": "-",
        "÷": "/",
        "×": "x",
        "→": "->",
        "•": "-",
    }
    detail = "、".join(f"U+{ord(character):04X}（换成 {suggestions.get(character, 'ASCII 写法')}）" for character in unsafe)
    raise UnsafeReportText(
        f"报告正文里出现了中文字体不一定有的字符：{detail}"
        "\n缺字只会变成方框、文件照样生成，故在导出前拦下。请改用 ASCII 写法或中文词。"
    )


@dataclass(frozen=True)
class Block:
    """一个报告块。

    Attributes:
        kind: 块类型，取本模块的 ``HEADING`` / ``PARAGRAPH`` / ``BULLETS`` / ``TABLE`` /
            ``IMAGE`` / ``NOTE`` 之一。
        text: 段落文本、标题文本或图片替代文本。
        level: 标题层级（``HEADING`` 用）。
        items: 条目（``BULLETS`` 用）。
        headers: 表头（``TABLE`` 用）。
        rows: 表体（``TABLE`` 用）。
        src: 图路径（``IMAGE`` 用）。
    """

    kind: str
    text: str = ""
    level: int = 2
    items: tuple[str, ...] = field(default_factory=tuple)
    headers: tuple[str, ...] = field(default_factory=tuple)
    rows: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    src: str = ""


def format_seconds(value: float | None) -> str:
    """秒 -> 便于阅读的单位。报告同时给数值与单位，省去读者换算。

    **单位写中文（微秒）而不是 `微秒`**：`微秒` 是中文字体不保证有的字符——实测 SimHei 就缺它，
    而报告里这一列有数百个格子，一旦缺字满屏方框。同时每个分支都把秒换算成该单位，
    否则会出现"2.7 毫秒被印成 0.003 ms"这种差 1000 倍的错误。
    """
    if value is None:
        return "—"
    if value >= 1.0:
        return f"{value:.3f} 秒"
    if value >= 1e-3:
        return f"{value * 1e3:.3f} 毫秒"
    return f"{value * 1e6:.2f} 微秒"


def format_ratio(value: float | None) -> str:
    """比值 -> 百分比。"""
    return "—" if value is None else f"{value * 100:.2f}%"


def _heading(text: str, level: int = 2) -> Block:
    return Block(HEADING, text=text, level=level)


def _environment_blocks(model: dict[str, Any]) -> list[Block]:
    environment = model.get("environment")
    if environment is None:
        return [
            _heading("运行环境"),
            Block(PARAGRAPH, text="**本报告缺少环境自述，不应被发布。**"),
        ]

    hardware = environment.get("hardware", {})
    operating_system = environment.get("os", {})
    python = environment.get("python", {})
    subject = environment.get("subject", {})
    harness = environment.get("harness", {})

    cpu = hardware.get("cpu_model") or f"不可用（{hardware.get('cpu_model_source')}）"
    rows = (
        ("CPU 型号", str(cpu)),
        ("逻辑核数", str(hardware.get("logical_cores"))),
        ("平台", str(hardware.get("platform"))),
        (
            "操作系统",
            f"{operating_system.get('system')} {operating_system.get('release')}"
            f"（{operating_system.get('version')}）",
        ),
        ("Python", f"{python.get('implementation')} {python.get('version')}"),
        ("解释器", str(python.get("executable"))),
        ("被测框架（发行元数据）", str(subject.get("dist_version"))),
        ("被测框架（模块 __version__）", f"{subject.get('module_version')}（仅附注，不作版本判据）"),
        ("harness", f"zoo-bench {harness.get('version')} @ {harness.get('commit')}"),
        ("复现命令", "`" + " ".join(environment.get("command", [])) + "`"),
    )

    blocks = [_heading("运行环境"), Block(TABLE, headers=("项", "值"), rows=rows)]
    if subject.get("note"):
        blocks.append(Block(NOTE, text=str(subject["note"])))
    return blocks


def _load_blocks(model: dict[str, Any]) -> list[Block]:
    load = model.get("load", {})
    return [
        _heading("负载"),
        Block(
            BULLETS,
            items=(
                f"构成：{load.get('composition')}",
                str(load.get("caveat", "")),
            ),
        ),
    ]


def _conclusion_blocks(model: dict[str, Any]) -> list[Block]:
    conclusion = model.get("conclusion", {})
    blocks = [
        _heading("结论摘要"),
        Block(BULLETS, items=tuple(conclusion.get("summary", []))),
    ]
    if conclusion.get("note"):
        blocks.append(Block(NOTE, text=str(conclusion["note"])))

    crossings = conclusion.get("overhead_crossings", [])
    if crossings:
        blocks += [
            _heading("开销阈值交叉点", level=3),
            Block(
                TABLE,
                headers=("方案", "并发度", "阈值", "首个不高于阈值的档位（微秒）", "所测档位（微秒）"),
                rows=tuple(
                    (
                        crossing["adapter"],
                        str(crossing["concurrency"]),
                        format_ratio(crossing["threshold"]),
                        "—"
                        if crossing["first_tier_at_or_below_threshold_us"] is None
                        else f"{crossing['first_tier_at_or_below_threshold_us']:g}",
                        ", ".join(f"{tier:g}" for tier in crossing["tiers_us"]),
                    )
                    for crossing in crossings
                ),
            ),
        ]

    turnings = conclusion.get("relative_turnings", [])
    if turnings:
        blocks += [
            _heading("相对转折点", level=3),
            Block(
                TABLE,
                headers=("对照方案", "并发度", "该方案不再更快的档位（微秒）", "说明"),
                rows=tuple(
                    (
                        turning["baseline"],
                        str(turning["concurrency"]),
                        "—"
                        if turning["first_tier_baseline_not_faster_us"] is None
                        else f"{turning['first_tier_baseline_not_faster_us']:g}",
                        turning["note"],
                    )
                    for turning in turnings
                ),
            ),
        ]
    return blocks


def _chart_blocks(charts: list[dict[str, Any]], figures_rel: str) -> list[Block]:
    if not charts:
        return []

    blocks: list[Block] = [_heading("图表")]
    for chart in charts:
        svg = chart["paths"].get("svg")
        if not svg:
            continue
        blocks.append(Block(IMAGE, text=chart["figure"], src=f"{figures_rel}/{Path(svg).name}"))
        if chart.get("scope"):
            blocks.append(Block(NOTE, text=str(chart["scope"])))
    return blocks


def _dimension_blocks(model: dict[str, Any]) -> list[Block]:
    dimensions = model.get("dimensions", {})
    blocks: list[Block] = []

    latency = dimensions.get("latency", {})
    blocks += [
        _heading(f"维度：{latency.get('title', '延迟')}"),
        Block(NOTE, text=str(latency.get("note", ""))),
        Block(
            TABLE,
            headers=("方案", "并发度", "执行体档位（微秒）", "中位数", "p95", "p99", "相对离散度"),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_seconds(row["end_to_end_per_task"]["median"]),
                    format_seconds(row["end_to_end_per_task"]["p95"]),
                    format_seconds(row["end_to_end_per_task"]["p99"]),
                    f"{float(row['end_to_end_per_task']['relative_spread']):.3f}",
                )
                for row in latency.get("rows", [])
            ),
        ),
    ]

    overhead = dimensions.get("overhead", {})
    blocks += [
        _heading(f"维度：{overhead.get('title', '框架开销')}"),
        Block(NOTE, text=str(overhead.get("note", ""))),
        Block(
            TABLE,
            headers=("方案", "并发度", "执行体档位（微秒）", "框架开销", "开销占比", "执行体实测"),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    format_seconds(row["framework_overhead_seconds"]),
                    format_ratio(row["framework_overhead_ratio"]),
                    format_seconds(row["body_seconds"]),
                )
                for row in overhead.get("rows", [])
            ),
        ),
    ]

    throughput = dimensions.get("throughput", {})
    blocks += [
        _heading(f"维度：{throughput.get('title', '吞吐')}"),
        Block(NOTE, text=str(throughput.get("note", ""))),
        Block(
            TABLE,
            headers=("方案", "并发度", "执行体档位（微秒）", "吞吐（任务/秒）"),
            rows=tuple(
                (
                    row["adapter"],
                    str(row["concurrency"]),
                    f"{row['body_tier_us']:g}",
                    f"{row['throughput_per_second']:.1f}",
                )
                for row in throughput.get("rows", [])
            ),
        ),
    ]

    semantics = dimensions.get("semantics", {})
    blocks += [
        _heading(f"维度：{semantics.get('title', '调度语义的代价')}"),
        Block(NOTE, text=f"口径：{semantics.get('scope_note', '')}"),
        Block(PARAGRAPH, text=f"**状态：{semantics.get('status')}**"),
    ]
    if semantics.get("reason"):
        blocks.append(Block(PARAGRAPH, text=str(semantics["reason"])))
    if semantics.get("items"):
        blocks.append(
            Block(
                TABLE,
                headers=("语义项", "层级", "探查方式", "可测", "证据 / 原因"),
                rows=tuple(
                    (
                        item["item"],
                        item["layer"],
                        item["probe"],
                        "是" if item["usable"] else "否",
                        item.get("reason") or item.get("evidence", ""),
                    )
                    for item in semantics["items"]
                ),
            )
        )
    if semantics.get("recheck_on_version_bump"):
        blocks.append(Block(NOTE, text=str(semantics["recheck_on_version_bump"])))
    return blocks


def _unfavorable_blocks(model: dict[str, Any]) -> list[Block]:
    unfavorable = model.get("unfavorable", {})
    blocks = [
        _heading("公开的不利数据"),
        Block(NOTE, text=str(unfavorable.get("note", ""))),
    ]

    items = unfavorable.get("items", [])
    if not items:
        blocks.append(Block(PARAGRAPH, text="所测档位内未出现被测框架处于劣势的情形。"))
        return blocks

    blocks.append(
        Block(
            TABLE,
            headers=("对照方案", "并发度", "执行体档位（微秒）", "差距", "被测框架中位数"),
            rows=tuple(
                (
                    item["baseline"],
                    str(item["concurrency"]),
                    f"{item['body_tier_us']:g}",
                    item["gap"],
                    format_seconds(item["subject_median_seconds"]),
                )
                for item in items
            ),
        )
    )
    return blocks


def caveat_text(caveat: dict[str, Any]) -> str:
    """一条口径局限的文字。适配器名可选——不是每条局限都归属于某个方案。"""
    adapter = f"（{caveat['adapter']}）" if caveat.get("adapter") else ""
    return f"**{caveat.get('kind', '')}**{adapter}：{caveat.get('text', '')}"


EXTENSION_NOTE = (
    "本 harness 的对照方案是**开放**的：实现 `zoo_bench.adapters.BaseAdapter` 的四个方法"
    "（`setup` / `submit` / `drain` / `teardown`），用 `zoo_bench.adapters.registry.register` "
    "登记，或在 `matrix.yaml` 的 `extra_modules` 里列出你的模块路径——**无需改动本仓库任何"
    "文件**。你的实现会通过同一套等价性验证（提交 N 个执行体、断言全部执行且各执行一次），"
    "不通过的不会被采信。"
    "**我们主动把这条路径写进报告**：一份由被测框架维护者撰写、测量被测框架的报告，读者有理由"
    "怀疑对照被写慢或口径被挑选；让任何人能提交自己的对照，是这份报告可被质疑、也可被证伪的前提。"
)


def _methodology_blocks(model: dict[str, Any]) -> list[Block]:
    """测量口径：让读者知道这些数字是怎么来的。

    独立成节而不是散在各处——**三条口径是数字可信度的全部依据**，读者要能一眼看全并据此判断
    适用性。三条各自对应一类曾被真实踩到的错误：
    ① 跨运行做减法会把开销高估一倍；② 预热不入统计，否则冷启动成本混进结果；
    ③ 绝对耗时跨运行不可比，故相对比才是证据主体。
    """
    run = model.get("run", {})
    warmups = run.get("warmup_rounds") or []
    rounds = run.get("measured_rounds") or []
    warmup_text = "、".join(str(value) for value in warmups) or "—"
    rounds_text = "、".join(str(value) for value in rounds) or "—"

    return [
        _heading("测量口径"),
        Block(
            BULLETS,
            items=(
                "**执行体耗时在同一次运行内埋点**：由执行体在自身内部测量并回传，框架开销 ="
                "端到端 - 它。**不做跨运行减法**——两次运行的状态不同（缓存、频率、调度噪声），"
                "相减引入的是系统性偏差。",
                f"**预热不入统计**：每个单元先跑 {warmup_text} 轮预热并丢弃，只统计随后的"
                f" {rounds_text} 轮正式采样；采样数等于「正式轮数 x 并发度」。",
                "**绝对耗时不可跨运行比较**：它只用于看量级；跨版本/跨机器要看的是**同一次运行内"
                "的相对量**（开销占比、相对各对照方案的倍数），它们对整体快慢不敏感。",
            ),
        ),
    ]


def _extension_blocks() -> list[Block]:
    """如何加入你自己的对照。

    独立成节而不是塞进脚注：它是这份报告**可被证伪**的前提——读者若能自己跑一遍对照，报告里
    的数字才不是只能听信的一面之词。
    """
    return [Block(HEADING, text="加入你自己的对照"), Block(PARAGRAPH, text=EXTENSION_NOTE)]


def _caveat_blocks(model: dict[str, Any]) -> list[Block]:
    caveats = model.get("caveats", [])
    items = tuple(caveat_text(caveat) for caveat in caveats)
    blocks = [_heading("口径局限与偏差来源")]
    blocks.append(Block(BULLETS, items=items or ("（无）",)))
    blocks.append(Block(NOTE, text=f"绝对耗时：{model.get('absolute', {}).get('note', '')}"))
    return blocks


def _self_check_blocks(model: dict[str, Any]) -> list[Block]:
    self_check = model.get("self_check", {})
    return [
        _heading("自检"),
        Block(
            BULLETS,
            items=(
                f"测量自检整体：{'通过' if self_check.get('ok') else '**未通过**'}",
                f"进程隔离：{model.get('absolute', {}).get('process_isolation', {})}",
            ),
        ),
    ]


def _header_blocks(model: dict[str, Any]) -> list[Block]:
    """标题与原始数据出处。

    放在块层而不是各序列化器里——三个后端都得有同一个标题与同一句出处，各写一遍就是三处漂移点。
    """
    blocks = [Block(HEADING, text="zoo-bench 性能报告", level=1)]
    source = model.get("source")
    if source and source.get("path"):
        blocks.append(Block(PARAGRAPH, text=f"原始数据：`{source['path']}`"))
    return blocks


def build_blocks(
    model: dict[str, Any], charts: list[dict[str, Any]], *, figures_rel: str = "figures"
) -> list[Block]:
    """由报告模型抽出与输出格式无关的块列表。

    Args:
        model: :func:`zoo_bench.report.build_model` 的返回值。
        charts: :func:`zoo_bench.render.charts.render_all` 的返回值。
        figures_rel: 图表目录相对报告文件的路径。

    Returns:
        块列表。
    """
    blocks: list[Block] = []
    for builder in (
        _header_blocks,
        _environment_blocks,
        _load_blocks,
        _methodology_blocks,
        _conclusion_blocks,
    ):
        blocks += builder(model)
    blocks += _chart_blocks(charts, figures_rel)
    blocks += _dimension_blocks(model)
    blocks += _unfavorable_blocks(model)
    blocks += _caveat_blocks(model)
    blocks += _extension_blocks()
    blocks += _self_check_blocks(model)
    return blocks
