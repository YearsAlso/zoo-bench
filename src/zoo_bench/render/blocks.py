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

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..i18n import LANG_EN, LANG_ZH, MESSAGES, has_translation, resolve_reason, t, template_fields
from ..native_line.forms import FORM_NATIVE, FORM_PURE, FORM_ROUTED

HEADING = "heading"
PARAGRAPH = "paragraph"
BULLETS = "bullets"
TABLE = "table"
IMAGE = "image"
NOTE = "note"


#: 几个章节标题按**身份**被 deck 渲染器识别（deck 少一页正是最难被发现的那种错）。
#: 放在这里而不是各写一份字符串：改名时两边一起变，识别失败还会被用例拦下。
def conclusion_section_title(lang: str) -> str:
    return t("blocks.section.conclusion", lang)


def chart_section_title(lang: str) -> str:
    return t("blocks.section.charts", lang)


def dimension_section_prefix(lang: str) -> str:
    return t("blocks.section.dimension_prefix", lang)


def appendix_section_prefix(lang: str) -> str:
    return t("blocks.section.appendix_prefix", lang)


def unfavorable_section_title(lang: str) -> str:
    return t("blocks.section.unfavourable", lang)


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


def assert_report_text_is_renderable(text: str, *, lang: str, source: str = "") -> None:
    """报告正文只使用该语言所选规则的可靠字符（design D6）。

    中文走 :func:`unsafe_characters` 的字体覆盖白名单；英文正文必须**全 ASCII**——那张白名单
    本是按中文字体覆盖逐字试出来的，把 ASCII 之外的字交给英文产物等于把方框风险重新引进来。

    违规时指出**具体条目**：光列码位读者无从知道是哪一条、也就无从修，故调用方应把 ``text``
    切成条目粒度并给出 ``source``（见 :func:`zoo_bench.render.pdf.report_text_units`）。

    Args:
        text: 要检查的文本（**一条**，不是整篇拼起来的）。
        lang: 产物语言。
        source: 该文本在报告里的位置（如 ``口径局限 / 条目 3``），失败时原样报出。

    Raises:
        UnsafeReportText: 出现该语言规则不允许的字符，并给出可执行的替换建议。
    """
    where = f"\n出现位置：{source}" if source else ""

    if lang == LANG_EN:
        if text.isascii():
            return
        offenders = sorted({character for character in text if not character.isascii()})
        listed = "、".join(f"U+{ord(c):04X}" for c in offenders[:6])
        if len(offenders) > 6:
            listed += f" 等 {len(offenders)} 种"
        at = min(text.find(character) for character in offenders)
        snippet = text[max(0, at - 40) : at + 40].replace("\n", " ")
        raise UnsafeReportText(
            f"英文产物正文含非 ASCII 字符：{listed}{where}\n原文：…{snippet}…\n"
            "英文正文的字符规则是全 ASCII（design D6）。若它来自目录里的文案，去补那条译文；"
            "若它来自留档数据（机器自述、旧档散文），那是「数据本身如此」，需在渲染前单独处置"
            "而不是放宽规则。"
        )

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
    detail = "、".join(
        f"U+{ord(character):04X}（换成 {suggestions.get(character, 'ASCII 写法')}）"
        for character in unsafe
    )
    raise UnsafeReportText(
        f"报告正文里出现了中文字体不一定有的字符：{detail}{where}"
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


def format_seconds(value: float | None, lang: str) -> str:
    """秒 -> 便于阅读的单位。报告同时给数值与单位，省去读者换算。

    **单位写中文（微秒）而不是 `微秒`**：`微秒` 是中文字体不保证有的字符——实测 SimHei 就缺它，
    而报告里这一列有数百个格子，一旦缺字满屏方框。同时每个分支都把秒换算成该单位，
    否则会出现"2.7 毫秒被印成 0.003 ms"这种差 1000 倍的错误。
    """
    if value is None:
        return t("blocks.empty_marker", lang)
    if value >= 1.0:
        return f"{value:.3f} {t('unit.seconds', lang)}"
    if value >= 1e-3:
        return f"{value * 1e3:.3f} {t('unit.milliseconds', lang)}"
    return f"{value * 1e6:.2f} {t('unit.microseconds', lang)}"


def format_ratio(value: float | None, lang: str) -> str:
    """比值 -> 百分比。"""
    return t("blocks.empty_marker", lang) if value is None else f"{value * 100:.2f}%"


def repository_relative_path(path: str) -> str:
    """留档路径 -> **仓库内**相对路径（``results/<框架>/<时间戳>.json``）。

    **不能印构建机的绝对路径**：本地是 ``F:\\Python\\zoo\\zoo-bench\\results\\...``、CI 是
    ``/home/runner/work/...``，两种都只在写它的那台机器上有意义，而报告是发到公开站点的。
    读者要照着去找的是仓库里的那一份，故从最后一段 ``results`` 起截、分隔符统一成正斜杠。

    找不到 ``results`` 段时**原样返回**：宁可印一条奇怪的路径，也不要编一个不存在的相对位置。
    """
    parts = [segment for segment in re.split(r"[\\/]+", path) if segment]
    if "results" not in parts:
        return path
    start = len(parts) - 1 - parts[::-1].index("results")
    return "/".join(parts[start:])


def _list_join(values: Iterable[Any], lang: str, *, clause: bool = False) -> str:
    """按语言拼一列值：中文枚举用顿号、句子级用分号；英文一律逗号/分号。

    拼接发生在**调用侧**而不是目录里：目录只收已经拼好的整体（如 ``{seals}``），
    否则分隔符就得进模板、每种语言各写一份。
    """
    if clause:
        return "；".join(values) if lang == LANG_ZH else "; ".join(values)
    return "、".join(values) if lang == LANG_ZH else ", ".join(values)


def resolve_params(params: dict[str, Any] | None, lang: str) -> dict[str, Any]:
    """键值参数里的**嵌套键**再解一层（旧留档没有键值参数，逐项透传不受影响）。

    **数字原样留着、不得转成字符串**：模板里有 ``{observed:.4f}`` 这类格式说明，转成字符串
    会让 ``str.format`` 抛 ``Unknown format code 'f' for object of type 'str'``。实测被这一处
    卡住过一次——旧留档存的是散文、参数根本走不到，故只在**新留档**上才暴露。
    """
    if not params:
        return {}
    resolved: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str) and has_translation(value):
            resolved[key] = t(value, lang)
        elif isinstance(value, (list, tuple)):
            resolved[key] = _list_join(
                (resolve_text(item, lang) for item in value), lang, clause=True
            )
        elif isinstance(value, dict):
            resolved[key] = resolve_params(value, lang)
        else:
            resolved[key] = value
    return resolved


def resolve_text(value: Any, lang: str, params: dict[str, Any] | None = None) -> str:
    """按语言解析一个「键或散文」值：目录键取译文，旧留档散文**原样透传**。

    键值数据（design D8）里嵌套的键（如证据参数里的 ``location``）经 ``params`` 一并解析；
    模板没声明的参数**过滤掉**（留档可能携带模板已不再使用的参数），模板要的参数缺失则照常抛。
    列表按句拼（分号）。
    """
    if isinstance(value, str):
        if not has_translation(value):
            return value
        fields = template_fields(next(iter(MESSAGES[value].values())))
        used = {k: v for k, v in resolve_params(params, lang).items() if k in fields}
        return t(value, lang, **used)
    if isinstance(value, (list, tuple)):
        return _list_join((resolve_text(item, lang, params) for item in value), lang, clause=True)
    return str(value)


def _heading(text: str, level: int = 2) -> Block:
    return Block(HEADING, text=text, level=level)


def _drive_generation_text(subject: dict[str, Any], lang: str) -> str:
    """被测框架的驱动面世代，**记不清就如实说记不清**。

    世代判定失败时给的是探测结果（见 :func:`zoo_bench.adapters.zoo.probe_drive_generation`），
    不是一句"未知"——"未知"和"探测到的是这样"在排查时是完全不同的两条信息。

    ``label`` 与 ``error`` 都是**留档里的值**：新档存的是目录键、旧档存的是散文，一律经
    :func:`resolve_text`；判定载荷（``error_params``，只有新档才有）的排版归
    :mod:`zoo_bench.generations`，本模块只负责把它交给该语言的模板。
    """
    generation = subject.get("drive_generation") or {}
    label = generation.get("label")
    if label:
        return resolve_text(label, lang)

    error = generation.get("error")
    if not error:
        detail = t("blocks.env.drive_generation_not_declared", lang)
    elif generation.get("error_params"):
        # 只在真出现键化载荷时才导入：generations 依赖被测框架，而渲染旧档/无框架的场景
        # 不该被它牵连（旧档的 error 是散文，走下面一支）
        from ..generations import format_mismatch

        detail = format_mismatch(generation["error_params"], lang)
    else:
        detail = str(error)
    return t("blocks.env.drive_generation_undetermined", lang, detail=detail)


def _environment_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    environment = model.get("environment")
    if environment is None:
        return [
            _heading(t("blocks.env.title", lang)),
            Block(PARAGRAPH, text=t("blocks.env.missing", lang)),
        ]

    hardware = environment.get("hardware", {})
    operating_system = environment.get("os", {})
    python = environment.get("python", {})
    subject = environment.get("subject", {})
    harness = environment.get("harness", {})

    cpu = hardware.get("cpu_model") or t(
        "blocks.env.cpu_unavailable",
        lang,
        source=resolve_text(
            str(hardware.get("cpu_model_source", "")),
            lang,
            hardware.get("cpu_model_source_params") or {},
        ),
    )
    rows: list[tuple[str, str]] = [
        (t("blocks.env.cpu_model", lang), str(cpu)),
        (t("blocks.env.logical_cores", lang), str(hardware.get("logical_cores"))),
        (t("blocks.env.platform", lang), str(hardware.get("platform"))),
        (
            t("blocks.env.os", lang),
            t(
                "blocks.env.os_value",
                lang,
                system=operating_system.get("system"),
                release=operating_system.get("release"),
                version=operating_system.get("version"),
            ),
        ),
        (
            t("blocks.env.python_label", lang),
            f"{python.get('implementation')} {python.get('version')}",
        ),
        (t("blocks.env.interpreter", lang), str(python.get("executable"))),
    ]
    # GIL 状态只在自述里有值时展示（历史留档没有这一项，不能补造）；
    # free-threaded 列的读数必须能被读者识别。
    if python.get("gil_mode"):
        rows.append((t("blocks.env.gil_label", lang), str(python.get("gil_mode"))))
    rows += [
        (t("blocks.env.subject_dist", lang), str(subject.get("dist_version"))),
        (
            t("blocks.env.subject_module", lang),
            f"{subject.get('module_version')}{t('blocks.env.module_note', lang)}",
        ),
        (
            t("blocks.env.drive_generation_label", lang),
            _drive_generation_text(subject, lang),
        ),
    ]

    # git 安装时把来源与 commit 摆出来：分支安装的版本号只是仓库里写着的声明值，
    # 真正回答"这份数据出自哪个提交"的是 direct_url.json
    vcs = (subject.get("install_source") or {}).get("vcs_info") or {}
    if vcs:
        rows += [
            (
                t("blocks.env.install_source", lang),
                t(
                    "blocks.env.install_source_value",
                    lang,
                    vcs=vcs.get("vcs", "vcs"),
                    ref=vcs.get("requested_revision") or t("blocks.env.ref_not_recorded", lang),
                ),
            ),
            (t("blocks.env.commit", lang), str(vcs.get("commit_id"))[:12]),
        ]

    rows += [
        ("harness", f"zoo-bench {harness.get('version')} @ {harness.get('commit')}"),
        (t("blocks.env.reproduce", lang), "`" + " ".join(environment.get("command", [])) + "`"),
    ]

    blocks = [
        _heading(t("blocks.env.title", lang)),
        Block(
            TABLE,
            headers=(t("blocks.env.header_item", lang), t("blocks.env.header_value", lang)),
            rows=tuple(rows),
        ),
    ]
    if subject.get("note"):
        blocks.append(Block(NOTE, text=resolve_text(subject["note"], lang)))
    return blocks


def _load_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    load = model.get("load", {})
    return [
        _heading(t("blocks.load.title", lang)),
        Block(
            BULLETS,
            items=(
                t("blocks.load.composition", lang, composition=load.get("composition")),
                str(load.get("caveat", "")),
            ),
        ),
    ]


def _conclusion_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    conclusion = model.get("conclusion", {})
    blocks = [
        _heading(conclusion_section_title(lang)),
        Block(BULLETS, items=tuple(conclusion.get("summary", []))),
    ]
    if conclusion.get("note"):
        blocks.append(Block(NOTE, text=str(conclusion["note"])))

    crossings = conclusion.get("overhead_crossings", [])
    if crossings:
        blocks += [
            _heading(t("blocks.crossing.title", lang), level=3),
            Block(
                TABLE,
                headers=(
                    t("blocks.header.scheme", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.threshold", lang),
                    t("blocks.header.first_tier_below", lang),
                    t("blocks.header.measured_tiers", lang),
                    t("blocks.header.note", lang),
                ),
                rows=tuple(
                    (
                        crossing["adapter"],
                        str(crossing["concurrency"]),
                        format_ratio(crossing["threshold"], lang),
                        t("blocks.empty_marker", lang)
                        if crossing["first_tier_at_or_below_threshold_us"] is None
                        else f"{crossing['first_tier_at_or_below_threshold_us']:g}",
                        ", ".join(f"{tier:g}" for tier in crossing["tiers_us"]),
                        # "—"的**原因**必须当场给出：受排队污染与被撤下开销是两回事，
                        # 光看一个横杠分不出来
                        str(crossing.get("note", "")),
                    )
                    for crossing in crossings
                ),
            ),
        ]

    turnings = conclusion.get("relative_turnings", [])
    if turnings:
        blocks += [
            _heading(t("blocks.turning.title", lang), level=3),
            Block(
                TABLE,
                headers=(
                    t("blocks.header.baseline", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.first_tier_not_faster", lang),
                    t("blocks.header.note", lang),
                ),
                rows=tuple(
                    (
                        turning["baseline"],
                        str(turning["concurrency"]),
                        t("blocks.empty_marker", lang)
                        if turning["first_tier_baseline_not_faster_us"] is None
                        else f"{turning['first_tier_baseline_not_faster_us']:g}",
                        turning["note"],
                    )
                    for turning in turnings
                ),
            ),
        ]
    return blocks


def _chart_blocks(charts: list[dict[str, Any]], figures_rel: str, lang: str) -> list[Block]:
    if not charts:
        return []

    blocks: list[Block] = [_heading(chart_section_title(lang))]
    for chart in charts:
        svg = chart["paths"].get("svg")
        if not svg:
            continue
        blocks.append(Block(IMAGE, text=chart["figure"], src=f"{figures_rel}/{Path(svg).name}"))
        if chart.get("scope"):
            blocks.append(Block(NOTE, text=str(chart["scope"])))
    return blocks


def _pivot_block(
    rows: list[dict[str, Any]], *, value_of: Any, subject: str | None, lang: str
) -> Block:
    """把逐单元行透成"行 = (并发度, 档位)、列 = 方案"的一张表。

    **列序把被测框架放最前**：读者的动作是"拿被测框架那一列去比别的列"，放在第一列就省掉在六个
    方案里找它。行数从"单元数"降到"并发度 × 档位"，比较于是变成扫一列——逐单元表做不到这一点。
    """
    adapters = sorted({row["adapter"] for row in rows})
    if subject in adapters:
        adapters.remove(subject)
        adapters.insert(0, subject)

    cells = {
        (row["adapter"], row["concurrency"], row["body_tier_us"]): value_of(row) for row in rows
    }
    keys = sorted({(row["concurrency"], row["body_tier_us"]) for row in rows})

    return Block(
        TABLE,
        headers=(
            t("blocks.header.concurrency", lang),
            t("blocks.header.body_tier", lang),
            *adapters,
        ),
        rows=tuple(
            (
                str(concurrency),
                f"{tier:g}",
                *(
                    cells.get((adapter, concurrency, tier), t("blocks.empty_marker", lang))
                    for adapter in adapters
                ),
            )
            for concurrency, tier in keys
        ),
    )


#: 正文给透视表配的指引：明细在附录里，位置写清楚，读者不必找。
def _appendix_pointer(lang: str) -> str:
    return t("blocks.appendix.pointer", lang)


def _dimension_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    """正文里的度量维度：**透视表**，明细留给附录。

    正文只回答"多大的任务用哪个方案"这一个问题，故每维只出一张"行 = (并发度, 档位)、列 = 方案"
    的透视表；分位数、离散度这类核对用的字段在附录里逐单元给出（见 :func:`_appendix_blocks`）。
    """
    dimensions = model.get("dimensions", {})
    subject = model.get("subject")
    blocks: list[Block] = []

    latency = dimensions.get("latency", {})
    blocks += [
        _heading(
            f"{dimension_section_prefix(lang)}"
            f"{latency.get('title') or t('report.dimension.latency.title', lang)}"
        ),
        Block(NOTE, text=str(latency.get("note", ""))),
        _pivot_block(
            latency.get("rows", []),
            value_of=lambda row: format_seconds(row["end_to_end_per_task"]["median"], lang),
            subject=subject,
            lang=lang,
        ),
        Block(PARAGRAPH, text=_appendix_pointer(lang)),
    ]

    overhead = dimensions.get("overhead", {})
    # 表里出现"—"时必须当场说明它是什么意思：读者跨到口径章节才知道，等于让他猜
    withheld = [row for row in overhead.get("rows", []) if not row.get("interpretable", True)]
    overhead_note = str(overhead.get("note", ""))
    if withheld:
        overhead_note += t("blocks.overhead.withheld", lang)
    blocks += [
        _heading(
            f"{dimension_section_prefix(lang)}"
            f"{overhead.get('title') or t('report.dimension.overhead.title', lang)}"
        ),
        Block(NOTE, text=overhead_note),
        _pivot_block(
            overhead.get("rows", []),
            value_of=lambda row: format_ratio(row["framework_overhead_ratio"], lang),
            subject=subject,
            lang=lang,
        ),
        Block(PARAGRAPH, text=_appendix_pointer(lang)),
    ]

    throughput = dimensions.get("throughput", {})
    blocks += [
        _heading(
            f"{dimension_section_prefix(lang)}"
            f"{throughput.get('title') or t('report.dimension.throughput.title', lang)}"
        ),
        Block(NOTE, text=str(throughput.get("note", ""))),
        _pivot_block(
            throughput.get("rows", []),
            value_of=lambda row: f"{float(row['throughput_per_second']):.1f}",
            subject=subject,
            lang=lang,
        ),
        Block(PARAGRAPH, text=_appendix_pointer(lang)),
    ]

    blocks += _attribution_blocks(dimensions, lang)

    semantics = dimensions.get("semantics", {})
    # 结论是在哪一代驱动面上得出的必须随报告给出：这一维的结论与坐标按版本成立，
    # 读者看不到"在哪一代得出"，就无法判断它对自己关心的那一版是否还作数
    derived_on = semantics.get("derived_on")
    status = t("blocks.semantics.status", lang, status=semantics.get("status"))
    if derived_on:
        status += t("blocks.semantics.derived_on", lang, generation=derived_on)
    blocks += [
        _heading(
            f"{dimension_section_prefix(lang)}"
            f"{resolve_text(semantics['title'], lang) if semantics.get('title') else t('report.semantics.title', lang)}"
        ),
        Block(
            NOTE,
            text=t(
                "blocks.scope_note",
                lang,
                note=resolve_text(semantics.get("scope_note") or "", lang),
            ),
        ),
        Block(PARAGRAPH, text=status),
    ]
    if semantics.get("reason"):
        blocks.append(Block(PARAGRAPH, text=resolve_text(semantics["reason"], lang)))
    if semantics.get("items"):
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.semantics_item", lang),
                    t("blocks.header.layer", lang),
                    t("blocks.header.probe", lang),
                    t("blocks.header.usable", lang),
                    t("blocks.header.evidence_or_reason", lang),
                ),
                rows=tuple(
                    (
                        resolve_text(item["item"], lang),
                        resolve_text(item["layer"], lang),
                        resolve_text(item["probe"], lang),
                        t("blocks.yes", lang) if item["usable"] else t("blocks.no", lang),
                        (
                            resolve_text(item["reason"], lang, item.get("reason_params") or {})
                            if item.get("reason")
                            else resolve_text(
                                item.get("evidence", ""),
                                lang,
                                item.get("evidence_params") or {},
                            )
                        ),
                    )
                    for item in semantics["items"]
                ),
            )
        )
    if semantics.get("recheck_on_version_bump"):
        blocks.append(Block(NOTE, text=resolve_text(semantics["recheck_on_version_bump"], lang)))
    blocks += _native_line_blocks(dimensions, lang)
    return blocks


def _native_line_blocks(dimensions: dict[str, Any], lang: str) -> list[Block]:
    """原生任务线维度：**同一被测框架**的三种执行形态.

    它**只在本节里出现**：数值不进主矩阵的透视表与交叉点——那几张表的列是"一个方案一个适配器"，
    把"同一框架的三种执行形态"混进去就等于给出一个不成立的横向比较。范围说明紧接标题，理由与别的
    维度相同：读者先看到限定，再看到数字。

    结论句（该线状态、门禁、分界）与数值同层，不塞进脚注：门禁没过时这些数字的前提就不成立，
    而前提残留在上、数字在下，读者会先接受数字。
    """
    line = dimensions.get("native_line")
    if not line:
        return []
    labels = {
        form: resolve_text(key, lang) for form, key in (line.get("form_labels") or {}).items()
    }
    blocks = [
        _heading(
            f"{dimension_section_prefix(lang)}"
            f"{resolve_text(line.get('title') or 'report.native_line.title', lang)}"
        ),
        Block(
            NOTE,
            text=t(
                "blocks.scope_note",
                lang,
                note=resolve_text(line.get("scope_note") or "", lang),
            ),
        ),
        Block(PARAGRAPH, text=t("blocks.native_line.status", lang, status=line.get("status"))),
    ]
    if line.get("reason"):
        blocks.append(
            Block(
                PARAGRAPH,
                text=resolve_text(line["reason"], lang, line.get("reason_params") or {}),
            )
        )

    gate = line.get("gate") or {}
    if gate.get("text"):
        blocks.append(
            Block(PARAGRAPH, text=resolve_text(gate["text"], lang, gate.get("text_params") or {}))
        )
    if gate.get("status") == "failed" and gate.get("mismatches"):
        # 只说"未通过"读者无从判断哪条输入有问题；而两侧的文字来自被测框架与扩展（本仓不可控、
        # 也保证不了中文字体覆盖），故报告给输入身份、全文留给留档
        blocks.append(Block(NOTE, text=t("blocks.native_line.gate_detail", lang)))
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.native_line.header_input", lang),
                    t("blocks.native_line.header_group", lang),
                    t("blocks.native_line.header_frame", lang),
                    t("blocks.header.frame_bytes", lang),
                ),
                rows=tuple(
                    (
                        str(item.get("key", "")),
                        str(item.get("group", "")),
                        _hex_head(str(item.get("frame_hex", "")), lang),
                        str(item.get("frame_bytes", "")),
                    )
                    for item in gate["mismatches"]
                ),
            )
        )

    plan = line.get("plan") or {}
    if plan:
        task_text = _native_line_task_text(plan, lang)
        if task_text:
            blocks.append(Block(NOTE, text=task_text))
        blocks.append(
            Block(NOTE, text=t("blocks.native_line.plan", lang, **_plan_params(plan, lang)))
        )

    # 分界结论紧接数字之前：它是这一维的回答，表是它的依据。三向（更快 / 更慢 / 未决）在同
    # 一句里给出，理由见下面的 helper
    crossover_text = _native_line_crossover_text(line.get("crossover") or {}, lang)
    if crossover_text:
        blocks.append(Block(PARAGRAPH, text=crossover_text))

    rows = line.get("rows") or []
    if rows:
        blocks.append(Block(NOTE, text=t("blocks.native_line.caliber", lang)))
        blocks.append(_native_line_table(rows, labels, lang))
        for item in line.get("closed_groups") or []:
            form = labels.get(str(item.get("form")), str(item.get("form")))
            blocks.append(
                Block(
                    PARAGRAPH,
                    text=t(
                        "blocks.native_line.closed_forms",
                        lang,
                        forms=form,
                        reason=resolve_text(
                            str(item.get("reason", "")), lang, item.get("reason_params") or {}
                        ),
                    ),
                )
            )

    blocks += _native_line_routing_blocks(line.get("routing") or {}, lang)

    notes = _native_line_notes(line)
    if notes:
        blocks.append(Block(NOTE, text=t("blocks.native_line.notes", lang)))
        blocks.append(Block(BULLETS, items=tuple(resolve_text(key, lang) for key in notes)))
    return blocks


def _native_line_notes(line: dict[str, Any]) -> list[str]:
    """该维度的口径注（**去重保序**）.

    口径注可能同时来自三个地方：该线自己的 ``note``、各格的口径块、以及分界结论自己的注。同名注
    重复出现在报告里会让人以为有两条不同的限定，故此处按出现顺序去重。
    """
    seen: dict[str, None] = {}
    for key in [
        *(line.get("notes") or []),
        *((line.get("caliber") or {}).get("notes") or []),
        *((line.get("crossover") or {}).get("note") or []),
    ]:
        seen.setdefault(str(key), None)
    # 范围说明已在标题下单独给出，不重复
    return [key for key in seen if key != "native_line.note.scope"]


def _native_line_task_text(plan: dict[str, Any], lang: str) -> str | None:
    """所测任务那一段（spec: native_line「负载是真实任务这一事实可核」）.

    任务名取自留档，不在渲染层另写一份：写死会让"报告说的任务"与"实际跑的任务"成为两处声明。
    留档缺这一项时（早于该字段的留档）宁可不印，也不给读者印一个 ``None``。
    """
    task = plan.get("task")
    if not task:
        return None
    return t("blocks.native_line.task", lang, task=task)


def _plan_params(plan: dict[str, Any], lang: str) -> dict[str, Any]:
    """规模档位那一段的参数（档位名与帧字节分开给，省得读者在括号里找数字）."""
    tiers = plan.get("tiers") or []
    return {
        "tiers": _list_join((str(tier.get("label", "")) for tier in tiers), lang),
        "frames": _list_join((str(tier.get("frame_bytes", "")) for tier in tiers), lang),
        "concurrencies": _list_join((str(item) for item in plan.get("concurrencies") or []), lang),
        "warmup": plan.get("warmup_rounds"),
        "measured": plan.get("measured_rounds"),
        "children": plan.get("child_count"),
    }


def _hex_head(frame_hex: str, lang: str) -> str:
    """帧十六进制的前若干字符（整帧太长，表格里放不下；全文在留档里）."""
    head = frame_hex[:_FRAME_HEX_HEAD]
    if len(frame_hex) <= _FRAME_HEX_HEAD:
        return head
    return t("blocks.native_line.frame_truncated", lang, head=head, total=len(frame_hex))


#: 报告里帧十六进制保留的字符数（= 16 字节的帧）。
_FRAME_HEX_HEAD = 32


def _native_line_table(rows: list[dict[str, Any]], labels: dict[str, str], lang: str) -> Block:
    """逐 (规模, 并发度) 的三形态数值与相对倍数.

    "更快的一方"一列取该格的**配对差**给出的先后（见 :mod:`zoo_bench.native_line.measure`）：
    分辨不出时留"未决"而不是留白——留白会被读成"没测"，而那是这份口径给不出结论。
    """
    forms = list(labels)
    judged = [form for form in forms if form in (FORM_NATIVE, FORM_ROUTED)]
    headers = (
        t("blocks.header.tier", lang),
        t("blocks.header.frame_bytes", lang),
        t("blocks.header.concurrency", lang),
        *(labels[form] for form in forms),
        *(t("blocks.native_line.header_ratio", lang, form=labels[form]) for form in judged),
        t("blocks.native_line.header_faster", lang),
    )
    table_rows = tuple(
        (
            str(row.get("tier", "")),
            str(row.get("frame_bytes", "")),
            str(row.get("concurrency", "")),
            *(format_seconds((row.get("medians_seconds") or {}).get(form), lang) for form in forms),
            *(_multiple((row.get("ratios_vs_pure") or {}).get(form), lang) for form in judged),
            (
                labels.get(str(row.get("faster_form")), "")
                if row.get("faster_form")
                else t("blocks.native_line.undecided", lang)
            ),
        )
        for row in rows
    )
    return Block(TABLE, headers=headers, rows=table_rows)


def _multiple(value: float | None, lang: str) -> str:
    """倍数 -> ``1.92x``（``x`` 与 :func:`_favorable_blocks` 里同一约定，两语言通用）."""
    return t("blocks.empty_marker", lang) if value is None else f"{float(value):.2f}x"


def _tier_list(tiers: Iterable[Any], lang: str) -> str:
    """档位清单；空清单写"无"——留白会被读成"这里漏了一行"，而"这一向没有档位"是要说的事实."""
    values = [str(tier) for tier in tiers]
    return _list_join(values, lang) if values else t("blocks.native_line.tiers_none", lang)


def _arm_label(arm: Any, lang: str) -> str:
    """路由两臂的显示名。**臂与形态不是同一套词表**（``python`` 臂 ↔ 纯解释形态），故分开取名；
    留档里出现本仓没收录的臂名时原样透传，不猜。"""
    key = f"native_line.arm.{arm}"
    return t(key, lang) if has_translation(key) else str(arm)


def _exploration_rate(value: Any, lang: str) -> str:
    """探索率 -> ``5.00%``（框架解析后的实际值）。

    关闭态没有解析值（框架压根没取决策器），此时写"未运行"而不是留白或写 0：写成 0 会被读成
    "探索率设成了 0"，那是另一个配置事实。
    """
    if value is None:
        return t("blocks.native_line.routing_exploration_off", lang)
    return f"{float(value) * 100.0:.2f}%"


def _native_line_crossover_text(crossover: dict[str, Any], lang: str) -> str | None:
    """规模分界结论的那句话；这份口径给不出结论时不返回一句空话。

    **三向必须同句给出**（更快 / 更慢 / 未决）：只报"哪一档更快"的话，不利与未决就只剩表格里
    的一列——读者读结论句时不会回头核对表，而那正是"不利数据 MUST 与有利数据同等公开"要防的
    呈现。分界点也只在实测给得出时给（单调且两个方向都出现），给不出时说明理由所在。

    Args:
        crossover: 留档里的分界块（:func:`zoo_bench.native_line.line._crossover` 的产物）。
        lang: 目标语言。

    Returns:
        结论句；没有判定并发度（该线没跑出数字）时为 None。
    """
    concurrency = crossover.get("judgment_concurrency")
    if concurrency is None:
        return None
    boundary = crossover.get("boundary") or {}
    boundary_text = (
        t(
            "blocks.native_line.crossover_boundary",
            lang,
            below=boundary.get("below_frame_bytes"),
            at_or_above=boundary.get("at_or_above_frame_bytes"),
        )
        if boundary
        else t("blocks.native_line.crossover_no_boundary", lang)
    )
    return t(
        "blocks.native_line.crossover",
        lang,
        concurrency=concurrency,
        faster=_tier_list(crossover.get("native_faster_tiers") or [], lang),
        slower=_tier_list(crossover.get("native_slower_tiers") or [], lang),
        undecided=_tier_list(crossover.get("undecided_tiers") or [], lang),
        boundary=boundary_text,
    )


def _agreement_clause(agreement: dict[str, Any], lang: str) -> str:
    """一致性那一层的一句话；给不出时**带上原因**，不静默略过。

    "给不出"与"不一致"是两件事：前者是这份口径分辨不出两形态，后者是路由走的臂与独立对照相左
    ——后者是结论，不能省。
    """
    agrees = agreement.get("agrees")
    arm = _arm_label(agreement.get("advantageous_arm"), lang)
    if agrees is True:
        return t("blocks.native_line.routing_agrees", lang, arm=arm)
    if agrees is False:
        return t("blocks.native_line.routing_disagrees", lang, arm=arm)
    return t(
        "blocks.native_line.routing_agreement_unknown",
        lang,
        reason=resolve_reason(
            str(agreement.get("reason", "")), lang, agreement.get("reason_params")
        ),
    )


def _native_line_routing_blocks(routing: dict[str, Any], lang: str) -> list[Block]:
    """路由结论：逐档给出两层（收敛 / 与独立对照一致）并给出所用探索率.

    **逐档而不是一句话**：收敛与否是逐档的事实（实测同一轮里可以一档收敛、一档未收敛、一档
    收敛且与对照一致），压成一句"部分收敛"读者就无从知道是哪一档，而"哪一档"正是这条结论的
    全部用处。逐臂次数与探索率并排给出，是为了让读者能自己复核"少数臂占比是否只由探索解释"。

    Args:
        routing: 留档里的路由块（:func:`zoo_bench.native_line.line._routing` 的产物）。
        lang: 目标语言。

    Returns:
        块列表；没有逐档数据（该线没跑出数字）时为空。
    """
    per_tier = routing.get("per_tier") or []
    if not per_tier:
        return []
    blocks: list[Block] = [
        Block(NOTE, text=resolve_text(routing.get("note") or "", lang)),
        Block(PARAGRAPH, text=t("blocks.native_line.routing", lang)),
    ]
    items: list[str] = []
    for item in per_tier:
        convergence = item.get("convergence") or {}
        if convergence.get("converged"):
            verdict = t(
                "blocks.native_line.routing_converged",
                lang,
                arm=_arm_label(item.get("dominant_arm"), lang),
            )
        else:
            verdict = resolve_reason(
                str(convergence.get("reason", "")), lang, convergence.get("reason_params")
            )
        verdict += _agreement_clause(item.get("agreement") or {}, lang)
        items.append(
            t(
                "blocks.native_line.routing_tier",
                lang,
                tier=str(item.get("tier", "")),
                frame_bytes=item.get("frame_bytes"),
                native_n=(item.get("counts") or {}).get("native"),
                python_n=(item.get("counts") or {}).get("python"),
                exploration=_exploration_rate(item.get("exploration"), lang),
                verdict=verdict,
            )
        )
    blocks.append(Block(BULLETS, items=tuple(items)))
    return blocks


def _paired_verdict(row: dict[str, Any], first: str, second: str) -> Any:
    """该格两形态对照的先后（留档里 ``faster_form`` 的三值原样取用）.

    按**留档里记的 ``first`` / ``second``** 找那一对，而不是自己拼键名——键名的约定是留档层的
    事，报告层再拼一遍就是第二处约定，两处迟早对不上而不报错。分辨不出时是 None（未决）。
    """
    for comparison in (row.get("comparisons") or {}).values():
        if comparison.get("first") == first and comparison.get("second") == second:
            return comparison.get("faster_form")
    return None


def _native_line_pointer_block(model: dict[str, Any], lang: str) -> Block | None:
    """「不利数据」章节里指向该线的一行指针（**不复制数字**，只指路）.

    为什么不把该线的行并进那一节的表：那张表的列是"方案 × 机身档位"，该线是"形态 × 帧规模"，
    两套列混排会被横向混读（该线自己的口径写在它那一节的开头）。故这里只回答"该线也有劣势吗、
    在哪看"——换了章节不等于少说一句，而它**只出现在不利章节**：该线的有利与不利在它自己那一
    节内由同一句话、同一张表对等给出，这里不需要第二次。

    Args:
        model: 报告模型。
        lang: 目标语言。

    Returns:
        指针块；该线没跑出数字、或两个方向都没有劣势时为 None。
    """
    dimension = (model.get("dimensions") or {}).get("native_line") or {}
    crossover = dimension.get("crossover") or {}
    rows = dimension.get("rows") or []
    concurrency = crossover.get("judgment_concurrency")
    if concurrency is None or not rows:
        return None
    native_slower = crossover.get("native_slower_tiers") or []
    # 路由形态的劣势取同一份配对差（纯解释 vs 路由）给出的先后——与表里"更快的一方"同一列，
    # 不另算一遍
    routed_slower = [
        str(row.get("tier"))
        for row in rows
        if row.get("is_judgment_concurrency")
        and _paired_verdict(row, FORM_PURE, FORM_ROUTED) == FORM_PURE
    ]
    if not native_slower and not routed_slower:
        return None
    return Block(
        NOTE,
        text=t(
            "blocks.unfavourable.native_line_pointer",
            lang,
            concurrency=concurrency,
            native_slower=_tier_list(native_slower, lang),
            routed_slower=_tier_list(routed_slower, lang),
            section=resolve_text(dimension.get("title") or "report.native_line.title", lang),
        ),
    )


def _attribution_blocks(dimensions: dict[str, Any], lang: str) -> list[Block]:
    """开销归因维度：四段、逐段结论、被测框架的细分。"""
    attribution = dimensions.get("attribution", {})
    labels: dict[str, str] = attribution.get("segment_labels", {})
    blocks = [
        _heading(
            f"{dimension_section_prefix(lang)}"
            f"{attribution.get('title') or t('report.attribution.title', lang)}"
        ),
        Block(NOTE, text=t("blocks.scope_note", lang, note=attribution.get("scope_note", ""))),
        Block(
            PARAGRAPH,
            text=t("blocks.semantics.status", lang, status=attribution.get("status")),
        ),
    ]
    if attribution.get("reason"):
        blocks.append(Block(PARAGRAPH, text=str(attribution["reason"])))
    if attribution.get("note"):
        blocks.append(
            Block(
                NOTE,
                text=resolve_text(
                    attribution["note"],
                    lang,
                    {"concurrencies": attribution.get("over_subscribed_concurrencies", [])},
                ),
            )
        )

    sampled = attribution.get("tiers_us") or []
    if sampled:
        blocks.append(
            Block(
                PARAGRAPH,
                text=t(
                    "blocks.attribution.sampled",
                    lang,
                    tiers=_list_join((f"{tier:g}" for tier in sampled), lang),
                    concurrencies=attribution.get("concurrencies"),
                    cores=attribution.get("cores"),
                ),
            )
        )

    groups = attribution.get("groups") or []
    if groups:
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.scheme", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.tier", lang),
                    *(labels.values()),
                    t("blocks.header.status", lang),
                ),
                rows=tuple(
                    (
                        group["adapter"],
                        str(group["concurrency"]),
                        f"{float(group['tier_us']):g}",
                        *(
                            format_seconds((group.get("segments") or {}).get(key), lang)
                            for key in labels
                        ),
                        group["status"]
                        if group["status"] == "ok"
                        else t(
                            "blocks.attribution.status_reason",
                            lang,
                            status=group["status"],
                            reason=resolve_text(group.get("reason") or "", lang),
                        ),
                    )
                    for group in groups
                ),
            )
        )

    findings = attribution.get("findings") or []
    if findings:
        blocks.append(_heading(t("blocks.attribution.findings_title", lang), level=3))
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.baseline", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.tier", lang),
                    t("blocks.header.excess_per_task", lang),
                    t("blocks.header.dominant_segment", lang),
                    t("blocks.header.segment_gap", lang),
                ),
                rows=tuple(
                    (
                        row["adapter"],
                        str(finding["concurrency"]),
                        f"{finding['tier_us']:g}",
                        format_seconds(row["excess_seconds"], lang),
                        labels.get(row["dominant_segment"], row["dominant_segment"]),
                        format_seconds(
                            row["segment_excess_seconds"][row["dominant_segment"]], lang
                        ),
                    )
                    for finding in findings
                    for row in finding["per_adapter"]
                ),
            )
        )

    seals = sorted({seal for group in groups for seal in (group.get("seals") or [])})
    if seals:
        blocks.append(
            Block(
                NOTE,
                text=t(
                    "blocks.attribution.seals",
                    lang,
                    seals=_list_join(
                        (resolve_text(seal, lang) for seal in seals), lang, clause=True
                    ),
                ),
            )
        )
    missing = sorted({name for group in groups for name in (group.get("unavailable_seals") or [])})
    if missing:
        # "这一代没有这处入口"与"有、但花了 0 微秒"含义相反，必须分开说
        blocks.append(
            Block(
                NOTE,
                text=t(
                    "blocks.attribution.seals_missing",
                    lang,
                    seals=_list_join(
                        (resolve_text(name, lang) for name in missing), lang, clause=True
                    ),
                ),
            )
        )

    costs = [
        (
            group["adapter"],
            float(group["tier_us"]),
            int(group["concurrency"]),
            group["instrumentation"],
        )
        for group in groups
        if group.get("instrumentation")
    ]
    if costs:
        # 插桩自身的成本必须随结论发布：不写出来，读者会把"细分项比对照大"整个读成框架的成本
        blocks.append(
            Block(
                NOTE,
                text=t(
                    "blocks.attribution.instrumentation_cost",
                    lang,
                    costs=_list_join(
                        (
                            t(
                                "blocks.attribution.instrumentation_item",
                                lang,
                                scheme=adapter,
                                tier=tier,
                                concurrency=concurrency,
                                before=format_seconds(values["baseline_end_to_end_seconds"], lang),
                                after=format_seconds(
                                    values["baseline_end_to_end_seconds"] + values["delta_seconds"],
                                    lang,
                                ),
                                delta=format_seconds(abs(values["delta_seconds"]), lang),
                            )
                            + (
                                t("blocks.attribution.instrumentation_drift", lang)
                                if values["delta_seconds"] < 0
                                else ""
                            )
                            for adapter, tier, concurrency, values in costs
                        ),
                        lang,
                        clause=True,
                    ),
                ),
            )
        )

    overlaps = [
        (
            group["adapter"],
            float(group["tier_us"]),
            int(group["concurrency"]),
            group["drill_overlap"],
        )
        for group in groups
        if (group.get("drill_overlap") or {}).get("max_overlap_ratio")
    ]
    if overlaps:
        # 细分能不能相加必须当场说清楚：不写出来，读者会把几个独立测量当成一个划分去加
        blocks.append(
            Block(
                NOTE,
                text=t(
                    "blocks.attribution.additivity",
                    lang,
                    note=resolve_text(str(overlaps[0][3].get("note", "")), lang),
                    overlaps=_list_join(
                        (
                            t(
                                "blocks.attribution.additivity_item",
                                lang,
                                scheme=adapter,
                                tier=tier,
                                concurrency=concurrency,
                                overlap=values["max_overlap_ratio"],
                            )
                            for adapter, tier, concurrency, values in overlaps
                        ),
                        lang,
                        clause=True,
                    ),
                ),
            )
        )

    drill_labels: dict[str, str] = attribution.get("drill_labels", {})
    drilled = [finding for finding in findings if finding.get("subject_drill_down")]
    if drilled and drill_labels:
        blocks.append(_heading(t("blocks.attribution.drills_title", lang), level=3))
        blocks.append(
            Block(
                BULLETS,
                items=tuple(
                    t(
                        "blocks.attribution.drill_item",
                        lang,
                        concurrency=finding["concurrency"],
                        tier=finding["tier_us"],
                        parts=_list_join(
                            (
                                f"{drill_labels.get(key, key)} {format_seconds(value, lang)}"
                                for key, value in finding["subject_drill_down"].items()
                            ),
                            lang,
                            clause=True,
                        ),
                    )
                    for finding in drilled
                ),
            )
        )

    if attribution.get("summary"):
        blocks.append(_heading(t("blocks.attribution.summary_title", lang), level=3))
        blocks.append(Block(BULLETS, items=tuple(str(line) for line in attribution["summary"])))
    return blocks


def _favorable_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    """被测框架处于优势的档位。

    **结构与「公开的不利数据」刻意逐列对称**（只有方向不同）：两节出自同一份同运行内比值，
    读者按「赢在哪 / 输在哪」的顺序读，任一方都不是脚注——这也让"本节缺席"与"本节为空"在
    版面上同样看得见。
    """
    favorable = model.get("favorable", {})
    blocks = [
        _heading(t("blocks.favourable.title", lang)),
        Block(NOTE, text=str(favorable.get("note", ""))),
    ]

    items = favorable.get("items", [])
    if not items:
        blocks.append(Block(PARAGRAPH, text=t("blocks.favourable.empty", lang)))
        return blocks

    blocks.append(
        Block(
            TABLE,
            headers=(
                t("blocks.header.baseline", lang),
                t("blocks.header.concurrency", lang),
                t("blocks.header.body_tier", lang),
                t("blocks.header.gap", lang),
                t("blocks.header.subject_median", lang),
            ),
            rows=tuple(
                (
                    item["baseline"],
                    str(item["concurrency"]),
                    f"{item['body_tier_us']:g}",
                    item["margin"],
                    format_seconds(item["subject_median_seconds"], lang),
                )
                for item in items
            ),
        )
    )
    return blocks


def _tied_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    """分不出胜负的档位：两侧差异小于该档位的带宽。

    **单列成节、覆盖全部所测档位**：它既不是"藏东西"的地方，也不是优/劣两节的子集。把噪声级差异
    报成「快 1.00x」才是问题所在，而不是把它们如实列出来。
    """
    tied = model.get("tied", {})
    blocks = [
        _heading(t("blocks.tied.title", lang)),
        Block(NOTE, text=str(tied.get("note", ""))),
    ]
    band = model.get("tie_band") or {}
    if band.get("definition"):
        blocks.append(
            Block(NOTE, text=t("blocks.tied.criterion", lang, definition=band["definition"]))
        )

    low, high = tied.get("band_min"), tied.get("band_max")
    if low is not None and high is not None:
        blocks.append(
            Block(
                PARAGRAPH,
                text=t("blocks.tied.band_now", lang, low=float(low), high=float(high)),
            )
        )

    items = tied.get("items", [])
    if not items:
        blocks.append(Block(PARAGRAPH, text=t("blocks.tied.empty", lang)))
        return blocks

    blocks.append(
        Block(
            TABLE,
            headers=(
                t("blocks.header.baseline", lang),
                t("blocks.header.concurrency", lang),
                t("blocks.header.body_tier", lang),
                t("blocks.header.ratio", lang),
                t("blocks.header.reason", lang),
            ),
            rows=tuple(
                (
                    item["baseline"],
                    str(item["concurrency"]),
                    f"{item['body_tier_us']:g}",
                    f"{float(item['baseline_ratio_vs_subject']):.2f}x",
                    item["reason"],
                )
                for item in items
            ),
        )
    )
    return blocks


#: 附录的说明：为什么明细在末尾、以及它一个都没少。
def _appendix_note(lang: str) -> str:
    return t("blocks.appendix.note", lang)


def _appendix_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    """附录：全部数值 —— 逐单元明细。

    位置在正文之后只解决**顺序**，不减少任何一行：读者要核对某个数字时翻到这里，逐单元逐字段都在。
    """
    dimensions = model.get("dimensions", {})
    blocks: list[Block] = [
        _heading(appendix_section_prefix(lang) + t("blocks.appendix.all_values", lang)),
        Block(NOTE, text=_appendix_note(lang)),
    ]

    latency = dimensions.get("latency", {})
    if latency.get("rows"):
        blocks.append(
            _heading(
                f"{appendix_section_prefix(lang)}"
                f"{latency.get('title') or t('report.dimension.latency.title', lang)}",
                level=3,
            )
        )
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.scheme", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.body_tier", lang),
                    t("blocks.header.median", lang),
                    t("blocks.header.p95", lang),
                    t("blocks.header.p99", lang),
                    t("blocks.header.relative_spread", lang),
                ),
                rows=tuple(
                    (
                        row["adapter"],
                        str(row["concurrency"]),
                        f"{row['body_tier_us']:g}",
                        format_seconds(row["end_to_end_per_task"]["median"], lang),
                        format_seconds(row["end_to_end_per_task"]["p95"], lang),
                        format_seconds(row["end_to_end_per_task"]["p99"], lang),
                        f"{float(row['end_to_end_per_task']['relative_spread']):.3f}",
                    )
                    for row in latency["rows"]
                ),
            )
        )

    overhead = dimensions.get("overhead", {})
    if overhead.get("rows"):
        blocks.append(
            _heading(
                f"{appendix_section_prefix(lang)}"
                f"{overhead.get('title') or t('report.dimension.overhead.title', lang)}",
                level=3,
            )
        )
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.scheme", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.body_tier", lang),
                    t("blocks.header.overhead", lang),
                    t("blocks.header.overhead_share", lang),
                    t("blocks.header.body_measured", lang),
                ),
                rows=tuple(
                    (
                        row["adapter"],
                        str(row["concurrency"]),
                        f"{row['body_tier_us']:g}",
                        format_seconds(row["framework_overhead_seconds"], lang),
                        format_ratio(row["framework_overhead_ratio"], lang),
                        format_seconds(row["body_seconds"], lang),
                    )
                    for row in overhead["rows"]
                ),
            )
        )

    throughput = dimensions.get("throughput", {})
    if throughput.get("rows"):
        blocks.append(
            _heading(
                f"{appendix_section_prefix(lang)}"
                f"{throughput.get('title') or t('report.dimension.throughput.title', lang)}",
                level=3,
            )
        )
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.scheme", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.body_tier", lang),
                    t("blocks.header.throughput", lang),
                ),
                rows=tuple(
                    (
                        row["adapter"],
                        str(row["concurrency"]),
                        f"{row['body_tier_us']:g}",
                        f"{float(row['throughput_per_second']):.1f}",
                    )
                    for row in throughput["rows"]
                ),
            )
        )
    return blocks


def _unfavorable_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    unfavorable = model.get("unfavorable", {})
    blocks = [
        _heading(unfavorable_section_title(lang)),
        Block(NOTE, text=str(unfavorable.get("note", ""))),
    ]

    items = unfavorable.get("items", [])
    if not items:
        blocks.append(Block(PARAGRAPH, text=t("blocks.unfavourable.empty", lang)))
    else:
        blocks.append(
            Block(
                TABLE,
                headers=(
                    t("blocks.header.baseline", lang),
                    t("blocks.header.concurrency", lang),
                    t("blocks.header.body_tier", lang),
                    t("blocks.header.gap", lang),
                    t("blocks.header.subject_median", lang),
                ),
                rows=tuple(
                    (
                        item["baseline"],
                        str(item["concurrency"]),
                        f"{item['body_tier_us']:g}",
                        item["gap"],
                        format_seconds(item["subject_median_seconds"], lang),
                    )
                    for item in items
                ),
            )
        )
    # 本节为空也要给指针：主矩阵没劣势，不等于该线没劣势——那正是读者会来这一节找的东西
    pointer = _native_line_pointer_block(model, lang)
    if pointer is not None:
        blocks.append(pointer)
    return blocks


def caveat_text(caveat: dict[str, Any], lang: str) -> str:
    """一条口径局限的文字。适配器名可选——不是每条局限都归属于某个方案。

    ``text`` 是**留档里的值**：新档存的是目录键（方案的 ``notes``、zoo 的 ``drive_level``），
    旧档存的是散文，一律经 :func:`resolve_text` 透传或解析。
    """
    adapter = (
        t("blocks.caveat.adapter", lang, adapter=caveat["adapter"]) if caveat.get("adapter") else ""
    )
    return t(
        "blocks.caveat.line",
        lang,
        kind=caveat.get("kind", ""),
        adapter=adapter,
        text=resolve_text(caveat.get("text", ""), lang),
    )


def _methodology_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    """测量口径：让读者知道这些数字是怎么来的。

    独立成节而不是散在各处——**三条口径是数字可信度的全部依据**，读者要能一眼看全并据此判断
    适用性。三条各自对应一类曾被真实踩到的错误：
    ① 跨运行做减法会把开销高估一倍；② 预热不入统计，否则冷启动成本混进结果；
    ③ 绝对耗时跨运行不可比，故相对比才是证据主体。
    """
    run = model.get("run", {})
    warmups = run.get("warmup_rounds") or []
    rounds = run.get("measured_rounds") or []
    warmup_text = _list_join((str(value) for value in warmups), lang) or t(
        "blocks.empty_marker", lang
    )
    rounds_text = _list_join((str(value) for value in rounds), lang) or t(
        "blocks.empty_marker", lang
    )

    return [
        _heading(t("blocks.methodology.title", lang)),
        Block(
            BULLETS,
            items=(
                t("blocks.methodology.in_run", lang),
                t("blocks.methodology.warmup", lang, warmups=warmup_text, rounds=rounds_text),
                t("blocks.methodology.absolute", lang),
            ),
        ),
    ]


def _extension_blocks(lang: str) -> list[Block]:
    """如何加入你自己的对照。

    独立成节而不是塞进脚注：它是这份报告**可被证伪**的前提——读者若能自己跑一遍对照，报告里
    的数字才不是只能听信的一面之词。
    """
    return [
        Block(HEADING, text=t("blocks.extension.title", lang)),
        Block(PARAGRAPH, text=t("blocks.extension.note", lang)),
    ]


def _caveat_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    caveats = model.get("caveats", [])
    items = tuple(caveat_text(caveat, lang) for caveat in caveats)
    blocks = [_heading(t("blocks.caveats.title", lang))]
    blocks.append(Block(BULLETS, items=items or (t("blocks.caveats.none", lang),)))
    blocks.append(
        Block(
            NOTE,
            text=t(
                "blocks.caveats.absolute",
                lang,
                note=resolve_text(str(model.get("absolute", {}).get("note", "")), lang),
            ),
        )
    )
    return blocks


def _self_check_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    self_check = model.get("self_check", {})
    return [
        _heading(t("blocks.self_check.title", lang)),
        Block(
            BULLETS,
            items=(
                t(
                    "blocks.self_check.overall",
                    lang,
                    verdict=(
                        t("blocks.self_check.passed", lang)
                        if self_check.get("ok")
                        else t("blocks.self_check.failed", lang)
                    ),
                ),
                t(
                    "blocks.self_check.isolation",
                    lang,
                    value=model.get("absolute", {}).get("process_isolation", {}),
                ),
            ),
        ),
    ]


def _header_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    """标题与原始数据出处。

    放在块层而不是各序列化器里——三个后端都得有同一个标题与同一句出处，各写一遍就是三处漂移点。
    """
    blocks = [Block(HEADING, text=t("blocks.report.title", lang), level=1)]
    source = model.get("source")
    if source and source.get("path"):
        blocks.append(
            Block(
                PARAGRAPH,
                text=t(
                    "blocks.report.source",
                    lang,
                    path=repository_relative_path(str(source["path"])),
                ),
            )
        )
    return blocks


def build_blocks(
    model: dict[str, Any],
    charts: list[dict[str, Any]],
    *,
    figures_rel: str = "figures",
    lang: str,
) -> list[Block]:
    """由报告模型抽出与输出格式无关的块列表。

    Args:
        model: :func:`zoo_bench.report.build_model` 的返回值。
        charts: :func:`zoo_bench.render.charts.render_all` 的返回值。
        figures_rel: 图表目录相对报告文件的路径。
        lang: 报告语言（``"en"`` 或 ``"zh"``）。

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
        blocks += builder(model, lang)
    blocks += _chart_blocks(charts, figures_rel, lang)
    blocks += _dimension_blocks(model, lang)
    # 优 / 平 / 劣三节同源同显著：读者按「赢在哪、分不出、输在哪」成对地读，任一方都不是脚注
    blocks += _favorable_blocks(model, lang)
    blocks += _unfavorable_blocks(model, lang)
    blocks += _tied_blocks(model, lang)
    blocks += _caveat_blocks(model, lang)
    blocks += _extension_blocks(lang)
    blocks += _self_check_blocks(model, lang)
    # 明细在最后：正文先给结论与透视，读者要核对时再翻附录
    blocks += _appendix_blocks(model, lang)
    return blocks
