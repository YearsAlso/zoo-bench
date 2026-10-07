"""跨版本对比的验证（spec: comparison-report 的"版本间的性能变化 MUST 可查询"）。

本文件的重点不是"能不能出结果"，而是三处**必须显式报告而不是静默处理**的地方：只在一侧出现的
单元、两次运行环境是否一致、两侧的驱动面机制是否相同（机制差异会混进差异里被读成性能改进）。
以及一条口径约束：对比的主证据必须是**同运行内的相对量**，不是绝对耗时——后者跨运行不可比
（design D6）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from zoo_bench import cli, compare, storage
from zoo_bench.i18n import LANG_ZH
from zoo_bench.render import compare as compare_renderer

BEFORE = "zoo-framework==1.0.0"
AFTER = "zoo-framework==2.0.0"


def _unit(
    adapter: str, concurrency: int, tier_us: float, overhead: float, median: float
) -> dict[str, Any]:
    """一个测量单元。

    ``framework_overhead_seconds`` 按留档里的真实关系给出（开销 = 占比 × 每任务端到端），
    而不是随便填一个数：口径判据要看它，填错了会让"可不可读"的判定与真实情况脱节。
    比例给负值时开销也为负——那正是实测里出现过、必须被撤下的情形。
    """
    return {
        "spec": {"adapter": adapter, "concurrency": concurrency, "body_tier_us": tier_us},
        "status": "ok",
        "absolute": {
            "framework_overhead_ratio": overhead,
            "framework_overhead_seconds": median * overhead,
            "end_to_end_per_task_seconds": {"median": median},
        },
    }


def _comparison(concurrency: int, tier_us: float, ratios: dict[str, float]) -> dict[str, Any]:
    return {"concurrency": concurrency, "body_tier_us": tier_us, "ratios_vs_subject": ratios}


def _run(
    units: list[dict[str, Any]],
    *,
    comparisons: list[dict[str, Any]] | None = None,
    cpu: str = "CPU A",
    self_check: bool = True,
    version: str = "1.0",
    drive_generation: str | None = "previous",
    cores: int = 4,
) -> dict[str, Any]:
    subject: dict[str, Any] = {"dist_version": version}
    if drive_generation is not None:
        subject["drive_generation"] = {"generation": drive_generation, "label": "（合成）"}
    return {
        "units": units,
        "relative": {"comparisons": comparisons or [], "note": ""},
        "environment": {
            "hardware": {"cpu_model": cpu, "logical_cores": cores, "platform": "Linux-x86_64"},
            "python": {"version": "3.13.0"},
            "subject": subject,
        },
        "self_check": {"ok": self_check},
    }


def _pair() -> tuple[dict[str, Any], dict[str, Any]]:
    before = _run(
        [
            _unit("zoo", 4, 300.0, 0.30, 0.0004),
            _unit("thread_pool", 4, 300.0, 0.10, 0.0003),
        ],
        comparisons=[_comparison(4, 300.0, {"thread_pool": 0.75})],
        version="1.0",
    )
    after = _run(
        [
            _unit("zoo", 4, 300.0, 0.18, 0.0003),
            _unit("thread_pool", 4, 300.0, 0.10, 0.0003),
        ],
        comparisons=[_comparison(4, 300.0, {"thread_pool": 1.20})],
        version="2.0",
    )
    return before, after


# ------------------------------------------------------------------ 各维度的方向与幅度


def test_comparison_reports_direction_and_magnitude() -> None:
    before, after = _pair()
    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    row = next(r for r in result["overhead_ratio"] if r["adapter"] == "zoo")
    assert row["direction"] == "下降", "开销占比从 30% 降到 18%，方向该是下降"
    assert row["ratio"] == pytest.approx(0.6)

    speedup = next(r for r in result["speedup_vs_baseline"] if r["baseline"] == "thread_pool")
    assert speedup["direction"] == "上升", "相对倍数从 0.75 升到 1.20"
    assert speedup["delta"] == pytest.approx(0.6)


def test_comparison_marks_flat_changes_as_flat() -> None:
    before, after = _pair()
    after["units"][1]["absolute"]["framework_overhead_ratio"] = 0.10
    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    row = next(r for r in result["overhead_ratio"] if r["adapter"] == "thread_pool")
    assert row["direction"] == "持平"


def test_metrics_declare_what_rising_means() -> None:
    """方向本身不带好坏含义——"上升"是变好还是变坏取决于度量。故每项度量都要自述。"""
    before, after = _pair()
    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert "下降即开销摊薄得更好" in result["metric_semantics"]["overhead_ratio"]
    assert "上升即被测框架相对更快" in result["metric_semantics"]["speedup_vs_baseline"]


# ------------------------------------------------------------------ 可比性


def test_absolute_durations_are_present_but_marked_not_comparable() -> None:
    """D6：绝对耗时跨运行不可比。它可以在场，但**不能作为版本差异的证据**。"""
    before, after = _pair()
    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["absolute_seconds"], "绝对耗时应在场，供读者看量级"
    assert "不可跨运行比较" in result["absolute_note"]
    assert "不要用它读版本差异" in result["absolute_note"]


def test_units_present_in_only_one_side_are_reported_not_dropped() -> None:
    """档位或适配器变过时两侧就不是同一批单元，差异无法归因——列出，而不是取交集悄悄丢掉。"""
    before, after = _pair()
    after["units"].append(_unit("zoo", 4, 2700.0, 0.05, 0.003))  # 2.0 新增的档位

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["shared_unit_count"] == 2
    assert [item["present_in"] for item in result["only_in_one"]] == ["after"]
    assert result["only_in_one"][0]["body_tier_us"] == 2700.0


def test_environment_difference_is_flagged() -> None:
    """两次运行环境不同时，版本差异与机器差异混在一起——必须标出来。"""
    before, after = _pair()
    after["environment"]["hardware"]["cpu_model"] = "CPU B"

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["environment"]["same"] is False
    # 键用点分路径，读者一眼看得出差异落在环境的哪一层
    assert "hardware.cpu_model" in result["environment"]["differences"]
    assert "不能只归因于版本" in result["environment"]["note"]


def test_same_environment_is_reported_as_same() -> None:
    before, after = _pair()
    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["environment"]["same"] is True
    assert result["environment"]["differences"] == {}


def test_differing_drive_surfaces_are_flagged_with_their_effect_on_the_numbers() -> None:
    """两侧驱动面机制不同时必须说出来，并讲清它对读数的影响。

    完成信号的取得方式随版本变过，而它**计入被测框架的端到端**：两侧差异里有一部分是机制自身
    的成本。不写出来，读者会把那部分整个读成性能改进——一个由插桩方式造成的"改进"。
    """
    before, after = _pair()
    after["environment"]["subject"]["drive_generation"] = {
        "generation": "current",
        "label": "（合成）",
    }

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["drive_generation"]["same"] is False
    assert result["drive_generation"]["before"]["generation"] == "previous"
    assert result["drive_generation"]["after"]["generation"] == "current"
    assert "驱动机制自身的成本" in result["drive_generation"]["note"]
    assert "不能整体读成一般意义的性能改进" in result["drive_generation"]["note"]


def test_same_drive_surface_is_reported_as_comparable() -> None:
    before, after = _pair()
    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["drive_generation"]["same"] is True
    assert "口径一致" in result["drive_generation"]["note"]


def test_missing_drive_surface_is_reported_as_unknown_not_assumed_same() -> None:
    """一侧没有该字段时**不假定一致**：那是"不知道"，不是"相同"。"""
    before, after = _pair()
    after["environment"]["subject"].pop("drive_generation", None)

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["drive_generation"]["same"] is False
    assert "无法判断两侧的读数口径是否一致" in result["drive_generation"]["note"]
    # 缺失要说出来，而且不能把它当成"两侧机制不同"那种有实质结论的情形
    assert "驱动机制自身的成本" not in result["drive_generation"]["note"]


def test_a_side_that_failed_self_check_makes_the_comparison_void() -> None:
    """自检未通过的一侧其数字不可信——对比要在文档里说清它不作数。"""
    before, after = _pair()
    after["self_check"]["ok"] = False

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )
    text = compare_renderer.render_markdown(result)

    assert result["after"]["self_check_ok"] is False
    assert "本对比不作数" in text
    assert "自检未通过" in text


# ------------------------------------------------------------------ 渲染


def test_comparison_renders_every_section() -> None:
    before, after = _pair()
    after["units"].append(_unit("zoo", 4, 2700.0, 0.05, 0.003))
    after["environment"]["subject"]["drive_generation"] = {
        "generation": "current",
        "label": "（合成）",
    }
    text = compare_renderer.render_markdown(
        compare.compare_versions(before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH)
    )

    for section in (
        "## 运行环境与读数口径",
        "## 只在一侧出现的单元",
        "## 框架开销占比的变化",
        "## 相对各对照方案的倍数的变化",
        "## 绝对耗时（不可跨运行比较）",
    ):
        assert section in text, f"对比缺少章节：{section}"

    # 读数口径那一节必须真的把驱动面机制的差异讲出来，光有标题不算
    assert "驱动机制自身的成本" in text


def test_comparison_markdown_and_html_read_the_same_result() -> None:
    """同源：扰动对比结果，两个后端都得跟着变。"""
    before, after = _pair()
    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    result["overhead_ratio"][0]["direction"] = "下降"

    assert "下降" in compare_renderer.render_markdown(result)
    assert "下降" in compare_renderer.render_html(result)


# ------------------------------------------------------------------ 命令行


def _archive(root: Path, specifier: str, run: dict[str, Any]) -> None:
    storage.save_run(run, framework=specifier, root=root)


def test_compare_refuses_and_names_the_missing_version(tmp_path: Path) -> None:
    """缺失的版本必须明确报出来并列出已留档的版本，不产出以缺失数据充数的对比。"""
    results = tmp_path / "results"
    before, _ = _pair()
    _archive(results, BEFORE, before)

    code = cli.main(["compare", "1.0.0", "2.0.0", "--results", str(results)])

    assert code == 7
    assert not (tmp_path / "out").exists()


def test_compare_writes_every_artifact(tmp_path: Path) -> None:
    """同时产出可读的对比页与**机器可读的对比结果**。

    后者不是多余的：站点首页据此列出对比页、并显示"两侧是否自检通过"，读者据此判断这份对比
    可不可信；它也保证对比里的每一项数字都能被复核，而不是只存在于 HTML 的排版里。
    """
    results = tmp_path / "results"
    before, after = _pair()
    _archive(results, BEFORE, before)
    _archive(results, AFTER, after)

    out = tmp_path / "out"
    code = cli.main(["compare", "1.0.0", "2.0.0", "--results", str(results), "--out", str(out)])

    assert code == 0
    assert (out / "compare.md").is_file()
    assert (out / "index.html").is_file()
    # 英文为主（design D3）：根目录的三件套是英文对比；中文对比页在 zh/ 子目录下（与版本页同一套
    # 镜像规则——语言切换链接的层数约定依赖这个结构）
    assert "version comparison" in (out / "compare.md").read_text(encoding="utf-8")
    zh = out / "zh"
    assert (zh / "compare.md").is_file()
    assert (zh / "index.html").is_file()
    assert "版本对比" in (zh / "compare.md").read_text(encoding="utf-8")

    payload = json.loads((out / "compare.json").read_text(encoding="utf-8"))
    assert payload["before"]["label"] == "1.0.0"
    assert payload["after"]["label"] == "2.0.0"
    assert payload["shared_unit_count"] == 2
    # 首页读的就是这两个字段，故它们必须真的在机器可读的那份里
    assert payload["before"]["self_check_ok"] is True
    assert payload["after"]["self_check_ok"] is True


# ------------------------------------------------------------------ 开销数字的适用口径


def test_overhead_change_is_withheld_above_the_machines_parallelism() -> None:
    """并发度超过机器并行能力时，两侧的比值与"变化"都不给。

    只给两侧比值、让读者自己相减，同样会把无效数字放出去——比为负的开销比直接给一个"不可读"
    更容易被当成真数字。故这一行连 before/after 都为空。
    """
    before = _run([_unit("zoo", 64, 300.0, 0.30, 0.0004)])
    after = _run([_unit("zoo", 64, 300.0, 0.31, 0.0005)], version="2.0")

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    row = result["overhead_ratio"][0]
    assert row["interpretable"] is False
    assert row["before"] is None and row["after"] is None
    assert row["ratio"] is None
    assert row["direction"] == "不可读"
    assert "并行能力" in row["note"], "要给出原因，读者才知道该跳过这一行"


def test_overhead_change_survives_within_the_machines_capacity() -> None:
    """容量之内照旧给出——判据是"超过"，不是"并发度不小"。"""
    before = _run([_unit("zoo", 4, 300.0, 0.30, 0.0004)])
    after = _run([_unit("zoo", 4, 300.0, 0.15, 0.0004)], version="2.0")

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    row = result["overhead_ratio"][0]
    assert row["interpretable"] is True
    assert row["ratio"] == pytest.approx(0.5)


def test_a_negative_overhead_is_withheld_even_at_a_high_core_count() -> None:
    """核数报得再大也拦得住：负开销不可能是合法结果，这条兜底不依赖核数估计。

    两组对照只差开销的符号，核数同为 256：正的可读、负的不可读。少了负的那一组，这条用例
    只证明"核数大就放行"，而证明不了兜底那条在干活。
    """
    positive = _run([_unit("zoo", 4, 300.0, 0.30, 0.0004)], cores=256)
    positive_after = _run([_unit("zoo", 4, 300.0, 0.20, 0.0004)], version="2.0", cores=256)
    readable = compare.compare_versions(
        positive, positive_after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    negative = _run([_unit("zoo", 4, 300.0, -9.1, 0.0105)], cores=256)
    negative_after = _run([_unit("zoo", 4, 300.0, -9.1, 0.0105)], version="2.0", cores=256)
    withheld = compare.compare_versions(
        negative, negative_after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert readable["overhead_ratio"][0]["interpretable"] is True
    assert withheld["overhead_ratio"][0]["interpretable"] is False


def test_either_side_being_unreadable_withholds_the_row() -> None:
    """只要有一侧不可读，这一行的变化就不可读——它比的是两个数之差。

    两侧的并发度必须相同（否则它们根本不构成同一行，那是"只在一侧出现的单元"那条管的事），
    故这里让差异只落在**证据**上：旧版是正的、新版是负的。
    """
    before = _run([_unit("zoo", 4, 300.0, 0.30, 0.0004)], cores=256)
    after = _run([_unit("zoo", 4, 300.0, -9.1, 0.0105)], version="2.0", cores=256)

    result = compare.compare_versions(
        before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH
    )

    assert result["shared_unit_count"] == 1
    assert result["overhead_ratio"][0]["interpretable"] is False


def test_compare_page_explains_what_an_unreadable_row_means() -> None:
    """页面里出现"不可读"时必须当场说明：让读者跨到别处才知道，等于让他猜。"""
    before = _run([_unit("zoo", 64, 300.0, 0.30, 0.0004)])
    after = _run([_unit("zoo", 64, 300.0, 0.31, 0.0005)], version="2.0")

    text = compare_renderer.render_markdown(
        compare.compare_versions(before, after, before_label="1.0", after_label="2.0", lang=LANG_ZH)
    )

    assert "不可读" in text
    assert "并行能力" in text
