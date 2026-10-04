"""渲染层与命令行门禁的验证（spec: comparison-report）。

两处刻意安排：

1. **中文字形用"没有缺字警告"来验**，而不是断言文件存在——缺字只会让图上出现方框，文件照样
   生成。这个检查在本仓库真的抓到过问题（SimHei 缺 MICRO SIGN）。
2. **"同源"用扰动模型来验**：改一个模型里的数字，渲染结果必须跟着变。若某个后端自己解析原始
   数据，这个用例就会发现它不跟着变。
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest

from zoo_bench import cli, storage
from zoo_bench.render import charts
from zoo_bench.render import markdown as markdown_renderer
from zoo_bench.render.fonts import CjkFontUnavailable, find_cjk_font, font_covers
from zoo_bench.runner import ABSOLUTE_NOTE

FRAMEWORK = "zoo-framework==9.9.9"


def _summary(value: float) -> dict[str, float | int]:
    return {
        "n": 3,
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


def _overhead_row(adapter: str, tier: float, ratio: float, *, concurrency: int = 4) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "concurrency": concurrency,
        "body_tier_us": tier,
        "framework_overhead_seconds": 0.0001,
        "framework_overhead_ratio": ratio,
        "body_seconds": tier / 1e6,
    }


def _throughput_row(adapter: str, concurrency: int, throughput: float) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "concurrency": concurrency,
        "body_tier_us": 300.0,
        "throughput_per_second": throughput,
    }


def _model(*, overhead_ratio: float = 0.26) -> dict[str, Any]:
    """一个够渲染的最小模型。只带渲染层要读的字段。"""
    return {
        "schema": "test",
        "source": {"path": "results/x.json"},
        "environment": {
            "hardware": {"cpu_model": "测试 CPU", "logical_cores": 8, "platform": "Test-AMD64"},
            "os": {"system": "TestOS", "release": "1", "version": "1.0"},
            "python": {"version": "3.13.0", "implementation": "CPython", "executable": "/py"},
            "subject": {"dist_version": "9.9.9", "module_version": "1.0", "note": "以发行元数据为准"},
            "harness": {"version": "0.0.1", "commit": "deadbeef"},
            "command": ["zoo-bench", "run"],
        },
        "run": {"absolute_note": ABSOLUTE_NOTE},
        "load": {"is_stand_in": True, "composition": "JSON 编解码", "caveat": "负载是替身"},
        "subject": "zoo",
        "conclusion": {
            "overhead_threshold": 0.15,
            "overhead_crossings": [],
            "relative_turnings": [],
            "summary": ["执行体时长达到约 2700 微秒 时开销占比降到 15% 以下。"],
            "note": "单点加速比没有选型含义",
        },
        "dimensions": {
            "latency": {
                "title": "延迟分位数与抖动",
                "unit": "秒/任务",
                "note": "端到端 / 并发度",
                "rows": [
                    {
                        "adapter": "zoo",
                        "concurrency": 4,
                        "body_tier_us": 300.0,
                        "end_to_end_per_task": _summary(0.00035),
                    }
                ],
            },
            "overhead": {
                "title": "框架自身开销占比",
                "unit": "秒",
                "note": "同一次运行内埋点",
                "rows": [
                    _overhead_row("zoo", 300.0, overhead_ratio),
                    _overhead_row("zoo", 2700.0, overhead_ratio / 4),
                    _overhead_row("bare_thread", 300.0, 0.10),
                    _overhead_row("bare_thread", 2700.0, 0.02),
                ],
            },
            "throughput": {
                "title": "吞吐与并发伸缩",
                "unit": "任务/秒",
                "note": "并发度 / 端到端中位数",
                "rows": [
                    _throughput_row("zoo", 1, 2000.0),
                    _throughput_row("zoo", 4, 7000.0),
                    _throughput_row("bare_thread", 1, 2500.0),
                    _throughput_row("bare_thread", 4, 9000.0),
                ],
            },
            "semantics": {
                "title": "调度语义的代价",
                "status": "not_measured",
                "scope_note": "内部开关对照，不与对照方案横向比较",
                "items": [],
                "reason": "0.6.0 上没有一项可测",
            },
        },
        "unfavorable": {
            "items": [
                {
                    "baseline": "bare_thread",
                    "concurrency": 4,
                    "body_tier_us": 300.0,
                    "subject_median_seconds": 0.00035,
                    "baseline_ratio_vs_subject": 0.8,
                    "gap": "bare_thread 比 zoo 快 1.25x",
                }
            ],
            "found": True,
            "note": "不利数据缺失的报告不合格",
        },
        "caveats": [{"kind": "被测层级", "text": "调度派发层"}],
        "absolute": {"note": ABSOLUTE_NOTE, "process_isolation": {"all_distinct": True}},
        "self_check": {"ok": True},
    }


# ------------------------------------------------------------------ Markdown 报告


REQUIRED_SECTIONS = (
    "## 运行环境",
    "## 负载",
    "## 结论摘要",
    "## 维度：延迟分位数与抖动",
    "## 维度：框架自身开销占比",
    "## 维度：吞吐与并发伸缩",
    "## 维度：调度语义的代价",
    "## 公开的不利数据",
    "## 口径局限与偏差来源",
)


def test_report_contains_every_required_section() -> None:
    text = markdown_renderer.render_markdown(_model(), [])
    for section in REQUIRED_SECTIONS:
        assert section in text, f"报告缺少章节：{section}"


def test_report_states_the_load_is_a_stand_in() -> None:
    assert "负载是替身" in markdown_renderer.render_markdown(_model(), [])


def test_report_declares_the_semantics_scope_even_when_unmeasured() -> None:
    text = markdown_renderer.render_markdown(_model(), [])
    assert "内部开关对照，不与对照方案横向比较" in text
    assert "0.6.0 上没有一项可测" in text


def test_report_labels_absolute_durations_as_not_comparable() -> None:
    text = markdown_renderer.render_markdown(_model(), [])
    assert ABSOLUTE_NOTE in text


def test_report_renders_environment_unavailable_reason() -> None:
    """CPU 型号取不到时要给出**原因**，而不是留一个空单元格。"""
    model = _model()
    model["environment"]["hardware"]["cpu_model"] = None
    model["environment"]["hardware"]["cpu_model_source"] = "读取 /proc/cpuinfo 失败"
    assert "读取 /proc/cpuinfo 失败" in markdown_renderer.render_markdown(model, [])


def test_report_refuses_to_fake_a_missing_environment() -> None:
    model = _model()
    model["environment"] = None
    text = markdown_renderer.render_markdown(model, [])
    assert "缺少环境自述" in text


def test_report_numbers_come_from_the_model() -> None:
    """同源判据：扰动模型里的数字，渲染结果必须跟着变。

    若渲染层自己解析原始数据（或缓存了一份），这个断言就会发现它不跟着变。
    """
    before = markdown_renderer.render_markdown(_model(overhead_ratio=0.26), [])
    after = markdown_renderer.render_markdown(_model(overhead_ratio=0.99), [])

    assert before != after
    assert "26.00%" in before
    assert "99.00%" in after


def test_report_marks_when_unfavorable_data_is_absent() -> None:
    model = _model()
    model["unfavorable"] = {"items": [], "found": False, "note": "缺失即不合格"}
    assert "未出现被测框架处于劣势" in markdown_renderer.render_markdown(model, [])


# ------------------------------------------------------------------ 图表与字体


def test_charts_render_without_missing_glyphs(tmp_path: Path) -> None:
    """中文字形必须全部被字体认得。

    **缺字不会让文件生成失败**——只会让图上出现方框，所以要断言的是"没有缺字警告"。
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rendered = charts.render_all(_model(), tmp_path / "figures", threshold=0.15)

    missing = [
        str(item.message) for item in caught if "missing from font" in str(item.message)
    ]
    assert not missing, f"图表里出现缺字（会显示为方框）：{missing[:3]}"
    assert {chart["figure"] for chart in rendered} == {"overhead_ratio", "throughput"}


def test_charts_export_both_svg_and_png(tmp_path: Path) -> None:
    """SVG 给站点、PNG 给 PDF——两种格式都从同一份 figure 出。"""
    rendered = charts.render_all(_model(), tmp_path / "figures", threshold=0.15)
    for chart in rendered:
        assert set(chart["paths"]) == {"svg", "png"}
        for path in chart["paths"].values():
            assert Path(path).is_file() and Path(path).stat().st_size > 0
        assert chart["scope"], "每张图都要说明它的适用范围"


def test_chart_records_the_font_it_used(tmp_path: Path) -> None:
    """字体必须可复核——"中文为何能显示"不该是个谜。"""
    rendered = charts.render_all(_model(), tmp_path / "figures", threshold=0.15)
    assert Path(rendered[0]["font"]).is_file()


def test_font_resolution_fails_with_an_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(CjkFontUnavailable) as excinfo:
        find_cjk_font(candidates=[str(tmp_path / "nope.ttf")])

    message = str(excinfo.value)
    assert "fonts-noto-cjk" in message, "必须给出可执行的安装指引"
    assert "ZOO_BENCH_CJK_FONT" in message, "必须告诉用户可以用环境变量指定"
    assert "nope.ttf" in message, "必须列出尝试过哪些路径"


def test_font_coverage_is_detected_both_ways() -> None:
    """覆盖检查要能双向判：认得中文，认不出私用区码位。"""
    font = find_cjk_font()
    assert font_covers(font, "中文报告")[0] is True

    covered, missing = font_covers(font, "")
    assert covered is False
    assert missing == [""]


# ------------------------------------------------------------------ 命令行


def test_recorded_command_does_not_duplicate_the_subcommand() -> None:
    """早先把命令记成 `zoo-bench run run …`——一条照抄跑不通的复现命令比不给更坏。"""
    command = cli.recorded_command(["run", "--rounds", "5"])
    assert command == ["zoo-bench", "run", "--rounds", "5"]


def _subject_stub_unit() -> dict[str, Any]:
    """一个最小的被测单元——桩数据也必须带被测对象，否则触发的是"没有被测框架"那道门禁。"""
    return {
        "spec": {"adapter": "zoo", "concurrency": 1, "body_tier_us": 300.0},
        "status": "ok",
        "adapter": {
            "name": "zoo",
            "tier": "under_test",
            "comparable": True,
            "notes": "",
            "drive_level": "调度派发层",
        },
        "absolute": {
            "end_to_end_per_task_seconds": {"median": 0.0003, "p95": 0.0003, "p99": 0.0003,
                                            "relative_spread": 0.0},
            "body_seconds": {"median": 0.0002},
            "framework_overhead_seconds": 0.0001,
            "framework_overhead_ratio": 0.33,
            "throughput_per_second": 3000.0,
        },
    }


def _save_stub_run(root: Path, *, environment: dict[str, Any] | None) -> None:
    """存一份**桩**留档：只有被测单元、没有任何对照单元，故也不含"不利数据"。

    用来验门禁本身——门禁的判别力靠直接构造条件，而不是指望真实测量恰好落在某种情形上。
    真实运行里"被测框架从未处于劣势"是不可控的，桩数据让它可控。
    """
    result = {
        "run": {"absolute_note": ABSOLUTE_NOTE},
        "units": [_subject_stub_unit()],
        "relative": {"comparisons": []},
        "environment": environment,
        "semantics": None,
        "self_check": {"ok": True},
    }
    storage.save_run(result, framework=FRAMEWORK, root=root)


def test_render_refuses_when_environment_is_missing(tmp_path: Path) -> None:
    """缺环境自述时**先**被拒——环境门禁排在不利数据门禁之前。"""
    root = tmp_path / "results"
    _save_stub_run(root, environment=None)

    code = cli.main(
        ["render", "--framework", "9.9.9", "--results", str(root), "--out", str(tmp_path / "site")]
    )
    assert code == 3, "缺环境自述的报告不得发布"
    assert not (tmp_path / "site" / "report.md").exists()


def test_render_refuses_when_unfavorable_data_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "results"
    _save_stub_run(
        root, environment={"hardware": {}, "os": {}, "python": {}, "harness": {}, "subject": {}}
    )

    code = cli.main(
        ["render", "--framework", "9.9.9", "--results", str(root), "--out", str(tmp_path / "site")]
    )
    assert code == 4
    assert not (tmp_path / "site" / "report.md").exists()


def test_unfavorable_exemption_is_recorded_in_the_report(tmp_path: Path) -> None:
    """豁免必须留下审计线索，而不是靠人记得自己跳过了一步。"""
    root = tmp_path / "results"
    _save_stub_run(
        root, environment={"hardware": {}, "os": {}, "python": {}, "harness": {}, "subject": {}}
    )

    code = cli.main(
        [
            "render",
            "--framework",
            "9.9.9",
            "--results",
            str(root),
            "--out",
            str(tmp_path / "site"),
            "--allow-empty-unfavorable",
        ]
    )
    assert code == 0, "显式豁免后应当出报告"
    report = (tmp_path / "site" / "report.md").read_text(encoding="utf-8")
    assert "发布豁免" in report
    assert "审计线索" in report


def test_render_refuses_when_the_subject_is_missing(tmp_path: Path) -> None:
    """没有被测对象的报告必须被拒——这是**配置错误**，不是测量结论。

    真事故的守卫：matrix.yaml 的 adapters 清单漏了 zoo，CI 于是测了 80 个单元、**一个被测对象
    都没有**。当时被"无不利数据"那道门禁拦住了，但理由误导了排查方向——所以这道门禁必须独立
    存在且说清楚。
    """
    results = tmp_path / "results"
    storage.save_run(
        {
            "run": {"absolute_note": ABSOLUTE_NOTE},
            "units": [
                {
                    "spec": {"adapter": "bare_thread", "concurrency": 1, "body_tier_us": 300.0},
                    "status": "ok",
                    "adapter": {"name": "bare_thread", "tier": "bare", "comparable": True,
                                "notes": "", "drive_level": ""},
                    "absolute": {
                        "end_to_end_per_task_seconds": {"median": 0.0002, "p95": 0.0002,
                                                        "p99": 0.0002, "relative_spread": 0.0},
                        "body_seconds": {"median": 0.0002},
                        "framework_overhead_seconds": 0.0,
                        "framework_overhead_ratio": 0.0,
                        "throughput_per_second": 5000.0,
                    },
                }
            ],
            "relative": {"comparisons": []},
            "environment": {"hardware": {}, "os": {}, "python": {}, "harness": {}, "subject": {}},
            "self_check": {"ok": True},
        },
        framework=FRAMEWORK,
        root=results,
    )

    code = cli.main(
        ["render", "--framework", "9.9.9", "--results", str(results), "--out", str(tmp_path / "site")]
    )
    assert code == 6
    assert not (tmp_path / "site" / "index.html").exists()


def test_render_reports_which_versions_are_archived(tmp_path: Path) -> None:
    """没给 --data 也没给 --framework 时，要告诉用户已留档了什么，而不是只说"需要参数"。"""
    code = cli.main(["render", "--results", str(tmp_path / "empty"), "--out", str(tmp_path / "site")])
    assert code == 2


def test_overhead_table_explains_a_withheld_row() -> None:
    """表里出现"—"时必须**当场**说明它是什么意思。

    读者要跨到口径章节才知道，等于让他猜；而"—"在这里可能有两个完全不同的原因（受排队污染、
    或开销数字本身不可读），光看一个横杠分不出来。
    """
    model = _model()
    model["dimensions"]["overhead"]["rows"][0].update(
        {
            "framework_overhead_seconds": None,
            "framework_overhead_ratio": None,
            "interpretable": False,
            "note": "该并发度超过这次运行所在机器的并行能力",
        }
    )
    model["conclusion"]["overhead_crossings"] = [
        {
            "adapter": "zoo",
            "concurrency": 64,
            "threshold": 0.15,
            "tiers_us": [300.0],
            "overhead_ratios": None,
            "first_tier_at_or_below_threshold_us": None,
            "queueing_contaminated": False,
            "uninterpretable": True,
            "note": "该并发度超过这次运行所在机器的并行能力：相减的结果没有意义。",
        }
    ]

    text = markdown_renderer.render_markdown(model, [])

    assert "带 — 的格" in text, "开销表要说明横杠的含义（透视表里横杠落在格上）"
    assert "并行能力" in text
    assert "相减的结果没有意义" in text, "交叉点表要把那一行的原因带出来，而不是只给一个横杠"


def test_overhead_table_gets_no_explanation_when_every_row_is_readable() -> None:
    """全部可读时不加那句说明——多余的话会让读者去找一个并不存在的例外。"""
    text = markdown_renderer.render_markdown(_model(), [])

    assert "带 — 的格" not in text


def test_attribution_section_renders_segments_and_the_conclusion() -> None:
    """归因维度要能读出来：四段、逐段对照、以及"落在哪一段"的结论。

    与其余维度同样的要求——**三种格式同源**，故这里只验块层；若渲染层自己写了一句结论，
    它就会与另外两个后端说的不一样。
    """
    model = _model()
    model["dimensions"]["attribution"] = {
        "title": "开销归因",
        "status": "ok",
        "scope_note": "口径说明",
        "segment_labels": {
            "submit_side_seconds": "提交侧",
            "handoff_seconds": "手交",
            "body_seconds": "执行体",
            "return_seconds": "回程",
        },
        "drill_labels": {"policy_lookup_seconds": "每轮策略查询（周期/相位/超时各查一次配置）"},
        "cores": 4,
        "tiers_us": [300.0, 2700.0],
        "concurrencies": [1],
        "note": "本维度是诊断",
        "groups": [
            {
                "adapter": "zoo",
                "tier_us": 300.0,
                "concurrency": 1,
                "status": "ok",
                "reason": "",
                "segments": {
                    "submit_side_seconds": 70e-6,
                    "handoff_seconds": 20e-6,
                    "body_seconds": 300e-6,
                    "return_seconds": 30e-6,
                },
            },
            {
                "adapter": "process_pool",
                "tier_us": 300.0,
                "concurrency": 1,
                "status": "not_measurable",
                "reason": "执行体的起止时刻读不到",
            },
        ],
        "findings": [
            {
                "tier_us": 300.0,
                "concurrency": 1,
                "subject": "zoo",
                "subject_segments": {
                    "submit_side_seconds": 70e-6,
                    "handoff_seconds": 20e-6,
                    "body_seconds": 300e-6,
                    "return_seconds": 30e-6,
                },
                "subject_drill_down": {"policy_lookup_seconds": 5e-6},
                "per_adapter": [
                    {
                        "adapter": "thread_pool",
                        "excess_seconds": 60e-6,
                        "segment_excess_seconds": {"submit_side_seconds": 60e-6},
                        "dominant_segment": "submit_side_seconds",
                    }
                ],
            }
        ],
        "summary": ["并发度 1、执行体 300 微秒 下，被测框架相对 thread_pool 每任务多花 60 微秒，其中主要落在「提交侧」（+60 微秒）。"],
        "reason": "",
    }

    text = markdown_renderer.render_markdown(model, [])

    assert "维度：开销归因" in text
    assert "提交侧" in text and "回程" in text, "四段都要在表里"
    assert "超出各对照方案的部分落在哪一段" in text
    assert "thread_pool" in text
    assert "主要落在" in text or "落在「提交侧」" in text
    assert "not_measurable" in text or "不可测" in text, "量不成的组要标出来"
    assert "每轮策略查询" in text, "被测框架的细分要单独呈现"
    # 导出前门禁：这张表里的每个字符中文字体都得有
    from zoo_bench.render.blocks import assert_report_text_is_renderable

    assert_report_text_is_renderable(text)


def test_a_model_without_the_attribution_key_still_renders() -> None:
    """模型里没有这一维时渲染不能炸（占位由报告模型负责，见 test_report_model 的对应用例）。

    这条防的是另一件事：旧留档重新渲染时模型里可能没有这个键，而渲染层若直接下标取值就会崩。
    """
    text = markdown_renderer.render_markdown(_model(), [])

    assert "维度：开销归因" in text
    assert "抽样档位" not in text, "没有数据时不摆一张空表"


def test_overhead_chart_survives_a_withheld_concurrency(tmp_path: Path) -> None:
    """最大的并发度整组被撤下时，图必须照样出得来。

    **真事故的回归守卫**：撤下"超出机器并行能力"的开销数字之后，4 核 runner 上最大的并发度
    （64）整组变成 ``None``，而图表仍取它作 y 序列 → 整条退化成 NaN → 对数轴报
    ``Data cannot be log-scaled because all values are <= 0``，CI 上三个目标全部渲染失败。
    本地用例当时没抓到它：模型里没有"整组被撤下"这种形态。这条补上那个形态。
    """
    model = _model()
    model["dimensions"]["overhead"]["rows"] = [
        _overhead_row("zoo", 300.0, 0.42, concurrency=1),
        {
            "adapter": "zoo",
            "concurrency": 64,
            "body_tier_us": 300.0,
            "framework_overhead_ratio": None,
            "framework_overhead_seconds": None,
            "body_seconds": 3e-4,
            "interpretable": False,
            "note": "超出机器并行能力",
        },
    ]

    rendered = charts.overhead_ratio_chart(model, tmp_path, threshold=0.15)

    assert "并发度 1" in rendered["scope"], "该图应退回到最大的**可读**并发度"
    assert "64" in rendered["scope"], "被撤下的并发度要点出来，否则读者以为图覆盖了全部"


def test_overhead_chart_is_omitted_when_no_row_is_readable(tmp_path: Path) -> None:
    """一行可读的都没有时不出这张图——不出空图，也不崩。"""
    model = _model()
    model["dimensions"]["overhead"]["rows"] = [
        {
            "adapter": "zoo",
            "concurrency": 64,
            "body_tier_us": 300.0,
            "framework_overhead_ratio": None,
            "framework_overhead_seconds": None,
            "body_seconds": 3e-4,
            "interpretable": False,
            "note": "超出机器并行能力",
        },
    ]

    rendered = charts.render_all(model, tmp_path / "figures", threshold=0.15)

    assert all(chart["figure"] != "overhead_ratio" for chart in rendered)


def test_advantage_section_mirrors_the_unfavorable_one() -> None:
    """优势节与不利节**逐列对称、紧邻**，且同处报告主体。

    对称不是为了好看：两节出自同一份同运行内比值，结构一致才能让读者按"赢在哪 / 输在哪"成对地
    读，也才让"某一节为空"在版面上同样看得见。
    """
    model = _model()
    model["favorable"] = {
        "items": [
            {
                "baseline": "thread_pool",
                "concurrency": 4,
                "body_tier_us": 2700.0,
                "subject_median_seconds": 0.0027,
                "baseline_ratio_vs_subject": 1.4,
                "margin": "被测框架比 thread_pool 快 1.40x",
            }
        ],
        "note": "与不利数据同源",
        "found": True,
    }
    model["unfavorable"] = {
        "items": [
            {
                "baseline": "bare_thread",
                "concurrency": 4,
                "body_tier_us": 40.0,
                "subject_median_seconds": 0.0001,
                "baseline_ratio_vs_subject": 0.6,
                "gap": "bare_thread 比 zoo 快 1.67x",
            }
        ],
        "note": "不利数据",
        "found": True,
    }

    text = markdown_renderer.render_markdown(model, [])

    assert "被测框架在哪些档位更快" in text
    assert text.index("被测框架在哪些档位更快") < text.index("公开的不利数据"), "优势在前、紧邻不利"
    assert "被测框架比 thread_pool 快 1.40x" in text
    # 两节的表头逐列相同（只有方向不同）——故同一个表头会出现两次
    assert text.count("执行体档位（微秒）") >= 2
    assert text.count("被测框架中位数") >= 2


def test_advantage_section_says_so_when_there_is_nothing_to_show() -> None:
    """"本节为空"也要看得见——不能因为节里没内容就把整个章节省掉。"""
    model = _model()
    model["favorable"] = {"items": [], "note": "同源", "found": False}

    text = markdown_renderer.render_markdown(model, [])

    assert "被测框架在哪些档位更快" in text
    assert "所测档位内未出现被测框架处于优势的情形" in text


def test_render_all_includes_the_relative_multiple_chart(tmp_path: Path) -> None:
    """相对倍数图要进图表清单——加了函数但没注册，报告里就看不到它。"""
    model = _model()
    model["conclusion"]["relative_turnings"] = [
        {
            "baseline": "thread_pool",
            "concurrency": 4,
            "subject": "zoo",
            "tiers_us": [300.0, 2700.0],
            "ratios_vs_subject": [0.8, 1.3],
            "first_tier_baseline_not_faster_us": 2700.0,
            "note": "该档位是所测档位中最小的满足者",
        }
    ]

    figures = charts.render_all(model, tmp_path / "figures", threshold=0.15)

    assert any(chart["figure"] == "relative_multiple" for chart in figures)


def test_body_shows_pivots_and_the_appendix_keeps_every_field() -> None:
    """正文是透视表，明细在附录——**而字段一个不少**。

    这条防的是"为了短而悄悄少一列"：那类改动没有任何告警，故用字段集合的包含关系把它钉住。
    """
    model = _model()
    text = markdown_renderer.render_markdown(model, [])

    # 按**标题**切：正文里那句指引也提到附录的名字，按名字切会切在指引上
    body = text[: text.index("## 附录：全部数值")]
    appendix = text[text.index("## 附录：全部数值") :]

    assert "| 并发度 | 执行体档位（微秒） |" in body, "正文应是透视表（行 = 并发度与档位、列 = 方案）"
    assert _APPENDIX_POINTER_TEXT in body, "正文要指向附录，读者不必找"
    for field_header in ("中位数", "p95", "p99", "相对离散度", "执行体实测", "框架开销", "开销占比"):
        assert field_header in appendix, f"附录里少了字段：{field_header}"


_APPENDIX_POINTER_TEXT = "见文末「附录：全部数值」"


def test_tie_section_states_the_band_and_its_definition() -> None:
    """平手节必须给出带宽与判据——不给判据的"分不出胜负"读者无法复核。"""
    model = _model()
    model["tied"] = {
        "items": [
            {
                "baseline": "thread_pool",
                "concurrency": 4,
                "body_tier_us": 2700.0,
                "subject_median_seconds": 0.0027,
                "baseline_ratio_vs_subject": 1.01,
                "band": 0.04,
                "reason": "两侧差异 1.0% 小于带宽 4.0%，分不出胜负",
            }
        ],
        "note": "平手节的说明",
        "found": True,
        "band_min": 0.012,
        "band_max": 0.045,
    }
    model["tie_band"] = {"definition": "带宽 = 两侧各自的跨轮相对离散度之和"}

    text = markdown_renderer.render_markdown(model, [])

    assert "分不出胜负的档位" in text
    assert "1.2% 到 4.5%" in text, "本期带宽要给出具体数值"
    assert "跨轮相对离散度" in text, "判据要随报告给出（不能只说分不出胜负）"
    assert "小于带宽" in text


def test_tie_section_says_so_when_every_tier_is_decidable() -> None:
    """每一档都分得出胜负时说清这一点——空节与"没测"必须分得开。"""
    model = _model()
    model["tied"] = {"items": [], "note": "说明", "found": False, "band_min": None, "band_max": None}

    text = markdown_renderer.render_markdown(model, [])

    assert "分不出胜负的档位" in text
    assert "每一档都分得出胜负" in text


def _spread_rounds(*, e2e: float, jitter: float, concurrency: int = 1) -> list[dict[str, Any]]:
    """逐轮样本：分类的带宽由它算出来，故桩数据要像真实留档那样带上。"""
    return [
        {
            "batch": concurrency,
            "wall_seconds": concurrency * e2e * (1.0 + jitter * ((index % 3) - 1)),
            "round": index,
        }
        for index in range(8)
    ]


def _baseline_stub_unit(adapter: str) -> dict[str, Any]:
    unit = _subject_stub_unit()
    unit["spec"] = {"adapter": adapter, "concurrency": 1, "body_tier_us": 300.0}
    unit["adapter"] = {
        "name": adapter,
        "tier": "bare",
        "comparable": True,
        "notes": "",
        "drive_level": "",
    }
    return unit


def _save_comparison_run(root: Path, *, ratios: dict[str, float]) -> None:
    """存一份**两侧都有逐轮样本**的桩留档：带宽算得出，三分类判得了。"""
    units = [_subject_stub_unit(), *(_baseline_stub_unit(name) for name in ratios)]
    for unit in units:
        unit["rounds"] = _spread_rounds(e2e=0.0003, jitter=0.02)

    storage.save_run(
        {
            "run": {"absolute_note": ABSOLUTE_NOTE},
            "units": units,
            "relative": {
                "comparisons": [
                    {
                        "concurrency": 1,
                        "body_tier_us": 300.0,
                        "subject_median_seconds": 0.0003,
                        "ratios_vs_subject": ratios,
                    }
                ]
            },
            "environment": {
                "hardware": {"logical_cores": 8},
                "os": {},
                "python": {},
                "harness": {},
                "subject": {},
            },
            "semantics": None,
            "self_check": {"ok": True},
        },
        framework=FRAMEWORK,
        root=root,
    )


def test_render_publishes_when_the_only_losing_evidence_is_a_tie(tmp_path: Path) -> None:
    """不利集为空、但有平手时**不得**被拒：那是判据的结果，不是"只展示自己赢"。"""
    root = tmp_path / "results"
    _save_comparison_run(root, ratios={"thread_pool": 1.00})

    code = cli.main(
        ["render", "--framework", "9.9.9", "--results", str(root), "--out", str(tmp_path / "site")]
    )

    assert code == 0, "处处打平时不利集为空，门禁若据此拒发就是误伤"
    report = (tmp_path / "site" / "report.md").read_text(encoding="utf-8")
    assert "分不出胜负的档位" in report
    assert "小于带宽" in report, "平手要给出来由，读者才知道这不是漏测"


def test_render_still_refuses_when_every_tier_favours_the_subject(tmp_path: Path) -> None:
    """每一档都占优时仍拒发——门禁的鉴别力不能因为引入平手类而失效。"""
    root = tmp_path / "results"
    _save_comparison_run(root, ratios={"thread_pool": 1.50})

    code = cli.main(
        ["render", "--framework", "9.9.9", "--results", str(root), "--out", str(tmp_path / "site")]
    )

    assert code == 4
    assert not (tmp_path / "site" / "report.md").exists()
