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
            "summary": ["执行体时长达到约 2700 µs 时开销占比降到 15% 以下。"],
            "note": "单点加速比没有选型含义",
        },
        "dimensions": {
            "latency": {
                "title": "延迟分位数与抖动",
                "unit": "秒/任务",
                "note": "端到端 ÷ 并发度",
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
                "note": "并发度 ÷ 端到端中位数",
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


def _save_stub_run(root: Path, *, environment: dict[str, Any] | None) -> None:
    """存一份**桩**留档：没有测量单元，故也不含任何"不利数据"。

    用来验门禁本身——门禁的判别力靠直接构造条件，而不是指望真实测量恰好落在某种情形上。
    真实运行里"被测框架从未处于劣势"是不可控的，桩数据让它可控。
    """
    result = {
        "run": {"absolute_note": ABSOLUTE_NOTE},
        "units": [],
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


def test_render_reports_which_versions_are_archived(tmp_path: Path) -> None:
    """没给 --data 也没给 --framework 时，要告诉用户已留档了什么，而不是只说"需要参数"。"""
    code = cli.main(["render", "--results", str(tmp_path / "empty"), "--out", str(tmp_path / "site")])
    assert code == 2
