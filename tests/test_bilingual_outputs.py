"""双语产物的验收（spec: comparison-report 的「站点双语」与「PDF 双语」增量）。

三条主轴（tasks 3.5）：

1. 英文产物的**正文**不留中文原文（样式与脚本里的注释是开发者可见的，不属正文，剥离后检查）；
2. 英文产物在**没有任何 CJK 字体**的环境里照样导出——模拟方式是把字体搜索打桩成必炸，
   英文产物一次都不该走到那一步，中文产物则必须在那里明确失败；
3. 两种语言的图表文件**并存于同一目录**且互不覆盖（文件名带语言前缀，design D5）。

模型一律由真实的 :func:`zoo_bench.report.build_model` 产出（而不是手写模型 dict）——
否则测的是 fixture 的文字，不是渲染链路的。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from zoo_bench import runner
from zoo_bench.adapters import Tier, registry
from zoo_bench.generations import (
    CURRENT_REQUIRED,
    PREVIOUS_REQUIRED,
    probe_drive_generation,
)
from zoo_bench.i18n import LANG_EN, LANG_ZH, LANGS, has_translation, t
from zoo_bench.render import charts, deck, pdf
from zoo_bench.render import markdown as markdown_renderer
from zoo_bench.render.blocks import UnsafeReportText, build_blocks
from zoo_bench.render.fonts import CjkFontUnavailable
from zoo_bench.report import OVERHEAD_THRESHOLD, build_model
from zoo_bench.runner import ABSOLUTE_NOTE

#: 机器自述里不含中文：fixture 的 CPU 型号是合成数据，英文正文检查才有判别力。
_ENVIRONMENT = {
    "hardware": {"cpu_model": "Synthetic CPU", "logical_cores": 8},
    "harness": {"version": "0.0.1"},
}

_ROUNDS = 8


def _summary(value: float, n: int = 3) -> dict[str, float | int]:
    return {
        "n": n,
        "median": value,
        "mean": value,
        "min": value,
        "max": value,
        "stdev": 0.0,
        "iqr": 0.0,
        "p50": value,
        "p95": value,
        "p99": value,
        "relative_spread": 0.0,
    }


def _rounds(e2e_per_task: float, concurrency: int) -> list[dict[str, Any]]:
    """分类"分得出胜负"要用逐轮样本（见 report.MIN_ROUNDS_FOR_BAND），fixture 必须带上。"""
    return [
        {
            "batch": concurrency,
            "wall_seconds": concurrency * e2e_per_task,
            "round": index,
        }
        for index in range(_ROUNDS)
    ]


def _unit(
    adapter: str, *, tier_us: float, e2e_per_task: float, concurrency: int, under_test: bool
) -> dict[str, Any]:
    body = e2e_per_task * 0.9
    return {
        "spec": {"adapter": adapter, "concurrency": concurrency, "body_tier_us": tier_us},
        "status": "ok",
        "process": {"pid": 1000},
        "adapter": {
            "name": adapter,
            "tier": "under_test" if under_test else "bare",
            "comparable": True,
            "notes": "",
            "drive_level": "",
        },
        "absolute": {
            "note": ABSOLUTE_NOTE,
            "concurrency": concurrency,
            "end_to_end_per_task_seconds": _summary(e2e_per_task),
            "body_seconds": _summary(body),
            "framework_overhead_seconds": e2e_per_task - body,
            "framework_overhead_ratio": (e2e_per_task - body) / e2e_per_task,
            "throughput_per_second": concurrency / e2e_per_task,
        },
        "checks": {"body": {"body_deviation_ok": True}},
        "rounds": _rounds(e2e_per_task, concurrency),
    }


def _units() -> list[dict[str, Any]]:
    """两个档位 × 两个并发度 × 两个方案：三张图与逐档倍数都有数可画。"""
    units: list[dict[str, Any]] = []
    for tier_us, e2e in ((300, 0.0004), (3000, 0.004)):
        for concurrency in (4, 8):
            units.append(
                _unit(
                    "zoo",
                    tier_us=tier_us,
                    e2e_per_task=e2e,
                    concurrency=concurrency,
                    under_test=True,
                )
            )
            units.append(
                _unit(
                    "baseline-a",
                    tier_us=tier_us,
                    e2e_per_task=e2e * 1.2,
                    concurrency=concurrency,
                    under_test=False,
                )
            )
    return units


def _comparisons() -> list[dict[str, Any]]:
    """相对倍数：对照方案处处慢 20%（>1），交叉点图与劣势归类都有内容。"""
    return [
        {
            "concurrency": concurrency,
            "body_tier_us": tier_us,
            "subject": "zoo",
            "subject_median_seconds": e2e,
            "ratios_vs_subject": {"baseline-a": 1.2},
            "excluded_incomparable": [],
            "note": "",
        }
        for tier_us, e2e in ((300, 0.0004), (3000, 0.004))
        for concurrency in (4, 8)
    ]


def _result() -> dict[str, Any]:
    return {
        "run": {"platform": "test", "python": "3.13", "absolute_note": ABSOLUTE_NOTE},
        "units": _units(),
        "relative": {"note": "", "comparisons": _comparisons()},
        "verification": {},
        "process_isolation": {"parent_pid": 1, "child_pids": [1000], "all_distinct": True},
        "self_check": {"ok": True},
    }


def _model(lang: str):
    model = build_model(_result(), environment=_ENVIRONMENT, lang=lang)
    # 真实 CLI 在 build_model 之后补 source（cli.py）——fixture 照做：否则
    # ``blocks.report.source`` 的英文文案从不出现在被检查的正文里，"英文无中文"对它没有判别力
    model["source"] = {"path": "results/zoo-framework/0.0.1/20260101T000000Z.json"}
    return model


def _visible_text(html: str) -> str:
    """剥掉 <style>/<script>（里面的中文注释是开发者文案，不属正文）、语言切换导航与全部标签
    后的可见文本。

    语言切换的标签是目标语言的本名（英文页上写「中文」）——导航惯例，豁免见
    :func:`test_english_messages_are_ascii` 的说明。
    """
    without_style = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    without_script = re.sub(r"<script.*?</script>", "", without_style, flags=re.S)
    without_switch = re.sub(r'<nav class="lang-switch">.*?</nav>', "", without_script, flags=re.S)
    return re.sub(r"<[^>]+>", " ", without_switch)


def _cjk(text: str) -> list[str]:
    return sorted({character for character in text if "一" <= character <= "鿿"})


def test_english_products_leave_no_chinese_prose(tmp_path: Path) -> None:
    """英文的 deck 与 Markdown 正文不得残留中文——残留即是漏翻译，不是风格问题。"""
    model = _model(LANG_EN)
    figures = charts.render_all(model, tmp_path / "figures", threshold=OVERHEAD_THRESHOLD)

    markdown = markdown_renderer.render_markdown(model, figures)
    assert not _cjk(markdown), f"英文 Markdown 残留中文：{_cjk(markdown)}"

    html = deck.render_deck(model, build_blocks(model, figures, lang=LANG_EN))
    offenders = _cjk(_visible_text(html))
    assert not offenders, f"英文 deck 残留中文：{offenders}"

    assert '<html lang="en"' in html
    assert "<title>zoo-framework benchmark</title>" in html


def test_chinese_products_still_render_chinese(tmp_path: Path) -> None:
    """反向对照：中文产物**必须**有中文——上面的检查若因为 Fixture 恰好全英文而恒真就废了。"""
    model = _model(LANG_ZH)
    figures = charts.render_all(model, tmp_path / "figures", threshold=OVERHEAD_THRESHOLD)
    html = deck.render_deck(model, build_blocks(model, figures, lang=LANG_ZH))

    assert _cjk(_visible_text(html)), "中文 deck 一个汉字都没有，fixture 或渲染链路坏了"
    assert '<html lang="zh-CN"' in html
    assert "<title>zoo-framework 性能评测</title>" in html


def test_english_pdf_needs_no_cjk_font(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """英文 PDF 不依赖 CJK 字体：把字体搜索打桩成必炸，英文产物一次都不该走到那里。"""

    def _no_fonts(text: str, candidates: list[str] | None = None) -> dict[str, object]:
        raise CjkFontUnavailable("本机没有任何 CJK 字体（测试模拟）")

    monkeypatch.setattr(pdf, "find_pdf_font", _no_fonts)

    model = _model(LANG_EN)
    figures = charts.render_all(model, tmp_path / "figures", threshold=OVERHEAD_THRESHOLD)
    outcome = pdf.render_pdf(model, figures, tmp_path / "report-en.pdf", figures_dir=tmp_path)

    assert Path(outcome["pdf"]).is_file()
    assert outcome["font"]["path"] is None, "英文产物用的是内置字体，不该报系统字体路径"


def test_chinese_pdf_still_fails_loudly_without_a_cjk_font(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个无字体环境里，中文产物必须明确失败——不能产出一份满屏方框的 PDF。"""

    def _no_fonts(text: str, candidates: list[str] | None = None) -> dict[str, object]:
        raise CjkFontUnavailable("本机没有任何 CJK 字体（测试模拟）")

    monkeypatch.setattr(pdf, "find_pdf_font", _no_fonts)

    model = _model(LANG_ZH)
    figures = charts.render_all(model, tmp_path / "figures", threshold=OVERHEAD_THRESHOLD)

    with pytest.raises(CjkFontUnavailable):
        pdf.render_pdf(model, figures, tmp_path / "report-zh.pdf", figures_dir=tmp_path)


def test_chart_files_of_both_languages_coexist(tmp_path: Path) -> None:
    """两种语言的图表落在**同一个** figures 目录里：文件名带语言前缀，互不覆盖。"""
    for lang in (LANG_EN, LANG_ZH):
        charts.render_all(_model(lang), tmp_path, threshold=OVERHEAD_THRESHOLD)

    names = [path.name for path in tmp_path.iterdir() if path.suffix in (".svg", ".png")]
    # 三张图 × 两种语言 × 两种格式；少一张图 = 该语言/该图被另一语言覆盖了
    assert len(names) == 3 * 2 * 2, f"图表文件数不对：{sorted(names)}"
    assert all(name.split("-", 1)[0] in {"en", "zh"} for name in names), f"缺语言前缀：{names}"


# ------------------------------------------------- 留档自述字段的键化（tasks 6.1/6.2/6.4）


def _assert_is_catalog_key(value: str, *, where: str) -> None:
    """自述字段必须是**目录键**而不是散文——留档因此才是语言无关的数据（design D8）。"""
    assert value, f"{where} 是空的：键化没做到"
    assert has_translation(value), (
        f"{where} 存的是散文而不是目录键：{value[:60]!r}——新留档存键，渲染时按语言解析（design D8）"
    )


def _assert_renders_in_both_languages(value: str, *, where: str, **params: Any) -> None:
    """键与它的模板参数**同行**时，连"两种语言都填得出参数"一并断言。

    参数对不上时 :func:`t` 抛错，故"键换了但模板参数没跟着换"也被拦下。
    """
    _assert_is_catalog_key(value, where=where)
    for lang in LANGS:
        t(value, lang, **params)


def test_adapter_self_description_is_keyed() -> None:
    """每个方案的 ``notes`` 都要是目录键；``drive_level`` 只有被测对象声明。

    名单从注册表取而**不是手抄**：新增方案时本用例自动覆盖它，而手抄的名单恰好在那一刻漏掉
    新方案——它正是最可能写散文的那一个。
    """
    registry.load_builtins()
    adapters = registry.registered()
    assert adapters, "注册表为空，本用例验的是空集合"

    for name, cls in adapters.items():
        _assert_renders_in_both_languages(getattr(cls, "notes", ""), where=f"方案 {name} 的 notes")

    declaring = {name for name, cls in adapters.items() if getattr(cls, "drive_level", "")}
    subject = {name for name, cls in adapters.items() if cls.tier is Tier.UNDER_TEST}
    assert declaring == subject, (
        f"声明 drive_level 的是 {sorted(declaring)}，而被测对象是 {sorted(subject)}"
        "——驱动层级只对被测对象有意义"
    )
    for name in declaring:
        _assert_renders_in_both_languages(
            adapters[name].drive_level, where=f"方案 {name} 的 drive_level"
        )


def test_generation_self_description_is_keyed() -> None:
    """世代自述（标签、探测项名、形态描述）全走目录——它随留档进报告，故必须是键。"""
    probe = probe_drive_generation()

    _assert_renders_in_both_languages(probe["label"], where="世代标签")
    assert probe["capabilities"], "探测结果里一个能力项都没有，本用例验的是空集合"
    for item in probe["capabilities"]:
        _assert_renders_in_both_languages(item["capability"], where=f"探测项 {item['capability']}")
        _assert_renders_in_both_languages(
            item["observed"],
            where=f"{item['capability']} 的形态描述",
            **(item.get("observed_params") or {}),
        )

    for key in (*CURRENT_REQUIRED, *PREVIOUS_REQUIRED):
        _assert_renders_in_both_languages(key, where="世代判定要求项")


def test_runner_notes_are_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    """runner 写进留档的注记必须是目录键——用数据把**每个产生注记的分支**走一遍来取证。

    第一版扫源码里 ``runner.``/``report.`` 前缀的字面量，被注入实验证伪：把某一支的键换回
    中文散文，散文不带前缀、根本不进扫描的视野，用例照样全绿。走真分支才有牙齿；分支与键
    的对应关系由 test_measurement_protocol 的判别力用例钉住，这里断言的是"每个分支产出的
    注记是键，且两种语言都解析得动"。

    归因探查例外：``_run_attribution`` 会跑真测量，打桩 ``run_in_child`` 后仍走真实的注记
    组装逻辑（含超订时追加那一条）。其键的参数由渲染侧从同一维度的
    ``over_subscribed_concurrencies`` 供给，留档里键与参数不同行（design D8），故归因注记
    只断言"是键"（:func:`_assert_renders_in_both_languages` 传不出那份参数）。
    """

    def _notes_of(checks: list[dict[str, Any]]) -> set[str]:
        return {check["note"] for check in checks if check.get("note")}

    notes: set[str] = set()

    # 最短档开销非正：同组两档、最短那档开销为 0 → 比值判据不适用
    nonpositive = [
        _unit("zoo", tier_us=300, e2e_per_task=0.0004, concurrency=1, under_test=True),
        _unit("zoo", tier_us=3000, e2e_per_task=0.004, concurrency=1, under_test=True),
    ]
    nonpositive[0]["absolute"]["framework_overhead_seconds"] = 0.0
    overhead = runner.check_overhead_across_tiers(nonpositive)
    assert _notes_of(overhead["checks"]) == {"runner.self_check.overhead_nonpositive"}
    notes |= _notes_of(overhead["checks"])

    # 高并发档受排队污染：判定档（并发度 1）开销平稳，并发度 8 的开销逐档涨 6 倍（> 上限 5）
    quiet = [
        _unit("zoo", tier_us=300, e2e_per_task=0.0004, concurrency=1, under_test=True),
        _unit("zoo", tier_us=3000, e2e_per_task=0.004, concurrency=1, under_test=True),
    ]
    for unit in quiet:
        unit["absolute"]["framework_overhead_seconds"] = 0.0001
    polluted = [
        _unit("zoo", tier_us=300, e2e_per_task=0.0004, concurrency=8, under_test=True),
        _unit("zoo", tier_us=3000, e2e_per_task=0.004, concurrency=8, under_test=True),
    ]
    polluted[0]["absolute"]["framework_overhead_seconds"] = 0.0001
    polluted[1]["absolute"]["framework_overhead_seconds"] = 0.0006
    overhead = runner.check_overhead_across_tiers([*quiet, *polluted])
    assert _notes_of(overhead["checks"]) == {"runner.self_check.overhead_polluted"}
    notes |= _notes_of(overhead["checks"])

    def _deviation_unit(tier_us: float, *, concurrency: int, ok: bool) -> dict[str, Any]:
        unit = _unit(
            "zoo",
            tier_us=tier_us,
            e2e_per_task=tier_us / 1e6,
            concurrency=concurrency,
            under_test=True,
        )
        unit["checks"]["body"] = {
            "body_deviation_ok": ok,
            "body_target_seconds": tier_us / 1e6,
            "body_observed_median_seconds": tier_us / 1e6 * (1.0 if ok else 2.0),
            "body_deviation": 0.0 if ok else 1.0,
        }
        return unit

    # 执行体偏差不判否的两条原因：档位过短（并发度 1 但 < 100 µs）与超订（并发度非最低）
    graded = runner.grade_body_deviation(
        [
            _deviation_unit(300, concurrency=1, ok=True),
            _deviation_unit(40, concurrency=1, ok=False),
            _deviation_unit(3000, concurrency=8, ok=False),
        ]
    )
    assert _notes_of(graded["checks"]) == {
        "runner.self_check.body_too_short",
        "runner.self_check.body_oversubscribed",
    }
    notes |= _notes_of(graded["checks"])

    # 相对比：块级注记 + 每组对照各一条
    relative = runner._relative_block(_units())
    assert relative["comparisons"], "fixture 没凑出任何对照组，本断言会是空集合恒真"
    assert _notes_of([relative, *relative["comparisons"]]) == {
        "runner.relative.note",
        "runner.relative.comparison_note",
    }
    notes |= _notes_of([relative, *relative["comparisons"]])

    # 绝对耗时数字的固定标注
    _assert_renders_in_both_languages(runner.ABSOLUTE_NOTE, where="ABSOLUTE_NOTE")

    # 归因探查：打桩子进程探针，注记组装逻辑照真走（并发度 1 不超订；10**6 必超订）
    def _stub_probe(kind: str, spec: dict[str, Any], *, timeout: float = 0.0) -> dict[str, Any]:
        return {"status": "ok", "child_elapsed_seconds": 0.0, "payload": {}}

    monkeypatch.setattr(runner, "run_in_child", _stub_probe)
    plain = runner._run_attribution(
        [runner.UnitSpec(adapter="zoo", framework="0.0.1", concurrency=1, body_tier_us=300.0)]
    )
    oversubscribed = runner._run_attribution(
        [runner.UnitSpec(adapter="zoo", framework="0.0.1", concurrency=10**6, body_tier_us=300.0)]
    )
    assert plain["note"] == ["report.attribution.diagnosis_note"]
    assert oversubscribed["note"] == [
        "report.attribution.diagnosis_note",
        "report.attribution.oversubscribed_note",
    ]
    for key in (*plain["note"], *oversubscribed["note"]):
        _assert_is_catalog_key(key, where=f"归因注记 {key!r}")

    for note in sorted(notes):
        _assert_renders_in_both_languages(note, where=f"runner 注记 {note!r}")


def _archive_with_notes(notes: str) -> dict[str, Any]:
    """把第一条单元的方案自述换成给定值：新档放键，旧档放散文（design D8）。"""
    result = _result()
    result["units"][0]["adapter"]["notes"] = notes
    return result


def test_keyed_self_description_renders_in_the_target_language() -> None:
    """新留档存键：同一份留档在两种语言下各渲染出各自的文字（6.2 的正向那一半）。

    没有这条，下面那条"旧档散文要失败"就分不清"渲染对了"与"压根没渲染"。
    """
    model = build_model(
        _archive_with_notes("adapters.notes.zoo"), environment=_ENVIRONMENT, lang=LANG_EN
    )
    markdown = markdown_renderer.render_markdown(model, [])

    assert not _cjk(markdown)
    assert t("adapters.notes.zoo", LANG_EN) in markdown, "键没被解析成英文文案"

    chinese = build_model(
        _archive_with_notes("adapters.notes.zoo"), environment=_ENVIRONMENT, lang=LANG_ZH
    )
    assert t("adapters.notes.zoo", LANG_ZH) in markdown_renderer.render_markdown(chinese, [])


def test_old_archive_prose_fails_the_english_export_and_names_the_item(tmp_path: Path) -> None:
    """旧留档里的中文自述走英文导出必须失败**并点名**（6.3）。

    旧档存的是散文（新档存键，design D8），故英文正文必然夹中文。原先只列码位转义——一屏
    `U+4E00` 起，读者无从知道是哪一条、也就无从修；这条钉住"要给出出现位置与原文片段"。
    """
    prose = "定时作业模型，含作业登记与触发判定成本"
    model = build_model(_archive_with_notes(prose), environment=_ENVIRONMENT, lang=LANG_EN)

    with pytest.raises(UnsafeReportText) as caught:
        pdf.render_pdf(model, [], tmp_path / "report-en.pdf")

    message = str(caught.value)
    assert t("blocks.caveats.title", LANG_EN) in message, "没说出是哪个章节"
    assert "条目" in message, "没说到第几条"
    assert prose in message, "没贴出原文片段，读者仍不知道该改哪一条"
